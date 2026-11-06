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

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from .config import load_yaml
from .context import collect_context
from .errors import ConfigurationError, ResolutionError
from .feature_context import feature_context_patterns
from .git import (
    changed_paths,
    current_branch,
    head_sha,
    is_ancestor,
    is_dirty,
    normalize_remote,
    origin_url,
    repository_root,
)
from .graph import task_order
from .lifecycle import (
    gate_evidence_outcome,
    initial_task_states,
    initial_workflow_state,
    next_stage_id,
    per_task_stages,
    stage_after_task_phase,
    validate_check_derivation,
    validate_gate_evidence,
)
from .models import WorkspaceTask
from .planner import WorkspacePlan, load_profile, resolve_profile_path
from .security import initialize_security_state, redact_value

STATE_VERSION = 1
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


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
class RunStore:
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

    def create(
        self,
        plan: WorkspacePlan,
        workspace_path: Path,
        requested_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = requested_id or new_run_id(plan.workspace.feature)
        run_dir = self.run_dir(run_id)
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ResolutionError(f"run already exists: {run_id}") from exc

        plan_value = plan.as_dict()
        now = utc_now()
        state: dict[str, Any] = {
            "stateVersion": STATE_VERSION,
            "runId": run_id,
            "feature": plan.workspace.feature,
            "status": "planned",
            "workspace": str(workspace_path.expanduser().resolve()),
            "checkoutOverrides": dict(plan.checkout_overrides),
            "planHash": _canonical_hash(plan_value),
            "plan": plan_value,
            "tasks": {
                task["id"]: {
                    "status": "planned",
                    "repo": task["repo"],
                    "branch": None,
                    "worktree": None,
                    "pullRequest": None,
                    "requiredChecks": task.get("requiredChecks", []),
                    "base": task.get("base") or {"kind": "repository-default"},
                    "requiredContext": task.get("context", []),
                    "contextAcknowledged": [],
                    "surfaces": task.get("surfaces", []),
                    "callers": task.get("callers", []),
                    "produces": task.get("produces", []),
                    "consumes": task.get("consumes", []),
                }
                for task in plan_value["tasks"]
            },
            "workflow": (
                initial_workflow_state(plan.workflow, plan_value["tasks"])
                if plan.workflow
                else None
            ),
            "createdAt": now,
            "updatedAt": now,
            "auditVersion": 1,
            "events": [],
            "security": initialize_security_state(),
        }
        if plan.phase == "discovery":
            state["discoveryPlanHash"] = state["planHash"]
        self.append_event(state, "run.started", "validated plan persisted")
        self.save(state)
        return state

    def add_discovery_repository(
        self,
        run_id: str,
        repository: dict[str, Any],
        *,
        reason: str,
        evidence: tuple[str, ...],
        actor: str = "system",
    ) -> dict[str, Any]:
        state = self.load(run_id)
        workflow = state.get("workflow") or {}
        if state.get("plan", {}).get("phase") != "discovery" or workflow.get("currentStage") != "0":
            raise ResolutionError(
                f"run {run_id} can engage repositories only during Stage 0 discovery"
            )
        repositories = state["plan"].setdefault("repositories", [])
        if any(item["id"] == repository["id"] for item in repositories):
            return state
        repositories.append(repository)
        state["plan"]["unavailableRepositories"] = [
            item
            for item in state["plan"].get("unavailableRepositories", [])
            if item["id"] != repository["id"]
        ]
        state.setdefault("repositoryEngagement", {})[repository["id"]] = {
            "status": "engaged",
            "reason": reason,
            "evidence": list(evidence),
            "engagedAt": utc_now(),
            "revision": repository["context"]["revision"],
        }
        state["planHash"] = _canonical_hash(state["plan"])
        state["discoveryPlanHash"] = state["planHash"]
        state["updatedAt"] = utc_now()
        self.append_event(
            state,
            "discovery.repository.engaged",
            {
                "repository": repository["id"],
                "actor": actor,
                "action": "engage-repository",
                "outcome": "engaged",
                "reason": reason,
                "evidence": list(evidence),
            },
        )
        self.save(state)
        return state

    def record_unavailable_discovery_repository(
        self,
        run_id: str,
        *,
        repo_id: str,
        remote: str,
        reason: str,
        evidence: tuple[str, ...],
        error: str,
        actor: str = "system",
    ) -> dict[str, Any]:
        state = self.load(run_id)
        if (
            state.get("plan", {}).get("phase") != "discovery"
            or (state.get("workflow") or {}).get("currentStage") != "0"
        ):
            raise ResolutionError(
                f"run {run_id} can engage repositories only during Stage 0 discovery"
            )
        unavailable = state["plan"].setdefault("unavailableRepositories", [])
        unavailable[:] = [item for item in unavailable if item["id"] != repo_id]
        unavailable.append(
            {
                "id": repo_id,
                "remote": remote,
                "reason": error,
                "requiredByDiscovery": True,
            }
        )
        state.setdefault("repositoryEngagement", {})[repo_id] = {
            "status": "unavailable",
            "reason": reason,
            "evidence": list(evidence),
            "error": error,
            "attemptedAt": utc_now(),
        }
        state["planHash"] = _canonical_hash(state["plan"])
        state["discoveryPlanHash"] = state["planHash"]
        state["updatedAt"] = utc_now()
        self.append_event(
            state,
            "discovery.repository.unavailable",
            {
                "repository": repo_id,
                "actor": actor,
                "action": "engage-repository",
                "outcome": "unavailable",
                "reason": reason,
                "evidence": list(evidence),
                "error": error,
            },
        )
        self.save(state)
        return state

    def migrate_author_permissions(
        self,
        run_id: str,
        permissions: dict[str, Any],
        *,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Explicitly migrate missing legacy permissions against pinned identities."""
        with self.lock(run_id):
            state = self.load(run_id)

            def deny(message: str, error_type: type[Exception] = ConfigurationError) -> NoReturn:
                self.append_event(
                    state,
                    "security.authorization.denied",
                    {
                        "actor": actor,
                        "action": "migrate-author-permissions",
                        "repositories": sorted(permissions)
                        if isinstance(permissions, dict)
                        else [],
                        "outcome": "denied",
                        "reason": message[:500],
                    },
                )
                self.save(state)
                raise error_type(message)

            if (
                not actor.strip()
                or not reason.strip()
                or not isinstance(permissions, dict)
                or not permissions
            ):
                deny("permission migration requires actor, reason and permissions")
            plan = state["plan"]
            if (
                plan.get("phase") != "discovery"
                or (state.get("workflow") or {}).get("currentStage") not in {"0", "1"}
                or state.get("status") in {"cancelled", "complete", "paused"}
            ):
                deny("permission migration requires an active discovery plan", ResolutionError)
            repositories = {item["id"]: item for item in plan["repositories"]}
            candidates = {item["id"]: item for item in plan.get("catalogRepositories", [])}
            changes = []
            for repo_id, permission in permissions.items():
                if repo_id not in repositories:
                    deny(f"unknown engaged repository: {repo_id}")
                if (
                    not isinstance(permission, dict)
                    or set(permission) != {"remote", "author"}
                    or type(permission["author"]) is not bool
                    or not isinstance(permission["remote"], str)
                ):
                    deny("permissions require explicit remote and boolean author")
                records = [repositories[repo_id]]
                if repo_id in candidates:
                    records.append(candidates[repo_id])
                for record in records:
                    if normalize_remote(record["remote"]) != normalize_remote(permission["remote"]):
                        deny(f"permission remote mismatch: {repo_id}")
                    if "author" in record and record["author"] != permission["author"]:
                        deny(f"cannot replace explicit author permission: {repo_id}")
                if "author" not in repositories[repo_id]:
                    changes.append({"repository": repo_id, **permission, "previousAuthor": None})
            previous_hash = state["planHash"]
            for repo_id, permission in permissions.items():
                repositories[repo_id].setdefault("author", permission["author"])
                if repo_id in candidates:
                    candidates[repo_id].setdefault("author", permission["author"])
            state["planHash"] = _canonical_hash(plan)
            state["discoveryPlanHash"] = state["planHash"]
            self.append_event(
                state,
                "security.author_permissions.migrated",
                {
                    "actor": actor,
                    "action": "migrate-author-permissions",
                    "outcome": "success",
                    "reason": reason,
                    "changes": changes,
                    "previousPlanHash": previous_hash,
                    "planHash": state["planHash"],
                },
            )
            self.save(state)
            return state

    def update_discovery_context(
        self,
        run_id: str,
        *,
        repo_id: str,
        context: dict[str, Any],
        added: tuple[str, ...],
        reason: str,
        actor: str = "system",
    ) -> dict[str, Any]:
        state = self.load(run_id)
        workflow = state.get("workflow") or {}
        phase = state.get("plan", {}).get("phase")
        pending_return = workflow.get("pendingReturn")
        clarification = False
        if phase == "execution" and isinstance(pending_return, dict):
            task_id = pending_return.get("task")
            task = (workflow.get("tasks") or {}).get(task_id)
            rewind_index = pending_return.get("rewindIndex")
            rewinds = workflow.get("rewinds") or []
            if type(rewind_index) is int and 0 <= rewind_index < len(rewinds):
                rewind = rewinds[rewind_index]
                clarification = (
                    pending_return.get("targetStage") == "0"
                    and pending_return.get("resumeStage") == "2"
                    and isinstance(task, dict)
                    and task.get("status") == "active"
                    and task.get("currentStage") == "2"
                    and rewind.get("task") == task_id
                    and rewind.get("fromStage") == "2"
                    and rewind.get("targetStage") == "0"
                    and "resumedAt" not in rewind
                )
        if workflow.get("currentStage") != "0" or not (phase == "discovery" or clarification):
            raise ResolutionError(f"run {run_id} can lock discovery context only during Stage 0")
        repository = next(
            (item for item in state["plan"].get("repositories", []) if item["id"] == repo_id),
            None,
        )
        if repository is None:
            raise ResolutionError(f"run state has no repository: {repo_id}")
        repository["context"] = context
        state["planHash"] = _canonical_hash(state["plan"])
        if phase == "discovery":
            state["discoveryPlanHash"] = state["planHash"]
        self.append_event(
            state,
            "discovery.context.locked",
            {
                "repository": repo_id,
                "actor": actor,
                "action": "lock-context",
                "outcome": "success",
                "added": list(added),
                "reason": reason,
                "revision": context["revision"],
            },
        )
        self.save(state)
        return state

    def clear_current_gate(
        self,
        run_id: str,
        evidence: dict[str, Any],
        task_id: str | None = None,
        *,
        actor: str = "system",
    ) -> dict[str, Any]:
        if not actor.strip():
            raise ConfigurationError("clearing a workflow gate requires an actor")
        with self.lock(run_id):
            state = self.load(run_id)
            workflow_state = state.get("workflow")
            if not isinstance(workflow_state, dict):
                raise ResolutionError(f"run {run_id} has no workflow")
            if workflow_state.get("status") == "complete":
                raise ResolutionError(f"run {run_id} workflow is already complete")
            current_stage = workflow_state.get("currentStage")
            if not isinstance(current_stage, str):
                raise ResolutionError(f"run {run_id} has no active workflow stage")
            workflow_plan = state.get("plan", {}).get("workflow")
            if not isinstance(workflow_plan, dict):
                raise ResolutionError(f"run {run_id} is missing its workflow definition")

            from .workflow import WorkflowDefinition

            definition = WorkflowDefinition.from_dict(workflow_plan)
            if current_stage == "tasks":
                self._clear_task_gate(
                    state,
                    definition,
                    evidence,
                    task_id,
                )
                self.save(state)
                return workflow_state
            if task_id is not None:
                raise ConfigurationError(
                    f"--task is valid only during per-task stages, not stage {current_stage}"
                )
            stage = next(
                (item for item in definition.stages if item.id == current_stage),
                None,
            )
            if stage is None:
                raise ResolutionError(
                    f"run {run_id} references unknown current stage {current_stage}"
                )
            policy_mode = state.get("plan", {}).get("policyMode", "evaluation")
            validate_gate_evidence(stage, evidence, policy_mode=policy_mode)
            outcome = gate_evidence_outcome(stage, evidence, policy_mode=policy_mode)
            if stage.gate.evidence_type == "specification":
                self._assert_specification_scope(state, evidence)
            materialized_task_plan = (
                stage.gate.evidence_type == "task-plan"
                and state["plan"].get("phase") == "discovery"
            )
            if materialized_task_plan:
                self._refresh_context_after_planning_docs(state)
                self._materialize_execution_plan(state, definition, evidence, actor=actor)
                pending_redecomposition = next(
                    (
                        rewind
                        for rewind in reversed(workflow_state.get("rewinds", []))
                        if rewind.get("targetStage") == "1" and "rematerializedAt" not in rewind
                    ),
                    None,
                )
                if pending_redecomposition is not None:
                    pending_redecomposition["rematerializedAt"] = utc_now()
                    pending_redecomposition["replacementExecutionPlanHash"] = state[
                        "executionPlanHash"
                    ]
                    self.append_event(
                        state,
                        "workflow.rewind.rematerialized",
                        {
                            "task": pending_redecomposition["task"],
                            "stage": "1",
                            "executionPlanHash": state["executionPlanHash"],
                        },
                    )
            if (
                outcome != "waived"
                and stage.gate.evidence_type == "integration"
                and state.get("executionPlanHash")
            ):
                self._assert_persisted_passing_signals(state, evidence["signals"])
            if outcome != "waived" and stage.gate.evidence_type == "knowledge":
                self._assert_cross_repository_rollback_plan(state, evidence)
                if stage.gate.requirements.manifest_closure:
                    self._assert_manifest_closure(state, evidence)
            state["status"] = "active"
            now = utc_now()
            stage_state = workflow_state["stages"][current_stage]
            stage_state.update({"status": outcome, "evidence": evidence, "completedAt": now})
            if outcome == "waived":
                self._record_waiver(state, current_stage, evidence)
            pending_return = workflow_state.get("pendingReturn")
            resumed_from_rewind = (
                isinstance(pending_return, dict)
                and pending_return.get("targetStage") == current_stage
            )
            if resumed_from_rewind:
                assert isinstance(pending_return, dict)
                self._resume_after_rewind(state, pending_return)
                following = None
            else:
                following = next_stage_id(definition, current_stage)
            if resumed_from_rewind:
                pass
            elif following is None:
                workflow_state.update({"status": "complete", "currentStage": None})
                state["status"] = self._terminal_completion_status(state)
            elif next(item for item in definition.stages if item.id == following).per_task:
                self._activate_task_phase(state, definition)
            else:
                workflow_state["currentStage"] = following
                workflow_state["stages"][following]["status"] = "active"
            self.append_event(
                state,
                "workflow.gate.cleared",
                f"stage {current_stage} ({stage.gate.kind})",
            )
            self.save(state)
            return workflow_state

    def rewind_workflow(
        self,
        run_id: str,
        target_stage: str,
        *,
        actor: str,
        reason: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Move to an approved earlier stage while retaining an auditable resume point."""
        if not actor.strip() or not reason.strip():
            raise ConfigurationError("workflow rewind requires non-empty actor and reason")
        state = self.load(run_id)
        workflow = state.get("workflow")
        if not isinstance(workflow, dict) or workflow.get("status") == "complete":
            raise ResolutionError(f"run {run_id} has no active workflow")
        if workflow.get("pendingReturn") is not None:
            raise ResolutionError("workflow already has a pending rewind return")
        if workflow.get("currentStage") != "tasks" or task_id is None:
            raise ConfigurationError("workflow rewind currently requires an active --task")
        task = (workflow.get("tasks") or {}).get(task_id)
        if not isinstance(task, dict) or task.get("status") != "active":
            raise ResolutionError(f"workflow task is not active: {task_id}")
        source_stage = task.get("currentStage")
        allowed = {("2", "0"), ("2", "1"), ("3", "2")}
        if (source_stage, target_stage) not in allowed:
            raise ConfigurationError(
                f"unsupported workflow rewind: task stage {source_stage} -> {target_stage}"
            )

        now = utc_now()
        rewind = {
            "task": task_id,
            "fromStage": source_stage,
            "targetStage": target_stage,
            "actor": actor,
            "reason": reason,
            "requestedAt": now,
        }
        workflow.setdefault("rewinds", []).append(rewind)
        if target_stage == "0":
            previous = dict(workflow["stages"][target_stage])
            rewind["previousTargetState"] = previous
            workflow["pendingReturn"] = {
                "targetStage": target_stage,
                "task": task_id,
                "resumeStage": source_stage,
                "rewindIndex": len(workflow["rewinds"]) - 1,
            }
            workflow["currentStage"] = target_stage
            workflow["stages"][target_stage].update(
                {"status": "active", "evidence": None, "completedAt": None}
            )
        elif target_stage == "1":
            plan = state["plan"]
            rewind["previousTargetState"] = copy.deepcopy(workflow["stages"][target_stage])
            rewind["previousTaskPlan"] = copy.deepcopy(plan.get("tasks", []))
            rewind["previousTasks"] = copy.deepcopy(state.get("tasks", {}))
            rewind["previousWorkflowTasks"] = copy.deepcopy(workflow.get("tasks", {}))
            rewind["previousExecutionPlanHash"] = state.get("executionPlanHash")
            rewind["previousDiscoveryPlanHash"] = state.get("discoveryPlanHash")

            plan["phase"] = "discovery"
            plan["tasks"] = []
            for repository in plan["repositories"]:
                repository["mode"] = "read-only"
            state["tasks"] = {}
            workflow["tasks"] = {}
            workflow["currentStage"] = target_stage
            workflow["stages"][target_stage].update(
                {"status": "active", "evidence": None, "completedAt": None}
            )
            for invalidated_stage in ("2", "3"):
                if invalidated_stage in workflow["stages"]:
                    workflow["stages"][invalidated_stage].update(
                        {"status": "pending", "evidence": None, "completedAt": None}
                    )
            state.pop("executionPlanHash", None)
            state["planHash"] = _canonical_hash(plan)
            state["discoveryPlanHash"] = state["planHash"]
        else:
            previous = dict(task["stages"][target_stage])
            rewind["previousTargetState"] = previous
            task["currentStage"] = target_stage
            task["stages"][target_stage].update(
                {"status": "active", "evidence": None, "completedAt": None}
            )
            task["stages"][source_stage]["status"] = "pending"
            workflow["stages"][target_stage]["status"] = "active"
            workflow["stages"][target_stage]["completedAt"] = None
            if all(
                candidate["stages"][source_stage]["status"] == "pending"
                for candidate in workflow["tasks"].values()
            ):
                workflow["stages"][source_stage]["status"] = "pending"
                workflow["stages"][source_stage]["completedAt"] = None
        state["status"] = "active"
        self.append_event(state, "workflow.rewound", rewind)
        self.save(state)
        return workflow

    def _resume_after_rewind(
        self,
        state: dict[str, Any],
        pending_return: dict[str, Any],
    ) -> None:
        workflow = state["workflow"]
        task_id = pending_return["task"]
        task = workflow["tasks"][task_id]
        resume_stage = pending_return["resumeStage"]
        workflow["currentStage"] = "tasks"
        task["currentStage"] = resume_stage
        task["status"] = "active"
        task["stages"][resume_stage]["status"] = "active"
        rewind = workflow["rewinds"][pending_return["rewindIndex"]]
        rewind["resumedAt"] = utc_now()
        workflow.pop("pendingReturn", None)
        self.append_event(
            state,
            "workflow.rewind.resumed",
            {"task": task_id, "stage": resume_stage},
        )

    def _materialize_execution_plan(
        self,
        state: dict[str, Any],
        definition: Any,
        evidence: dict[str, Any],
        *,
        actor: str,
    ) -> None:
        """Freeze the Stage 1 task DAG and grant write scope only to task owners."""
        plan = state["plan"]
        repository_records = {item["id"]: item for item in plan["repositories"]}
        raw_tasks = evidence["taskPlan"]["tasks"]
        task_plan_stage = next(
            stage for stage in definition.stages if stage.gate.evidence_type == "task-plan"
        )
        check_derivation_enabled = (
            task_plan_stage.gate.requirements.check_derivation
            or "checkDerivation" in evidence.get("taskPlan", {})
        )
        if check_derivation_enabled:
            if not task_plan_stage.gate.requirements.check_derivation:
                validate_check_derivation(evidence["taskPlan"], raw_tasks)
            self._assert_profile_check_derivation(state, evidence, raw_tasks)
        if task_plan_stage.gate.requirements.test_traceability:
            self._assert_test_traceability(state, evidence)
        tasks: list[WorkspaceTask] = []
        for raw in raw_tasks:
            normalized = dict(raw)
            normalized["description"] = raw["requirement"]
            task = WorkspaceTask.from_dict(normalized)
            if task.repo not in repository_records:
                raise ConfigurationError(
                    f"taskPlan task {task.id} targets unavailable repository {task.repo}"
                )
            if not repository_records[task.repo].get("author", False):
                # Context refresh may have changed the in-memory plan. Persist
                # the denial against the last valid snapshot, not a partial plan.
                denied_state = self.load(state["runId"])
                self.append_event(
                    denied_state,
                    "security.authorization.denied",
                    {
                        "actor": actor,
                        "action": "author-task",
                        "repository": task.repo,
                        "task": task.id,
                        "outcome": "denied",
                    },
                )
                self.save(denied_state)
                raise ConfigurationError(
                    f"taskPlan task {task.id} targets non-authorable repository {task.repo}"
                )
            if not task.required_checks:
                raise ConfigurationError(
                    f"taskPlan task {task.id} requires non-empty requiredChecks"
                )
            if len(task.required_checks) != len(set(task.required_checks)):
                raise ConfigurationError(
                    f"taskPlan task {task.id} requiredChecks must not contain duplicates"
                )
            commands = repository_records[task.repo]["profile"]["commands"]
            unknown_checks = sorted(set(task.required_checks) - set(commands))
            if unknown_checks:
                raise ConfigurationError(
                    f"taskPlan task {task.id} names unknown checks for {task.repo}: "
                    f"{unknown_checks}"
                )
            reason = task.ownership.get("reason")
            ownership_evidence = task.ownership.get("evidence")
            if not isinstance(reason, str) or not reason.strip():
                raise ConfigurationError(f"taskPlan task {task.id} requires an ownership reason")
            if not isinstance(ownership_evidence, list) or not ownership_evidence:
                raise ConfigurationError(f"taskPlan task {task.id} requires ownership evidence")
            if not all(
                isinstance(item, dict)
                and isinstance(item.get("repo"), str)
                and item["repo"].strip()
                and isinstance(item.get("path"), str)
                and item["path"].strip()
                for item in ownership_evidence
            ):
                raise ConfigurationError(
                    f"taskPlan task {task.id} ownership evidence requires repo and path"
                )
            if not any(item["repo"] == task.repo for item in ownership_evidence):
                raise ConfigurationError(
                    f"taskPlan task {task.id} ownership evidence must include owner {task.repo}"
                )
            for item in ownership_evidence:
                repo_id = item["repo"]
                if repo_id not in repository_records:
                    raise ConfigurationError(
                        f"taskPlan task {task.id} ownership evidence names unknown repo {repo_id}"
                    )
                relative = Path(item["path"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ConfigurationError(
                        f"taskPlan task {task.id} ownership path must be repo-relative: "
                        f"{item['path']}"
                    )
                root = Path(repository_records[repo_id]["root"])
                if not (root / relative).exists():
                    raise ConfigurationError(
                        f"taskPlan task {task.id} ownership evidence does not exist: "
                        f"{repo_id}:{item['path']}"
                    )
            if not task.required_context:
                raise ConfigurationError(f"taskPlan task {task.id} requires non-empty context")
            seen_context: set[tuple[str, str]] = set()
            for item in task.required_context:
                reference = (item["repo"], item["path"])
                if reference in seen_context:
                    raise ConfigurationError(
                        f"taskPlan task {task.id} context must not contain duplicates"
                    )
                seen_context.add(reference)
                repo_id, raw_path = reference
                if repo_id not in repository_records:
                    raise ConfigurationError(
                        f"taskPlan task {task.id} context names unknown repo {repo_id}"
                    )
                relative = Path(raw_path)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ConfigurationError(
                        f"taskPlan task {task.id} context path must be repo-relative: {raw_path}"
                    )
                locked_paths = {
                    locked["path"] for locked in repository_records[repo_id]["context"]["files"]
                }
                if raw_path not in locked_paths:
                    raise ConfigurationError(
                        f"taskPlan task {task.id} context is not SHA-locked: {repo_id}:{raw_path}"
                    )
            tasks.append(task)

        ordered_ids = task_order(tuple(tasks))
        by_id = {task.id: task for task in tasks}
        self._assert_surface_coverage(state, evidence, by_id)
        self._assert_caller_coverage(state, evidence, by_id)
        self._assert_surface_required_checks(
            state,
            evidence,
            by_id,
            allow_derived_not_applicable=check_derivation_enabled,
        )
        for task in tasks:
            kind = task.base.get("kind", "repository-default")
            if kind not in {"repository-default", "dependency-head"}:
                raise ConfigurationError(
                    f"taskPlan task {task.id} has unsupported base kind {kind}"
                )
            if kind == "dependency-head":
                dependency = task.base.get("task")
                if dependency not in task.depends_on:
                    raise ConfigurationError(
                        f"taskPlan task {task.id} dependency-head base must name a dependency"
                    )
                if by_id[dependency].repo != task.repo:
                    raise ConfigurationError(
                        f"taskPlan task {task.id} cannot base on a dependency in another repo"
                    )
            same_repo_dependencies = [
                dependency for dependency in task.depends_on if by_id[dependency].repo == task.repo
            ]
            if same_repo_dependencies and kind != "dependency-head":
                raise ConfigurationError(
                    f"taskPlan task {task.id} depends on same-repository task(s) "
                    f"{same_repo_dependencies} and must use dependency-head or be aggregated"
                )

            consumed_by_task = {item["fromTask"]: item["artifact"] for item in task.consumes}
            unknown_consumption_sources = sorted(set(consumed_by_task) - set(task.depends_on))
            if unknown_consumption_sources:
                raise ConfigurationError(
                    f"taskPlan task {task.id} consumes artifacts from non-dependencies: "
                    f"{unknown_consumption_sources}"
                )
            for dependency in task.depends_on:
                producer = by_id[dependency]
                if producer.repo == task.repo:
                    continue
                artifact_id = consumed_by_task.get(dependency)
                if artifact_id is None:
                    raise ConfigurationError(
                        f"taskPlan task {task.id} cross-repository dependency {dependency} "
                        "requires a consumed artifact"
                    )
                if artifact_id not in {item["id"] for item in producer.produces}:
                    raise ConfigurationError(
                        f"taskPlan task {task.id} consumes undeclared artifact {artifact_id} "
                        f"from {dependency}"
                    )

        owners = {task.repo for task in tasks}
        for repository in plan["repositories"]:
            repository["mode"] = "read-write" if repository["id"] in owners else "read-only"

        def serialize(task: WorkspaceTask) -> dict[str, Any]:
            return {
                "id": task.id,
                "repo": task.repo,
                "mode": "read-write",
                "dependsOn": list(task.depends_on),
                "description": task.description,
                "source": task.source,
                "declaredStatus": task.declared_status,
                "testCases": list(task.test_cases),
                "requiredChecks": list(task.required_checks),
                "ownership": task.ownership,
                "base": task.base or {"kind": "repository-default"},
                "context": list(task.required_context),
                "surfaces": list(task.surfaces),
                "callers": list(task.callers),
                "produces": list(task.produces),
                "consumes": list(task.consumes),
            }

        plan["tasks"] = [serialize(by_id[task_id]) for task_id in ordered_ids]
        plan["phase"] = "execution"
        state["tasks"] = {
            task["id"]: {
                "status": "planned",
                "repo": task["repo"],
                "branch": None,
                "worktree": None,
                "pullRequest": None,
                "requiredChecks": task["requiredChecks"],
                "base": task["base"],
                "requiredContext": task["context"],
                "contextAcknowledged": [],
                "surfaces": task["surfaces"],
                "callers": task["callers"],
                "produces": task["produces"],
                "consumes": task["consumes"],
            }
            for task in plan["tasks"]
        }
        state["workflow"]["tasks"] = initial_task_states(definition, plan["tasks"])
        state["planHash"] = _canonical_hash(plan)
        state["executionPlanHash"] = state["planHash"]
        self.append_event(
            state,
            "execution-plan.materialized",
            {
                "taskOrder": list(ordered_ids),
                "writableRepositories": sorted(owners),
                "planHash": state["executionPlanHash"],
            },
        )

    def _assert_profile_check_derivation(
        self,
        state: dict[str, Any],
        evidence: dict[str, Any],
        tasks: list[dict[str, Any]],
    ) -> None:
        """Require every repository command to be selected or explicitly ruled out."""

        repositories = {item["id"]: item for item in state.get("plan", {}).get("repositories", [])}
        derivations = {
            item["task"]: item
            for item in evidence.get("taskPlan", {}).get("checkDerivation", [])
            if isinstance(item, dict) and isinstance(item.get("task"), str)
        }
        for task in tasks:
            task_id = task["id"]
            repo_id = task["repo"]
            commands = repositories[repo_id]["profile"]["commands"]
            audited = {
                check["id"]: check
                for check in derivations.get(task_id, {}).get("checks", [])
                if isinstance(check, dict) and isinstance(check.get("id"), str)
            }
            missing = sorted(set(commands) - set(audited))
            unknown = sorted(set(audited) - set(commands))
            if missing or unknown:
                raise ConfigurationError(
                    f"checkDerivation {task_id} profile commands mismatch; "
                    f"missing={missing}, unknown={unknown}"
                )
            mismatched = sorted(
                check_id
                for check_id, command in commands.items()
                if audited[check_id].get("command") != command
            )
            if mismatched:
                raise ConfigurationError(
                    f"checkDerivation {task_id} command text mismatch: {mismatched}"
                )

    def _assert_manifest_closure(self, state: dict[str, Any], evidence: dict[str, Any]) -> None:
        plan = state.get("plan", {})
        sponsor_id = plan.get("sponsor")
        sponsor = next(
            (
                repository
                for repository in plan.get("repositories", [])
                if repository.get("id") == sponsor_id
            ),
            None,
        )
        if not isinstance(sponsor, dict):
            raise ConfigurationError("manifestClosure requires a sponsor repository")
        expected = sponsor.get("featureManifest")
        closure = evidence["manifestClosure"]
        if not isinstance(expected, str) or closure.get("manifest") != expected:
            raise ConfigurationError(
                f"manifestClosure must name sponsor feature manifest {expected}"
            )
        manifest_path = Path(sponsor["root"]) / expected
        if not manifest_path.is_file():
            raise ConfigurationError(
                f"manifestClosure repository manifest does not exist: {manifest_path}"
            )
        manifest = load_yaml(manifest_path)
        if manifest.get("status") != "complete":
            raise ConfigurationError(
                "manifestClosure requires repository status=complete; "
                f"found {manifest.get('status')!r}"
            )

    def _assert_test_traceability(self, state: dict[str, Any], evidence: dict[str, Any]) -> None:
        specification_evidence = next(
            (
                item.get("evidence")
                for item in state.get("workflow", {}).get("stages", {}).values()
                if item.get("gate", {}).get("evidenceType") == "specification"
            ),
            None,
        )
        if not isinstance(specification_evidence, dict):
            raise ConfigurationError("test traceability requires specification evidence")
        specification = specification_evidence["specification"]
        literal_audit = specification.get("literalAudit", {})
        required = {
            *(f"literal:{item['id']}" for item in literal_audit.get("literals", [])),
            *(f"surface:{item['id']}" for item in specification["affectedSurfaces"]),
            *(f"caller:{item['id']}" for item in specification.get("callerMatrix", [])),
            *(
                f"interface:{interface_id}"
                for interface_id in specification["interfaceImpact"]["affectedInterfaces"]
            ),
        }
        traced = {
            trace
            for task in evidence["taskPlan"]["tasks"]
            for case in task["testCases"]
            for trace in case.get("traces", [])
        }
        missing = sorted(required - traced)
        unknown = sorted(traced - required)
        if missing or unknown:
            raise ConfigurationError(
                f"taskPlan test traceability mismatch; missing={missing}, unknown={unknown}"
            )

    def _assert_specification_scope(self, state: dict[str, Any], evidence: dict[str, Any]) -> None:
        specification = evidence["specification"]
        repository_records = {
            item["id"]: item for item in state.get("plan", {}).get("repositories", [])
        }
        evaluated = set(specification["interfaceImpact"]["evaluatedRepositories"])
        required = set(state.get("plan", {}).get("requiredEngagements", []))
        missing_engagements = sorted(required - set(repository_records))
        if missing_engagements:
            raise ConfigurationError(
                "specification requires engagement of explicitly included repositories: "
                f"{missing_engagements}"
            )
        expected_repositories = set(repository_records)
        missing = sorted(expected_repositories - evaluated)
        unknown = sorted(evaluated - expected_repositories)
        if missing or unknown:
            raise ConfigurationError(
                "specification evaluatedRepositories must exactly cover selected repositories; "
                f"missing={missing}, unknown={unknown}"
            )

        expected_unavailable = {
            item["id"]: item for item in state.get("plan", {}).get("unavailableRepositories", [])
        }
        classified_unavailable = {
            item["id"]: item
            for item in specification["interfaceImpact"].get("unavailableRepositories", [])
        }
        missing_unavailable = sorted(set(expected_unavailable) - set(classified_unavailable))
        unknown_unavailable = sorted(set(classified_unavailable) - set(expected_unavailable))
        if missing_unavailable or unknown_unavailable:
            raise ConfigurationError(
                "specification unavailableRepositories must exactly cover inaccessible catalog "
                f"repositories; missing={missing_unavailable}, unknown={unknown_unavailable}"
            )
        required_unavailable = sorted(
            repo_id
            for repo_id, item in classified_unavailable.items()
            if item["relevance"] == "required"
            or expected_unavailable[repo_id].get("requiredByManifest")
            or expected_unavailable[repo_id].get("requiredByDiscovery")
        )
        if required_unavailable:
            details = [
                {
                    "id": repo_id,
                    "remote": expected_unavailable[repo_id]["remote"],
                    "reason": classified_unavailable[repo_id]["reason"],
                }
                for repo_id in required_unavailable
            ]
            raise ResolutionError(
                "Stage 0 requires repository read access before specification approval; "
                f"obtain access and resume the run: {details}"
            )

        def assert_reference(reference: dict[str, str], field_name: str) -> None:
            repo_id = reference["repo"]
            repository = repository_records.get(repo_id)
            if repository is None:
                raise ConfigurationError(f"{field_name} names unavailable repository {repo_id}")
            locked = {item["path"] for item in repository["context"]["files"]}
            if reference["path"] not in locked:
                raise ConfigurationError(
                    f"{field_name} is not SHA-locked: {repo_id}:{reference['path']}"
                )

        for surface in specification["affectedSurfaces"]:
            if surface["ownerRepo"] not in repository_records:
                raise ConfigurationError(
                    f"affected surface {surface['id']} owner is unavailable: {surface['ownerRepo']}"
                )
            for reference in surface["evidence"]:
                assert_reference(reference, f"affected surface {surface['id']} evidence")
        for audit in specification.get("surfaceAudit", []):
            for reference in audit["evidence"]:
                assert_reference(reference, f"surface audit {audit['kind']} evidence")
        for precedent in specification.get("precedentAudit", []):
            for reference in precedent["selected"]:
                assert_reference(
                    reference,
                    f"precedent audit {precedent['surface']} selected precedent",
                )
        for caller in specification.get("callerMatrix", []):
            if caller["repo"] not in repository_records:
                raise ConfigurationError(
                    f"caller {caller['id']} repository is unavailable: {caller['repo']}"
                )
            for reference in caller["evidence"]:
                assert_reference(reference, f"caller {caller['id']} evidence")
            direct_reference = {"repo": caller["repo"], "path": caller["path"]}
            assert_reference(direct_reference, f"caller {caller['id']} path")
        for decision in specification["interfaceDecisions"]:
            if decision["ownerRepo"] not in repository_records:
                raise ConfigurationError(
                    f"interface decision {decision['id']} owner is unavailable: "
                    f"{decision['ownerRepo']}"
                )
            unknown_consumers = sorted(
                set(decision["consumersEvaluated"]) - set(repository_records)
            )
            if unknown_consumers:
                raise ConfigurationError(
                    f"interface decision {decision['id']} names unavailable consumers: "
                    f"{unknown_consumers}"
                )
            for reference in decision["compatibilityPeers"]:
                assert_reference(
                    reference, f"interface decision {decision['id']} compatibility peer"
                )

    def _assert_surface_coverage(
        self,
        state: dict[str, Any],
        evidence: dict[str, Any],
        tasks: dict[str, WorkspaceTask],
    ) -> None:
        workflow_stages = state["workflow"]["stages"]
        specification_evidence = next(
            (
                item.get("evidence")
                for item in workflow_stages.values()
                if item.get("gate", {}).get("evidenceType") == "specification"
            ),
            None,
        )
        if not isinstance(specification_evidence, dict):
            raise ConfigurationError("task plan requires completed specification evidence")
        surfaces = {
            item["id"]: item for item in specification_evidence["specification"]["affectedSurfaces"]
        }
        coverage = {item["surface"]: item for item in evidence["taskPlan"]["surfaceCoverage"]}
        missing = sorted(set(surfaces) - set(coverage))
        extra = sorted(set(coverage) - set(surfaces))
        if missing or extra:
            raise ConfigurationError(
                f"taskPlan surfaceCoverage mismatch; missing={missing}, unknown={extra}"
            )
        task_surface_pairs = {
            (task.id, surface_id) for task in tasks.values() for surface_id in task.surfaces
        }
        repository_records = {
            item["id"]: item for item in state.get("plan", {}).get("repositories", [])
        }

        def assert_locked(reference: dict[str, str], surface_id: str) -> None:
            repository = repository_records.get(reference["repo"])
            if repository is None:
                raise ConfigurationError(
                    f"surfaceCoverage {surface_id} names unavailable evidence repository "
                    f"{reference['repo']}"
                )
            locked = {item["path"] for item in repository["context"]["files"]}
            if reference["path"] not in locked:
                raise ConfigurationError(
                    f"surfaceCoverage {surface_id} evidence is not SHA-locked: "
                    f"{reference['repo']}:{reference['path']}"
                )

        for task_id, surface_id in task_surface_pairs:
            if surface_id not in surfaces:
                raise ConfigurationError(
                    f"taskPlan task {task_id} names unknown surface {surface_id}"
                )
        for surface_id, decision in coverage.items():
            assigned_tasks = sorted(
                task_id
                for task_id, assigned_surface in task_surface_pairs
                if assigned_surface == surface_id
            )
            if decision["disposition"] != "task":
                if assigned_tasks:
                    raise ConfigurationError(
                        f"surface {surface_id} is {decision['disposition']} but is also assigned"
                    )
                if decision["disposition"] == "no-change":
                    for reference in decision["evidence"]:
                        assert_locked(reference, surface_id)
                continue
            task_id = decision["task"]
            task = tasks.get(task_id)
            if task is None:
                raise ConfigurationError(
                    f"surfaceCoverage {surface_id} names unknown task {task_id}"
                )
            if assigned_tasks != [task_id]:
                raise ConfigurationError(
                    f"surfaceCoverage {surface_id} must be declared only by task {task_id}; "
                    f"found {assigned_tasks}"
                )
            if task.repo != surfaces[surface_id]["ownerRepo"]:
                raise ConfigurationError(
                    f"surface {surface_id} owner {surfaces[surface_id]['ownerRepo']} "
                    f"does not match task repository {task.repo}"
                )

    def _assert_caller_coverage(
        self,
        state: dict[str, Any],
        evidence: dict[str, Any],
        tasks: dict[str, WorkspaceTask],
    ) -> None:
        specification_evidence = next(
            (
                item.get("evidence")
                for item in state["workflow"]["stages"].values()
                if item.get("gate", {}).get("evidenceType") == "specification"
            ),
            None,
        )
        if not isinstance(specification_evidence, dict):
            raise ConfigurationError("caller coverage requires completed specification evidence")
        callers = {
            item["id"]: item
            for item in specification_evidence["specification"].get("callerMatrix", [])
        }
        coverage_items = evidence["taskPlan"].get("callerCoverage", [])
        coverage = {item["caller"]: item for item in coverage_items}
        missing = sorted(set(callers) - set(coverage))
        extra = sorted(set(coverage) - set(callers))
        if missing or extra:
            raise ConfigurationError(
                f"taskPlan callerCoverage mismatch; missing={missing}, unknown={extra}"
            )
        task_caller_pairs = {
            (task.id, caller_id) for task in tasks.values() for caller_id in task.callers
        }
        for task_id, caller_id in task_caller_pairs:
            if caller_id not in callers:
                raise ConfigurationError(
                    f"taskPlan task {task_id} names unknown caller {caller_id}"
                )
        for caller_id, decision in coverage.items():
            assigned = sorted(
                task_id
                for task_id, assigned_caller in task_caller_pairs
                if assigned_caller == caller_id
            )
            if decision["disposition"] != "task":
                if assigned:
                    raise ConfigurationError(
                        f"caller {caller_id} is {decision['disposition']} but is also assigned"
                    )
                continue
            task_id = decision["task"]
            task = tasks.get(task_id)
            if task is None:
                raise ConfigurationError(f"callerCoverage {caller_id} names unknown task {task_id}")
            if assigned != [task_id]:
                raise ConfigurationError(
                    f"callerCoverage {caller_id} must be declared only by task {task_id}; "
                    f"found {assigned}"
                )
            if task.repo != callers[caller_id]["repo"]:
                raise ConfigurationError(
                    f"caller {caller_id} repository {callers[caller_id]['repo']} "
                    f"does not match task repository {task.repo}"
                )

    def _assert_surface_required_checks(
        self,
        state: dict[str, Any],
        evidence: dict[str, Any],
        tasks: dict[str, WorkspaceTask],
        *,
        allow_derived_not_applicable: bool = False,
    ) -> None:
        specification_evidence = next(
            (
                item.get("evidence")
                for item in state["workflow"]["stages"].values()
                if item.get("gate", {}).get("evidenceType") == "specification"
            ),
            None,
        )
        if not isinstance(specification_evidence, dict):
            raise ConfigurationError("required-check coverage needs specification evidence")
        surfaces = {
            item["id"]: item for item in specification_evidence["specification"]["affectedSurfaces"]
        }
        repositories = {item["id"]: item for item in state.get("plan", {}).get("repositories", [])}
        for surface_id, surface in surfaces.items():
            verification = surface.get("verification")
            if not isinstance(verification, dict):
                continue
            required = set(verification.get("requiredChecks", []))
            available = set(repositories[surface["ownerRepo"]]["profile"]["commands"])
            unknown = sorted(required - available)
            if unknown:
                raise ConfigurationError(
                    f"affected surface {surface_id} names unavailable owner checks: {unknown}"
                )
        derived_not_applicable: dict[str, set[str]] = {}
        if allow_derived_not_applicable:
            for derivation in evidence.get("taskPlan", {}).get("checkDerivation", []):
                task_id = derivation["task"]
                derived_not_applicable[task_id] = {
                    check["id"]
                    for check in derivation["checks"]
                    if check.get("disposition") == "not-applicable"
                }
        for task in tasks.values():
            required = {
                check
                for surface_id in task.surfaces
                for check in surfaces[surface_id].get("verification", {}).get("requiredChecks", [])
            }
            missing = sorted(
                required - set(task.required_checks) - derived_not_applicable.get(task.id, set())
            )
            if missing:
                raise ConfigurationError(
                    f"taskPlan task {task.id} omits surface-required checks: {missing}"
                )

    def _refresh_context_after_planning_docs(self, state: dict[str, Any]) -> None:
        """Advance the sponsor lock across an approved Stage 0/1 docs-only commit.

        Discovery begins before the specification audit and task decomposition are
        merged. Those required planning artifacts legitimately advance the sponsor
        repository HEAD. Only committed files beneath the active feature directory
        may change here; code, configuration outside that directory, non-sponsor
        repositories, dirty trees, force-pushes, and branch changes remain rejected.
        """

        plan = state["plan"]
        sponsor_id = plan["sponsor"]
        allowed_suffixes = {".md", ".yaml", ".yml", ".json"}

        for repository in plan["repositories"]:
            root = Path(repository["root"])
            context = repository["context"]
            old_revision = context["revision"]

            if repository_root(root) != root.resolve():
                raise ResolutionError(f"repository root changed for {repository['id']}: {root}")
            if normalize_remote(origin_url(root)) != normalize_remote(repository["remote"]):
                raise ResolutionError(f"repository remote changed for {repository['id']}")
            if is_dirty(root):
                raise ResolutionError(
                    f"source repo became dirty after planning: {repository['id']}"
                )

            new_revision = head_sha(root)
            if new_revision == old_revision:
                continue
            if repository["id"] != sponsor_id:
                raise ResolutionError(
                    f"non-sponsor repository HEAD changed since planning for {repository['id']}"
                )
            if current_branch(root) != context["branch"]:
                raise ResolutionError(
                    f"sponsor repository branch changed since planning for {sponsor_id}"
                )
            if not is_ancestor(root, old_revision, new_revision):
                raise ResolutionError(
                    f"sponsor repository history diverged since planning for {sponsor_id}"
                )

            manifest = repository.get("featureManifest")
            if not isinstance(manifest, str) or not manifest:
                raise ResolutionError(f"sponsor repository {sponsor_id} has no feature manifest")
            feature_directory = Path(manifest).parent
            changes = changed_paths(root, old_revision, new_revision)
            disallowed = [
                path
                for path in changes
                if not Path(path).is_relative_to(feature_directory)
                or Path(path).suffix.lower() not in allowed_suffixes
            ]
            if disallowed:
                raise ResolutionError(
                    f"sponsor repository changed outside approved planning documents: {disallowed}"
                )

            profile_path = resolve_profile_path(
                root, repository.get("profilePath", ".signal-harness/repo.yaml")
            )
            profile = load_profile(profile_path)
            previous = {item["path"]: item for item in context["files"]}
            refreshed = collect_context(
                root,
                profile,
                (
                    profile_path.relative_to(root).as_posix(),
                    *feature_context_patterns(root, manifest),
                ),
                required_files=tuple(
                    path for path in previous if path not in changes or (root / path).is_file()
                ),
            )
            refreshed_files = {item.path: asdict(item) for item in refreshed.files}
            for path, locked in previous.items():
                if path not in changes and refreshed_files.get(path) != locked:
                    raise ResolutionError(f"locked context changed during planning: {path}")
            repository["context"] = asdict(refreshed)
            self.append_event(
                state,
                "context.relocked-after-planning",
                {
                    "repo": sponsor_id,
                    "fromRevision": old_revision,
                    "toRevision": new_revision,
                    "paths": list(changes),
                },
            )

    def _activate_task_phase(
        self,
        state: dict[str, Any],
        definition: Any,
    ) -> None:
        workflow_state = state["workflow"]
        task_stages = per_task_stages(definition)
        if not task_stages:
            raise ResolutionError("workflow has no per-task stages to activate")
        workflow_state["currentStage"] = "tasks"
        workflow_state["stages"][task_stages[0].id]["status"] = "active"
        for task in workflow_state["tasks"].values():
            if not task["dependsOn"]:
                task["status"] = "active"
                task["stages"][task_stages[0].id]["status"] = "active"
        from .tdd import initialize_task_tdd

        for task_id in workflow_state["tasks"]:
            initialize_task_tdd(state, task_id)

    def _clear_task_gate(
        self,
        state: dict[str, Any],
        definition: Any,
        evidence: dict[str, Any],
        task_id: str | None,
    ) -> None:
        if task_id is None:
            raise ConfigurationError("per-task workflow gate requires --task")
        workflow_state = state["workflow"]
        tasks = workflow_state["tasks"]
        task = tasks.get(task_id)
        if not isinstance(task, dict):
            raise ResolutionError(f"run has no workflow task: {task_id}")
        if task["status"] != "active":
            raise ResolutionError(
                f"workflow task {task_id} is {task['status']}; dependencies must merge first"
            )
        stage_id = task["currentStage"]
        stages = per_task_stages(definition)
        stage = next((item for item in stages if item.id == stage_id), None)
        if stage is None:
            raise ResolutionError(f"workflow task {task_id} references unknown stage {stage_id}")
        policy_mode = state.get("plan", {}).get("policyMode", "evaluation")
        validate_gate_evidence(stage, evidence, policy_mode=policy_mode)
        outcome = gate_evidence_outcome(stage, evidence, policy_mode=policy_mode)
        if stage.gate.evidence_type == "tdd" and outcome != "waived":
            from .tdd import assert_recorded_tdd_evidence

            assert_recorded_tdd_evidence(state, task_id, evidence)
            self._assert_required_checks(state, task_id)
        elif stage.gate.evidence_type == "tdd":
            from .tdd import waive_task_tdd

            waive_task_tdd(state, task_id)
        if stage.gate.evidence_type == "review":
            tdd_stage = next(
                (candidate for candidate in stages if candidate.gate.evidence_type == "tdd"),
                None,
            )
            if (
                tdd_stage is not None
                and task["stages"].get(tdd_stage.id, {}).get("status") != "complete"
            ):
                raise ConfigurationError(
                    f"task {task_id} review cannot clear because {tdd_stage.name} did not complete"
                )
            self._assert_review_coverage(state, task_id, evidence)
        state["status"] = "active"
        now = utc_now()
        task["stages"][stage_id].update(
            {"status": outcome, "evidence": evidence, "completedAt": now}
        )
        if outcome == "waived":
            self._record_waiver(state, stage_id, evidence, task_id=task_id)
        stage_index = [item.id for item in stages].index(stage_id)
        if stage_index + 1 < len(stages):
            following = stages[stage_index + 1]
            task["currentStage"] = following.id
            task["stages"][following.id]["status"] = "active"
            workflow_state["stages"][following.id]["status"] = "active"
        else:
            task["currentStage"] = None
            task["status"] = "complete"
            for candidate in tasks.values():
                if candidate["status"] != "pending":
                    continue
                if all(
                    tasks[dependency]["status"] == "complete"
                    for dependency in candidate["dependsOn"]
                ):
                    candidate["status"] = "active"
                    candidate["stages"][stages[0].id]["status"] = "active"

        for task_stage in stages:
            if all(
                item["stages"][task_stage.id]["status"] in {"complete", "waived"}
                for item in tasks.values()
            ):
                workflow_state["stages"][task_stage.id]["status"] = (
                    "waived"
                    if any(
                        item["stages"][task_stage.id]["status"] == "waived"
                        for item in tasks.values()
                    )
                    else "complete"
                )
                workflow_state["stages"][task_stage.id]["completedAt"] = now

        if all(item["status"] == "complete" for item in tasks.values()):
            following_global = stage_after_task_phase(definition)
            if following_global is None:
                workflow_state.update({"status": "complete", "currentStage": None})
                state["status"] = self._terminal_completion_status(state)
            else:
                workflow_state["currentStage"] = following_global.id
                workflow_state["stages"][following_global.id]["status"] = "active"

        self.append_event(
            state,
            "workflow.task.gate.cleared",
            f"{task_id} stage {stage_id} ({stage.gate.kind})",
        )

    def _record_waiver(
        self,
        state: dict[str, Any],
        stage_id: str,
        evidence: dict[str, Any],
        *,
        task_id: str | None = None,
    ) -> None:
        waiver = {
            "stage": stage_id,
            "task": task_id,
            **evidence["waiver"],
            "recordedAt": utc_now(),
        }
        state.setdefault("waivers", []).append(waiver)
        self.append_event(
            state,
            "workflow.gate.waived",
            {
                "stage": stage_id,
                "task": task_id,
                "authorizedBy": waiver["authorizedBy"],
                "scope": waiver["scope"],
            },
        )

    def _assert_review_coverage(
        self, state: dict[str, Any], task_id: str, evidence: dict[str, Any]
    ) -> None:
        task_plan = next(
            (
                item.get("evidence", {}).get("taskPlan")
                for item in state["workflow"]["stages"].values()
                if item.get("gate", {}).get("evidenceType") == "task-plan"
                and isinstance(item.get("evidence"), dict)
            ),
            None,
        )
        if not isinstance(task_plan, dict):
            raise ConfigurationError("review reconciliation requires completed task-plan evidence")
        planned_task = next(item for item in task_plan["tasks"] if item["id"] == task_id)
        review = evidence["review"]
        reviewed_criteria = {item["id"] for item in review["criteria"]}
        expected_criteria = {item["id"] for item in planned_task["testCases"]}
        reviewed_surfaces = {item["id"] for item in review["surfaces"]}
        expected_surfaces = set(planned_task["surfaces"])
        reviewed_callers = {item["id"] for item in review.get("callers", [])}
        expected_callers = set(planned_task.get("callers", []))
        if reviewed_criteria != expected_criteria:
            raise ConfigurationError(
                f"review criteria mismatch for {task_id}; missing="
                f"{sorted(expected_criteria - reviewed_criteria)}, unknown="
                f"{sorted(reviewed_criteria - expected_criteria)}"
            )
        if reviewed_surfaces != expected_surfaces:
            raise ConfigurationError(
                f"review surfaces mismatch for {task_id}; missing="
                f"{sorted(expected_surfaces - reviewed_surfaces)}, unknown="
                f"{sorted(reviewed_surfaces - expected_surfaces)}"
            )
        if reviewed_callers != expected_callers:
            raise ConfigurationError(
                f"review callers mismatch for {task_id}; missing="
                f"{sorted(expected_callers - reviewed_callers)}, unknown="
                f"{sorted(reviewed_callers - expected_callers)}"
            )

    @staticmethod
    def _terminal_completion_status(state: dict[str, Any]) -> str:
        return "evaluation-complete-with-waivers" if state.get("waivers") else "complete"

    def _assert_required_checks(self, state: dict[str, Any], task_id: str) -> None:
        task = state["tasks"][task_id]
        required = task.get("requiredChecks", [])
        latest: dict[str, dict[str, Any]] = {}
        for signal in task.get("signals", []):
            command_id = signal.get("metadata", {}).get("commandId")
            if isinstance(command_id, str):
                latest[command_id] = signal
        missing = [
            command_id
            for command_id in required
            if command_id not in latest or latest[command_id].get("status") != "pass"
        ]
        if missing:
            raise ResolutionError(
                f"task {task_id} required checks do not have latest passing engine signals: "
                f"{missing}"
            )

    def _assert_persisted_passing_signals(
        self,
        state: dict[str, Any],
        submitted_ids: list[str],
    ) -> None:
        persisted = {
            signal.get("id"): signal
            for task in state.get("tasks", {}).values()
            for signal in task.get("signals", [])
            if isinstance(signal, dict)
        }
        invalid = [
            signal_id
            for signal_id in submitted_ids
            if signal_id not in persisted or persisted[signal_id].get("status") != "pass"
        ]
        if invalid:
            raise ResolutionError(
                f"integration evidence must reference persisted passing engine signals: {invalid}"
            )

    def _assert_cross_repository_rollback_plan(
        self,
        state: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        owners = {
            task.get("repo")
            for task in state.get("tasks", {}).values()
            if isinstance(task, dict) and isinstance(task.get("repo"), str)
        }
        if len(owners) <= 1:
            return
        rollback = evidence.get("rollbackPlan")
        if not isinstance(rollback, dict):
            raise ConfigurationError("cross-repository knowledge evidence requires rollbackPlan")
        for field in ("owner", "alerting"):
            value = rollback.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ConfigurationError(f"rollbackPlan requires {field}")
        for field in ("unwindOrder", "deploymentOrder"):
            value = rollback.get(field)
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item.strip() for item in value)
            ):
                raise ConfigurationError(f"rollbackPlan requires non-empty {field}")

    def load(self, run_id: str) -> dict[str, Any]:
        path = self.state_path(run_id)
        if not path.is_file():
            raise ResolutionError(f"run does not exist: {run_id}")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ResolutionError(f"cannot read run state {path}: {exc}") from exc
        if state.get("stateVersion") != STATE_VERSION or state.get("runId") != run_id:
            raise ResolutionError(f"unsupported or mismatched run state: {path}")
        if _canonical_hash(state.get("plan")) != state.get("planHash"):
            raise ResolutionError(f"run plan integrity check failed: {path}")
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
        state.setdefault("security", initialize_security_state())
        return state

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
