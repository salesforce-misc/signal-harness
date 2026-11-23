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

from dataclasses import asdict, dataclass
from pathlib import Path

from signal_harness.runtime.config import load_yaml
from signal_harness.runtime.context import ContextLock, collect_context
from signal_harness.runtime.errors import ConfigurationError, ResolutionError
from signal_harness.runtime.git import normalize_remote, origin_url, repository_root
from signal_harness.sdd.feature_context import external_prior_features, feature_context_patterns
from signal_harness.sdd.graph import task_order
from signal_harness.sdd.models import (
    ContextConfig,
    RepositoryProfile,
    Workspace,
    WorkspaceRepository,
    WorkspaceTask,
)
from signal_harness.sdd.workflow import WorkflowDefinition, load_workflow_reference


@dataclass(frozen=True)
class ResolvedRepository:
    workspace: WorkspaceRepository
    root: Path
    profile: RepositoryProfile
    context: ContextLock
    profile_source: str = "repository"


def resolved_repository_dict(resolved: ResolvedRepository) -> dict:
    return {
        "id": resolved.workspace.id,
        "mode": resolved.workspace.mode,
        "root": str(resolved.root),
        "remote": resolved.workspace.remote,
        "profilePath": resolved.workspace.profile,
        "featureManifest": resolved.workspace.feature_manifest,
        "author": resolved.workspace.author,
        "interactsWith": list(resolved.workspace.interacts_with),
        "profileSource": resolved.profile_source,
        "profile": {
            "defaultBranch": resolved.profile.default_branch,
            **({"forge": resolved.profile.forge} if resolved.profile.forge else {}),
            "commands": resolved.profile.commands,
            "signals": [source.as_dict() for source in resolved.profile.signal_sources],
        },
        "context": asdict(resolved.context),
    }


@dataclass(frozen=True)
class PlanningSnapshot:
    root: Path
    identities: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WorkspacePlan:
    workspace: Workspace
    repositories: tuple[ResolvedRepository, ...]
    task_order: tuple[str, ...]
    workflow: WorkflowDefinition | None = None
    distribution: dict | None = None
    phase: str = "execution"
    proposed_tasks: tuple[WorkspaceTask, ...] = ()
    policy_mode: str = "evaluation"
    unavailable_repositories: tuple[dict[str, object], ...] = ()
    catalog_repositories: tuple[dict[str, object], ...] = ()
    required_engagements: tuple[str, ...] = ()
    checkout_overrides: tuple[tuple[str, str], ...] = ()
    # In-process ownership only; never persisted as part of the plan contract.
    planning_snapshots: tuple[PlanningSnapshot, ...] = ()

    def as_dict(self) -> dict:
        tasks = {task.id: task for task in self.workspace.tasks}

        def serialize_task(task: WorkspaceTask, *, mode: str) -> dict:
            return {
                "id": task.id,
                "repo": task.repo,
                "mode": mode,
                "dependsOn": list(task.depends_on),
                "description": task.description,
                "source": task.source,
                "declaredStatus": task.declared_status,
                "testCases": list(task.test_cases),
                "requiredChecks": list(task.required_checks),
                "ownership": task.ownership,
                "base": task.base,
                "context": list(task.required_context),
                "surfaces": list(task.surfaces),
                "callers": list(task.callers),
                "produces": list(task.produces),
                "consumes": list(task.consumes),
            }

        return {
            "feature": self.workspace.feature,
            "sponsor": self.workspace.sponsor,
            "phase": self.phase,
            "policyMode": self.policy_mode,
            "workflow": self.workflow.as_dict() if self.workflow else None,
            "distribution": self.distribution,
            "repositories": [resolved_repository_dict(resolved) for resolved in self.repositories],
            "catalogRepositories": list(self.catalog_repositories),
            "requiredEngagements": list(self.required_engagements),
            "unavailableRepositories": list(self.unavailable_repositories),
            "interactions": [
                {"repositories": list(pair)} for pair in interaction_edges(self.workspace)
            ],
            "tasks": [
                serialize_task(
                    tasks[task_id],
                    mode=self.workspace.repositories[tasks[task_id].repo].mode,
                )
                for task_id in self.task_order
            ],
            "proposedTasks": [
                serialize_task(task, mode="read-only") for task in self.proposed_tasks
            ],
        }


def load_profile(path: Path) -> RepositoryProfile:
    return RepositoryProfile.from_dict(load_yaml(path))


def load_workspace(path: Path) -> Workspace:
    return Workspace.from_dict(load_yaml(path))


def resolve_profile_path(root: Path, requested: str) -> Path:
    return root / requested


def selected_repo_ids(workspace: Workspace) -> tuple[str, ...]:
    if len(workspace.include) != len(set(workspace.include)):
        raise ConfigurationError("selection.include must not contain duplicates")
    if len(workspace.exclude) != len(set(workspace.exclude)):
        raise ConfigurationError("selection.exclude must not contain duplicates")
    unknown_include = sorted(set(workspace.include) - set(workspace.repositories))
    unknown_exclude = sorted(set(workspace.exclude) - set(workspace.repositories))
    if unknown_include:
        raise ConfigurationError(f"selection.include names unknown repos: {unknown_include}")
    if unknown_exclude:
        raise ConfigurationError(f"selection.exclude names unknown repos: {unknown_exclude}")

    selected = tuple(repo_id for repo_id in workspace.include if repo_id not in workspace.exclude)
    if workspace.sponsor not in selected:
        raise ConfigurationError("workspace sponsor must be included and not excluded")
    return selected


def validate_references(workspace: Workspace, selected: tuple[str, ...]) -> None:
    selected_set = set(selected)
    known = set(workspace.repositories)
    for repo_id, repository in workspace.repositories.items():
        unknown = sorted(set(repository.interacts_with) - known)
        if unknown:
            raise ConfigurationError(
                f"repository {repo_id} interactsWith names unknown repositories: {unknown}"
            )
        if repo_id in repository.interacts_with:
            raise ConfigurationError(f"repository {repo_id} cannot interact with itself")

    tasks_by_id = {task.id: task for task in workspace.tasks}
    for task in workspace.tasks:
        if task.repo not in selected_set:
            raise ConfigurationError(
                f"task {task.id} targets excluded or unavailable repo {task.repo}"
            )
        if workspace.repositories[task.repo].mode == "read-only":
            raise ConfigurationError(f"task {task.id} targets read-only repo {task.repo}")
        if not workspace.repositories[task.repo].author:
            raise ConfigurationError(f"task {task.id} targets non-authorable repo {task.repo}")
        same_repo_dependencies = [
            dependency
            for dependency in task.depends_on
            if tasks_by_id.get(dependency) and tasks_by_id[dependency].repo == task.repo
        ]
        base_kind = task.base.get("kind", "repository-default")
        if same_repo_dependencies and base_kind != "dependency-head":
            raise ConfigurationError(
                f"task {task.id} depends on same-repository task(s) {same_repo_dependencies} "
                "and must use dependency-head or be aggregated"
            )
        if base_kind == "dependency-head" and task.base.get("task") not in same_repo_dependencies:
            raise ConfigurationError(
                f"task {task.id} dependency-head must name a same-repository dependency"
            )
        consumed = {item["fromTask"]: item["artifact"] for item in task.consumes}
        unknown_consumption_sources = sorted(set(consumed) - set(task.depends_on))
        if unknown_consumption_sources:
            raise ConfigurationError(
                f"task {task.id} consumes artifacts from non-dependencies: "
                f"{unknown_consumption_sources}"
            )
        for dependency in task.depends_on:
            producer = tasks_by_id.get(dependency)
            if producer is None or producer.repo == task.repo:
                continue
            artifact_id = consumed.get(dependency)
            if artifact_id is None:
                raise ConfigurationError(
                    f"task {task.id} cross-repository dependency {dependency} requires a "
                    "consumed artifact"
                )
            if artifact_id not in {item["id"] for item in producer.produces}:
                raise ConfigurationError(
                    f"task {task.id} consumes undeclared artifact {artifact_id} from {dependency}"
                )


def interaction_edges(workspace: Workspace) -> tuple[tuple[str, str], ...]:
    selected = set(workspace.include) - set(workspace.exclude)
    edges: set[tuple[str, str]] = set()
    for repo_id, repository in workspace.repositories.items():
        if repo_id not in selected:
            continue
        for target in repository.interacts_with:
            if target in selected:
                first, second = sorted((repo_id, target))
                edges.add((first, second))
    return tuple(sorted(edges))


def _resolve_root(
    repo: WorkspaceRepository,
    current_root: Path,
    overrides: dict[str, Path],
) -> Path:
    candidate = overrides.get(repo.id)
    if candidate is None and normalize_remote(origin_url(current_root)) == normalize_remote(
        repo.remote
    ):
        candidate = current_root
    if candidate is None:
        raise ResolutionError(f"no local path for {repo.id}; pass --repo {repo.id}=/absolute/path")
    root = repository_root(candidate)
    actual_remote = normalize_remote(origin_url(root))
    expected_remote = normalize_remote(repo.remote)
    if actual_remote != expected_remote:
        raise ResolutionError(
            f"repository remote mismatch for {repo.id}: expected {repo.remote}, got {actual_remote}"
        )
    return root


def _inferred_default_branch(root: Path) -> str:
    import subprocess

    symbolic = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "symbolic-ref",
            "--quiet",
            "--short",
            "refs/remotes/origin/HEAD",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    remote_branch = symbolic.stdout.strip()
    if symbolic.returncode == 0 and remote_branch.startswith("origin/"):
        branch = remote_branch.removeprefix("origin/")
        verified = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/remotes/origin/{branch}",
            ],
            check=False,
        )
        if verified.returncode == 0:
            return branch
    raise ResolutionError(
        f"cannot infer repository default branch in {root}; "
        "set a valid origin/HEAD or provide a repository profile"
    )


def _local_ci_context(root: Path) -> tuple[str, ...]:
    """Follow only repository-local GitLab includes, with bounded scope."""
    pending = [".gitlab-ci.yml"]
    selected: set[str] = set()
    while pending:
        pattern = pending.pop()
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ConfigurationError("GitLab local include must stay inside the repository")
        for candidate in root.glob(pattern):
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise ConfigurationError("GitLab local include escapes repository")
            relative = candidate.relative_to(root).as_posix()
            if not resolved.is_file() or relative in selected:
                continue
            selected.add(relative)
            if len(selected) > 100:
                raise ConfigurationError("GitLab local include context exceeds 100 files")
            data = load_yaml(resolved)
            includes = data.get("include", []) if isinstance(data, dict) else []
            if not isinstance(includes, list):
                includes = [includes]
            for include in includes:
                local = (
                    include
                    if isinstance(include, str)
                    else (include.get("local") if isinstance(include, dict) else None)
                )
                if isinstance(local, str) and not local.startswith(("http://", "https://")):
                    pending.append(local.lstrip("/"))
    return tuple(sorted(selected))


def infer_repository_profile(root: Path, repo_id: str, remote: str) -> RepositoryProfile:
    """Build a read-only, evidence-backed profile without modifying the repository."""
    required = tuple(
        path for path in ("AGENTS.md", "CLAUDE.md", "README.md") if (root / path).is_file()
    )
    optional = (
        "README*",
        "docs/**/*.md",
        "specs/**/*.md",
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "pom.xml",
        "package.json",
        "pyproject.toml",
        "go.mod",
        "Cargo.toml",
        "Makefile",
        "*.tf",
    )
    return RepositoryProfile(
        id=repo_id,
        remote=remote,
        default_branch=_inferred_default_branch(root),
        context=ContextConfig(
            required=required,
            optional=(*optional, *_local_ci_context(root)),
            exclude=(
                ".git/**",
                ".venv/**",
                "node_modules/**",
                "build/**",
                "target/**",
                "dist/**",
                "coverage/**",
            ),
        ),
        commands={},
        signal_sources=(),
    )


def build_plan(
    workspace_path: Path,
    current_path: Path,
    repo_overrides: dict[str, Path] | None = None,
) -> WorkspacePlan:
    workspace_path = workspace_path.resolve()
    workspace = load_workspace(workspace_path)
    return resolve_workspace(
        workspace,
        workspace_path.parent,
        current_path,
        repo_overrides,
    )


def resolve_workspace(
    workspace: Workspace,
    workspace_directory: Path,
    current_path: Path,
    repo_overrides: dict[str, Path] | None = None,
    *,
    phase: str = "execution",
    proposed_tasks: tuple[WorkspaceTask, ...] = (),
    unavailable_repositories: tuple[dict[str, object], ...] = (),
    catalog_repositories: tuple[dict[str, object], ...] = (),
    required_engagements: tuple[str, ...] = (),
    checkout_overrides: tuple[tuple[str, str], ...] = (),
) -> WorkspacePlan:
    if phase not in {"discovery", "execution"}:
        raise ConfigurationError(f"unsupported workspace plan phase: {phase}")
    selected = selected_repo_ids(workspace)
    validate_references(workspace, selected)
    ordered_tasks = task_order(workspace.tasks)
    current_root = repository_root(current_path)
    overrides = repo_overrides or {}

    external_context: dict[str, list[tuple[str, str]]] = {}
    sponsor_repository = workspace.repositories[workspace.sponsor]
    if sponsor_repository.feature_manifest:
        remote_owners = {
            normalize_remote(repository.remote): repo_id
            for repo_id, repository in workspace.repositories.items()
            if repo_id in selected
        }
        sponsor_root = _resolve_root(sponsor_repository, current_root, overrides)
        # Every catalog identity can disambiguate a two-segment GitHub-style
        # blob URL without loading an unengaged peer. Selected profiles refine
        # that identity with an explicit provider for self-hosted forges.
        forge_by_remote: dict[str, str | None] = {
            normalize_remote(repository.remote): None
            for repository in workspace.repositories.values()
        }
        forge_by_remote.update(
            {
                normalize_remote(str(repository["remote"])): None
                for repository in catalog_repositories
                if isinstance(repository.get("remote"), str)
            }
        )
        forge_by_remote.update(
            {
                normalize_remote(repository.remote): load_profile(
                    resolve_profile_path(
                        _resolve_root(repository, current_root, overrides), repository.profile
                    )
                ).forge
                for repo_id, repository in workspace.repositories.items()
                if repo_id in selected
            }
        )
        for reference in external_prior_features(
            sponsor_root, sponsor_repository.feature_manifest, forge_by_remote=forge_by_remote
        ):
            target = remote_owners.get(normalize_remote(reference.remote))
            if target is None:
                if phase == "discovery":
                    # Stage 0 engages cross-repository prior features only after
                    # sponsor evidence identifies them as relevant.
                    continue
                raise ConfigurationError(
                    "cross-repository prior_features must target a selected catalog "
                    f"repository: {reference.remote}"
                )
            external_context.setdefault(target, []).append((reference.ref, reference.path))

    resolved: list[ResolvedRepository] = []
    for repo_id in selected:
        workspace_repo = workspace.repositories[repo_id]
        root = _resolve_root(workspace_repo, current_root, overrides)
        profile_path = resolve_profile_path(root, workspace_repo.profile)
        if profile_path.is_file():
            profile = load_profile(profile_path)
            profile_source = "repository"
            if profile.id != repo_id:
                raise ConfigurationError(
                    f"repository profile id mismatch at {profile_path}: "
                    f"expected {repo_id}, got {profile.id}"
                )
            if normalize_remote(profile.remote) != normalize_remote(workspace_repo.remote):
                raise ConfigurationError(
                    f"profile/workspace remote mismatch for {repo_id}: "
                    f"{profile.remote} != {workspace_repo.remote}"
                )
        else:
            if repo_id == workspace.sponsor:
                raise ResolutionError(
                    f"sponsor repository {repo_id} has no repository profile at {profile_path}"
                )
            if phase == "execution" and any(task.repo == repo_id for task in workspace.tasks):
                raise ResolutionError(
                    f"executable task owner {repo_id} requires a repository profile "
                    f"at {profile_path}"
                )
            profile = infer_repository_profile(root, repo_id, workspace_repo.remote)
            profile_source = "inferred"
        for ref, _ in external_context.get(repo_id, []):
            if ref != profile.default_branch:
                raise ConfigurationError(
                    f"cross-repository prior_features for {repo_id} must use its "
                    f"default branch {profile.default_branch}, got {ref}"
                )
        feature_context = (
            feature_context_patterns(root, workspace_repo.feature_manifest)
            if workspace_repo.feature_manifest
            else ()
        )
        resolved.append(
            ResolvedRepository(
                workspace=workspace_repo,
                root=root,
                profile=profile,
                context=collect_context(
                    root,
                    profile,
                    tuple(
                        item
                        for item in (
                            profile_path.relative_to(root).as_posix()
                            if profile_source == "repository"
                            else None,
                            *feature_context,
                            *(path for _, path in external_context.get(repo_id, [])),
                        )
                        if item
                    ),
                ),
                profile_source=profile_source,
            )
        )

    resolved_repositories = tuple(resolved)
    workflow = (
        load_workflow_reference(workspace.workflow, workspace_directory)
        if workspace.workflow
        else None
    )
    if (
        phase == "execution"
        and workflow
        and any(stage.per_task for stage in workflow.stages)
        and not workspace.tasks
    ):
        raise ConfigurationError(
            f"workflow {workflow.id} has per-task stages but the workspace has no tasks"
        )
    return WorkspacePlan(
        workspace=workspace,
        repositories=resolved_repositories,
        task_order=ordered_tasks,
        workflow=workflow,
        phase=phase,
        proposed_tasks=proposed_tasks,
        unavailable_repositories=unavailable_repositories,
        catalog_repositories=catalog_repositories,
        required_engagements=required_engagements,
        checkout_overrides=checkout_overrides,
    )
