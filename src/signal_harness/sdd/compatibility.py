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

import json
from typing import Any

from signal_harness.sdd.planner import WorkspacePlan


def snapshot_plan(plan: WorkspacePlan) -> dict[str, Any]:
    """Return the portable behavior surface used for native/central comparisons."""
    value = plan.as_dict()
    repositories: list[dict[str, Any]] = []
    for repository in sorted(value["repositories"], key=lambda item: item["id"]):
        profile = repository["profile"]
        repositories.append(
            {
                "id": repository["id"],
                "remote": repository["remote"],
                "mode": repository["mode"],
                "author": repository["author"],
                "profileSource": repository["profileSource"],
                "featureManifest": repository["featureManifest"],
                "defaultBranch": profile["defaultBranch"],
                "context": [
                    {"path": item["path"], "sha256": item["sha256"]}
                    for item in repository["context"]["files"]
                ],
                "commands": dict(sorted(profile["commands"].items())),
                "signals": sorted(profile["signals"], key=lambda item: item["id"]),
                "interactsWith": sorted(repository["interactsWith"]),
            }
        )
    workflow = value.get("workflow") or {}
    snapshot = {
        "schemaVersion": 1,
        "distribution": value.get("distribution"),
        "feature": value["feature"],
        "sponsor": value["sponsor"],
        "phase": value["phase"],
        "policyMode": value["policyMode"],
        "catalogRepositories": sorted(value["catalogRepositories"], key=lambda item: item["id"]),
        "requiredEngagements": sorted(value["requiredEngagements"]),
        "unavailableRepositories": [
            {key: item[key] for key in ("id", "remote", "requiredByDiscovery") if key in item}
            for item in sorted(value["unavailableRepositories"], key=lambda item: item["id"])
        ],
        "repositories": repositories,
        "interactions": value["interactions"],
        "tasks": value["tasks"],
        "workflow": workflow.get("stages", []),
    }
    return json.loads(json.dumps(snapshot, sort_keys=True))


def snapshot_run(state: dict[str, Any]) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    for task_id, task in sorted(state.get("tasks", {}).items()):
        pull_request = task.get("pullRequest")
        tasks[task_id] = {
            "repo": task.get("repo"),
            "status": task.get("status"),
            "branch": task.get("branch"),
            "pullRequest": (
                {
                    key: pull_request.get(key)
                    for key in (
                        "url",
                        "state",
                        "reviewDecision",
                        "head",
                        "base",
                        "checks",
                    )
                    if key in pull_request
                }
                if isinstance(pull_request, dict)
                else None
            ),
            "signals": [
                {
                    key: signal.get(key)
                    for key in ("kind", "source", "repository", "status", "summary", "evidence")
                    if key in signal
                }
                for signal in task.get("signals", [])
            ],
        }
    workflow = state.get("workflow") or {}
    snapshot = {
        "schemaVersion": 1,
        "feature": state.get("feature"),
        "status": state.get("status"),
        "currentStage": workflow.get("currentStage"),
        "gates": {
            stage_id: {
                key: stage.get(key)
                for key in ("name", "status", "gate", "evidence")
                if key in stage
            }
            for stage_id, stage in sorted((workflow.get("stages") or {}).items())
        },
        "tasks": tasks,
        "artifacts": [
            {key: artifact.get(key) for key in ("kind", "reference", "task")}
            for artifact in state.get("artifacts", [])
        ],
        "humanRequests": {
            request_id: {
                key: request.get(key)
                for key in ("kind", "prompt", "status", "resolution")
                if key in request
            }
            for request_id, request in sorted(state.get("humanRequests", {}).items())
        },
        "externalIds": state.get("externalIds", {}),
        "findings": {
            finding_id: {
                key: finding.get(key)
                for key in (
                    "task",
                    "source",
                    "severity",
                    "summary",
                    "evidence",
                    "status",
                    "resolution",
                )
                if key in finding
            }
            for finding_id, finding in sorted(state.get("findings", {}).items())
        },
    }
    return json.loads(json.dumps(snapshot, sort_keys=True))


def compare_snapshots(expected: Any, actual: Any) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    _compare(expected, actual, "$", differences)
    return differences


def _compare(expected: Any, actual: Any, path: str, differences: list[dict[str, Any]]) -> None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}"
            if key not in expected:
                differences.append({"path": child, "expected": None, "actual": actual[key]})
            elif key not in actual:
                differences.append({"path": child, "expected": expected[key], "actual": None})
            else:
                _compare(expected[key], actual[key], child, differences)
        return
    if isinstance(expected, list) and isinstance(actual, list):
        for index in range(max(len(expected), len(actual))):
            child = f"{path}[{index}]"
            if index >= len(expected):
                differences.append({"path": child, "expected": None, "actual": actual[index]})
            elif index >= len(actual):
                differences.append({"path": child, "expected": expected[index], "actual": None})
            else:
                _compare(expected[index], actual[index], child, differences)
        return
    if expected != actual:
        differences.append({"path": path, "expected": expected, "actual": actual})
