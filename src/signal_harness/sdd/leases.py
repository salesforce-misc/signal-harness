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

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from signal_harness.runtime.errors import ConfigurationError, ResolutionError
from signal_harness.sdd.state import RunStore, utc_now


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _active_lease(lease: dict[str, Any], now: datetime) -> bool:
    return lease.get("status") == "active" and _parse(lease["expiresAt"]) > now


def claim_task(
    store: RunStore,
    run_id: str,
    task_id: str,
    *,
    owner: str,
    ttl_seconds: int = 3600,
) -> dict[str, Any]:
    owner = owner.strip()
    if not owner:
        raise ConfigurationError("lease owner must not be empty")
    if not 1 <= ttl_seconds <= 86_400:
        raise ConfigurationError("lease TTL must be between 1 and 86400 seconds")
    with store.lock(run_id):
        state = store.load(run_id)
        workflow_task = (state.get("workflow") or {}).get("tasks", {}).get(task_id)
        if not isinstance(workflow_task, dict) or workflow_task.get("status") != "active":
            raise ResolutionError(f"workflow task is not active: {task_id}")
        task = state.get("tasks", {}).get(task_id)
        if not isinstance(task, dict):
            raise ResolutionError(f"unknown task: {task_id}")
        repo = task["repo"]
        now = datetime.now(UTC)
        leases = state.setdefault("leases", {})
        existing = leases.get(repo)
        if existing and _active_lease(existing, now):
            if existing["task"] == task_id and existing["owner"] == owner:
                return existing
            raise ResolutionError(
                f"repository {repo} is leased by {existing['owner']} for {existing['task']}"
            )
        max_parallel = state.get("security", {}).get("limits", {}).get("maxParallelTasks", 4)
        active_count = sum(_active_lease(item, now) for item in leases.values())
        if active_count >= max_parallel:
            raise ResolutionError(f"run reached max parallel task limit: {max_parallel}")
        lease = {
            "repo": repo,
            "task": task_id,
            "owner": owner,
            "token": uuid.uuid4().hex,
            "status": "active",
            "claimedAt": utc_now(),
            "expiresAt": (now + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z"),
        }
        leases[repo] = lease
        store.append_event(state, "task.claimed", f"{task_id}@{repo} by {owner}")
        store.save(state)
        return lease


def release_task(
    store: RunStore,
    run_id: str,
    task_id: str,
    *,
    owner: str,
    token: str,
) -> None:
    with store.lock(run_id):
        state = store.load(run_id)
        task = state.get("tasks", {}).get(task_id)
        if not isinstance(task, dict):
            raise ResolutionError(f"unknown task: {task_id}")
        lease = state.get("leases", {}).get(task["repo"])
        if not lease:
            return
        if (
            lease.get("task") != task_id
            or lease.get("owner") != owner
            or lease.get("token") != token
        ):
            raise ResolutionError("lease release credentials do not match the active lease")
        lease.update({"status": "released", "releasedAt": utc_now()})
        store.append_event(state, "task.released", f"{task_id}@{task['repo']} by {owner}")
        store.save(state)
