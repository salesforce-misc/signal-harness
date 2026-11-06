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

"""Frozen 0.8 WorkspacePlan serializer from the PR base, without 0.9 grants.

Use the original serialized shape (including absent catalog candidates), rather
than deleting only author from a modern plan. Inputs below are fixture adapters.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from signal_harness.context import ContextFile, ContextLock
from signal_harness.models import WorkspaceTask


def serialize_plan_08(self) -> dict:
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
        "repositories": [
            {
                "id": resolved.workspace.id,
                "mode": resolved.workspace.mode,
                "root": str(resolved.root),
                "remote": resolved.workspace.remote,
                "profilePath": resolved.workspace.profile,
                "featureManifest": resolved.workspace.feature_manifest,
                "interactsWith": list(resolved.workspace.interacts_with),
                "profile": {
                    "defaultBranch": resolved.profile.default_branch,
                    "commands": resolved.profile.commands,
                    "signals": [source.as_dict() for source in resolved.profile.signal_sources],
                },
                "context": asdict(resolved.context),
            }
            for resolved in self.repositories
        ],
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
        "proposedTasks": [serialize_task(task, mode="read-only") for task in self.proposed_tasks],
    }


def interaction_edges(workspace) -> tuple[tuple[str, str], ...]:
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


def legacy_discovery_plan(plan):
    records = []
    repositories = {}
    for raw in plan["repositories"]:
        workspace = SimpleNamespace(
            id=raw["id"],
            mode=raw["mode"],
            remote=raw["remote"],
            profile=raw["profilePath"],
            feature_manifest=raw["featureManifest"],
            interacts_with=tuple(),
        )
        repositories[raw["id"]] = workspace
        context = {
            **raw["context"],
            "files": tuple(ContextFile(**f) for f in raw["context"]["files"]),
        }
        signals = [
            SimpleNamespace(as_dict=lambda item=item: item) for item in raw["profile"]["signals"]
        ]
        records.append(
            SimpleNamespace(
                workspace=workspace,
                root=Path(raw["root"]),
                context=ContextLock(**context),
                profile=SimpleNamespace(
                    default_branch=raw["profile"]["defaultBranch"],
                    commands=raw["profile"]["commands"],
                    signal_sources=signals,
                ),
            )
        )
    workspace = SimpleNamespace(
        feature=plan["feature"],
        sponsor=plan["sponsor"],
        tasks=(),
        repositories=repositories,
        include=tuple(repositories),
        exclude=(),
    )
    return serialize_plan_08(
        SimpleNamespace(
            workspace=workspace,
            repositories=records,
            task_order=(),
            workflow=SimpleNamespace(as_dict=lambda: plan["workflow"]),
            distribution=plan["distribution"],
            phase="discovery",
            policy_mode=plan["policyMode"],
            proposed_tasks=(),
        )
    )
