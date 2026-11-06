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
from dataclasses import asdict, dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Protocol

from .errors import ConfigurationError, ResolutionError
from .state import RunStore, utc_now

_DIGEST = re.compile(r"^[a-z0-9][a-z0-9._+-]*:[0-9a-fA-F]+$")
ARTIFACT_PROVIDER_ENTRY_POINT_GROUP = "signal_harness.artifact_providers"


@dataclass(frozen=True)
class ArtifactRecord:
    """Immutable provenance returned by a distribution-owned artifact provider."""

    id: str
    uri: str
    digest: str
    producer_task: str
    source_revision: str
    created_by: str

    def as_dict(self) -> dict[str, str]:
        value = asdict(self)
        return {
            "id": value["id"],
            "uri": value["uri"],
            "digest": value["digest"],
            "producerTask": value["producer_task"],
            "sourceRevision": value["source_revision"],
            "createdBy": value["created_by"],
        }


class ArtifactProvider(Protocol):
    """Distribution extension boundary; core never assumes Maven, npm, or OCI."""

    def publish(
        self,
        *,
        artifact_id: str,
        producer_task: str,
        source_revision: str,
        source: str,
    ) -> ArtifactRecord: ...


def installed_artifact_providers() -> dict[str, EntryPoint]:
    providers: dict[str, EntryPoint] = {}
    duplicates: set[str] = set()
    for entry_point in entry_points(group=ARTIFACT_PROVIDER_ENTRY_POINT_GROUP):
        if entry_point.name in providers:
            duplicates.add(entry_point.name)
        providers[entry_point.name] = entry_point
    if duplicates:
        raise ConfigurationError(f"duplicate artifact providers: {sorted(duplicates)}")
    return providers


def load_artifact_provider(name: str, config: dict[str, Any]) -> ArtifactProvider:
    entry_point = installed_artifact_providers().get(name)
    if entry_point is None:
        raise ConfigurationError(
            f"unknown artifact provider {name!r}; installed: "
            f"{sorted(installed_artifact_providers())}"
        )
    factory = entry_point.load()
    provider = factory(config)
    if not hasattr(provider, "publish"):
        raise ConfigurationError(f"artifact provider {name!r} does not implement publish")
    return provider


def record_artifact_handoff(
    store: RunStore,
    run_id: str,
    *,
    task_id: str,
    artifact_id: str,
    uri: str,
    digest: str,
    source_revision: str,
    provider: str,
) -> dict[str, str]:
    """Persist a provider result only when it matches the frozen task declaration."""

    values = {
        "artifact id": artifact_id,
        "URI": uri,
        "source revision": source_revision,
        "provider": provider,
    }
    for field_name, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"artifact handoff {field_name} must not be empty")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ConfigurationError("artifact handoff digest must be algorithm:hex")

    state = store.load(run_id)
    task = state.get("tasks", {}).get(task_id)
    if not isinstance(task, dict):
        raise ResolutionError(f"run state has no task: {task_id}")
    declaration = next(
        (item for item in task.get("produces", []) if item.get("id") == artifact_id), None
    )
    if declaration is None:
        raise ConfigurationError(f"task {task_id} does not declare produced artifact {artifact_id}")
    published_head = task.get("publishedHead")
    if not published_head and state.get("plan", {}).get("policyMode") == "evaluation":
        published_head = task.get("frozenHead")
    if not isinstance(published_head, str) or not published_head:
        raise ResolutionError(
            f"task {task_id} must publish or locally freeze its immutable source revision "
            "before artifact handoff"
        )
    if source_revision != published_head:
        raise ConfigurationError(
            f"artifact {artifact_id} source revision {source_revision} does not match "
            f"published task head {published_head}"
        )
    if state.get("plan", {}).get("policyMode") == "production" and (
        uri.startswith(("file://", "local://")) or provider.endswith((".local", ".fixture"))
    ):
        raise ConfigurationError(
            f"production artifact {artifact_id} requires a remotely resolvable immutable URI"
        )

    record = ArtifactRecord(
        id=artifact_id.strip(),
        uri=uri.strip(),
        digest=digest.lower(),
        producer_task=task_id,
        source_revision=source_revision,
        created_by=provider.strip(),
    ).as_dict()
    existing = next(
        (
            item
            for item in state.get("artifactHandoffs", [])
            if item.get("producerTask") == task_id and item.get("id") == artifact_id
        ),
        None,
    )
    if existing is not None:
        comparable = {key: existing.get(key) for key in record}
        if comparable != record:
            raise ResolutionError(
                f"artifact handoff {task_id}:{artifact_id} is immutable and already differs"
            )
        return existing

    record["recordedAt"] = utc_now()
    state.setdefault("artifactHandoffs", []).append(record)
    store.append_event(
        state,
        "artifact.handoff.recorded",
        {
            "task": task_id,
            "artifact": artifact_id,
            "digest": record["digest"],
            "provider": record["createdBy"],
        },
    )
    store.save(state)
    return record


def resolved_task_artifacts(state: dict[str, Any], task_id: str) -> list[dict[str, str]]:
    task = state.get("tasks", {}).get(task_id)
    if not isinstance(task, dict):
        raise ResolutionError(f"run state has no task: {task_id}")
    records = {
        (item.get("producerTask"), item.get("id")): item
        for item in state.get("artifactHandoffs", [])
        if isinstance(item, dict)
    }
    resolved: list[dict[str, str]] = []
    missing: list[str] = []
    for consumption in task.get("consumes", []):
        key = (consumption["fromTask"], consumption["artifact"])
        record = records.get(key)
        if record is None:
            missing.append(f"{key[0]}:{key[1]}")
        else:
            resolved.append(record)
    if missing:
        raise ResolutionError(
            f"task {task_id} is waiting for provenance-checked artifacts: {sorted(missing)}"
        )
    return resolved
