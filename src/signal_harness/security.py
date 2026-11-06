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

import re
import shlex
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .errors import ConfigurationError, ResolutionError

if TYPE_CHECKING:
    from .state import RunStore

DEFAULT_SECURITY: dict[str, Any] = {
    "requireCommandApproval": True,
    "limits": {
        "maxCommandTimeoutSeconds": 1800,
        "maxCommandRuns": 100,
        "maxParallelTasks": 4,
        "maxOutputBytes": 20_000,
        "maxCostUsd": 100.0,
    },
    "retentionDays": 30,
    "commandApprovals": {},
    "usage": {"commandRuns": 0, "costUsd": 0.0},
}

_SHELL_WRAPPERS = {"bash", "cmd", "fish", "powershell", "pwsh", "sh", "zsh"}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]+\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(password|passwd|token|secret|api[_-]?key)\s*([=:])\s*([^\s,;]+)"),
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def initialize_security_state() -> dict[str, Any]:
    return deepcopy(DEFAULT_SECURITY)


def redact_text(value: str) -> str:
    redacted = value
    redacted = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", redacted)
    for pattern in _SECRET_PATTERNS[1:4]:
        redacted = pattern.sub("[REDACTED]", redacted)
    redacted = _SECRET_PATTERNS[4].sub(r"\1\2[REDACTED]", redacted)
    redacted = re.sub(r"\bglpat-[A-Za-z0-9_-]+", "[REDACTED]", redacted)
    redacted = re.sub(r"(https?://)[^/\s@]+@", r"\1[REDACTED]@", redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.fullmatch(
                r"(?i)(password|passwd|token|secret|api[_-]?key|access[_-]?token|authorization)",
                str(key),
            )
            else redact_value(item)
            for key, item in value.items()
        }
    return value


def _repository(state: dict[str, Any], repository: str) -> dict[str, Any]:
    repositories = state.get("plan", {}).get("repositories", [])
    match = next((item for item in repositories if item.get("id") == repository), None)
    if not isinstance(match, dict):
        raise ResolutionError(f"run has no repository: {repository}")
    return match


def _trusted_signal_commands(repository: dict[str, Any]) -> set[str]:
    sources = repository.get("profile", {}).get("signals", [])
    return {
        source["commandRef"]
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("commandRef"), str)
    }


def authorize_command(
    state: dict[str, Any],
    repository: str,
    command_id: str,
    raw_command: str,
    timeout: int,
) -> list[str]:
    security = state.get("security") or initialize_security_state()
    limits = security["limits"]
    if timeout <= 0:
        raise ConfigurationError("verification timeout must be positive")
    if timeout > limits["maxCommandTimeoutSeconds"]:
        raise ConfigurationError(
            f"verification timeout exceeds maximum {limits['maxCommandTimeoutSeconds']} seconds"
        )
    try:
        command = shlex.split(raw_command)
    except ValueError as exc:
        raise ConfigurationError(
            f"repository command {command_id} cannot be parsed: {exc}"
        ) from exc
    if not command:
        raise ConfigurationError(f"repository command {command_id} is empty")
    executable = command[0].rsplit("/", 1)[-1].lower()
    if executable in _SHELL_WRAPPERS:
        raise ConfigurationError(
            f"repository command {command_id} uses prohibited shell wrapper {executable}"
        )
    if security.get("usage", {}).get("commandRuns", 0) >= limits["maxCommandRuns"]:
        raise ConfigurationError("run has reached the maximum verification command count")

    repository_value = _repository(state, repository)
    approval_key = f"{repository}:{command_id}"
    trusted = command_id in _trusted_signal_commands(repository_value)
    approved = approval_key in security.get("commandApprovals", {})
    if security.get("requireCommandApproval", True) and not (trusted or approved):
        raise ConfigurationError(
            f"repository command {approval_key} is not trusted; record an explicit approval"
        )
    return command


def approve_command(
    store: RunStore,
    run_id: str,
    *,
    repository: str,
    command_id: str,
    actor: str,
) -> dict[str, Any]:
    if not actor.strip():
        raise ConfigurationError("command approval requires an actor")
    state = store.load(run_id)
    repository_value = _repository(state, repository)
    commands = repository_value.get("profile", {}).get("commands", {})
    if command_id not in commands:
        raise ResolutionError(f"repository {repository} has no command: {command_id}")
    security = state.setdefault("security", initialize_security_state())
    key = f"{repository}:{command_id}"
    existing = security["commandApprovals"].get(key)
    if existing:
        return existing
    approval = {
        "repository": repository,
        "commandId": command_id,
        "actor": actor,
        "approvedAt": _utc_now(),
    }
    security["commandApprovals"][key] = approval
    store.append_event(state, "security.command.approved", approval)
    store.save(state)
    return approval


def record_command_run(state: dict[str, Any]) -> None:
    security = state.setdefault("security", initialize_security_state())
    usage = security.setdefault("usage", {"commandRuns": 0})
    usage["commandRuns"] = int(usage.get("commandRuns", 0)) + 1


def record_cost(
    store: RunStore,
    run_id: str,
    *,
    amount_usd: float,
    source: str,
) -> dict[str, Any]:
    if amount_usd <= 0:
        raise ConfigurationError("cost amount must be positive")
    source = source.strip()
    if not source:
        raise ConfigurationError("cost source must not be empty")
    state = store.load(run_id)
    security = state.setdefault("security", initialize_security_state())
    usage = security.setdefault("usage", {"commandRuns": 0, "costUsd": 0.0})
    current = float(usage.get("costUsd", 0.0))
    maximum = float(security["limits"]["maxCostUsd"])
    updated = round(current + amount_usd, 6)
    if updated > maximum:
        raise ConfigurationError(
            f"cost budget would be exceeded: {updated:.2f} > {maximum:.2f} USD"
        )
    usage["costUsd"] = updated
    entry = {"source": source, "amountUsd": amount_usd, "costUsd": updated}
    state.setdefault("costEvents", []).append({**entry, "recordedAt": _utc_now()})
    store.append_event(state, "security.cost.recorded", entry)
    store.save(state)
    return entry
