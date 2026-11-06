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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import require_list, require_mapping, require_string, string_list
from .errors import ConfigurationError

API_VERSION = "signal-harness/v1alpha1"
SUPPORTED_API_VERSIONS = {API_VERSION}


@dataclass(frozen=True)
class ContextConfig:
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextConfig:
        return cls(
            required=string_list(data.get("required"), "context.required"),
            optional=string_list(data.get("optional"), "context.optional"),
            exclude=string_list(data.get("exclude"), "context.exclude"),
        )


@dataclass(frozen=True)
class SignalSource:
    id: str
    kind: str
    command_ref: str | None = None
    provider: str | None = None
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], field_name: str) -> SignalSource:
        command_ref = data.get("commandRef")
        provider = data.get("provider")
        if command_ref is not None:
            command_ref = require_string(command_ref, f"{field_name}.commandRef")
        if provider is not None:
            provider = require_string(provider, f"{field_name}.provider")
        if command_ref is None and provider is None:
            raise ConfigurationError(f"{field_name} must declare commandRef, provider, or both")
        return cls(
            id=require_string(data.get("id"), f"{field_name}.id"),
            kind=require_string(data.get("kind"), f"{field_name}.kind"),
            command_ref=command_ref,
            provider=provider,
            config=require_mapping(data.get("config", {}), f"{field_name}.config"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "commandRef": self.command_ref,
            "provider": self.provider,
            "config": self.config,
        }


@dataclass(frozen=True)
class RepositoryProfile:
    id: str
    remote: str
    default_branch: str
    context: ContextConfig
    commands: dict[str, str] = field(default_factory=dict)
    signal_sources: tuple[SignalSource, ...] = ()
    forge: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryProfile:
        if data.get("apiVersion") not in SUPPORTED_API_VERSIONS:
            raise ConfigurationError(
                f"repository profile apiVersion must be one of {sorted(SUPPORTED_API_VERSIONS)}"
            )
        if data.get("kind") != "RepositoryProfile":
            raise ConfigurationError("repository profile kind must be RepositoryProfile")
        if "contracts" in data:
            raise ConfigurationError(
                "repository profiles must not declare contracts; declare only repository "
                "interactions in the external catalog and discover interfaces at runtime"
            )

        repo = require_mapping(data.get("repo"), "repo")
        forge = repo.get("forge")
        if forge is not None and (not isinstance(forge, str) or forge not in {"github", "gitlab"}):
            raise ConfigurationError("repo.forge must be github or gitlab")
        context = ContextConfig.from_dict(require_mapping(data.get("context", {}), "context"))
        commands_data = require_mapping(data.get("commands", {}), "commands")
        commands = {
            require_string(name, "commands key"): require_string(command, f"commands.{name}")
            for name, command in commands_data.items()
        }

        signals = require_mapping(data.get("signals", {}), "signals")
        sources_data = require_list(signals.get("sources", []), "signals.sources")
        signal_sources = tuple(
            SignalSource.from_dict(require_mapping(item, "signals.sources[]"), "signals.sources[]")
            for item in sources_data
        )
        source_ids = [source.id for source in signal_sources]
        if len(source_ids) != len(set(source_ids)):
            raise ConfigurationError("signals.sources ids must be unique")
        for source in signal_sources:
            if source.command_ref and source.command_ref not in commands:
                raise ConfigurationError(
                    f"signal source {source.id} references unknown command {source.command_ref}"
                )

        return cls(
            id=require_string(repo.get("id"), "repo.id"),
            remote=require_string(repo.get("remote"), "repo.remote"),
            default_branch=require_string(repo.get("defaultBranch", "main"), "repo.defaultBranch"),
            context=context,
            commands=commands,
            signal_sources=signal_sources,
            forge=forge,
        )


@dataclass(frozen=True)
class WorkspaceRepository:
    id: str
    remote: str
    mode: str
    feature_manifest: str | None = None
    profile: str = ".signal-harness/repo.yaml"
    interacts_with: tuple[str, ...] = ()
    author: bool = False

    @classmethod
    def from_dict(cls, repo_id: str, data: dict[str, Any]) -> WorkspaceRepository:
        mode = require_string(data.get("mode", "read-write"), f"repositories.{repo_id}.mode")
        if mode not in {"read-write", "read-only"}:
            raise ConfigurationError(f"repositories.{repo_id}.mode must be read-write or read-only")
        feature_manifest = data.get("featureManifest")
        if feature_manifest is not None and not isinstance(feature_manifest, str):
            raise ConfigurationError(f"repositories.{repo_id}.featureManifest must be a string")
        profile = require_string(
            data.get("profile", ".signal-harness/repo.yaml"),
            f"repositories.{repo_id}.profile",
        )
        for field_name, repo_path in (
            ("profile", profile),
            ("featureManifest", feature_manifest),
        ):
            if repo_path is None:
                continue
            candidate = Path(repo_path)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ConfigurationError(
                    f"repositories.{repo_id}.{field_name} must be repo-relative"
                )

        interacts_with = string_list(
            data.get("interactsWith"), f"repositories.{repo_id}.interactsWith"
        )
        if len(interacts_with) != len(set(interacts_with)):
            raise ConfigurationError(
                f"repositories.{repo_id}.interactsWith must not contain duplicates"
            )
        author = data.get("author", False)
        if not isinstance(author, bool):
            raise ConfigurationError(f"repositories.{repo_id}.author must be a boolean")
        return cls(
            id=repo_id,
            remote=require_string(data.get("remote"), f"repositories.{repo_id}.remote"),
            mode=mode,
            feature_manifest=feature_manifest,
            profile=profile,
            interacts_with=interacts_with,
            author=author,
        )


@dataclass(frozen=True)
class WorkspaceTask:
    id: str
    repo: str
    depends_on: tuple[str, ...] = ()
    description: str = ""
    source: str | None = None
    declared_status: str | None = None
    test_cases: tuple[dict[str, Any], ...] = ()
    required_checks: tuple[str, ...] = ()
    ownership: dict[str, Any] = field(default_factory=dict)
    base: dict[str, str] = field(default_factory=dict)
    required_context: tuple[dict[str, str], ...] = ()
    surfaces: tuple[str, ...] = ()
    callers: tuple[str, ...] = ()
    produces: tuple[dict[str, str], ...] = ()
    consumes: tuple[dict[str, str], ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceTask:
        task_id = require_string(data.get("id"), "tasks[].id")
        source = data.get("source")
        if source is not None:
            source = require_string(source, f"tasks[{task_id}].source")
        declared_status = data.get("declaredStatus")
        if declared_status is not None:
            declared_status = require_string(declared_status, f"tasks[{task_id}].declaredStatus")
        test_cases_data = require_list(data.get("testCases", []), f"tasks[{task_id}].testCases")
        test_cases: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, value in enumerate(test_cases_data):
            entry = require_mapping(value, f"tasks[{task_id}].testCases[{index}]")
            case_id = require_string(entry.get("id"), f"tasks[{task_id}].testCases[{index}].id")
            if case_id in seen:
                raise ConfigurationError(f"task {task_id} has duplicate test case {case_id}")
            seen.add(case_id)
            kind = require_string(entry.get("kind"), f"tasks[{task_id}].testCases[{index}].kind")
            if kind not in {"delta", "guard"}:
                raise ConfigurationError(
                    f"tasks[{task_id}].testCases[{index}].kind must be delta or guard"
                )
            test_case: dict[str, Any] = {
                "id": case_id,
                "kind": kind,
                "requirement": require_string(
                    entry.get("requirement"),
                    f"tasks[{task_id}].testCases[{index}].requirement",
                ),
            }
            if "traces" in entry:
                test_case["traces"] = list(
                    string_list(
                        entry.get("traces"),
                        f"tasks[{task_id}].testCases[{index}].traces",
                    )
                )
            test_cases.append(test_case)
        ownership = require_mapping(data.get("ownership", {}), f"tasks[{task_id}].ownership")
        base = require_mapping(data.get("base", {}), f"tasks[{task_id}].base")
        normalized_base = {
            require_string(key, f"tasks[{task_id}].base key"): require_string(
                value, f"tasks[{task_id}].base.{key}"
            )
            for key, value in base.items()
        }
        context_data = require_list(data.get("context", []), f"tasks[{task_id}].context")
        required_context = tuple(
            {
                "repo": require_string(
                    require_mapping(item, f"tasks[{task_id}].context[]").get("repo"),
                    f"tasks[{task_id}].context[].repo",
                ),
                "path": require_string(
                    require_mapping(item, f"tasks[{task_id}].context[]").get("path"),
                    f"tasks[{task_id}].context[].path",
                ),
            }
            for item in context_data
        )
        surfaces = string_list(data.get("surfaces"), f"tasks[{task_id}].surfaces")
        if len(surfaces) != len(set(surfaces)):
            raise ConfigurationError(f"task {task_id} surfaces must not contain duplicates")
        callers = string_list(data.get("callers"), f"tasks[{task_id}].callers")
        if len(callers) != len(set(callers)):
            raise ConfigurationError(f"task {task_id} callers must not contain duplicates")

        produces_data = require_list(data.get("produces", []), f"tasks[{task_id}].produces")
        produces: list[dict[str, str]] = []
        produced_ids: set[str] = set()
        for index, value in enumerate(produces_data):
            entry = require_mapping(value, f"tasks[{task_id}].produces[{index}]")
            artifact_id = require_string(entry.get("id"), f"tasks[{task_id}].produces[{index}].id")
            if artifact_id in produced_ids:
                raise ConfigurationError(
                    f"task {task_id} has duplicate produced artifact {artifact_id}"
                )
            produced_ids.add(artifact_id)
            produces.append(
                {
                    "id": artifact_id,
                    "kind": require_string(
                        entry.get("kind"), f"tasks[{task_id}].produces[{index}].kind"
                    ),
                }
            )

        consumes_data = require_list(data.get("consumes", []), f"tasks[{task_id}].consumes")
        consumes: list[dict[str, str]] = []
        consumed_keys: set[tuple[str, str]] = set()
        for index, value in enumerate(consumes_data):
            entry = require_mapping(value, f"tasks[{task_id}].consumes[{index}]")
            item = {
                "fromTask": require_string(
                    entry.get("fromTask"), f"tasks[{task_id}].consumes[{index}].fromTask"
                ),
                "artifact": require_string(
                    entry.get("artifact"), f"tasks[{task_id}].consumes[{index}].artifact"
                ),
            }
            key = (item["fromTask"], item["artifact"])
            if key in consumed_keys:
                raise ConfigurationError(
                    f"task {task_id} has duplicate consumed artifact {key[1]} from {key[0]}"
                )
            consumed_keys.add(key)
            consumes.append(item)
        return cls(
            id=task_id,
            repo=require_string(data.get("repo"), f"tasks[{task_id}].repo"),
            depends_on=string_list(data.get("dependsOn"), f"tasks[{task_id}].dependsOn"),
            description=str(data.get("description", "")).strip(),
            source=source,
            declared_status=declared_status,
            test_cases=tuple(test_cases),
            required_checks=string_list(
                data.get("requiredChecks"), f"tasks[{task_id}].requiredChecks"
            ),
            ownership=ownership,
            base=normalized_base,
            required_context=required_context,
            surfaces=surfaces,
            callers=callers,
            produces=tuple(produces),
            consumes=tuple(consumes),
        )


@dataclass(frozen=True)
class Workspace:
    feature: str
    sponsor: str
    workflow: str | None
    repositories: dict[str, WorkspaceRepository]
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    tasks: tuple[WorkspaceTask, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workspace:
        if data.get("apiVersion") not in SUPPORTED_API_VERSIONS:
            raise ConfigurationError(
                f"workspace apiVersion must be one of {sorted(SUPPORTED_API_VERSIONS)}"
            )
        if data.get("kind") != "Workspace":
            raise ConfigurationError("workspace kind must be Workspace")
        if "contracts" in data:
            raise ConfigurationError(
                "workspaces must not inventory contracts; interface impact is discovered "
                "from selected repositories during the SDD run"
            )

        repos_data = require_mapping(data.get("repositories"), "repositories")
        repositories = {
            require_string(repo_id, "repositories key"): WorkspaceRepository.from_dict(
                repo_id, require_mapping(repo_data, f"repositories.{repo_id}")
            )
            for repo_id, repo_data in repos_data.items()
        }
        selection = require_mapping(data.get("selection", {}), "selection")
        include = string_list(selection.get("include"), "selection.include")
        exclude = string_list(selection.get("exclude"), "selection.exclude")
        if not include:
            include = tuple(repositories)

        tasks_data = require_list(data.get("tasks", []), "tasks")
        workflow = data.get("workflow")
        if workflow is not None:
            workflow = require_string(workflow, "workflow")
            workflow_path = Path(workflow)
            if workflow_path.is_absolute() or ".." in workflow_path.parts:
                raise ConfigurationError("workflow must be workspace-relative")
        return cls(
            feature=require_string(data.get("feature"), "feature"),
            sponsor=require_string(data.get("sponsor"), "sponsor"),
            workflow=workflow,
            repositories=repositories,
            include=include,
            exclude=exclude,
            tasks=tuple(
                WorkspaceTask.from_dict(require_mapping(item, "tasks[]")) for item in tasks_data
            ),
        )
