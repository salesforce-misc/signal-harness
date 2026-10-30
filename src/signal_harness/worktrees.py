from __future__ import annotations

import getpass
import hashlib
import subprocess
from pathlib import Path
from typing import Any

from .errors import ConfigurationError, ResolutionError
from .git import (
    current_branch,
    head_sha,
    is_dirty,
    normalize_remote,
    origin_url,
    repository_root,
)
from .state import RunStore, slug, utc_now


def _run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ResolutionError(f"git {' '.join(args)} failed in {root}: {detail}")
    return completed


def _repo_record(state: dict[str, Any], repo_id: str) -> dict[str, Any]:
    for repo in state["plan"]["repositories"]:
        if repo["id"] == repo_id:
            return repo
    raise ResolutionError(f"run state has no repository: {repo_id}")


def _task_record(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    task = state.get("tasks", {}).get(task_id)
    if not isinstance(task, dict):
        raise ResolutionError(f"run state has no task: {task_id}")
    return task


def _verify_context(repo: dict[str, Any]) -> None:
    root = Path(repo["root"])
    context = repo["context"]
    if context.get("dirty"):
        raise ResolutionError(
            f"source repo {repo['id']} was dirty when planned; "
            "commit/stash changes and start a new run"
        )
    if repository_root(root) != root.resolve():
        raise ResolutionError(f"repository root changed for {repo['id']}: {root}")
    if normalize_remote(origin_url(root)) != normalize_remote(repo["remote"]):
        raise ResolutionError(f"repository remote changed for {repo['id']}")
    if head_sha(root) != context["revision"]:
        raise ResolutionError(f"repository HEAD changed since planning for {repo['id']}")
    if is_dirty(root):
        raise ResolutionError(f"source repo became dirty after planning: {repo['id']}")

    for item in context["files"]:
        path = root / item["path"]
        if not path.is_file():
            raise ResolutionError(f"locked context file is missing: {repo['id']}:{item['path']}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise ResolutionError(f"locked context file changed: {repo['id']}:{item['path']}")


def _git_user(root: Path) -> str:
    completed = _run_git(root, "config", "user.email", check=False)
    candidate = completed.stdout.strip().partition("@")[0] if completed.returncode == 0 else ""
    return slug(candidate or getpass.getuser())


def _branch_exists(root: Path, branch: str) -> bool:
    return (
        _run_git(root, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0
    )


def _base_revision(state: dict[str, Any], task_id: str, repo: dict[str, Any]) -> str:
    task = _task_record(state, task_id)
    base = task.get("base") or {"kind": "repository-default"}
    if base.get("kind") != "dependency-head":
        return repo["context"]["revision"]
    dependency_id = base.get("task")
    if not isinstance(dependency_id, str) or not dependency_id:
        raise ResolutionError(f"task {task_id} dependency-head base is missing task")
    dependency = _task_record(state, dependency_id)
    revision = dependency.get("publishedHead")
    if not revision and state.get("plan", {}).get("policyMode") == "evaluation":
        revision = dependency.get("frozenHead")
    if not isinstance(revision, str) or not revision:
        raise ResolutionError(
            f"task {task_id} requires an immutable dependency head from {dependency_id}"
        )
    if dependency.get("repo") != task.get("repo"):
        raise ResolutionError(
            f"task {task_id} dependency-head base must be in repository {task['repo']}"
        )
    exists = _run_git(Path(repo["root"]), "cat-file", "-e", f"{revision}^{{commit}}", check=False)
    if exists.returncode != 0:
        raise ResolutionError(
            f"immutable dependency head {dependency_id}@{revision} is unavailable locally"
        )
    return revision


def _compensate_preparation(
    store: RunStore,
    run_id: str,
    repo: dict[str, Any],
    target: Path,
    branch: str,
    revision: str,
) -> None:
    """Undo only this invocation's pristine, still unrecorded Git resources."""
    persisted = store.load(run_id)
    for task in persisted.get("tasks", {}).values():
        if task.get("worktree") == str(target) or task.get("branch") == branch:
            raise ResolutionError("created resources are now recorded by a task")
    if target.is_symlink() or repository_root(target) != target.resolve():
        raise ResolutionError("created worktree root changed")
    if current_branch(target) != branch:
        raise ResolutionError("created worktree branch changed")
    if normalize_remote(origin_url(target)) != normalize_remote(repo["remote"]):
        raise ResolutionError("created worktree remote changed")
    ignored = _run_git(target, "ls-files", "--others", "--ignored", "--exclude-standard").stdout
    if head_sha(target) != revision or is_dirty(target) or ignored:
        raise ResolutionError("created worktree has changed; preserving its contents")
    source = Path(repo["root"])
    _run_git(source, "worktree", "remove", str(target))
    remaining = _run_git(source, "worktree", "list", "--porcelain").stdout.splitlines()
    if f"branch refs/heads/{branch}" in remaining:
        raise ResolutionError("created branch is in use by another worktree")
    # Compare-and-delete: never delete a branch that advanced after validation.
    _run_git(source, "update-ref", "-d", f"refs/heads/{branch}", revision)


def prepare_task(store: RunStore, run_id: str, task_id: str) -> dict[str, Any]:
    state = store.load(run_id)
    task = _task_record(state, task_id)
    from .artifact_handoff import resolved_task_artifacts

    task["resolvedArtifacts"] = resolved_task_artifacts(state, task_id)
    repo = _repo_record(state, task["repo"])
    if repo["mode"] != "read-write":
        raise ResolutionError(f"task {task_id} targets non-writable repo {repo['id']}")
    required_context = {
        f"{item['repo']}:{item['path']}" for item in task.get("requiredContext", [])
    }
    acknowledged_context = set(task.get("contextAcknowledged", []))
    missing_context = sorted(required_context - acknowledged_context)
    if missing_context:
        raise ResolutionError(
            f"task {task_id} requires context acknowledgement before prepare: {missing_context}"
        )

    expected_branch = f"dev/{_git_user(Path(repo['root']))}/{slug(run_id)}-{slug(task_id)}"
    target = store.run_dir(run_id) / "worktrees" / f"{slug(task_id)}--{slug(repo['id'])}"

    if task.get("status") == "prepared":
        if not target.is_dir() or repository_root(target) != target.resolve():
            raise ResolutionError(f"prepared worktree is missing or invalid: {target}")
        if current_branch(target) != expected_branch:
            raise ResolutionError(f"prepared worktree branch changed: {target}")
        return task

    for participant in state["plan"]["repositories"]:
        _verify_context(participant)
    if target.exists():
        raise ResolutionError(f"worktree target already exists but is not recorded: {target}")

    for participant in state["plan"]["repositories"]:
        participant_root = Path(participant["root"]).resolve()
        try:
            target.resolve().relative_to(participant_root)
        except ValueError:
            continue
        raise ResolutionError(
            f"worktree target must be outside participant repos: "
            f"{target} is inside {participant_root}"
        )

    source_root = Path(repo["root"])
    if _branch_exists(source_root, expected_branch):
        raise ResolutionError(
            f"branch already exists but is not recorded for this task: {expected_branch}"
        )

    base_revision = _base_revision(state, task_id, repo)
    target.parent.mkdir(parents=True, exist_ok=True)
    _run_git(
        source_root,
        "worktree",
        "add",
        "-b",
        expected_branch,
        str(target),
        base_revision,
    )
    if repository_root(target) != target.resolve():
        raise ResolutionError(f"created worktree failed root verification: {target}")
    if normalize_remote(origin_url(target)) != normalize_remote(repo["remote"]):
        raise ResolutionError(f"created worktree failed remote verification: {target}")

    task.update(
        {
            "status": "prepared",
            "branch": expected_branch,
            "worktree": str(target),
            "baseRevision": base_revision,
        }
    )
    state["status"] = "active"
    store.append_event(
        state,
        "task.prepared",
        f"{task_id} -> {repo['id']} on {expected_branch}",
    )
    try:
        store.save(state)
    except ResolutionError as exc:
        try:
            _compensate_preparation(store, run_id, repo, target, expected_branch, base_revision)
        except ResolutionError as recovery:
            raise ResolutionError(
                f"{exc}; preparation recovery required for {target} "
                f"on {expected_branch}: {recovery}"
            ) from exc
        raise
    return task


def verify_prepared_task(store: RunStore, run_id: str, task_id: str) -> tuple[dict, dict]:
    state = store.load(run_id)
    task = _task_record(state, task_id)
    if task.get("status") != "prepared":
        raise ResolutionError(f"task is not prepared: {task_id}")
    repo = _repo_record(state, task["repo"])
    worktree = Path(task["worktree"])
    if repository_root(worktree) != worktree.resolve():
        raise ResolutionError(f"task worktree is invalid: {worktree}")
    if current_branch(worktree) != task["branch"]:
        raise ResolutionError(f"task worktree branch changed: {worktree}")
    if normalize_remote(origin_url(worktree)) != normalize_remote(repo["remote"]):
        raise ResolutionError(f"task worktree remote changed: {worktree}")
    return task, repo


def freeze_task_head(store: RunStore, run_id: str, task_id: str) -> dict[str, str]:
    """Freeze a clean committed task head without remote mutation in evaluation mode."""

    verify_run_context(store, run_id)
    task, repo = verify_prepared_task(store, run_id, task_id)
    state = store.load(run_id)
    if state.get("plan", {}).get("policyMode") != "evaluation":
        raise ConfigurationError("local task-head freeze is available only in evaluation mode")
    worktree = Path(task["worktree"])
    if is_dirty(worktree):
        raise ResolutionError("task worktree must be clean after the session-owned commit")
    revision = head_sha(worktree)
    existing = task.get("frozenHead")
    if isinstance(existing, str) and existing:
        if existing != revision:
            raise ResolutionError(
                f"task {task_id} was already frozen at {existing}; current head is {revision}"
            )
        return {
            "head": revision,
            "branch": task["branch"],
            "repository": repo["id"],
            "evidence": "local-evaluation",
        }
    base_revision = task.get("baseRevision") or repo["context"]["revision"]
    ahead = _run_git(worktree, "rev-list", "--count", f"{base_revision}..HEAD")
    if not ahead.stdout.strip().isdigit() or int(ahead.stdout.strip()) < 1:
        raise ResolutionError("task branch has no session-owned commit to freeze")

    persisted = state["tasks"][task_id]
    persisted["frozenHead"] = revision
    persisted["frozenAt"] = utc_now()
    persisted["freezeEvidence"] = {
        "kind": "local-evaluation",
        "branch": task["branch"],
        "repository": repo["id"],
        "immutableRevision": revision,
        "remoteMutation": False,
    }
    store.append_event(state, "task.head.frozen", f"{task_id} -> {revision}")
    store.save(state)
    return {
        "head": revision,
        "branch": task["branch"],
        "repository": repo["id"],
        "evidence": "local-evaluation",
    }


def verify_run_context(store: RunStore, run_id: str) -> None:
    state = store.load(run_id)
    for repository in state["plan"]["repositories"]:
        _verify_context(repository)


def cleanup_task(store: RunStore, run_id: str, task_id: str) -> dict[str, Any]:
    """Remove an owned worktree with durable, revision-safe recovery."""
    with store.lock(run_id):
        return _cleanup_task(store, run_id, task_id)


def _cleanup_task(store: RunStore, run_id: str, task_id: str) -> dict[str, Any]:
    for _attempt in range(5):
        state = store.load(run_id)
        task = _task_record(state, task_id)
        cleanup = task.get("worktreeCleanup")
        if isinstance(cleanup, dict) and cleanup.get("status") == "complete":
            return cleanup
        raw_worktree = task.get("worktree")
        if not isinstance(raw_worktree, str):
            raise ResolutionError(f"task has no recorded worktree: {task_id}")
        workflow_task = (state.get("workflow", {}).get("tasks") or {}).get(task_id) or {}
        if (
            state.get("status") not in {"cancelled", "complete", "evaluation-complete-with-waivers"}
            and workflow_task.get("status") != "complete"
        ):
            raise ResolutionError("worktree cleanup requires a terminal run or completed task")
        repo = _repo_record(state, task["repo"])
        worktree = Path(raw_worktree).resolve()
        owned_root = (store.run_dir(run_id) / "worktrees").resolve()
        expected = (owned_root / f"{slug(task_id)}--{slug(repo['id'])}").resolve()
        if worktree != expected or worktree.parent != owned_root:
            raise ResolutionError(f"refusing to remove unowned worktree path: {worktree}")
        identity = {
            "path": str(worktree),
            "branch": task["branch"],
            "repository": repo["id"],
            "remote": normalize_remote(repo["remote"]),
            "root": str(Path(repo["root"]).resolve()),
        }
        intent = task.get("worktreeCleanupIntent")
        if intent is not None and (
            not isinstance(intent, dict)
            or any(intent.get(key) != value for key, value in identity.items())
        ):
            raise ResolutionError("cleanup intent no longer matches recorded ownership")
        if worktree.exists():
            if not worktree.is_dir() or repository_root(worktree) != worktree:
                raise ResolutionError(f"recorded worktree is missing or invalid: {worktree}")
            if current_branch(worktree) != task.get("branch"):
                raise ResolutionError(f"recorded worktree branch changed: {worktree}")
            if normalize_remote(origin_url(worktree)) != identity["remote"]:
                raise ResolutionError(f"recorded worktree remote changed: {worktree}")
            if is_dirty(worktree):
                raise ResolutionError(f"refusing to remove dirty worktree: {worktree}")
            if intent is not None and intent.get("head") != head_sha(worktree):
                raise ResolutionError("worktree changed after cleanup intent")
        elif intent is None:
            raise ResolutionError(f"recorded worktree is missing or invalid: {worktree}")
        if intent is None:
            intent = {**identity, "head": head_sha(worktree)}
            task["worktreeCleanupIntent"] = intent
            store.append_event(state, "task.worktree.cleanup_intended", {"task": task_id, **intent})
            try:
                store.save(state)
            except ResolutionError as exc:
                if "stale run state" not in str(exc):
                    raise
                continue
        if worktree.exists():
            _run_git(Path(repo["root"]), "worktree", "remove", str(worktree))
        listing = _run_git(Path(repo["root"]), "worktree", "list", "--porcelain").stdout
        if worktree.exists() or any(
            line == f"worktree {worktree}" for line in listing.splitlines()
        ):
            raise ResolutionError("cleanup removal is incomplete; retry after inspecting worktrees")
        retained_head = _run_git(
            Path(repo["root"]), "rev-parse", f"refs/heads/{task['branch']}"
        ).stdout.strip()
        if retained_head != intent["head"]:
            raise ResolutionError("retained branch changed after cleanup intent")
        # Merge only completion into the latest state, retaining concurrent costs/events.
        latest = store.load(run_id)
        latest_task = _task_record(latest, task_id)
        if (
            latest_task.get("worktreeCleanupIntent") != intent
            or latest_task.get("worktree") != raw_worktree
            or latest_task.get("branch") != task["branch"]
            or latest_task.get("repo") != repo["id"]
            or _repo_record(latest, repo["id"]) != repo
        ):
            raise ResolutionError("cleanup ownership changed before completion")
        cleanup = {
            "status": "complete",
            "path": str(worktree),
            "branchRetained": task["branch"],
            "cleanedAt": utc_now(),
        }
        latest_task["worktree"] = None
        latest_task["worktreeCleanup"] = cleanup
        store.append_event(latest, "task.worktree.cleaned", f"{task_id} -> {worktree}")
        try:
            store.save(latest)
        except ResolutionError as exc:
            if "stale run state" not in str(exc):
                raise
            continue
        return cleanup
    raise ResolutionError("cleanup state remained busy; durable intent retained, retry cleanup")
