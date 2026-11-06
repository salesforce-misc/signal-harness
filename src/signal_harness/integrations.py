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
from typing import Any, Protocol

from .errors import ConfigurationError, ResolutionError
from .security import redact_text
from .state import RunStore, utc_now

SAFE_EXTERNAL_KEY = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$")


class NotificationProvider(Protocol):
    def send(self, event: dict[str, Any]) -> str: ...


def _safe_key(value: str, field: str) -> str:
    value = value.strip()
    if not SAFE_EXTERNAL_KEY.fullmatch(value):
        raise ConfigurationError(f"invalid {field}: {value!r}")
    return value


def enqueue_integration_event(
    store: RunStore,
    run_id: str,
    *,
    event_id: str,
    channel: str,
    kind: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    event_id = _safe_key(event_id, "event id")
    channel = _safe_key(channel, "channel")
    kind = _safe_key(kind, "event kind")
    if not isinstance(payload, dict):
        raise ConfigurationError("integration event payload must be an object")
    intended = {
        "id": event_id,
        "channel": channel,
        "kind": kind,
        "payload": payload,
    }
    state = store.load(run_id)
    existing = next((item for item in state.get("outbox", []) if item.get("id") == event_id), None)
    if existing:
        if all(existing.get(key) == value for key, value in intended.items()):
            return existing
        raise ResolutionError(f"integration event {event_id} already exists with different content")
    event = {
        **intended,
        "status": "pending",
        "attempts": 0,
        "externalId": None,
    }
    state.setdefault("outbox", []).append(event)
    store.append_event(state, "integration.queued", f"{channel}:{kind}:{event_id}")
    store.save(state)
    return event


def request_human_input(
    store: RunStore,
    run_id: str,
    *,
    request_id: str,
    kind: str,
    prompt: str,
    channels: tuple[str, ...] = (),
) -> dict[str, Any]:
    request_id = _safe_key(request_id, "request id")
    if kind not in {"question", "approval"}:
        raise ConfigurationError("human request kind must be question or approval")
    prompt = prompt.strip()
    if not prompt:
        raise ConfigurationError("human request prompt must not be empty")
    normalized_channels = tuple(dict.fromkeys(_safe_key(item, "channel") for item in channels))
    state = store.load(run_id)
    if state["status"] in {"complete", "evaluation-complete-with-waivers", "cancelled"}:
        raise ResolutionError(f"cannot add human request to {state['status']} run")
    existing = state.setdefault("humanRequests", {}).get(request_id)
    intended = {
        "id": request_id,
        "kind": kind,
        "prompt": prompt,
        "channels": list(normalized_channels),
    }
    if existing:
        if all(existing.get(key) == value for key, value in intended.items()):
            return existing
        raise ResolutionError(f"human request {request_id} already exists with different content")
    request = {
        **intended,
        "status": "pending",
        "requestedAt": utc_now(),
        "resolution": None,
    }
    state["humanRequests"][request_id] = request
    for channel in normalized_channels:
        state.setdefault("outbox", []).append(
            {
                "id": f"human:{request_id}:requested:{channel}",
                "channel": channel,
                "kind": "human.requested",
                "payload": {
                    "runId": run_id,
                    "requestId": request_id,
                    "requestKind": kind,
                    "prompt": prompt,
                },
                "status": "pending",
                "attempts": 0,
                "externalId": None,
            }
        )
    state["status"] = "waiting"
    store.append_event(state, "human.requested", f"{request_id} ({kind})")
    store.save(state)
    return request


def resolve_human_input(
    store: RunStore,
    run_id: str,
    request_id: str,
    *,
    actor: str,
    decision: str,
    response: str,
) -> dict[str, Any]:
    if decision not in {"answered", "approved", "rejected"}:
        raise ConfigurationError("human decision must be answered, approved, or rejected")
    actor = actor.strip()
    response = response.strip()
    if not actor or not response:
        raise ConfigurationError("human resolution requires actor and response")
    state = store.load(run_id)
    request = state.get("humanRequests", {}).get(request_id)
    if not isinstance(request, dict):
        raise ResolutionError(f"unknown human request: {request_id}")
    resolution = {
        "actor": actor,
        "decision": decision,
        "response": response,
    }
    if request["status"] != "pending":
        existing = request.get("resolution") or {}
        if all(existing.get(key) == value for key, value in resolution.items()):
            return request
        raise ResolutionError(f"human request {request_id} already resolved differently")
    request["status"] = "resolved"
    request["resolution"] = {**resolution, "resolvedAt": utc_now()}
    if not any(item.get("status") == "pending" for item in state.get("humanRequests", {}).values()):
        state["status"] = "active"
    store.append_event(state, "human.resolved", f"{request_id}: {decision} by {actor}")
    store.save(state)
    return request


def record_external_id(
    store: RunStore,
    run_id: str,
    system: str,
    kind: str,
    value: str,
) -> str:
    system = _safe_key(system, "external system")
    kind = _safe_key(kind, "external id kind")
    value = _safe_key(value, "external id")
    state = store.load(run_id)
    external = state.setdefault("externalIds", {}).setdefault(system, {})
    existing = external.get(kind)
    if existing is not None and existing != value:
        raise ResolutionError(f"{system}:{kind} already mapped to {existing}")
    if existing == value:
        return value
    external[kind] = value
    store.append_event(state, "external-id.recorded", f"{system}:{kind} -> {value}")
    store.save(state)
    return value


def dispatch_outbox(
    store: RunStore,
    run_id: str,
    providers: dict[str, NotificationProvider],
) -> dict[str, int]:
    with store.lock(run_id):
        state = store.load(run_id)
        sent = 0
        failed = 0
        for item in state.get("outbox", []):
            if item.get("status") != "pending":
                continue
            provider = providers.get(item["channel"])
            if provider is None:
                continue
            item["attempts"] += 1
            try:
                external_id = provider.send(
                    {"id": item["id"], "kind": item["kind"], "payload": item["payload"]}
                )
                if not isinstance(external_id, str) or not external_id.strip():
                    raise ValueError("provider returned an empty external id")
            except Exception as exc:  # Provider failures are persisted for operator recovery.
                item["lastError"] = redact_text(str(exc))
                failed += 1
                continue
            item.update({"status": "sent", "externalId": external_id, "sentAt": utc_now()})
            store.append_event(state, "notification.sent", f"{item['id']} -> {external_id}")
            sent += 1
        store.save(state)
        pending = sum(item.get("status") == "pending" for item in state.get("outbox", []))
        return {"sent": sent, "pending": pending, "failed": failed}
