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

from typing import Any

from .errors import ConfigurationError, ResolutionError
from .lifecycle import validate_tdd_evidence
from .state import RunStore, utc_now

PHASES = ("red", "green", "refactor")
INVALID_EVIDENCE_VALUES = {"undefined", "null", "none", "n/a", "na"}


def _meaningful(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.strip().lower() not in INVALID_EVIDENCE_VALUES
    )


def initialize_task_tdd(state: dict[str, Any], task_id: str) -> None:
    """Create the replayable, ordered TDD state when a task becomes active."""
    workflow_task = state["workflow"]["tasks"][task_id]
    if "tdd" in workflow_task:
        return
    task_plan_evidence = next(
        (
            stage.get("evidence")
            for stage in state["workflow"]["stages"].values()
            if stage.get("gate", {}).get("evidenceType") == "task-plan"
        ),
        None,
    )
    plan_task = next(
        (
            item
            for item in (task_plan_evidence or {}).get("taskPlan", {}).get("tasks", [])
            if item.get("id") == task_id
        ),
        None,
    )
    if not isinstance(plan_task, dict):
        raise ResolutionError(f"task plan has no task: {task_id}")
    requirement = str(plan_task.get("requirement", "")).strip()
    cases: list[dict[str, Any]] = []
    for index, raw_case in enumerate(plan_task.get("testCases", []), start=1):
        if isinstance(raw_case, str):
            case_id = raw_case
            kind = "delta"
            case_requirement = requirement
        elif isinstance(raw_case, dict):
            case_id = str(raw_case.get("id", "")).strip()
            kind = str(raw_case.get("kind", "delta"))
            case_requirement = str(raw_case.get("requirement", requirement)).strip()
        else:
            raise ConfigurationError(f"task {task_id} testCases[{index}] is invalid")
        if not case_id or not case_requirement or kind not in {"delta", "guard"}:
            raise ConfigurationError(f"task {task_id} test case {index} is incomplete")
        cases.append(
            {
                "id": case_id,
                "kind": kind,
                "requirement": case_requirement,
                "status": "pending",
                "currentPhase": "red",
                "lifecycle": [],
            }
        )
    if not cases:
        raise ConfigurationError(f"task {task_id} requires at least one ordered test case")
    cases[0]["status"] = "active"
    workflow_task["tdd"] = {"status": "active", "activeCase": cases[0]["id"], "cases": cases}


def waive_task_tdd(state: dict[str, Any], task_id: str) -> None:
    """Close pending TDD as waived, while preserving an already completed lifecycle."""
    workflow_task = state["workflow"]["tasks"][task_id]
    tdd = workflow_task.get("tdd")
    if not isinstance(tdd, dict):
        raise ResolutionError(f"task {task_id} has no initialized TDD state")
    if tdd.get("status") == "complete":
        return
    for case in tdd.get("cases", []):
        if case.get("status") != "complete":
            case["status"] = "waived"
            case["currentPhase"] = None
    tdd["status"] = "waived"
    tdd["activeCase"] = None


def _task_tdd(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    workflow = state.get("workflow") or {}
    task = (workflow.get("tasks") or {}).get(task_id)
    if not isinstance(task, dict):
        raise ResolutionError(f"run has no workflow task: {task_id}")
    if task.get("status") != "active":
        raise ResolutionError(f"workflow task {task_id} is not active")
    if workflow.get("currentStage") != "tasks" or task.get("currentStage") != "2":
        raise ResolutionError(f"task {task_id} is not at the TDD stage")
    tdd = task.get("tdd")
    if not isinstance(tdd, dict):
        raise ResolutionError(f"task {task_id} has no initialized TDD state")
    return tdd


def record_tdd_phase(
    store: RunStore,
    run_id: str,
    task_id: str,
    case_id: str,
    phase: str,
    *,
    status: str,
    signal: str,
    failure_kind: str | None = None,
    sensitivity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = store.load(run_id)
    tdd = _task_tdd(state, task_id)
    if tdd.get("status") == "complete":
        raise ResolutionError(f"task {task_id} TDD lifecycle is already complete")
    if case_id != tdd.get("activeCase"):
        raise ResolutionError(f"next TDD case is {tdd.get('activeCase')}, not {case_id}")
    case = next(item for item in tdd["cases"] if item["id"] == case_id)
    expected = case["currentPhase"]
    if phase != expected:
        raise ResolutionError(f"test case {case_id} expects {expected}, not {phase}")
    if not _meaningful(signal):
        raise ConfigurationError("TDD phase requires a signal reference")
    entry: dict[str, Any] = {
        "phase": phase,
        "status": status,
        "signal": signal.strip(),
        "observedAt": utc_now(),
    }
    if phase == "red":
        if status != "fail" or failure_kind not in {"assertion", "missing-behavior"}:
            raise ConfigurationError(
                "RED requires an observed assertion or missing-behavior failure"
            )
        entry["failureKind"] = failure_kind
    elif status != "pass":
        raise ConfigurationError(f"{phase.upper()} requires passing evidence")
    case["lifecycle"].append(entry)
    phase_index = PHASES.index(phase)
    if phase_index + 1 < len(PHASES):
        case["currentPhase"] = PHASES[phase_index + 1]
    else:
        if case["kind"] == "guard":
            if (
                not isinstance(sensitivity, dict)
                or not _meaningful(sensitivity.get("mutation"))
                or sensitivity.get("status") != "fail"
                or not _meaningful(sensitivity.get("signal"))
            ):
                raise ConfigurationError("guard test case requires sensitivity evidence")
            case["sensitivity"] = sensitivity
        case["currentPhase"] = None
        case["status"] = "complete"
        case_index = tdd["cases"].index(case)
        if case_index + 1 < len(tdd["cases"]):
            following = tdd["cases"][case_index + 1]
            following["status"] = "active"
            tdd["activeCase"] = following["id"]
        else:
            tdd["status"] = "complete"
            tdd["activeCase"] = None
    store.append_event(state, "tdd.phase.recorded", f"{task_id}:{case_id}:{phase}")
    store.save(state)
    return tdd


def recorded_tdd_evidence(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    task = (state.get("workflow", {}).get("tasks") or {}).get(task_id) or {}
    tdd = task.get("tdd")
    if not isinstance(tdd, dict) or tdd.get("status") != "complete":
        raise ResolutionError(f"task {task_id} has incomplete TDD lifecycle evidence")
    cases = []
    signals: list[str] = []
    for persisted in tdd["cases"]:
        case = {key: persisted[key] for key in ("id", "requirement", "kind")}
        case["lifecycle"] = [
            {key: value for key, value in entry.items() if key != "observedAt"}
            for entry in persisted["lifecycle"]
        ]
        if "sensitivity" in persisted:
            case["sensitivity"] = persisted["sensitivity"]
        cases.append(case)
        signals.extend(
            entry["signal"] for entry in persisted["lifecycle"] if entry["phase"] != "red"
        )
    evidence = {"status": "pass", "signals": signals, "testCases": cases}
    validate_tdd_evidence("2", evidence)
    return evidence


def assert_recorded_tdd_evidence(
    state: dict[str, Any], task_id: str, submitted: dict[str, Any]
) -> None:
    expected = recorded_tdd_evidence(state, task_id)
    if submitted != expected:
        raise ResolutionError(
            "TDD gate evidence must equal the lifecycle persisted one phase at a time"
        )
