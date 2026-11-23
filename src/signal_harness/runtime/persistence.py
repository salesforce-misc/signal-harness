# Copyright (c) 2026 Salesforce, Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .errors import ConfigurationError, ResolutionError
from .security import redact_value

SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class StateStore(Protocol):
    def load(self, run_id: str) -> dict[str, Any]: ...
    def save(self, state: dict[str, Any]) -> None: ...
    def append_event(self, state: dict[str, Any], event_type: str, detail: Any) -> None: ...
    def lock(self, run_id: str) -> AbstractContextManager[Any]: ...


def default_state_root() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
    return base / "signal-harness"


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "run"


def new_run_id(feature: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")
    return f"{slug(feature)}-{timestamp}-{uuid.uuid4().hex[:6]}"


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event_hash(event: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in event.items() if key != "hash"}
    return _canonical_hash(unsigned)


@dataclass(frozen=True)
class PersistentRunStore:
    root: Path

    def __init__(self, root: Path | None = None):
        object.__setattr__(self, "root", (root or default_state_root()).expanduser().resolve())

    def run_dir(self, run_id: str) -> Path:
        if not SAFE_ID.fullmatch(run_id):
            raise ConfigurationError(f"invalid run id: {run_id}")
        return self.root / run_id

    def state_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run.json"

    @contextmanager
    def lock(self, run_id: str):
        run_dir = self.run_dir(run_id)
        if not run_dir.is_dir():
            raise ResolutionError(f"run does not exist: {run_id}")
        lock_path = run_dir / ".lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def save(self, state: dict[str, Any]) -> None:
        run_id = state.get("runId")
        if not isinstance(run_id, str):
            raise ConfigurationError("run state is missing runId")
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        # Separate from the operation lock: callers may already hold that lock.
        # Every writer participates here, including host read-modify-save APIs.
        with (run_dir / ".save.lock").open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                path = self.state_path(run_id)
                persisted = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                revision = persisted.get("stateRevision", 0)
                expected = state.get("stateRevision", 0)
                if type(revision) is not int or type(expected) is not int or revision != expected:
                    raise ResolutionError(
                        f"stale run state for {run_id}; reload before retrying the operation"
                    )
                updated = {**state, "stateRevision": revision + 1, "updatedAt": utc_now()}
                payload = json.dumps(updated, indent=2, sort_keys=True) + "\n"
                descriptor, raw_path = tempfile.mkstemp(prefix="run-", suffix=".json", dir=run_dir)
                temp_path = Path(raw_path)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, path)
                finally:
                    if temp_path.exists():
                        temp_path.unlink()
                state.update(updated)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def append_event(self, state: dict[str, Any], event_type: str, detail: Any) -> None:
        events = state.setdefault("events", [])
        if state.get("auditVersion") != 1:
            events.append({"at": utc_now(), "type": event_type, "detail": redact_value(detail)})
            return
        event = {
            "sequence": len(events) + 1,
            "previousHash": events[-1]["hash"] if events else None,
            "at": utc_now(),
            "type": event_type,
            "detail": redact_value(detail),
        }
        event["hash"] = _event_hash(event)
        events.append(event)

    def load(self, run_id: str) -> dict[str, Any]:
        path = self.state_path(run_id)
        if not path.is_file():
            raise ResolutionError(f"run does not exist: {run_id}")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ResolutionError(f"cannot read run state {path}: {exc}") from exc
        self.validate_state(state, path, run_id)
        if state.get("auditVersion") == 1:
            previous_hash: str | None = None
            for sequence, event in enumerate(state.get("events", []), start=1):
                if (
                    event.get("sequence") != sequence
                    or event.get("previousHash") != previous_hash
                    or event.get("hash") != _event_hash(event)
                ):
                    raise ResolutionError(f"run audit log integrity check failed: {path}")
                previous_hash = event["hash"]
        return state

    def validate_state(self, state: dict[str, Any], path: Path, run_id: str) -> None:
        if state.get("runId") != run_id:
            raise ResolutionError(f"unsupported or mismatched run state: {path}")
