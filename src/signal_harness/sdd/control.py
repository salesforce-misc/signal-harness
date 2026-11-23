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

from datetime import UTC, datetime
from typing import Any

from signal_harness.runtime.errors import ConfigurationError, ResolutionError
from signal_harness.sdd.state import RunStore, utc_now

TERMINAL_STATUSES = {"complete", "evaluation-complete-with-waivers", "cancelled"}


def _actor(value: str) -> str:
    value = value.strip()
    if not value:
        raise ConfigurationError("actor must not be empty")
    return value


def pause_run(store: RunStore, run_id: str, *, actor: str, reason: str) -> dict[str, Any]:
    state = store.load(run_id)
    actor = _actor(actor)
    reason = reason.strip()
    if not reason:
        raise ConfigurationError("pause reason must not be empty")
    if state["status"] in TERMINAL_STATUSES:
        raise ResolutionError(f"cannot pause {state['status']} run {run_id}")
    if state["status"] == "paused":
        return state
    state["control"] = {
        "previousStatus": state["status"],
        "pausedBy": actor,
        "pausedAt": utc_now(),
        "reason": reason,
    }
    state["status"] = "paused"
    store.append_event(state, "run.paused", f"{actor}: {reason}")
    store.save(state)
    return state


def resume_run(store: RunStore, run_id: str, actor: str) -> dict[str, Any]:
    state = store.load(run_id)
    actor = _actor(actor)
    if state["status"] in TERMINAL_STATUSES:
        raise ResolutionError(f"cannot resume {state['status']} run {run_id}")
    pending = [
        request_id
        for request_id, request in state.get("humanRequests", {}).items()
        if request.get("status") == "pending"
    ]
    if pending:
        raise ResolutionError(f"run has unresolved human requests: {pending}")
    if state["status"] == "active":
        return state
    state["status"] = "active"
    state.setdefault("control", {})["resumedBy"] = actor
    state["control"]["resumedAt"] = utc_now()
    store.append_event(state, "run.resumed", actor)
    store.save(state)
    return state


def cancel_run(store: RunStore, run_id: str, *, actor: str, reason: str) -> dict[str, Any]:
    state = store.load(run_id)
    actor = _actor(actor)
    reason = reason.strip()
    if not reason:
        raise ConfigurationError("cancel reason must not be empty")
    if state["status"] == "cancelled":
        return state
    if state["status"] in TERMINAL_STATUSES:
        raise ResolutionError(f"cannot cancel {state['status']} run {run_id}")
    state["status"] = "cancelled"
    state["cancelledAt"] = utc_now()
    state["cancelledBy"] = actor
    state["cancelReason"] = reason
    store.append_event(state, "run.cancelled", f"{actor}: {reason}")
    store.save(state)
    return state


def recover_run(store: RunStore, run_id: str) -> dict[str, Any]:
    state = store.load(run_id)
    blockers: list[str] = []
    if state["status"] in TERMINAL_STATUSES:
        blockers.append(f"terminal run status is {state['status']}")
    pending = [
        request_id
        for request_id, request in state.get("humanRequests", {}).items()
        if request.get("status") == "pending"
    ]
    if pending:
        blockers.append(f"unresolved human requests: {pending}")
    now = datetime.now(UTC)
    expired = []
    for repo, lease in state.get("leases", {}).items():
        expires_at = lease.get("expiresAt")
        if lease.get("status") == "expired" or (
            lease.get("status") == "active"
            and isinstance(expires_at, str)
            and datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= now
        ):
            expired.append(repo)
    if expired:
        blockers.append(f"expired repository leases require release: {expired}")
    return {
        "runId": run_id,
        "status": state["status"],
        "recoverable": not blockers,
        "blockers": blockers,
        "nextActions": _next_actions(state, pending),
    }


def _next_actions(state: dict[str, Any], pending: list[str]) -> list[str]:
    if state["status"] in TERMINAL_STATUSES:
        return []
    actions: list[str] = []
    workflow = state.get("workflow") or {}
    if pending:
        actions.append("resolve-human")
    if state["status"] == "paused":
        actions.append("resume-run")
    if state["status"] in {"planned", "active"}:
        actions.append("next")
    unavailable = state.get("plan", {}).get("unavailableRepositories", [])
    if workflow.get("currentStage") == "0" and unavailable:
        access_action = (
            "obtain-repository-access"
            if any(
                item.get("requiredByManifest") or item.get("requiredByDiscovery")
                for item in unavailable
            )
            else "assess-repository-access"
        )
        actions.insert(0, access_action)
    return actions


def run_summary(store: RunStore, run_id: str) -> dict[str, Any]:
    state = store.load(run_id)
    workflow = state.get("workflow") or {}
    pending = [
        request_id
        for request_id, request in state.get("humanRequests", {}).items()
        if request.get("status") == "pending"
    ]
    task_summary = {
        task_id: {
            "status": (
                "complete"
                if (workflow.get("tasks", {}).get(task_id) or {}).get("status") == "complete"
                else task.get("status")
            ),
            "repo": task.get("repo"),
            "signals": len(task.get("signals", [])),
            "pullRequest": (task.get("pullRequest") or {}).get("url"),
        }
        for task_id, task in state.get("tasks", {}).items()
    }
    return {
        "runId": run_id,
        "feature": state["feature"],
        "status": state["status"],
        "currentStage": workflow.get("currentStage"),
        "pendingHumanRequests": pending,
        "pendingNotifications": sum(
            item.get("status") == "pending" for item in state.get("outbox", [])
        ),
        "artifacts": len(state.get("artifacts", [])),
        "tasks": task_summary,
        "nextActions": _next_actions(state, pending),
        "unavailableRepositories": state.get("plan", {}).get("unavailableRepositories", []),
    }
