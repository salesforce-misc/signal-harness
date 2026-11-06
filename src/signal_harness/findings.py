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
from typing import Any

from .errors import ConfigurationError, ResolutionError
from .lifecycle import validate_tdd_evidence
from .state import RunStore, utc_now

SAFE_FINDING_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$")


def record_finding(
    store: RunStore,
    run_id: str,
    task_id: str,
    *,
    finding_id: str,
    source: str,
    severity: str,
    summary: str,
    evidence: str,
    affected_commands: tuple[str, ...] = (),
    behavioral_fix: bool = False,
) -> dict[str, Any]:
    if not SAFE_FINDING_ID.fullmatch(finding_id):
        raise ConfigurationError(f"invalid finding id: {finding_id!r}")
    if severity not in {"critical", "high", "medium", "low", "info"}:
        raise ConfigurationError("finding severity must be critical, high, medium, low, or info")
    if not all(value.strip() for value in (source, summary, evidence)):
        raise ConfigurationError("finding source, summary, and evidence must not be empty")
    if not all(isinstance(value, str) and value.strip() for value in affected_commands):
        raise ConfigurationError("affected command ids must not be empty")
    state = store.load(run_id)
    task = state.get("tasks", {}).get(task_id)
    if not isinstance(task, dict):
        raise ResolutionError(f"unknown task: {task_id}")
    repository = next(item for item in state["plan"]["repositories"] if item["id"] == task["repo"])
    unknown_commands = sorted(
        set(affected_commands) - set(repository["profile"].get("commands", {}))
    )
    if unknown_commands:
        raise ResolutionError(
            f"finding references unknown commands for {task['repo']}: {unknown_commands}"
        )
    intended = {
        "id": finding_id,
        "task": task_id,
        "source": source.strip(),
        "severity": severity,
        "summary": summary.strip(),
        "evidence": evidence.strip(),
        "affectedCommands": list(dict.fromkeys(affected_commands)),
        "behavioralFix": behavioral_fix,
    }
    findings = state.setdefault("findings", {})
    existing = findings.get(finding_id)
    if existing:
        if all(existing.get(key) == value for key, value in intended.items()):
            return existing
        raise ResolutionError(f"finding {finding_id} already exists with different content")
    finding = {
        **intended,
        "status": "open",
        "recordedAt": utc_now(),
        "resolution": None,
    }
    findings[finding_id] = finding
    store.append_event(state, "finding.recorded", intended)
    store.save(state)
    return finding


def resolve_finding(
    store: RunStore,
    run_id: str,
    task_id: str,
    finding_id: str,
    *,
    disposition: str,
    actor: str,
    rationale: str,
    evidence: str | None = None,
    regression_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if disposition not in {"address", "disagree", "defer"}:
        raise ConfigurationError("finding disposition must be address, disagree, or defer")
    if not actor.strip() or not rationale.strip():
        raise ConfigurationError("finding resolution requires actor and rationale")
    if disposition in {"disagree", "defer"} and not (evidence or "").strip():
        raise ResolutionError(f"{disposition} disposition requires evidence")
    state = store.load(run_id)
    finding = state.get("findings", {}).get(finding_id)
    if not isinstance(finding, dict) or finding.get("task") != task_id:
        raise ResolutionError(f"unknown finding for task {task_id}: {finding_id}")
    if disposition == "address" and finding.get("behavioralFix"):
        if not isinstance(regression_evidence, dict):
            raise ResolutionError("behavioral fix requires regression TDD evidence")
        validate_tdd_evidence("regression", regression_evidence)
        if not any(
            case.get("findingId") == finding_id
            for case in regression_evidence.get("testCases", [])
            if isinstance(case, dict)
        ):
            raise ResolutionError(
                f"regression evidence must trace a test case to finding {finding_id}"
            )
    resolution = {
        "disposition": disposition,
        "actor": actor.strip(),
        "rationale": rationale.strip(),
        "evidence": evidence.strip() if evidence else None,
        "regressionEvidence": regression_evidence,
    }
    if finding["status"] == "resolved":
        existing = finding.get("resolution") or {}
        if all(existing.get(key) == value for key, value in resolution.items()):
            return finding
        raise ResolutionError(f"finding {finding_id} already resolved differently")
    finding["status"] = "resolved"
    finding["resolution"] = {**resolution, "resolvedAt": utc_now()}
    store.append_event(state, "finding.resolved", {"id": finding_id, **resolution})
    store.save(state)
    return finding


def review_readiness(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    blockers: list[str] = []
    for finding in state.get("findings", {}).values():
        if finding.get("task") != task_id:
            continue
        if finding.get("status") != "resolved":
            blockers.append(f"finding {finding['id']} is open")
            continue
        resolution = finding.get("resolution") or {}
        if resolution.get("disposition") != "address":
            continue
        resolved_at = resolution.get("resolvedAt", "")
        for command_id in finding.get("affectedCommands", []):
            signals = state.get("tasks", {}).get(task_id, {}).get("signals", [])
            rerun = next(
                (
                    signal
                    for signal in reversed(signals)
                    if signal.get("metadata", {}).get("commandId") == command_id
                    and signal.get("observedAt", "") > resolved_at
                    and signal.get("status") == "pass"
                ),
                None,
            )
            if rerun is None:
                blockers.append(f"finding {finding['id']} requires passing rerun of {command_id}")
    return {"ready": not blockers, "blockers": blockers}
