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
from dataclasses import asdict
from pathlib import Path
from typing import Any, NoReturn

from signal_harness.runtime.context import collect_context
from signal_harness.runtime.errors import ConfigurationError, ResolutionError
from signal_harness.runtime.git import (
    head_sha,
    managed_repository,
    normalize_remote,
    origin_url,
    repository_root,
)
from signal_harness.runtime.security import redact_text
from signal_harness.sdd.models import WorkspaceRepository
from signal_harness.sdd.planner import (
    ResolvedRepository,
    infer_repository_profile,
    load_profile,
    resolve_profile_path,
    resolved_repository_dict,
)
from signal_harness.sdd.state import RunStore, utc_now


def _repository(state: dict[str, Any], repo_id: str) -> dict[str, Any]:
    for repository in state["plan"]["repositories"]:
        if repository["id"] == repo_id:
            return repository
    raise ResolutionError(f"run state has no repository: {repo_id}")


def _stage(state: dict[str, Any], stage_id: str) -> dict[str, Any]:
    workflow = state.get("plan", {}).get("workflow")
    if not isinstance(workflow, dict):
        raise ResolutionError(f"run {state['runId']} has no workflow")
    for stage in workflow["stages"]:
        if stage["id"] == stage_id:
            return stage
    raise ResolutionError(f"run references unknown workflow stage: {stage_id}")


def _active_task(state: dict[str, Any], task_id: str | None) -> tuple[str, dict[str, Any]]:
    workflow_tasks = state["workflow"]["tasks"]
    if task_id is not None:
        task = workflow_tasks.get(task_id)
        if not isinstance(task, dict):
            raise ResolutionError(f"run has no workflow task: {task_id}")
        if task["status"] != "active":
            raise ResolutionError(f"workflow task {task_id} is not active: {task['status']}")
        return task_id, task
    active = [(key, value) for key, value in workflow_tasks.items() if value["status"] == "active"]
    if len(active) != 1:
        raise ConfigurationError(
            f"select --task when {len(active)} workflow tasks are active: "
            f"{[item[0] for item in active]}"
        )
    return active[0]


def context_bundle(store: RunStore, run_id: str, task_id: str | None = None) -> dict[str, Any]:
    state = store.load(run_id)
    write_target = None
    if task_id is not None:
        task = state.get("tasks", {}).get(task_id)
        if not isinstance(task, dict):
            raise ResolutionError(f"run state has no task: {task_id}")
        repo = _repository(state, task["repo"])
        write_target = {
            "task": task_id,
            "repo": repo["id"],
            "worktree": task.get("worktree"),
            "branch": task.get("branch"),
            "requiredContext": task.get("requiredContext", []),
            "contextAcknowledged": task.get("contextAcknowledged", []),
        }

    files = [
        {
            "repo": repository["id"],
            "root": repository["root"],
            "path": item["path"],
            "sha256": item["sha256"],
            "revision": repository["context"]["revision"],
            "mode": repository["mode"],
        }
        for repository in state["plan"]["repositories"]
        for item in repository["context"]["files"]
    ]
    return {
        "runId": run_id,
        "planHash": state["planHash"],
        "writeTarget": write_target,
        "files": files,
    }


def engage_repository(
    store: RunStore,
    run_id: str,
    repo_id: str,
    *,
    reason: str,
    evidence: tuple[str, ...],
    override: Path | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Resolve and SHA-lock one Stage 0 repository after interaction evidence exists."""
    with store.lock(run_id):
        state = store.load(run_id)

        def deny(message: str, error_type: type[Exception] = ConfigurationError) -> NoReturn:
            store.append_event(
                state,
                "security.authorization.denied",
                {
                    "actor": actor,
                    "action": "engage-repository",
                    "repository": repo_id,
                    "outcome": "denied",
                    "reason": message[:500],
                },
            )
            store.save(state)
            raise error_type(message)

        if (
            state.get("plan", {}).get("phase") != "discovery"
            or (state.get("workflow") or {}).get("currentStage") != "0"
        ):
            deny(
                f"run {run_id} can engage repositories only during Stage 0 discovery",
                ResolutionError,
            )

        reason = reason.strip()
        if not reason or not evidence:
            deny("repository engagement requires a reason and source evidence")
        plan = state.get("plan", {})
        engaged = {item["id"]: item for item in plan.get("repositories", [])}
        if repo_id in engaged:
            return {"repository": repo_id, "status": "already-engaged"}
        catalog = {item["id"]: item for item in plan.get("catalogRepositories", [])}
        target = catalog.get(repo_id)
        if target is None:
            deny(f"repository {repo_id} is not in the run catalog")
        for reference in evidence:
            source_repo, separator, path = reference.partition(":")
            source = engaged.get(source_repo)
            if not separator or source is None:
                deny(f"repository engagement evidence is not from engaged context: {reference}")
            locked = {item["path"] for item in source["context"]["files"]}
            if path not in locked:
                deny(f"repository engagement evidence is not SHA-locked: {reference}")

        saved_override = state.get("checkoutOverrides", {}).get(repo_id)
        override = (
            override if override is not None else (Path(saved_override) if saved_override else None)
        )
        try:
            root = (
                repository_root(override)
                if override
                else managed_repository(
                    target["remote"],
                    repo_id,
                    snapshot_root=store.run_dir(run_id) / "repositories",
                )
            )
            if normalize_remote(origin_url(root)) != normalize_remote(target["remote"]):
                raise ResolutionError(
                    f"catalog remote mismatch for {repo_id}: expected {target['remote']}, "
                    f"got {origin_url(root)}"
                )
        except ResolutionError as exc:
            detail = re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+", "[remote]", str(exc))
            diagnostic = redact_text(detail)[:500]
            store.record_unavailable_discovery_repository(
                run_id,
                repo_id=repo_id,
                remote=target["remote"],
                reason=reason,
                evidence=evidence,
                error=diagnostic,
                actor=actor,
            )
            return {
                "repository": repo_id,
                "status": "unavailable",
                "error": diagnostic,
                "action": "obtain-repository-access",
            }
        workspace = WorkspaceRepository(
            id=repo_id,
            remote=target["remote"],
            mode="read-only",
            author=bool(target.get("author", False)),
        )
        profile_path = resolve_profile_path(root, workspace.profile)
        if profile_path.is_file():
            profile = load_profile(profile_path)
            if profile.id != repo_id:
                raise ConfigurationError(
                    f"repository profile id mismatch at {profile_path}: "
                    f"expected {repo_id}, got {profile.id}"
                )
            if normalize_remote(profile.remote) != normalize_remote(workspace.remote):
                raise ConfigurationError(
                    f"profile/workspace remote mismatch for {repo_id}: "
                    f"{profile.remote} != {workspace.remote}"
                )
            profile_source = "repository"
        else:
            profile = infer_repository_profile(root, repo_id, workspace.remote)
            profile_source = "inferred"
        resolved = ResolvedRepository(
            workspace=workspace,
            root=root,
            profile=profile,
            context=collect_context(
                root,
                profile,
                additional_required=(profile_path.relative_to(root).as_posix(),)
                if profile_source == "repository"
                else (),
            ),
            profile_source=profile_source,
        )
        store.add_discovery_repository(
            run_id,
            resolved_repository_dict(resolved),
            reason=reason,
            evidence=evidence,
            actor=actor,
        )
        return {
            "repository": repo_id,
            "status": "engaged",
            "root": str(root),
            "revision": resolved.context.revision,
        }


def lock_discovery_context(
    store: RunStore,
    run_id: str,
    repo_id: str,
    *,
    paths: tuple[str, ...],
    reason: str,
    actor: str = "system",
) -> dict[str, Any]:
    """SHA-lock exact Stage 0 evidence paths after targeted code search."""
    reason = reason.strip()
    if not paths or not reason:
        raise ConfigurationError("context locking requires paths and a reason")
    with store.lock(run_id):
        state = store.load(run_id)
        repository = _repository(state, repo_id)
        root = Path(repository["root"])

        def deny(message: str, reason_code: str) -> NoReturn:
            # Host cost writers use the save CAS independently of the operation lock.
            # Reload for every attempt so neither their state nor the audit chain is lost.
            for attempt in range(3):
                latest = store.load(run_id)
                store.append_event(
                    latest,
                    "security.authorization.denied",
                    {
                        "actor": actor,
                        "action": "lock-context",
                        "repository": repo_id,
                        "outcome": "denied",
                        "reason": reason_code,
                    },
                )
                try:
                    store.save(latest)
                except ResolutionError as exc:
                    if (
                        str(exc)
                        != f"stale run state for {run_id}; reload before retrying the operation"
                        or attempt == 2
                    ):
                        raise
                else:
                    break
            raise ResolutionError(message)

        locked_revision = repository["context"]["revision"]
        if head_sha(root) != locked_revision:
            deny(
                f"repository {repo_id} moved after engagement; expected {locked_revision}. "
                "Start a new run from the current revision to lock additional evidence.",
                "repository-moved-after-engagement",
            )
        raw_profile_path = repository.get("profilePath")
        profile_path = resolve_profile_path(
            root,
            raw_profile_path if isinstance(raw_profile_path, str) else ".signal-harness/repo.yaml",
        )
        if repository.get("profileSource", "repository") == "repository" and profile_path.is_file():
            profile = load_profile(profile_path)
        else:
            profile = infer_repository_profile(root, repo_id, repository["remote"])
        previous = {item["path"]: item for item in repository["context"]["files"]}
        context = collect_context(root, profile, required_files=(*previous, *paths))
        collected = {item.path: asdict(item) for item in context.files}
        for path, locked in previous.items():
            if collected.get(path) != locked:
                deny(
                    f"previously locked context changed in {repo_id}: {path}",
                    "previous-context-changed",
                )
        if context.revision != locked_revision:
            deny(
                f"repository {repo_id} moved while locking discovery context",
                "repository-moved-during-context-lock",
            )
        current = {item.path for item in context.files}
        added = tuple(sorted(current - set(previous)))
        store.update_discovery_context(
            run_id,
            repo_id=repo_id,
            context=asdict(context),
            added=added,
            reason=reason,
            actor=actor,
        )
        return {
            "repository": repo_id,
            "revision": context.revision,
            "added": list(added),
        }


def acknowledge_task_context(
    store: RunStore,
    run_id: str,
    task_id: str,
    references: tuple[str, ...],
) -> dict[str, Any]:
    if not references:
        raise ConfigurationError("context acknowledgement requires at least one repo:path")
    state = store.load(run_id)
    task = state.get("tasks", {}).get(task_id)
    if not isinstance(task, dict):
        raise ResolutionError(f"run state has no task: {task_id}")
    required = {f"{item['repo']}:{item['path']}" for item in task.get("requiredContext", [])}
    submitted = set(references)
    unknown = sorted(submitted - required)
    if unknown:
        raise ConfigurationError(
            f"task {task_id} context acknowledgement is not required or locked: {unknown}"
        )
    acknowledged = set(task.get("contextAcknowledged", []))
    acknowledged.update(submitted)
    task["contextAcknowledged"] = sorted(acknowledged)
    missing = sorted(required - acknowledged)
    store.append_event(
        state,
        "task.context.acknowledged",
        {"task": task_id, "acknowledged": sorted(submitted), "missing": missing},
    )
    store.save(state)
    return {
        "task": task_id,
        "acknowledged": sorted(acknowledged),
        "missing": missing,
        "complete": not missing,
    }


def next_action(store: RunStore, run_id: str, task_id: str | None = None) -> dict[str, Any]:
    state = store.load(run_id)
    workflow_state = state.get("workflow")
    if not isinstance(workflow_state, dict):
        raise ResolutionError(f"run {run_id} has no workflow")
    if workflow_state["status"] == "complete":
        return {"runId": run_id, "action": "complete", "status": state["status"]}

    current = workflow_state["currentStage"]
    selected_task_id = None
    task_record = None
    if current == "tasks":
        selected_task_id, workflow_task = _active_task(state, task_id)
        current = workflow_task["currentStage"]
        task_record = state["tasks"][selected_task_id]
    elif task_id is not None:
        raise ConfigurationError(
            f"--task is valid only during per-task stages, not stage {current}"
        )

    stage = _stage(state, current)
    bundle = context_bundle(store, run_id, selected_task_id)
    action = "complete-stage"
    worktree = None
    writes: list[str] = []
    unavailable = state.get("plan", {}).get("unavailableRepositories", [])
    required_unavailable = [
        item
        for item in unavailable
        if item.get("requiredByManifest") or item.get("requiredByDiscovery")
    ]
    if current == "0" and required_unavailable:
        action = "obtain-repository-access"
    if selected_task_id is not None:
        assert isinstance(task_record, dict)
        worktree = task_record.get("worktree")
        required = {
            f"{item['repo']}:{item['path']}" for item in task_record.get("requiredContext", [])
        }
        acknowledged = set(task_record.get("contextAcknowledged", []))
        if required - acknowledged:
            action = "ack-context"
        elif not worktree:
            action = "prepare"
        elif current == "2":
            action = "implement"
            writes = [worktree]
        elif current == "3":
            action = "review"
            writes = [worktree]

    result = {
        "runId": run_id,
        "action": action,
        "task": selected_task_id,
        "repo": task_record["repo"] if task_record else None,
        "worktree": worktree,
        "stage": stage,
        "context": bundle,
        "permissions": {
            "write": writes,
            "read": [str(Path(item["root"]) / item["path"]) for item in bundle["files"]],
        },
    }
    if current == "0" and unavailable:
        result["repositoryAccess"] = {
            "warning": (
                "Stage 0 has incomplete repository context. Obtain read access for required "
                "repositories, then resume the run; classify all other unavailable repositories "
                "in specification.interfaceImpact.unavailableRepositories."
            ),
            "unavailable": unavailable,
            "required": required_unavailable,
        }
    if selected_task_id is not None and current == "2":
        tdd = workflow_state["tasks"][selected_task_id].get("tdd", {})
        active_case = next(
            (item for item in tdd.get("cases", []) if item["id"] == tdd.get("activeCase")),
            None,
        )
        result["tdd"] = {
            "status": tdd.get("status"),
            "activeCase": active_case,
        }
    return result


def record_artifact(
    store: RunStore,
    run_id: str,
    *,
    kind: str,
    reference: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    if not kind.strip() or not reference.strip():
        raise ConfigurationError("artifact kind and reference must not be empty")
    state = store.load(run_id)
    if task_id is not None and task_id not in state.get("tasks", {}):
        raise ResolutionError(f"run state has no task: {task_id}")
    artifact = {
        "kind": kind.strip(),
        "reference": reference.strip(),
        "task": task_id,
        "recordedAt": utc_now(),
    }
    state.setdefault("artifacts", []).append(artifact)
    store.append_event(state, "artifact.recorded", f"{kind}: {reference}")
    store.save(state)
    return artifact
