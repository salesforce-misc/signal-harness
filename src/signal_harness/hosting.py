from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .errors import ResolutionError
from .findings import review_readiness
from .forge import CommandRunner, provider_for
from .git import head_sha, is_dirty
from .state import RunStore, utc_now
from .worktrees import verify_prepared_task, verify_run_context


def _save_pull_request(
    store: RunStore, run_id: str, task_id: str, pull_request: dict[str, Any], event: str
) -> dict[str, Any]:
    state = store.load(run_id)
    previous = state["tasks"][task_id].get("pullRequest") or {}
    evidence = pull_request.get("approvalEvidence")
    if evidence is not None and evidence != previous.get("approvalEvidence"):
        store.append_event(state, "pull-request.approval-decision", {"task": task_id, **evidence})
    state["tasks"][task_id]["pullRequest"] = pull_request
    store.append_event(state, event, f"{task_id} -> {pull_request['url']}")
    store.save(state)
    return pull_request


def _sync_review_findings(
    store: RunStore, run_id: str, task_id: str, entries: list[dict[str, Any]], provider: str
) -> None:
    if not entries:
        return
    state = store.load(run_id)
    findings = state.setdefault("findings", {})
    changed = False
    for entry in entries:
        finding_id = f"{provider}:{entry['kind']}:{entry['id']}"
        intended = {
            "id": finding_id,
            "task": task_id,
            "source": f"{provider}-{entry['kind']}",
            "severity": "medium",
            "summary": entry["body"].strip(),
            "evidence": entry.get("url") or f"{provider}://change-request",
            "affectedCommands": [],
            "behavioralFix": False,
        }
        existing = findings.get(finding_id)
        if existing and all(existing.get(key) == value for key, value in intended.items()):
            if entry.get("resolved") and existing.get("status") == "open":
                existing["status"] = "resolved"
                existing["resolution"] = {
                    "disposition": "address",
                    "actor": provider,
                    "rationale": f"{provider} review thread was resolved",
                    "evidence": intended["evidence"],
                    "regressionEvidence": None,
                    "resolvedAt": utc_now(),
                }
                store.append_event(state, f"finding.{provider}.resolved", finding_id)
                changed = True
            elif (
                entry.get("resolved") is False
                and existing.get("status") == "resolved"
                and (existing.get("resolution") or {}).get("actor") == provider
            ):
                existing.update({"status": "open", "resolution": None})
                store.append_event(state, f"finding.{provider}.reopened", finding_id)
                changed = True
            continue
        resolution = None
        status = "open"
        if entry.get("resolved"):
            status = "resolved"
            resolution = {
                "disposition": "address",
                "actor": provider,
                "rationale": f"{provider} review thread was resolved",
                "evidence": intended["evidence"],
                "regressionEvidence": None,
                "resolvedAt": utc_now(),
            }
        if existing:
            existing.update(intended)
            existing.update({"status": status, "resolution": resolution, "recordedAt": utc_now()})
        else:
            findings[finding_id] = {
                **intended,
                "status": status,
                "resolution": resolution,
                "recordedAt": utc_now(),
            }
        store.append_event(state, f"finding.{provider}.synchronized", finding_id)
        changed = True
    if changed:
        store.save(state)


def publish_branch(
    store: RunStore,
    run_id: str,
    task_id: str,
    *,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    verify_run_context(store, run_id)
    task, repo = verify_prepared_task(store, run_id, task_id)
    worktree = Path(task["worktree"])
    if is_dirty(worktree):
        raise ResolutionError("task worktree must be clean after the session-owned commit")
    revision = head_sha(worktree)
    if task.get("publishedHead") == revision:
        return {"head": revision, "branch": task["branch"], "remote": repo["remote"]}
    base_revision = task.get("baseRevision") or repo["context"]["revision"]
    ahead = subprocess.run(
        ["git", "-C", str(worktree), "rev-list", "--count", f"{base_revision}..HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if ahead.returncode != 0 or not ahead.stdout.strip().isdigit():
        raise ResolutionError("cannot determine whether the task branch contains a commit")
    if int(ahead.stdout.strip()) < 1:
        raise ResolutionError("task branch has no session-owned commit to publish")
    completed = runner(
        ["git", "push", "--set-upstream", "origin", task["branch"]],
        cwd=worktree,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ResolutionError(f"git push failed: {detail}")
    state = store.load(run_id)
    state["tasks"][task_id]["publishedHead"] = revision
    state["tasks"][task_id]["publishedAt"] = utc_now()
    store.append_event(state, "branch.published", f"{task_id} -> {task['branch']}@{revision}")
    store.save(state)
    return {"head": revision, "branch": task["branch"], "remote": repo["remote"]}


def open_pull_request(
    store: RunStore,
    run_id: str,
    task_id: str,
    title: str,
    *,
    body_file: Path | None = None,
    draft: bool = False,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    verify_run_context(store, run_id)
    task, repo = verify_prepared_task(store, run_id, task_id)
    provider = provider_for(repo)
    if task.get("pullRequest"):
        return task["pullRequest"]
    if not title.strip():
        raise ResolutionError("pull request title must not be empty")
    if body_file is not None and not body_file.expanduser().resolve().is_file():
        raise ResolutionError(f"pull request body file does not exist: {body_file}")
    value = provider.open(repo, task, title, body_file, draft, runner)
    return _save_pull_request(store, run_id, task_id, value, "pull-request.opened")


def refresh_pull_request(
    store: RunStore,
    run_id: str,
    task_id: str,
    *,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    task, repo = verify_prepared_task(store, run_id, task_id)
    recorded = task.get("pullRequest")
    if not isinstance(recorded, dict) or not recorded.get("url"):
        raise ResolutionError(f"task has no recorded pull request: {task_id}")
    provider = provider_for(repo)
    value, entries = provider.refresh(repo, task, recorded, runner)
    saved = _save_pull_request(store, run_id, task_id, value, "pull-request.refreshed")
    _sync_review_findings(store, run_id, task_id, entries, provider.name)
    return saved


def resume_from_pull_request(
    store: RunStore,
    run_id: str,
    task_id: str,
    *,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    pull_request = refresh_pull_request(store, run_id, task_id, runner=runner)
    state = store.load(run_id)
    workflow = state.get("workflow") or {}
    workflow_task = (workflow.get("tasks") or {}).get(task_id) or {}
    checks = pull_request.get("checks", [])
    checks_pass = not checks or all(check["status"] == "pass" for check in checks)
    readiness = review_readiness(state, task_id)
    eligible = (
        workflow.get("currentStage") == "tasks"
        and workflow_task.get("status") == "active"
        and workflow_task.get("currentStage") == "3"
        and pull_request["state"] == "MERGED"
        and pull_request["reviewDecision"] == "APPROVED"
        and checks_pass
        and readiness["ready"]
    )
    if (
        workflow.get("currentStage") != "tasks"
        or workflow_task.get("status") != "active"
        or workflow_task.get("currentStage") != "3"
    ):
        reason = "not-at-review-gate"
    elif pull_request.get("headDeleted") and pull_request["state"] != "MERGED":
        reason = "branch-deleted"
    elif pull_request.get("mergeStateStatus") in {"BEHIND", "DIRTY"}:
        reason = "stale-base"
    elif pull_request["state"] == "CLOSED":
        reason = "closed-unmerged"
    elif any(check["status"] == "fail" for check in checks):
        reason = "ci-failed"
    elif any(check["status"] == "pending" for check in checks):
        reason = "ci-pending"
    elif pull_request["state"] == "OPEN" and pull_request["reviewDecision"] == "CHANGES_REQUESTED":
        reason = "changes-requested"
    elif pull_request["state"] == "OPEN" and pull_request["reviewDecision"] == "APPROVED":
        reason = "awaiting-merge"
    elif pull_request["state"] == "OPEN":
        reason = "awaiting-approval"
    elif pull_request["state"] == "MERGED" and not readiness["ready"]:
        reason = "merged-incomplete-evidence"
    elif pull_request["state"] == "MERGED" and pull_request["reviewDecision"] != "APPROVED":
        reason = "merged-without-approval"
    else:
        reason = "advanced"
    if eligible:
        store.clear_current_gate(
            run_id,
            {
                "state": pull_request["state"],
                "reviewDecision": pull_request["reviewDecision"],
                "url": pull_request["url"],
            },
            task_id,
        )
    return {
        "pullRequest": pull_request,
        "advanced": eligible,
        "reason": reason,
        "blockers": readiness["blockers"],
    }
