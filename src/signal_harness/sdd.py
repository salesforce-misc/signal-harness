from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .catalog import RepositoryCatalog, load_policy_catalog
from .errors import ConfigurationError, ResolutionError
from .git import (
    _run_git,
    current_branch,
    default_repository_cache,
    head_sha,
    managed_repository,
    normalize_remote,
    origin_url,
    repository_root,
)
from .models import (
    RepositoryProfile,
    Workspace,
    WorkspaceRepository,
    WorkspaceTask,
)
from .planner import (
    PlanningSnapshot,
    WorkspacePlan,
    load_profile,
    resolve_profile_path,
    resolve_workspace,
)
from .security import redact_text
from .task_cards import TaskCard, discover_task_cards


def discover_feature_manifest(root: Path, feature: str) -> str:
    matches = sorted(root.glob(f"specs/**/sdd/{feature}/manifest.yaml"))
    if len(matches) != 1:
        relative = [path.relative_to(root).as_posix() for path in matches]
        raise ResolutionError(
            f"feature {feature} must resolve to exactly one manifest; found {relative}"
        )
    return matches[0].relative_to(root).as_posix()


def _candidate_root(parent: Path, remote: str) -> Path:
    return parent / remote.removesuffix(".git").rsplit("/", 1)[-1]


def discover_catalog_roots(
    catalog: RepositoryCatalog,
    current_root: Path,
    overrides: dict[str, Path],
    exclude: tuple[str, ...] = (),
    unavailable: dict[str, str] | None = None,
    repository_ids: tuple[str, ...] | None = None,
    snapshot_roots: list[PlanningSnapshot] | None = None,
) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    current_remote = normalize_remote(origin_url(current_root))
    requested = set(repository_ids) if repository_ids is not None else set(catalog.repositories)
    for repo_id, repository in catalog.repositories.items():
        if repo_id in exclude or repo_id not in requested:
            continue
        candidate = overrides.get(repo_id)
        if candidate is None and normalize_remote(repository.remote) == current_remote:
            candidate = current_root
        if candidate is None:
            sibling = _candidate_root(current_root.parent, repository.remote)
            if sibling.is_dir():
                candidate = sibling
        if candidate is None:
            owned_root = default_repository_cache() / "plans" / uuid4().hex
            if snapshot_roots is not None:
                snapshot_roots.append(PlanningSnapshot(owned_root))
            try:
                # Planning documents may advance the sponsor HEAD. Share fetch
                # objects, but give each plan its own writable snapshot outside
                # the run directory (which RunStore.create reserves later).
                candidate = managed_repository(
                    repository.remote,
                    repo_id,
                    snapshot_root=owned_root,
                )
            except ResolutionError as exc:
                if snapshot_roots is None:
                    _release_planning_snapshots((_planning_snapshot(owned_root),))
                if unavailable is None:
                    raise
                unavailable[repo_id] = str(exc)
                continue
            finally:
                if snapshot_roots is not None:
                    snapshot_roots[-1] = _planning_snapshot(owned_root)
        root = repository_root(candidate)
        if normalize_remote(origin_url(root)) != normalize_remote(repository.remote):
            raise ResolutionError(
                f"catalog remote mismatch for {repo_id}: expected {repository.remote}, "
                f"got {origin_url(root)}"
            )
        roots[repo_id] = root
    return roots


def _load_profiles(roots: dict[str, Path]) -> dict[str, RepositoryProfile]:
    result: dict[str, RepositoryProfile] = {}
    for expected_id, root in roots.items():
        profile_path = resolve_profile_path(root, ".signal-harness/repo.yaml")
        if not profile_path.is_file():
            continue
        profile = load_profile(profile_path)
        if profile.id != expected_id:
            raise ConfigurationError(
                f"catalog/profile id mismatch: expected {expected_id}, got {profile.id}"
            )
        result[expected_id] = profile
    return result


def _task_id(prefix: str, feature: str, repo_id: str) -> str:
    value = f"{prefix}-{feature}-{repo_id}".lower()
    return re.sub(r"[^a-z0-9-]+", "-", value).strip("-")


def _resolve_repo_id(raw: str, catalog: RepositoryCatalog) -> str:
    if raw in catalog.repositories:
        return raw
    candidates = [
        repo_id
        for repo_id, repository in catalog.repositories.items()
        if repository.remote.removesuffix(".git").rsplit("/", 1)[-1] == raw
    ]
    if len(candidates) != 1:
        raise ConfigurationError(
            f"task-card repository {raw!r} must resolve once in catalog "
            f"{catalog.id}; found {candidates}"
        )
    return candidates[0]


def synthesize_interaction_workspace(
    *,
    feature: str,
    sponsor: str,
    feature_manifest: str,
    catalog: RepositoryCatalog,
    profiles: dict[str, RepositoryProfile],
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    task_cards: tuple[TaskCard, ...] = (),
    discovery_only: bool = False,
    available_ids: tuple[str, ...] | None = None,
) -> Workspace:
    active_cards = tuple(card for card in task_cards if not card.deferred)
    task_repo_ids = tuple(dict.fromkeys(card.repo for card in active_cards))
    required_ids = tuple(dict.fromkeys((sponsor, *task_repo_ids)))
    unknown_selection = sorted((set(include) | set(exclude)) - set(catalog.repositories))
    if unknown_selection:
        raise ConfigurationError(
            f"repository selection names unknown catalog entries: {unknown_selection}"
        )
    forbidden_excludes = sorted(set(exclude) & set(required_ids))
    if forbidden_excludes:
        raise ConfigurationError(
            f"cannot exclude sponsor or task repositories: {forbidden_excludes}"
        )
    # The catalog defines the discovery universe. Stage 0 determines the actual
    # interfaces and callers; a predeclared interaction graph must not narrow it.
    resolved_ids = set(available_ids or catalog.repositories)
    active_ids = tuple(
        repo_id
        for repo_id in catalog.repositories
        if repo_id not in exclude and repo_id in resolved_ids
    )
    participant_ids = tuple(repo_id for repo_id in catalog.repositories if repo_id in resolved_ids)

    write_ids = set() if discovery_only else set(task_repo_ids or (sponsor,))
    repositories = {
        repo_id: WorkspaceRepository(
            id=repo_id,
            remote=catalog.repositories[repo_id].remote,
            mode="read-write" if repo_id in write_ids else "read-only",
            feature_manifest=feature_manifest if repo_id == sponsor else None,
            interacts_with=(),
            author=catalog.repositories[repo_id].author,
        )
        for repo_id in participant_ids
    }
    tasks = list(_tasks_from_cards(feature, sponsor, active_cards))
    if discovery_only:
        tasks = []

    return Workspace(
        feature=feature,
        sponsor=sponsor,
        workflow=catalog.workflow,
        repositories=repositories,
        include=active_ids,
        exclude=tuple(repo_id for repo_id in exclude if repo_id in repositories),
        tasks=tuple(tasks),
    )


def _tasks_from_cards(
    feature: str,
    sponsor: str,
    active_cards: tuple[TaskCard, ...],
    *,
    allow_fallback: bool = True,
) -> tuple[WorkspaceTask, ...]:
    tasks: list[WorkspaceTask] = []
    if active_cards:
        active_card_ids = {card.id for card in active_cards}
        for card in active_cards:
            unavailable = sorted(set(card.depends_on) - active_card_ids)
            if unavailable:
                raise ConfigurationError(
                    f"active task {card.id} depends on deferred or missing tasks: {unavailable}"
                )
            tasks.append(
                WorkspaceTask(
                    id=card.id,
                    repo=card.repo,
                    depends_on=card.depends_on,
                    description=card.description,
                    source=card.source,
                    declared_status=card.declared_status,
                    test_cases=card.test_cases,
                )
            )
    elif allow_fallback:
        tasks.append(
            WorkspaceTask(
                id=_task_id("implement", feature, sponsor),
                repo=sponsor,
                description=f"Implement the {feature} feature in {sponsor}.",
            )
        )

    return tuple(tasks)


def _build_sdd_plan(
    *,
    feature: str,
    current_path: Path,
    sponsor_id: str | None = None,
    feature_manifest: str | None = None,
    policy: str | Path | None = None,
    repo_overrides: dict[str, Path] | None = None,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    snapshot_roots: list[PlanningSnapshot],
) -> WorkspacePlan:
    current_root = repository_root(current_path)
    invoking_profile_path = resolve_profile_path(current_root, ".signal-harness/repo.yaml")
    if not invoking_profile_path.is_file():
        raise ConfigurationError(
            f"repository {current_root} cannot initiate SDD without a repository profile at "
            f"{invoking_profile_path}"
        )
    invoking_profile = load_profile(invoking_profile_path)
    catalog = load_policy_catalog(policy)
    if invoking_profile.id not in catalog.repositories:
        raise ResolutionError(
            f"invoking repository {invoking_profile.id} is not available in policy {policy}"
        )
    sponsor = sponsor_id or invoking_profile.id
    if sponsor not in catalog.repositories:
        raise ConfigurationError(f"unknown sponsor repository {sponsor!r} in policy {policy}")
    unavailable: dict[str, str] = {}
    roots = discover_catalog_roots(
        catalog,
        current_root,
        repo_overrides or {},
        exclude,
        unavailable,
        (sponsor,),
        snapshot_roots,
    )
    profiles = _load_profiles(roots)
    if sponsor not in profiles:
        diagnostic = ""
        if sponsor in unavailable:
            # Remote URLs may contain credentials; do not expose them in diagnostics.
            detail = re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+", "[remote]", unavailable[sponsor])
            diagnostic = f"; acquisition failed: {redact_text(detail)[:500]}"
        raise ResolutionError(
            f"no local checkout/profile for sponsor {sponsor}{diagnostic}; "
            f"pass --repo {sponsor}=PATH"
        )
    sponsor_root = roots[sponsor]
    manifest = feature_manifest or discover_feature_manifest(sponsor_root, feature)
    task_cards = discover_task_cards(
        sponsor_root,
        manifest,
        sponsor,
        lambda raw: _resolve_repo_id(raw, catalog),
    )
    workspace = synthesize_interaction_workspace(
        feature=feature,
        sponsor=sponsor,
        feature_manifest=manifest,
        catalog=catalog,
        profiles=profiles,
        include=include,
        exclude=exclude,
        task_cards=task_cards,
        discovery_only=True,
        available_ids=tuple(roots),
    )
    proposed_tasks = _tasks_from_cards(
        feature,
        sponsor,
        tuple(card for card in task_cards if not card.deferred),
        allow_fallback=False,
    )
    return resolve_workspace(
        workspace,
        Path.cwd(),
        sponsor_root,
        roots,
        phase="discovery",
        proposed_tasks=proposed_tasks,
        unavailable_repositories=tuple(
            {
                "id": repo_id,
                "remote": catalog.repositories[repo_id].remote,
                "reason": reason,
                "requiredByManifest": any(card.repo == repo_id for card in task_cards),
            }
            for repo_id, reason in unavailable.items()
        ),
        required_engagements=tuple(sorted(set(include) - set(exclude))),
        checkout_overrides=tuple(
            (repo_id, str(path.expanduser().resolve()))
            for repo_id, path in (repo_overrides or {}).items()
            if repo_id in catalog.repositories and repo_id not in exclude
        ),
        catalog_repositories=tuple(
            {
                "id": repo_id,
                "remote": repository.remote,
                "author": repository.author,
            }
            for repo_id, repository in catalog.repositories.items()
            if repo_id not in exclude
        ),
    )


def _planning_snapshot(root: Path) -> PlanningSnapshot:
    return PlanningSnapshot(
        root,
        tuple(
            (path.name, path.read_text(encoding="utf-8"))
            for path in sorted(root.glob(".*.snapshot.json"))
        ),
    )


def _release_planning_snapshots(roots: tuple[PlanningSnapshot, ...]) -> None:
    """Release only allocations made by this invocation; preserve changed resources."""
    for allocation in roots:
        root = allocation.root
        if not root.exists():
            continue
        try:
            if root.is_symlink() or not re.fullmatch(r"[0-9a-f]{32}", root.name):
                raise ResolutionError("invalid planning allocation")
            if _planning_snapshot(root) != allocation:
                raise ResolutionError("snapshot ownership descriptor changed")
            entries = list(root.iterdir())
            identities = list(root.glob(".*.snapshot.json"))
            expected_entries = set(identities) | {
                root / item.name[1 : -len(".snapshot.json")] for item in identities
            }
            if any(entry not in expected_entries for entry in entries):
                raise ResolutionError("allocation contains unexpected files")
            for identity_path in identities:
                if identity_path.is_symlink():
                    raise ResolutionError("snapshot identity is a symlink")
                identity = json.loads(identity_path.read_text(encoding="utf-8"))
                snapshot = root / identity_path.name[1 : -len(".snapshot.json")]
                if set(identity) != {"repository", "remote", "revision"}:
                    raise ResolutionError("snapshot identity is invalid")
                if snapshot.exists():
                    if snapshot.is_symlink() or repository_root(snapshot) != snapshot.resolve():
                        raise ResolutionError("snapshot path changed")
                    if (
                        normalize_remote(origin_url(snapshot)) != identity["remote"]
                        or head_sha(snapshot) != identity["revision"]
                        or current_branch(snapshot) != "(detached)"
                        or _run_git(
                            "status",
                            "--porcelain",
                            "--untracked-files=all",
                            "--ignored",
                            cwd=snapshot,
                        )
                    ):
                        raise ResolutionError("snapshot content or identity changed")
                    common = Path(
                        _run_git(
                            "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=snapshot
                        )
                    )
                    # Git refuses dirty worktrees; never force removal or recursively delete.
                    _run_git("worktree", "remove", str(snapshot), cwd=common.parent)
                identity_path.unlink()
            root.rmdir()
        except (OSError, ValueError, KeyError, TypeError, ResolutionError) as exc:
            raise ResolutionError(
                f"planning snapshot recovery required at {root}; "
                f"preserved remaining resources: {exc}"
            ) from exc


def release_sdd_plan(plan: WorkspacePlan) -> None:
    """Release a preview/unpersisted plan. Never release after RunStore.create succeeds."""
    _release_planning_snapshots(plan.planning_snapshots)


def build_sdd_plan(
    *,
    feature: str,
    current_path: Path,
    sponsor_id: str | None = None,
    feature_manifest: str | None = None,
    policy: str | Path | None = None,
    repo_overrides: dict[str, Path] | None = None,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> WorkspacePlan:
    """Build a plan; callers release_sdd_plan unless successfully persisted to a run."""
    roots: list[PlanningSnapshot] = []
    try:
        plan = _build_sdd_plan(
            feature=feature,
            current_path=current_path,
            sponsor_id=sponsor_id,
            feature_manifest=feature_manifest,
            policy=policy,
            repo_overrides=repo_overrides,
            include=include,
            exclude=exclude,
            snapshot_roots=roots,
        )
        return replace(plan, planning_snapshots=tuple(roots))
    except BaseException:
        _release_planning_snapshots(tuple(roots))
        raise
