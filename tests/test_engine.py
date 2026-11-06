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
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import yaml

import signal_harness.session as session_api
from signal_harness.artifact_handoff import record_artifact_handoff
from signal_harness.capabilities.assurance import AssuranceReport, QualityGateResult
from signal_harness.catalog import CatalogRepository, RepositoryCatalog, load_policy_catalog
from signal_harness.compatibility import snapshot_plan
from signal_harness.context import collect_context
from signal_harness.control import run_summary
from signal_harness.errors import ConfigurationError, ResolutionError
from signal_harness.feature_context import feature_context_patterns
from signal_harness.git import head_sha, managed_repository, repository_root
from signal_harness.models import RepositoryProfile, WorkspaceTask
from signal_harness.planner import build_plan, infer_repository_profile, resolve_profile_path
from signal_harness.pull_requests import pull_request_plan
from signal_harness.sdd import (
    _load_profiles,
    build_sdd_plan,
    synthesize_interaction_workspace,
)
from signal_harness.session import next_action
from signal_harness.signals import EngineeringSignal, SignalStatus
from signal_harness.state import RunStore, _canonical_hash
from signal_harness.task_cards import TaskCard, discover_task_cards
from signal_harness.tdd import record_tdd_phase, recorded_tdd_evidence
from signal_harness.workflow import WorkflowDefinition, load_workflow_reference
from signal_harness.worktrees import freeze_task_head, prepare_task

EXAMPLE_WORKFLOW = str((Path(__file__).resolve().parent / "fixtures/policy/sdd.yaml").resolve())


def run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def profile(repo_id: str, remote: str) -> dict:
    return {
        "apiVersion": "signal-harness/v1alpha1",
        "kind": "RepositoryProfile",
        "repo": {"id": repo_id, "remote": remote, "defaultBranch": "main"},
        "context": {
            "required": ["AGENTS.md"],
            "optional": ["docs/**/*.md"],
            "exclude": ["docs/private/**"],
        },
        "commands": {"unit": "./gradlew test", "compile": "./gradlew compileJava"},
        "signals": {"sources": [{"id": "unit-tests", "kind": "test", "commandRef": "unit"}]},
    }


def tdd_evidence(signal: str) -> dict:
    return {
        "status": "pass",
        "signals": [signal],
        "testCases": [
            {
                "id": "case-1",
                "kind": "delta",
                "requirement": "the task behavior is covered",
                "lifecycle": [
                    {
                        "phase": "red",
                        "status": "fail",
                        "failureKind": "assertion",
                        "signal": f"{signal}/red",
                    },
                    {"phase": "green", "status": "pass", "signal": f"{signal}/green"},
                    {"phase": "refactor", "status": "pass", "signal": f"{signal}/refactor"},
                ],
            }
        ],
    }


def merged_planning_evidence() -> dict:
    return {
        "state": "MERGED",
        "reviewDecision": "APPROVED",
        "url": "https://example.test/pr/1",
        "specification": {
            "artifact": "artifact://spec/1",
            "scope": "fixture scope",
            "behavior": "fixture behavior",
            "errorHandling": "fail closed",
            "repositoryBoundaries": ["service-a"],
            "interfaceImpact": {
                "evaluatedRepositories": ["service-a", "service-b"],
                "affectedInterfaces": [],
                "summary": "No cross-repository interface impact.",
            },
            "interfaceDecisions": [],
            "affectedSurfaces": [
                {
                    "id": "producer-behavior",
                    "kind": "behavior",
                    "ownerRepo": "service-a",
                    "evidence": [{"repo": "service-a", "path": "AGENTS.md"}],
                },
                {
                    "id": "consumer-behavior",
                    "kind": "behavior",
                    "ownerRepo": "service-b",
                    "evidence": [{"repo": "service-b", "path": "AGENTS.md"}],
                },
            ],
            "rollout": "test rollout",
            "decisions": ["fixture decision"],
            "openQuestions": [],
            "sourceAudit": {
                "consulted": ["service-a:AGENTS.md"],
                "missing": [],
                "delegationFindings": [],
                "unresolvedDelegations": [],
            },
        },
        "taskPlan": {
            "artifact": "artifact://task-plan/1",
            "tasks": [
                {
                    "id": "producer",
                    "repo": "service-a",
                    "requirement": "publish behavior",
                    "dependsOn": [],
                    "surfaces": ["producer-behavior"],
                    "produces": [{"id": "producer-output", "kind": "build-artifact"}],
                    "testCases": [
                        {"id": "case-1", "kind": "delta", "requirement": "publish behavior"}
                    ],
                },
                {
                    "id": "consumer",
                    "repo": "service-b",
                    "requirement": "consume behavior",
                    "dependsOn": ["producer"],
                    "surfaces": ["consumer-behavior"],
                    "consumes": [{"fromTask": "producer", "artifact": "producer-output"}],
                    "testCases": [
                        {"id": "case-1", "kind": "delta", "requirement": "consume behavior"}
                    ],
                },
            ],
            "surfaceCoverage": [
                {"surface": "producer-behavior", "disposition": "task", "task": "producer"},
                {"surface": "consumer-behavior", "disposition": "task", "task": "consumer"},
            ],
        },
    }


class RepositoryFixture:
    def __init__(self, parent: Path, repo_id: str):
        self.id = repo_id
        self.remote = f"example-org/{repo_id}"
        self.root = parent / repo_id
        self.root.mkdir()
        run("git", "init", "-b", "main", cwd=self.root)
        run("git", "config", "user.email", "test@example.com", cwd=self.root)
        run("git", "config", "user.name", "Signal Harness Test", cwd=self.root)
        run("git", "remote", "add", "origin", f"git@github.com:{self.remote}.git", cwd=self.root)
        (self.root / "AGENTS.md").write_text(f"# {repo_id}\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "architecture.md").write_text("architecture\n", encoding="utf-8")
        (self.root / "docs" / "private").mkdir()
        (self.root / "docs" / "private" / "secret.md").write_text("excluded\n", encoding="utf-8")
        write_yaml(
            self.root / ".signal-harness" / "repo.yaml",
            profile(repo_id, self.remote),
        )
        run("git", "add", ".", cwd=self.root)
        run("git", "commit", "-m", "fixture", cwd=self.root)


class ContextTests(unittest.TestCase):
    def test_feature_context_resolves_local_prior_feature_from_manifest_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature = root / "specs" / "area" / "sdd" / "login"
            prior = root / "specs" / "area" / "sdd" / "foundation"
            feature.mkdir(parents=True)
            prior.mkdir(parents=True)
            (root / "AGENTS.md").write_text("rules\n", encoding="utf-8")
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            (prior / "manifest.yaml").write_text("feature: foundation\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "login",
                    "sources": ["specs/area/sdd/login/source.md"],
                    "constraints": ["AGENTS.md"],
                    "prior_features": [
                        "../foundation/manifest.yaml",
                        "https://github.example/org/repo/blob/main/manifest.yaml",
                    ],
                },
            )

            patterns = feature_context_patterns(root, "specs/area/sdd/login/manifest.yaml")

            self.assertIn("specs/area/sdd/foundation/manifest.yaml", patterns)

    def test_stage_zero_lazily_locks_cross_repository_prior_feature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            sponsor = RepositoryFixture(parent, "service-a")
            peer = RepositoryFixture(parent, "service-b")
            prior = peer.root / "specs" / "shared" / "sdd" / "foundation"
            prior.mkdir(parents=True)
            (prior / "manifest.yaml").write_text("feature: foundation\n", encoding="utf-8")
            run("git", "add", ".", cwd=peer.root)
            run("git", "commit", "-m", "add prior feature", cwd=peer.root)
            feature = sponsor.root / "specs" / "area" / "sdd" / "change"
            feature.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "change",
                    "sources": ["specs/area/sdd/change/source.md"],
                    "constraints": ["AGENTS.md"],
                    "prior_features": [
                        "https://github.com/example-org/service-b/blob/main/"
                        "specs/shared/sdd/foundation/manifest.yaml"
                    ],
                },
            )
            run("git", "add", ".", cwd=sponsor.root)
            run("git", "commit", "-m", "add feature", cwd=sponsor.root)
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "service-a": CatalogRepository(
                        id="service-a",
                        remote="example-org/service-a",
                    ),
                    "service-b": CatalogRepository(id="service-b", remote="example-org/service-b"),
                },
            )

            with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
                plan = build_sdd_plan(
                    feature="change",
                    current_path=sponsor.root,
                    repo_overrides={"service-b": peer.root},
                )
            self.assertEqual(
                [item.workspace.id for item in plan.repositories],
                ["service-a"],
            )
            store = RunStore(parent / "state")
            store.create(plan, feature / "manifest.yaml", "prior-feature-run")
            session_api.engage_repository(
                store,
                "prior-feature-run",
                "service-b",
                reason="The sponsor manifest names a service-b prior feature.",
                evidence=("service-a:specs/area/sdd/change/manifest.yaml",),
                override=peer.root,
            )
            session_api.lock_discovery_context(
                store,
                "prior-feature-run",
                "service-b",
                paths=("specs/shared/sdd/foundation/manifest.yaml",),
                reason="Lock the exact prior-feature manifest named by the sponsor.",
            )
            peer_context = next(
                item["context"]
                for item in store.load("prior-feature-run")["plan"]["repositories"]
                if item["id"] == "service-b"
            )
            self.assertIn(
                "specs/shared/sdd/foundation/manifest.yaml",
                [item["path"] for item in peer_context["files"]],
            )

    def test_collects_declared_context_and_honors_excludes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RepositoryFixture(Path(directory), "service-a")
            loaded = RepositoryProfile.from_dict(profile(fixture.id, fixture.remote))

            lock = collect_context(fixture.root, loaded)

            self.assertEqual(lock.repo, "service-a")
            self.assertFalse(lock.dirty)
            self.assertEqual(
                [item.path for item in lock.files],
                ["AGENTS.md", "docs/architecture.md"],
            )

    def test_exact_context_rejects_case_aliases_before_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RepositoryFixture(Path(directory), "service-a")
            loaded = RepositoryProfile.from_dict(profile(fixture.id, fixture.remote))
            if not (fixture.root / "DOCS/private/secret.md").is_file():
                self.skipTest("requires a case-insensitive filesystem")
            with self.assertRaisesRegex(ResolutionError, "on-disk spelling"):
                collect_context(fixture.root, loaded, required_files=("DOCS/private/secret.md",))
            with self.assertRaisesRegex(ResolutionError, "excluded"):
                collect_context(fixture.root, loaded, required_files=("docs/private/secret.md",))
            extra = fixture.root / "extra.py"
            extra.write_text("# untracked discovery evidence\n", encoding="utf-8")
            lock = collect_context(fixture.root, loaded, required_files=("extra.py",))
            self.assertIn("extra.py", [item.path for item in lock.files])

    def test_missing_required_context_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RepositoryFixture(Path(directory), "service-a")
            value = profile(fixture.id, fixture.remote)
            value["context"]["required"] = ["missing.md"]
            loaded = RepositoryProfile.from_dict(value)

            with self.assertRaisesRegex(ResolutionError, "matched no files"):
                collect_context(fixture.root, loaded)

    def test_required_context_cannot_be_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RepositoryFixture(Path(directory), "service-a")
            value = profile(fixture.id, fixture.remote)
            value["context"]["exclude"] = ["AGENTS.md"]
            loaded = RepositoryProfile.from_dict(value)

            with self.assertRaisesRegex(ResolutionError, "fully excluded"):
                collect_context(fixture.root, loaded)

    def test_profile_exposes_declared_signal_sources(self) -> None:
        loaded = RepositoryProfile.from_dict(profile("service-a", "example-org/service-a"))

        self.assertEqual(loaded.signal_sources[0].id, "unit-tests")
        self.assertEqual(loaded.signal_sources[0].command_ref, "unit")

    def test_signal_source_rejects_unknown_command_reference(self) -> None:
        value = profile("service-a", "example-org/service-a")
        value["signals"]["sources"][0]["commandRef"] = "missing"

        with self.assertRaisesRegex(ConfigurationError, "unknown command missing"):
            RepositoryProfile.from_dict(value)

    def test_profile_rejects_static_contract_inventory(self) -> None:
        value = profile("service-a", "example-org/service-a")
        value["contracts"] = {"produces": ["event-v2"]}

        with self.assertRaisesRegex(ConfigurationError, "must not declare contracts"):
            RepositoryProfile.from_dict(value)

    def test_profile_resolution_never_falls_back_to_product_specific_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RepositoryFixture(Path(directory), "service-a")
            canonical = fixture.root / ".signal-harness" / "repo.yaml"
            legacy = fixture.root / ".legacy-sdd" / "repo.yaml"
            legacy.parent.mkdir()
            value = yaml.safe_load(canonical.read_text(encoding="utf-8"))
            value["apiVersion"] = "legacy.sdd/v1"
            write_yaml(legacy, value)
            canonical.unlink()

            resolved = resolve_profile_path(fixture.root, ".signal-harness/repo.yaml")

            self.assertEqual(resolved, canonical)
            self.assertFalse(resolved.exists())


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.a = RepositoryFixture(self.base, "service-a")
        self.b = RepositoryFixture(self.base, "service-b")
        self.c = RepositoryFixture(self.base, "service-c")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def workspace(self) -> dict:
        return {
            "apiVersion": "signal-harness/v1alpha1",
            "kind": "Workspace",
            "feature": "cross-service-change",
            "sponsor": "service-a",
            "selection": {
                "include": ["service-a", "service-b", "service-c"],
                "exclude": ["service-c"],
            },
            "repositories": {
                "service-a": {
                    "remote": self.a.remote,
                    "mode": "read-write",
                    "author": True,
                    "interactsWith": ["service-b"],
                },
                "service-b": {
                    "remote": self.b.remote,
                    "mode": "read-write",
                    "author": True,
                },
                "service-c": {
                    "remote": self.c.remote,
                    "mode": "read-only",
                },
            },
            "tasks": [
                {
                    "id": "consumer",
                    "repo": "service-b",
                    "dependsOn": ["producer"],
                    "consumes": [{"fromTask": "producer", "artifact": "contract"}],
                },
                {
                    "id": "producer",
                    "repo": "service-a",
                    "produces": [{"id": "contract", "kind": "build-artifact"}],
                },
            ],
        }

    def write_workspace(self, value: dict | None = None) -> Path:
        path = self.a.root / "specs" / "feature" / "workspace.yaml"
        workspace = value or self.workspace()
        if workspace.get("workflow") == "sdd.yaml":
            path.parent.mkdir(parents=True, exist_ok=True)
            (path.parent / "sdd.yaml").write_text(
                Path(EXAMPLE_WORKFLOW).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        write_yaml(path, workspace)
        return path

    def test_builds_multi_repo_plan_and_skips_excluded_repo(self) -> None:
        plan = build_plan(
            self.write_workspace(),
            self.a.root,
            {"service-b": self.b.root},
        )

        self.assertEqual(
            [repo.workspace.id for repo in plan.repositories], ["service-a", "service-b"]
        )
        self.assertEqual(plan.task_order, ("producer", "consumer"))
        self.assertEqual(plan.as_dict()["tasks"][1]["repo"], "service-b")
        self.assertEqual(
            plan.as_dict()["interactions"],
            [{"repositories": ["service-a", "service-b"]}],
        )

    def test_task_cannot_target_read_only_repo(self) -> None:
        value = self.workspace()
        value["selection"]["exclude"] = []
        value["tasks"].append({"id": "forbidden", "repo": "service-c"})

        with self.assertRaisesRegex(ConfigurationError, "read-only repo service-c"):
            build_plan(
                self.write_workspace(value),
                self.a.root,
                {"service-b": self.b.root, "service-c": self.c.root},
            )

    def test_task_cannot_target_non_authorable_repo(self) -> None:
        value = self.workspace()
        value["selection"]["exclude"] = []
        value["repositories"]["service-c"]["mode"] = "read-write"
        value["repositories"]["service-c"]["author"] = False
        value["tasks"].append({"id": "forbidden", "repo": "service-c"})

        with self.assertRaisesRegex(ConfigurationError, "non-authorable repo service-c"):
            build_plan(
                self.write_workspace(value),
                self.a.root,
                {"service-b": self.b.root, "service-c": self.c.root},
            )

    def test_missing_author_permission_defaults_to_deny(self) -> None:
        value = self.workspace()
        value["repositories"]["service-b"].pop("author")

        with self.assertRaisesRegex(ConfigurationError, "non-authorable repo service-b"):
            build_plan(
                self.write_workspace(value),
                self.a.root,
                {"service-b": self.b.root},
            )

    def test_interaction_cannot_reference_unknown_repo(self) -> None:
        value = self.workspace()
        value["repositories"]["service-a"]["interactsWith"].append("missing")

        with self.assertRaisesRegex(ConfigurationError, "unknown repositories"):
            build_plan(self.write_workspace(value), self.a.root, {"service-b": self.b.root})

    def test_workspace_rejects_static_contract_inventory(self) -> None:
        value = self.workspace()
        value["contracts"] = [{"id": "event-v2"}]

        with self.assertRaisesRegex(ConfigurationError, "must not inventory contracts"):
            build_plan(self.write_workspace(value), self.a.root, {"service-b": self.b.root})

    def test_cycle_is_rejected(self) -> None:
        value = self.workspace()
        value["tasks"][1]["dependsOn"] = ["consumer"]
        value["tasks"][1]["consumes"] = [{"fromTask": "consumer", "artifact": "consumer-output"}]
        value["tasks"][0]["produces"] = [{"id": "consumer-output", "kind": "build-artifact"}]

        with self.assertRaisesRegex(ConfigurationError, "cycle"):
            build_plan(self.write_workspace(value), self.a.root, {"service-b": self.b.root})

    def test_same_repository_dependency_requires_dependency_head(self) -> None:
        value = self.workspace()
        value["tasks"] = [
            {"id": "first", "repo": "service-a"},
            {"id": "second", "repo": "service-a", "dependsOn": ["first"]},
        ]

        with self.assertRaisesRegex(ConfigurationError, "dependency-head"):
            build_plan(self.write_workspace(value), self.a.root, {"service-b": self.b.root})

        value["tasks"][1]["base"] = {"kind": "dependency-head", "task": "first"}
        plan = build_plan(self.write_workspace(value), self.a.root, {"service-b": self.b.root})
        self.assertEqual(plan.task_order, ("first", "second"))

    def test_feature_manifest_is_part_of_locked_context(self) -> None:
        value = self.workspace()
        manifest = self.a.root / "specs" / "feature" / "manifest.yaml"
        write_yaml(
            manifest,
            {"description": "fixture", "sources": [], "constraints": []},
        )
        value["repositories"]["service-a"]["featureManifest"] = "specs/feature/manifest.yaml"

        plan = build_plan(
            self.write_workspace(value),
            self.a.root,
            {"service-b": self.b.root},
        )

        sponsor = next(repo for repo in plan.repositories if repo.workspace.id == "service-a")
        self.assertIn(
            "specs/feature/manifest.yaml",
            [item.path for item in sponsor.context.files],
        )

    def test_feature_manifest_sources_and_constraints_are_locked(self) -> None:
        value = self.workspace()
        source = self.a.root / "specs" / "feature" / "design.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("design\n", encoding="utf-8")
        manifest = self.a.root / "specs" / "feature" / "manifest.yaml"
        write_yaml(
            manifest,
            {
                "description": "fixture",
                "sources": ["specs/feature/design.md"],
                "constraints": ["AGENTS.md"],
            },
        )
        value["repositories"]["service-a"]["featureManifest"] = "specs/feature/manifest.yaml"

        plan = build_plan(
            self.write_workspace(value),
            self.a.root,
            {"service-b": self.b.root},
        )

        sponsor = next(repo for repo in plan.repositories if repo.workspace.id == "service-a")
        locked = {item.path for item in sponsor.context.files}
        self.assertIn("specs/feature/design.md", locked)
        self.assertIn("AGENTS.md", locked)

    def test_feature_manifest_locks_touchpoints_and_resumed_stage_artifacts(self) -> None:
        value = self.workspace()
        feature = self.a.root / "specs" / "feature"
        feature.mkdir(parents=True, exist_ok=True)
        touchpoint = self.a.root / "src" / "handler.py"
        touchpoint.parent.mkdir()
        touchpoint.write_text("handler\n", encoding="utf-8")
        (feature / "spec.md").write_text("spec\n", encoding="utf-8")
        (feature / "stage-0-decisions.md").write_text("decisions\n", encoding="utf-8")
        write_yaml(
            feature / "manifest.yaml",
            {
                "description": "fixture",
                "sources": ["specs/feature/spec.md"],
                "constraints": ["AGENTS.md"],
                "touchpoints": ["src/handler.py"],
            },
        )
        value["repositories"]["service-a"]["featureManifest"] = "specs/feature/manifest.yaml"

        plan = build_plan(
            self.write_workspace(value),
            self.a.root,
            {"service-b": self.b.root},
        )

        sponsor = next(repo for repo in plan.repositories if repo.workspace.id == "service-a")
        locked = {item.path for item in sponsor.context.files}
        self.assertIn("src/handler.py", locked)
        self.assertIn("specs/feature/stage-0-decisions.md", locked)

    def test_missing_feature_manifest_fails_before_planning(self) -> None:
        value = self.workspace()
        value["repositories"]["service-a"]["featureManifest"] = "missing/manifest.yaml"

        with self.assertRaisesRegex(ResolutionError, "missing/manifest.yaml"):
            build_plan(
                self.write_workspace(value),
                self.a.root,
                {"service-b": self.b.root},
            )


class RunStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.a = RepositoryFixture(self.base, "service-a")
        self.b = RepositoryFixture(self.base, "service-b")
        self.workspace_path = self.base / "workspace.yaml"
        (self.base / "sdd.yaml").write_text(
            Path(EXAMPLE_WORKFLOW).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        write_yaml(
            self.workspace_path,
            {
                "apiVersion": "signal-harness/v1alpha1",
                "kind": "Workspace",
                "feature": "stateful-cross-repo-change",
                "sponsor": "service-a",
                "workflow": "sdd.yaml",
                "repositories": {
                    "service-a": {
                        "remote": self.a.remote,
                        "mode": "read-write",
                        "author": True,
                    },
                    "service-b": {
                        "remote": self.b.remote,
                        "mode": "read-write",
                        "author": True,
                    },
                },
                "tasks": [
                    {
                        "id": "producer",
                        "repo": "service-a",
                        "produces": [{"id": "contract", "kind": "build-artifact"}],
                    },
                    {
                        "id": "consumer",
                        "repo": "service-b",
                        "dependsOn": ["producer"],
                        "consumes": [{"fromTask": "producer", "artifact": "contract"}],
                    },
                ],
            },
        )
        self.plan = build_plan(
            self.workspace_path,
            self.a.root,
            {"service-b": self.b.root},
        )
        self.store = RunStore(self.base / "state")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_persists_plan_and_prepares_idempotent_worktree(self) -> None:
        state = self.store.create(self.plan, self.workspace_path, "fixture-run")

        prepared = prepare_task(self.store, state["runId"], "producer")
        prepared_again = prepare_task(self.store, state["runId"], "producer")

        self.assertEqual(prepared, prepared_again)
        self.assertEqual(prepared["status"], "prepared")
        self.assertEqual(repository_root(Path(prepared["worktree"])), Path(prepared["worktree"]))
        saved = self.store.load("fixture-run")
        self.assertEqual(saved["status"], "active")
        self.assertEqual(saved["tasks"]["consumer"]["status"], "planned")

    def test_consumer_prepare_requires_provenance_checked_artifact_handoff(self) -> None:
        self.store.create(self.plan, self.workspace_path, "artifact-run")
        producer = prepare_task(self.store, "artifact-run", "producer")
        producer_worktree = Path(producer["worktree"])
        (producer_worktree / "producer-change.txt").write_text("implemented\n", encoding="utf-8")
        run("git", "add", "producer-change.txt", cwd=producer_worktree)
        run("git", "commit", "-m", "implement producer", cwd=producer_worktree)
        frozen = freeze_task_head(self.store, "artifact-run", "producer")
        revision = frozen["head"]

        with self.assertRaisesRegex(ResolutionError, "waiting for provenance-checked"):
            prepare_task(self.store, "artifact-run", "consumer")

        record = record_artifact_handoff(
            self.store,
            "artifact-run",
            task_id="producer",
            artifact_id="contract",
            uri="file:///tmp/contract.jar",
            digest=f"sha256:{'a' * 64}",
            source_revision=revision,
            provider="fixture.local",
        )
        consumer = prepare_task(self.store, "artifact-run", "consumer")

        self.assertEqual(record["producerTask"], "producer")
        self.assertEqual(consumer["resolvedArtifacts"][0]["id"], "contract")
        self.assertEqual(frozen["evidence"], "local-evaluation")

    def test_production_run_rejects_local_task_head_freeze(self) -> None:
        production_plan = replace(self.plan, policy_mode="production")
        self.store.create(production_plan, self.workspace_path, "production-freeze-run")
        producer = prepare_task(self.store, "production-freeze-run", "producer")
        producer_worktree = Path(producer["worktree"])
        (producer_worktree / "producer-change.txt").write_text("implemented\n", encoding="utf-8")
        run("git", "add", "producer-change.txt", cwd=producer_worktree)
        run("git", "commit", "-m", "implement producer", cwd=producer_worktree)

        with self.assertRaisesRegex(ConfigurationError, "evaluation mode"):
            freeze_task_head(self.store, "production-freeze-run", "producer")

    def test_production_artifact_handoff_rejects_local_provider_results(self) -> None:
        production_plan = replace(self.plan, policy_mode="production")
        self.store.create(production_plan, self.workspace_path, "production-artifact-run")
        producer = prepare_task(self.store, "production-artifact-run", "producer")
        state = self.store.load("production-artifact-run")
        revision = head_sha(Path(producer["worktree"]))
        state["tasks"]["producer"]["publishedHead"] = revision
        self.store.save(state)

        with self.assertRaisesRegex(ConfigurationError, "remotely resolvable"):
            record_artifact_handoff(
                self.store,
                "production-artifact-run",
                task_id="producer",
                artifact_id="contract",
                uri="file:///tmp/contract.jar",
                digest=f"sha256:{'a' * 64}",
                source_revision=revision,
                provider="fixture.local",
            )

    def test_pr_plan_has_explicit_repo_base_head_and_cwd(self) -> None:
        self.store.create(self.plan, self.workspace_path, "fixture-run")
        prepare_task(self.store, "fixture-run", "producer")

        result = pull_request_plan(
            self.store,
            "fixture-run",
            "producer",
            "@ITEM-12345678: publish contract",
        )

        self.assertEqual(result["repo"], "service-a")
        self.assertEqual(result["remote"], self.a.remote)
        self.assertIn("--repo", result["command"])
        self.assertIn(self.a.remote, result["command"])
        self.assertIn("--base", result["command"])
        self.assertIn("main", result["command"])
        self.assertIn("--body", result["command"])
        self.assertEqual(result["cwd"], result["worktree"])
        self.assertFalse(result["executed"])

    def test_prepare_rejects_context_drift(self) -> None:
        self.store.create(self.plan, self.workspace_path, "fixture-run")
        (self.a.root / "AGENTS.md").write_text("changed\n", encoding="utf-8")

        with self.assertRaisesRegex(ResolutionError, "became dirty"):
            prepare_task(self.store, "fixture-run", "producer")

    def test_plan_integrity_tampering_is_detected(self) -> None:
        self.store.create(self.plan, self.workspace_path, "fixture-run")
        path = self.store.state_path("fixture-run")
        state = json.loads(path.read_text(encoding="utf-8"))
        state["plan"]["feature"] = "tampered"
        path.write_text(json.dumps(state), encoding="utf-8")

        with self.assertRaisesRegex(ResolutionError, "integrity check failed"):
            self.store.load("fixture-run")

    def test_workflow_gate_evidence_controls_stage_progression(self) -> None:
        state = self.store.create(self.plan, self.workspace_path, "fixture-run")

        self.assertEqual(state["workflow"]["currentStage"], "0")
        with self.assertRaisesRegex(ConfigurationError, "state=MERGED"):
            self.store.clear_current_gate(
                "fixture-run",
                {
                    "state": "OPEN",
                    "reviewDecision": "APPROVED",
                    "url": "https://example.test/pr/1",
                },
            )

        workflow = self.store.clear_current_gate(
            "fixture-run",
            merged_planning_evidence(),
        )

        self.assertEqual(workflow["stages"]["0"]["status"], "complete")
        self.assertEqual(workflow["currentStage"], "1")
        self.assertEqual(workflow["stages"]["1"]["status"], "active")

    def test_waived_final_gate_has_a_non_passing_terminal_status(self) -> None:
        workflow = WorkflowDefinition.from_dict(
            {
                "apiVersion": "signal-harness/v1alpha1",
                "kind": "Workflow",
                "id": "waiver-test",
                "version": "1",
                "stages": [
                    {
                        "id": "integration",
                        "name": "Integration",
                        "objective": "Verify integration",
                        "activities": ["Run integration checks."],
                        "gate": {
                            "kind": "human",
                            "evidenceType": "integration",
                            "waivableInEvaluation": True,
                            "clearsWhen": "verified or explicitly waived",
                        },
                    }
                ],
            }
        )
        self.store.create(replace(self.plan, workflow=workflow), self.workspace_path, "waiver-run")

        result = self.store.clear_current_gate(
            "waiver-run",
            {
                "outcome": "waived",
                "waiver": {
                    "reason": "environment unavailable",
                    "authorizedBy": "release-owner@example.com",
                    "scope": "this evaluation run",
                },
            },
        )
        state = self.store.load("waiver-run")

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["stages"]["integration"]["status"], "waived")
        self.assertEqual(state["status"], "evaluation-complete-with-waivers")
        self.assertEqual(state["waivers"][0]["stage"], "integration")

    def test_per_task_workflow_respects_dependency_order(self) -> None:
        self.store.create(self.plan, self.workspace_path, "fixture-run")
        pr_evidence = merged_planning_evidence()
        self.store.clear_current_gate("fixture-run", pr_evidence)
        workflow = self.store.clear_current_gate("fixture-run", pr_evidence)

        self.assertEqual(workflow["currentStage"], "tasks")
        self.assertEqual(workflow["tasks"]["producer"]["status"], "active")
        self.assertEqual(workflow["tasks"]["consumer"]["status"], "pending")
        with self.assertRaisesRegex(ResolutionError, "dependencies must merge first"):
            self.store.clear_current_gate(
                "fixture-run",
                {"status": "pass", "signals": ["signal://consumer-tests"]},
                "consumer",
            )

        signal = "signal://producer-tests"
        record_tdd_phase(
            self.store,
            "fixture-run",
            "producer",
            "case-1",
            "red",
            status="fail",
            failure_kind="assertion",
            signal=f"{signal}/red",
        )
        record_tdd_phase(
            self.store,
            "fixture-run",
            "producer",
            "case-1",
            "green",
            status="pass",
            signal=f"{signal}/green",
        )
        record_tdd_phase(
            self.store,
            "fixture-run",
            "producer",
            "case-1",
            "refactor",
            status="pass",
            signal=f"{signal}/refactor",
        )
        self.store.clear_current_gate(
            "fixture-run",
            recorded_tdd_evidence(self.store.load("fixture-run"), "producer"),
            "producer",
        )
        workflow = self.store.clear_current_gate(
            "fixture-run",
            pr_evidence,
            "producer",
        )

        self.assertEqual(workflow["tasks"]["producer"]["status"], "complete")
        self.assertEqual(workflow["tasks"]["consumer"]["status"], "active")

    def test_stage_two_can_rewind_to_stage_zero_and_resume_same_task(self) -> None:
        self.store.create(self.plan, self.workspace_path, "fixture-run")
        evidence = merged_planning_evidence()
        self.store.clear_current_gate("fixture-run", evidence)
        self.store.clear_current_gate("fixture-run", evidence)

        rewound = self.store.rewind_workflow(
            "fixture-run",
            "0",
            actor="developer@example.com",
            reason="implementation exposed an unresolved authorization decision",
            task_id="producer",
        )
        self.assertEqual(rewound["currentStage"], "0")
        self.assertEqual(rewound["rewinds"][0]["fromStage"], "2")

        resumed = self.store.clear_current_gate("fixture-run", evidence)
        self.assertEqual(resumed["currentStage"], "tasks")
        self.assertEqual(resumed["tasks"]["producer"]["currentStage"], "2")
        self.assertNotIn("pendingReturn", resumed)
        self.assertIn("resumedAt", resumed["rewinds"][0])

    def test_stage_two_can_rewind_to_stage_one_and_replace_task_graph(self) -> None:
        self.store.create(self.plan, self.workspace_path, "fixture-run")
        evidence = merged_planning_evidence()
        self.store.clear_current_gate("fixture-run", evidence)
        self.store.clear_current_gate("fixture-run", evidence)

        rewound = self.store.rewind_workflow(
            "fixture-run",
            "1",
            actor="developer@example.com",
            reason="implementation showed that the approved task was not a small change",
            task_id="producer",
        )

        self.assertEqual(rewound["currentStage"], "1")
        state = self.store.load("fixture-run")
        self.assertEqual(state["plan"]["phase"], "discovery")
        self.assertEqual(state["plan"]["tasks"], [])
        self.assertEqual(state["tasks"], {})
        self.assertEqual(state["workflow"]["tasks"], {})
        self.assertEqual(
            [task["id"] for task in state["workflow"]["rewinds"][0]["previousTaskPlan"]],
            ["producer", "consumer"],
        )

        evidence["taskPlan"]["tasks"] = [
            {
                "id": "replacement",
                "repo": "service-b",
                "requirement": "implement the corrected small change",
                "dependsOn": [],
                "surfaces": ["consumer-behavior"],
                "testCases": [
                    {
                        "id": "case-1",
                        "kind": "delta",
                        "requirement": "the corrected behavior is observable",
                    }
                ],
                "requiredChecks": ["unit"],
                "context": [{"repo": "service-b", "path": "AGENTS.md"}],
                "ownership": {
                    "reason": "service-b owns the corrected behavior",
                    "evidence": [{"repo": "service-b", "path": "AGENTS.md"}],
                },
            }
        ]
        evidence["taskPlan"]["surfaceCoverage"] = [
            {
                "surface": "producer-behavior",
                "disposition": "no-change",
                "reason": "the corrected plan does not change the producer",
                "evidence": [{"repo": "service-a", "path": "AGENTS.md"}],
            },
            {
                "surface": "consumer-behavior",
                "disposition": "task",
                "task": "replacement",
            },
        ]

        resumed = self.store.clear_current_gate("fixture-run", evidence)
        state = self.store.load("fixture-run")
        self.assertEqual(resumed["currentStage"], "tasks")
        self.assertEqual(list(resumed["tasks"]), ["replacement"])
        self.assertEqual(list(state["tasks"]), ["replacement"])
        self.assertEqual(state["plan"]["phase"], "execution")
        self.assertIn("rematerializedAt", resumed["rewinds"][0])

    def test_stage_three_can_rewind_to_stage_two_for_review_fix(self) -> None:
        self.store.create(self.plan, self.workspace_path, "fixture-run")
        evidence = merged_planning_evidence()
        self.store.clear_current_gate("fixture-run", evidence)
        self.store.clear_current_gate("fixture-run", evidence)
        for phase, status in (("red", "fail"), ("green", "pass"), ("refactor", "pass")):
            record_tdd_phase(
                self.store,
                "fixture-run",
                "producer",
                "case-1",
                phase,
                status=status,
                failure_kind="assertion" if phase == "red" else None,
                signal=f"signal://producer/{phase}",
            )
        self.store.clear_current_gate(
            "fixture-run",
            recorded_tdd_evidence(self.store.load("fixture-run"), "producer"),
            "producer",
        )

        rewound = self.store.rewind_workflow(
            "fixture-run",
            "2",
            actor="reviewer@example.com",
            reason="review found a behavioral defect requiring implementation changes",
            task_id="producer",
        )
        task = rewound["tasks"]["producer"]
        self.assertEqual(task["currentStage"], "2")
        self.assertEqual(task["stages"]["2"]["status"], "active")
        self.assertEqual(task["stages"]["3"]["status"], "pending")

    def test_cross_repository_knowledge_gate_requires_rollback_plan(self) -> None:
        workflow = WorkflowDefinition.from_dict(
            {
                "apiVersion": "signal-harness/v1alpha1",
                "kind": "Workflow",
                "id": "knowledge-test",
                "version": "1",
                "stages": [
                    {
                        "id": "6",
                        "name": "Knowledge",
                        "objective": "Capture operational knowledge",
                        "activities": ["Record rollback and learning artifacts."],
                        "gate": {
                            "kind": "automatic",
                            "evidenceType": "knowledge",
                            "clearsWhen": "knowledge is complete",
                        },
                    }
                ],
            }
        )
        self.store.create(
            replace(self.plan, workflow=workflow), self.workspace_path, "knowledge-run"
        )
        evidence = {
            "status": "pass",
            "signals": ["artifact://learning"],
            "artifacts": ["docs/learnings.md"],
            "learningCandidates": [],
        }
        with self.assertRaisesRegex(ConfigurationError, "requires rollbackPlan"):
            self.store.clear_current_gate("knowledge-run", evidence)

        evidence["rollbackPlan"] = {
            "owner": "release-owner@example.com",
            "unwindOrder": ["service-b", "service-a"],
            "deploymentOrder": ["service-a", "service-b"],
            "alerting": "Page the service owner when rollback starts.",
        }
        result = self.store.clear_current_gate("knowledge-run", evidence)
        self.assertEqual(result["status"], "complete")


class SignalContractTests(unittest.TestCase):
    def test_signal_round_trip_and_assurance_report(self) -> None:
        signal = EngineeringSignal.from_dict(
            {
                "id": "unit-tests:service-a",
                "kind": "test",
                "source": "unit-tests",
                "repository": "service-a",
                "status": "pass",
                "summary": "42 tests passed",
                "evidence": ["run://unit-tests/42"],
            }
        )
        report = AssuranceReport(
            repository="service-a",
            gates=(
                QualityGateResult(
                    id="unit-tests",
                    status=signal.status,
                    summary=signal.summary,
                    signal_ids=(signal.id,),
                ),
            ),
        )

        self.assertEqual(signal.as_dict()["status"], "pass")
        self.assertNotIn("observedAt", signal.as_dict())
        self.assertTrue(report.passed)

    def test_assurance_report_fails_on_non_passing_gate(self) -> None:
        report = AssuranceReport(
            repository="service-a",
            gates=(
                QualityGateResult(
                    id="static-analysis",
                    status=SignalStatus.WARN,
                    summary="new warning",
                ),
            ),
        )

        self.assertFalse(report.passed)

    def test_assurance_report_fails_closed_without_gates(self) -> None:
        self.assertFalse(AssuranceReport(repository="service-a", gates=()).passed)


class CentralWorkflowTests(unittest.TestCase):
    def test_delivery_dependency_is_preserved_from_native_task_card_prose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = "specs/feature/manifest.yaml"
            write_yaml(root / manifest, {"feature": "fixture"})
            tasks = root / "specs" / "feature" / "tasks"
            tasks.mkdir()
            (tasks / "01-cache.md").write_text(
                "# Cache guardrail\n\n- Dependencies: none.\n", encoding="utf-8"
            )
            (tasks / "02-eviction.md").write_text(
                "# Eviction\n\n- Delivery dependency: Task 01; no code-level dependency.\n",
                encoding="utf-8",
            )

            cards = discover_task_cards(root, manifest, "service-a", str)

            self.assertEqual(cards[1].depends_on, ("01-cache",))

    def test_inline_depends_on_tasks_is_preserved_from_native_task_card_prose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = "specs/feature/manifest.yaml"
            write_yaml(root / manifest, {"feature": "fixture"})
            tasks = root / "specs" / "feature" / "tasks"
            tasks.mkdir()
            for name in ("01-dto.md", "02-client.md"):
                (tasks / name).write_text(f"# {name}\n", encoding="utf-8")
            (tasks / "03-service.md").write_text(
                "# Service\n\n**Stage 2 task.** Depends on Tasks 01, 02.\n",
                encoding="utf-8",
            )

            cards = discover_task_cards(root, manifest, "service-a", str)

            self.assertEqual(cards[2].depends_on, ("01-dto", "02-client"))

    def test_task_cards_preserve_wrapped_dependencies_and_deferred_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = "specs/feature/manifest.yaml"
            write_yaml(root / manifest, {"feature": "fixture"})
            tasks = root / "specs" / "feature" / "tasks"
            tasks.mkdir()
            (tasks / "01-contract.md").write_text(
                "---\nrepo: events-library\nstatus: complete (GREEN in PR #1)\n---\n"
                "# Publish contract\n\n- Dependencies: none.\n",
                encoding="utf-8",
            )
            (tasks / "02-storage.md").write_text(
                "---\nrepo: service-a\nstatus: complete\n---\n"
                "# Add storage\n\n- Dependencies: none.\n",
                encoding="utf-8",
            )
            (tasks / "03-handler.md").write_text(
                "---\nrepo: service-a\nstatus: pending\n---\n"
                "# Add handler\n\n"
                "- Dependencies: **01** (published contract),\n"
                "  **02** (storage operation).\n"
                "- Production: handler.py\n",
                encoding="utf-8",
            )
            (tasks / "04-subscription.md").write_text(
                "---\nrepo: service-a\nstatus: deferred pending rollout\n---\n"
                "# Enable subscription\n\n- Dependencies: **03**.\n",
                encoding="utf-8",
            )

            cards = discover_task_cards(root, manifest, "service-a", str)

            self.assertEqual(cards[0].declared_status, "complete")
            self.assertEqual(
                cards[2].depends_on,
                ("01-contract", "02-storage"),
            )
            self.assertEqual(cards[3].declared_status, "deferred")
            self.assertTrue(cards[3].deferred)

    def test_external_fixture_workflow_defines_complete_sdd_lifecycle(self) -> None:
        workflow = load_workflow_reference(EXAMPLE_WORKFLOW, Path.cwd())

        self.assertEqual(workflow.id, "fixture-sdd")
        self.assertEqual([stage.id for stage in workflow.stages], list("0123456"))
        self.assertEqual(workflow.stages[0].gate.kind, "pr")
        self.assertEqual(workflow.stages[4].gate.kind, "human")

    def test_external_policy_directory_resolves_its_workflow(self) -> None:
        catalog = load_policy_catalog(Path("tests/fixtures/policy"))

        self.assertEqual(catalog.id, "fixture-distribution")
        self.assertEqual(Path(catalog.workflow), Path(EXAMPLE_WORKFLOW).resolve())

    def test_policy_is_required_and_never_implicitly_product_specific(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "external policy pack is required"):
            load_policy_catalog(None)

    def test_catalog_rejects_static_interaction_graph(self) -> None:
        value = {
            "apiVersion": "signal-harness/v1alpha1",
            "kind": "RepositoryCatalog",
            "id": "fixture",
            "workflow": EXAMPLE_WORKFLOW,
            "repositories": {
                "service-a": {
                    "remote": "example-org/service-a",
                    "interactsWith": ["service-b"],
                },
                "service-b": {"remote": "example-org/service-b"},
            },
        }

        with self.assertRaisesRegex(ConfigurationError, "interactsWith was removed.*Stage 0"):
            RepositoryCatalog.from_dict(value)

    def test_catalog_checkout_without_profile_is_ignored_until_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = RepositoryFixture(Path(directory), "missing-profile")
            (missing.root / ".signal-harness/repo.yaml").unlink()

            profiles = _load_profiles({"missing-profile": missing.root})

            self.assertEqual(profiles, {})

    def test_profileless_repository_cannot_initiate_sdd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sponsor = RepositoryFixture(Path(directory), "service-a")
            (sponsor.root / ".signal-harness/repo.yaml").unlink()

            with self.assertRaisesRegex(
                ConfigurationError, "cannot initiate SDD without a repository profile"
            ):
                build_sdd_plan(feature="missing-profile", current_path=sponsor.root)

    def test_catalog_repository_without_profile_remains_discoverable(self) -> None:
        catalog = RepositoryCatalog(
            id="fixture",
            workflow=EXAMPLE_WORKFLOW,
            repositories={
                "service-a": CatalogRepository(
                    id="service-a",
                    remote="example-org/service-a",
                ),
                "service-b": CatalogRepository(id="service-b", remote="example-org/service-b"),
            },
        )
        profiles = {
            "service-a": RepositoryProfile.from_dict(profile("service-a", "example-org/service-a"))
        }

        workspace = synthesize_interaction_workspace(
            feature="cross-service-change",
            sponsor="service-a",
            feature_manifest="specs/example/sdd/change/manifest.yaml",
            catalog=catalog,
            profiles=profiles,
        )

        self.assertEqual(workspace.include, ("service-a", "service-b"))
        self.assertIn("service-b", workspace.repositories)

    def test_inferred_profile_is_minimal_and_has_no_invented_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run("git", "init", "-b", "main", cwd=root)
            run("git", "config", "user.email", "test@example.com", cwd=root)
            run("git", "config", "user.name", "Test", cwd=root)
            run("git", "remote", "add", "origin", "example-org/profileless", cwd=root)
            (root / "README.md").write_text("# Profileless\n", encoding="utf-8")
            run("git", "add", ".", cwd=root)
            run("git", "commit", "-m", "seed", cwd=root)

            with self.assertRaisesRegex(ResolutionError, "cannot infer repository default"):
                infer_repository_profile(root, "profileless", "example-org/profileless")
            run("git", "update-ref", "refs/remotes/origin/main", "HEAD", cwd=root)
            run(
                "git",
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/main",
                cwd=root,
            )
            inferred = infer_repository_profile(root, "profileless", "example-org/profileless")

            self.assertEqual(inferred.commands, {})
            self.assertEqual(inferred.signal_sources, ())
            self.assertNotIn("src/**/*", inferred.context.optional)
            self.assertNotIn("app/**/*", inferred.context.optional)
            self.assertNotIn("terraform/**/*", inferred.context.optional)

    def test_sdd_start_persists_catalog_but_resolves_only_sponsor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            sponsor_parent = base / "workspace"
            sponsor_parent.mkdir()
            sponsor = RepositoryFixture(sponsor_parent, "service-a")

            feature = sponsor.root / "specs/area/sdd/managed-peer"
            feature.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "managed-peer",
                    "sources": ["specs/area/sdd/managed-peer/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "service-a": CatalogRepository(
                        id="service-a", remote="example-org/service-a", author=True
                    ),
                    "service-b": CatalogRepository(
                        id="service-b", remote="example-org/service-b", author=True
                    ),
                },
            )
            with (
                patch("signal_harness.sdd.load_policy_catalog", return_value=catalog),
                patch(
                    "signal_harness.sdd.managed_repository",
                    side_effect=AssertionError("startup must not resolve peer repositories"),
                ),
            ):
                plan = build_sdd_plan(feature="managed-peer", current_path=sponsor.root)
                excluded = build_sdd_plan(
                    feature="managed-peer", current_path=sponsor.root, exclude=("service-b",)
                ).as_dict()
                included = build_sdd_plan(
                    feature="managed-peer", current_path=sponsor.root, include=("service-b",)
                )
            self.assertEqual([item["id"] for item in excluded["repositories"]], ["service-a"])
            self.assertEqual(
                [item["id"] for item in excluded["catalogRepositories"]], ["service-a"]
            )
            self.assertEqual(included.as_dict()["requiredEngagements"], ["service-b"])
            self.assertEqual(
                [item["id"] for item in included.as_dict()["repositories"]], ["service-a"]
            )
            store = RunStore(base / "include-state")
            state = store.create(included, feature / "manifest.yaml", "include-run")
            evidence = merged_planning_evidence()
            evidence["specification"]["interfaceImpact"]["evaluatedRepositories"] = ["service-a"]
            evidence["specification"]["affectedSurfaces"] = [
                evidence["specification"]["affectedSurfaces"][0]
            ]
            with self.assertRaisesRegex(ConfigurationError, "requires engagement"):
                store.clear_current_gate("include-run", evidence)
            participant = RepositoryFixture(base, "service-b")
            session_api.engage_repository(
                store,
                "include-run",
                "service-b",
                reason="Explicit requested peer is relevant",
                evidence=("service-a:AGENTS.md",),
                override=participant.root,
            )
            with self.assertRaisesRegex(ConfigurationError, "missing=.*service-b"):
                store.clear_current_gate("include-run", evidence)
            evidence["specification"]["interfaceImpact"]["evaluatedRepositories"].append(
                "service-b"
            )
            store.clear_current_gate("include-run", evidence)
            self.assertEqual(store.load("include-run")["workflow"]["currentStage"], "1")

            serialized = plan.as_dict()
            self.assertEqual([item["id"] for item in serialized["repositories"]], ["service-a"])
            self.assertEqual(
                serialized["catalogRepositories"],
                [
                    {
                        "id": "service-a",
                        "remote": "example-org/service-a",
                        "author": True,
                    },
                    {
                        "id": "service-b",
                        "remote": "example-org/service-b",
                        "author": True,
                    },
                ],
            )
            state = RunStore(base / "state").create(
                plan, feature / "manifest.yaml", "managed-peer-run"
            )
            self.assertEqual(state["workflow"]["currentStage"], "0")

    def test_stage_zero_engages_one_evidenced_repository_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            sponsor = RepositoryFixture(base, "service-a")
            participant = RepositoryFixture(base, "service-b")
            feature = sponsor.root / "specs/area/sdd/lazy-peer"
            feature.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "lazy-peer",
                    "sources": ["specs/area/sdd/lazy-peer/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "service-a": CatalogRepository(
                        id="service-a", remote="example-org/service-a", author=True
                    ),
                    "service-b": CatalogRepository(
                        id="service-b", remote="example-org/service-b", author=True
                    ),
                    "service-c": CatalogRepository(
                        id="service-c", remote="example-org/service-c", author=True
                    ),
                },
            )
            with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
                plan = build_sdd_plan(feature="lazy-peer", current_path=sponsor.root)
            store = RunStore(base / "state")
            store.create(plan, feature / "manifest.yaml", "lazy-peer-run")

            self.assertTrue(
                hasattr(session_api, "engage_repository"),
                "Stage 0 needs an explicit lazy repository engagement operation",
            )
            with patch("signal_harness.session.managed_repository") as fetch:
                for target, evidence, message in (
                    ("outside", ("service-a:AGENTS.md",), "not in the run catalog"),
                    ("service-b", ("unknown:AGENTS.md",), "not from engaged context"),
                    ("service-b", ("service-a:unlocked.py",), "not SHA-locked"),
                ):
                    with self.assertRaisesRegex(ConfigurationError, message):
                        session_api.engage_repository(
                            store,
                            "lazy-peer-run",
                            target,
                            reason="Discover peer",
                            evidence=evidence,
                            actor="developer@example.com",
                        )
                    denial = store.load("lazy-peer-run")["events"][-1]
                    self.assertEqual(denial["type"], "security.authorization.denied")
                    self.assertEqual(denial["detail"]["actor"], "developer@example.com")
                    self.assertEqual(denial["detail"]["action"], "engage-repository")
                    self.assertEqual(denial["detail"]["outcome"], "denied")
                    self.assertEqual(denial["detail"]["repository"], target)
                fetch.assert_not_called()
            result = session_api.engage_repository(
                store,
                "lazy-peer-run",
                "service-b",
                actor="developer@example.com",
                reason="The sponsor calls the service-b API.",
                evidence=("service-a:AGENTS.md",),
                override=participant.root,
            )

            self.assertEqual(result["status"], "engaged")
            state = store.load("lazy-peer-run")
            event = state["events"][-1]
            self.assertEqual(event["type"], "discovery.repository.engaged")
            self.assertEqual(event["detail"]["actor"], "developer@example.com")
            self.assertEqual(event["detail"]["outcome"], "engaged")
            self.assertEqual(
                [item["id"] for item in state["plan"]["repositories"]],
                ["service-a", "service-b"],
            )
            self.assertNotIn("service-c", {item["id"] for item in state["plan"]["repositories"]})
            self.assertEqual(state["plan"]["repositories"][1]["mode"], "read-only")
            self.assertEqual(
                state["repositoryEngagement"]["service-b"]["evidence"],
                ["service-a:AGENTS.md"],
            )

    def test_stage_zero_locks_only_discovered_source_evidence_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            sponsor = RepositoryFixture(base, "service-a")
            participant = RepositoryFixture(base, "service-b")
            source = participant.root / "src" / "consumer.py"
            source.parent.mkdir()
            source.write_text("def consume():\n    return True\n", encoding="utf-8")
            (source.parent / "contract.py").write_text("CONTRACT = True\n", encoding="utf-8")
            (participant.root / "docs" / "[guide].md").write_text(
                "literal path\n", encoding="utf-8"
            )
            run("git", "add", ".", cwd=participant.root)
            run("git", "commit", "-m", "add consumer", cwd=participant.root)
            feature = sponsor.root / "specs/area/sdd/source-evidence"
            feature.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "source-evidence",
                    "sources": ["specs/area/sdd/source-evidence/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "service-a": CatalogRepository(
                        id="service-a", remote="example-org/service-a", author=True
                    ),
                    "service-b": CatalogRepository(
                        id="service-b", remote="example-org/service-b", author=True
                    ),
                },
            )
            with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
                plan = build_sdd_plan(feature="source-evidence", current_path=sponsor.root)
            store = RunStore(base / "state")
            store.create(plan, feature / "manifest.yaml", "source-evidence-run")
            session_api.engage_repository(
                store,
                "source-evidence-run",
                "service-b",
                reason="The sponsor names the service-b consumer.",
                evidence=("service-a:AGENTS.md",),
                override=participant.root,
            )

            result = session_api.lock_discovery_context(
                store,
                "source-evidence-run",
                "service-b",
                paths=("src/consumer.py",),
                reason="Stage 0 code search identified the concrete consumer.",
            )

            self.assertEqual(result["added"], ["src/consumer.py"])
            state = store.load("source-evidence-run")
            repository = next(
                item for item in state["plan"]["repositories"] if item["id"] == "service-b"
            )
            self.assertIn(
                "src/consumer.py",
                {item["path"] for item in repository["context"]["files"]},
            )
            self.assertEqual(state["events"][-1]["type"], "discovery.context.locked")
            first_files = {item["path"]: item for item in repository["context"]["files"]}
            session_api.lock_discovery_context(
                store,
                "source-evidence-run",
                "service-b",
                paths=("src/contract.py",),
                reason="Search found contract",
            )
            state = store.load("source-evidence-run")
            second_files = {
                item["path"]: item for item in state["plan"]["repositories"][1]["context"]["files"]
            }
            self.assertTrue(all(second_files[path] == item for path, item in first_files.items()))
            self.assertIn("src/contract.py", second_files)
            self.assertIn("docs/[guide].md", second_files)
            self.assertEqual(second_files["docs/[guide].md"], first_files["docs/[guide].md"])
            for invalid in ("../outside.md", str(source.resolve()), "docs/private/secret.md"):
                with self.assertRaises(ResolutionError):
                    session_api.lock_discovery_context(
                        store,
                        "source-evidence-run",
                        "service-b",
                        paths=(invalid,),
                        reason="Reject invalid exact paths",
                    )
                self.assertEqual(
                    store.load("source-evidence-run")["plan"]["repositories"][1]["context"][
                        "files"
                    ],
                    list(second_files.values()),
                )
            sponsor_before = state["plan"]["repositories"][0]["context"]["files"]
            (sponsor.root / "extra.py").write_text("EXTRA = True\n", encoding="utf-8")
            session_api.lock_discovery_context(
                store,
                "source-evidence-run",
                "service-a",
                paths=("extra.py",),
                reason="Search found sponsor source",
            )
            sponsor_after = store.load("source-evidence-run")["plan"]["repositories"][0]["context"]
            self.assertTrue(all(item in sponsor_after["files"] for item in sponsor_before))
            self.assertIn(".signal-harness/repo.yaml", {item["path"] for item in sponsor_before})
            self.assertIn(
                "specs/area/sdd/source-evidence/manifest.yaml",
                {item["path"] for item in sponsor_before},
            )
            source.write_text("changed without a commit\n", encoding="utf-8")
            with self.assertRaisesRegex(ResolutionError, "previously locked context changed"):
                session_api.lock_discovery_context(
                    store,
                    "source-evidence-run",
                    "service-b",
                    paths=("src/contract.py",),
                    reason="Must not refresh earlier evidence",
                )
            self.assertEqual(
                store.load("source-evidence-run")["plan"]["repositories"][1]["context"]["files"],
                list(second_files.values()),
            )

            for phase, stage in (("discovery", "1"), ("discovery", "2"), ("execution", "0")):
                state = store.load("source-evidence-run")
                state["plan"]["phase"] = phase
                state["planHash"] = _canonical_hash(state["plan"])
                state["workflow"]["currentStage"] = stage
                store.save(state)
                with patch("signal_harness.session.managed_repository") as resolver:
                    with self.assertRaisesRegex(ResolutionError, "only during Stage 0"):
                        session_api.engage_repository(
                            store,
                            "source-evidence-run",
                            "service-b",
                            actor="developer@example.com",
                            reason="Cannot engage after discovery",
                            evidence=("service-a:AGENTS.md",),
                        )
                    resolver.assert_not_called()
                audited = store.load("source-evidence-run")
                self.assertEqual(
                    {
                        key: value
                        for key, value in audited.items()
                        if key not in {"events", "updatedAt", "stateRevision"}
                    },
                    {
                        key: value
                        for key, value in state.items()
                        if key not in {"events", "updatedAt", "stateRevision"}
                    },
                )
                self.assertEqual(audited["stateRevision"], state["stateRevision"] + 1)
                self.assertEqual(audited["events"][:-1], state["events"])
                denial = audited["events"][-1]
                self.assertEqual(denial["type"], "security.authorization.denied")
                self.assertEqual(
                    denial["detail"],
                    {
                        "actor": "developer@example.com",
                        "action": "engage-repository",
                        "repository": "service-b",
                        "outcome": "denied",
                        "reason": (
                            "run source-evidence-run can engage repositories "
                            "only during Stage 0 discovery"
                        ),
                    },
                )
                self.assertIsNotNone(datetime.fromisoformat(denial["at"]).tzinfo)
                with self.assertRaisesRegex(ResolutionError, "only during Stage 0"):
                    store.record_unavailable_discovery_repository(
                        "source-evidence-run",
                        repo_id="service-b",
                        remote="example-org/service-b",
                        reason="Cannot record after discovery",
                        evidence=("service-a:AGENTS.md",),
                        error="unavailable",
                    )
                self.assertEqual(store.load("source-evidence-run"), audited)

    def test_managed_repository_keeps_prior_run_context_stable_after_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            run("git", "init", "-b", "main", cwd=source)
            run("git", "config", "user.email", "test@example.com", cwd=source)
            run("git", "config", "user.name", "Test", cwd=source)
            (source / "README.md").write_text("first\n", encoding="utf-8")
            run("git", "add", ".", cwd=source)
            run("git", "commit", "-m", "first", cwd=source)
            remote = base / "service-b.git"
            run("git", "clone", "--bare", str(source), str(remote), cwd=base)
            run("git", "remote", "add", "origin", str(remote), cwd=source)
            cache = base / "cache"

            with patch.dict(
                "os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(cache)}, clear=False
            ):
                first = managed_repository(str(remote), "service-b", snapshot_root=base / "run-a")
                first_revision = head_sha(first)
                first_profile = infer_repository_profile(first, "service-b", str(remote))
                first_context = collect_context(first, first_profile)
                (source / "README.md").write_text("second\n", encoding="utf-8")
                run("git", "add", ".", cwd=source)
                run("git", "commit", "-m", "second", cwd=source)
                run("git", "push", "origin", "main", cwd=source)
                second = managed_repository(str(remote), "service-b", snapshot_root=base / "run-b")

            self.assertNotEqual(first, second)
            self.assertEqual(head_sha(first), first_revision)
            self.assertNotEqual(head_sha(second), first_revision)
            self.assertEqual((first / "README.md").read_text(encoding="utf-8"), "first\n")
            self.assertEqual(collect_context(first, first_profile), first_context)

    def test_inferred_profile_uses_remote_default_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            run("git", "init", "-b", "develop", cwd=source)
            run("git", "config", "user.email", "test@example.com", cwd=source)
            run("git", "config", "user.name", "Test", cwd=source)
            (source / "README.md").write_text("develop\n", encoding="utf-8")
            run("git", "add", ".", cwd=source)
            run("git", "commit", "-m", "seed", cwd=source)
            remote = base / "service-b.git"
            run("git", "clone", "--bare", str(source), str(remote), cwd=base)
            with patch.dict(
                "os.environ",
                {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")},
                clear=False,
            ):
                snapshot = managed_repository(str(remote), "service-b", snapshot_root=base / "run")

            inferred = infer_repository_profile(snapshot, "service-b", str(remote))
            self.assertEqual(inferred.default_branch, "develop")
            run("git", "clone", str(remote), str(base / "checkout"), cwd=base)
            checkout = base / "checkout"
            run("git", "checkout", "-b", "feature/foo", cwd=checkout)
            inferred = infer_repository_profile(checkout, "service-b", str(remote))
            self.assertEqual(inferred.default_branch, "develop")
            run("git", "symbolic-ref", "--delete", "refs/remotes/origin/HEAD", cwd=checkout)
            with self.assertRaisesRegex(ResolutionError, "cannot infer repository default"):
                infer_repository_profile(checkout, "service-b", str(remote))

    def test_unengaged_inaccessible_repository_is_not_resolved_at_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            sponsor = RepositoryFixture(base, "service-a")
            feature = sponsor.root / "specs/area/sdd/access-aware"
            feature.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "access-aware",
                    "sources": ["specs/area/sdd/access-aware/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "service-a": CatalogRepository(
                        id="service-a", remote="example-org/service-a", author=True
                    ),
                    "private-svc": CatalogRepository(
                        id="private-svc", remote="example-org/private-svc", author=True
                    ),
                },
            )
            with (
                patch("signal_harness.sdd.load_policy_catalog", return_value=catalog),
                patch(
                    "signal_harness.sdd.managed_repository",
                    side_effect=ResolutionError("repository access denied"),
                ),
            ):
                plan = build_sdd_plan(feature="access-aware", current_path=sponsor.root)

            self.assertEqual([item["id"] for item in plan.as_dict()["repositories"]], ["service-a"])
            self.assertEqual(plan.as_dict()["unavailableRepositories"], [])
            store = RunStore(base / "state")
            state = store.create(plan, feature / "manifest.yaml", "access-aware-run")
            self.assertEqual(state["workflow"]["currentStage"], "0")
            action = next_action(store, "access-aware-run")
            self.assertEqual(action["action"], "complete-stage")
            self.assertNotIn("repositoryAccess", action)

    def test_required_inaccessible_repository_stops_for_human_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            sponsor = RepositoryFixture(base, "service-a")
            feature = sponsor.root / "specs/area/sdd/access-required"
            feature.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "access-required",
                    "sources": ["specs/area/sdd/access-required/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "service-a": CatalogRepository(
                        id="service-a", remote="example-org/service-a", author=True
                    ),
                    "private-svc": CatalogRepository(
                        id="private-svc", remote="example-org/private-svc", author=True
                    ),
                },
            )
            with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
                plan = build_sdd_plan(feature="access-required", current_path=sponsor.root)

            store = RunStore(base / "state")
            store.create(plan, feature / "manifest.yaml", "access-required-run")
            with patch(
                "signal_harness.session.managed_repository",
                side_effect=ResolutionError("repository access denied"),
            ):
                engagement = session_api.engage_repository(
                    store,
                    "access-required-run",
                    "private-svc",
                    reason="The feature specification names a private-svc API.",
                    evidence=("service-a:AGENTS.md",),
                )
            self.assertEqual(engagement["status"], "unavailable")
            action = next_action(store, "access-required-run")
            self.assertEqual(action["action"], engagement["action"])
            self.assertEqual(action["action"], "obtain-repository-access")
            self.assertEqual(
                run_summary(store, "access-required-run")["nextActions"][0],
                action["action"],
            )
            self.assertEqual(
                [item["id"] for item in action["repositoryAccess"]["required"]],
                ["private-svc"],
            )
            evidence = merged_planning_evidence()
            evidence["specification"]["repositoryBoundaries"] = ["service-a", "private-svc"]
            evidence["specification"]["interfaceImpact"]["evaluatedRepositories"] = ["service-a"]
            evidence["specification"]["interfaceImpact"]["unavailableRepositories"] = [
                {
                    "id": "private-svc",
                    "relevance": "not-required",
                    "reason": "The feature specification names a private-svc API.",
                }
            ]
            evidence["specification"]["affectedSurfaces"] = [
                evidence["specification"]["affectedSurfaces"][0]
            ]

            with self.assertRaisesRegex(
                ResolutionError, "obtain access and resume the run.*private-svc"
            ):
                store.clear_current_gate("access-required-run", evidence)
            self.assertEqual(store.load("access-required-run")["workflow"]["currentStage"], "0")

            participant = RepositoryFixture(base, "private-svc")
            session_api.engage_repository(
                store,
                "access-required-run",
                "private-svc",
                reason="The feature specification names a private-svc API.",
                evidence=("service-a:AGENTS.md",),
                override=participant.root,
            )
            refreshed = store.load("access-required-run")
            self.assertEqual(refreshed["plan"]["unavailableRepositories"], [])
            self.assertEqual(
                {item["id"] for item in refreshed["plan"]["repositories"]},
                {"service-a", "private-svc"},
            )

    def test_catalog_universe_does_not_predeclare_interaction_edges(self) -> None:
        catalog = RepositoryCatalog(
            id="fixture",
            workflow=EXAMPLE_WORKFLOW,
            repositories={
                "service-a": CatalogRepository(
                    id="service-a",
                    remote="example-org/service-a",
                ),
                "service-b": CatalogRepository(id="service-b", remote="example-org/service-b"),
                "observer": CatalogRepository(id="observer", remote="example-org/observer"),
            },
        )
        profiles = {
            repo_id: RepositoryProfile.from_dict(profile(repo_id, f"example-org/{repo_id}"))
            for repo_id in catalog.repositories
        }

        workspace = synthesize_interaction_workspace(
            feature="cross-service-change",
            sponsor="service-a",
            feature_manifest="specs/example/sdd/change/manifest.yaml",
            catalog=catalog,
            profiles=profiles,
            include=("observer",),
        )

        self.assertEqual(workspace.include, ("service-a", "service-b", "observer"))
        self.assertEqual(workspace.repositories["service-b"].mode, "read-only")
        self.assertEqual(workspace.repositories["observer"].mode, "read-only")
        self.assertEqual(workspace.tasks[0].repo, "service-a")

        inbound = synthesize_interaction_workspace(
            feature="inbound-change",
            sponsor="service-b",
            feature_manifest="specs/example/sdd/inbound/manifest.yaml",
            catalog=catalog,
            profiles=profiles,
        )
        self.assertEqual(inbound.include, ("service-a", "service-b", "observer"))
        self.assertEqual(inbound.repositories["service-a"].mode, "read-only")

    def test_interaction_workspace_uses_explicit_task_routes(self) -> None:
        catalog = RepositoryCatalog(
            id="fixture",
            workflow=EXAMPLE_WORKFLOW,
            repositories={
                repo_id: CatalogRepository(
                    id=repo_id,
                    remote=f"example-org/{repo_id}",
                )
                for repo_id in ("service-a", "service-b", "observer")
            },
        )
        profiles = {
            repo_id: RepositoryProfile.from_dict(profile(repo_id, f"example-org/{repo_id}"))
            for repo_id in catalog.repositories
        }
        cards = (
            TaskCard(
                id="01-api",
                repo="service-a",
                depends_on=(),
                description="Add API",
                source="specs/feature/tasks/01-api.md",
            ),
            TaskCard(
                id="02-consumer",
                repo="service-b",
                depends_on=("01-api",),
                description="Use API",
                source="specs/feature/tasks/02-consumer.md",
            ),
        )

        workspace = synthesize_interaction_workspace(
            feature="fixture",
            sponsor="service-a",
            feature_manifest="specs/feature/manifest.yaml",
            catalog=catalog,
            profiles=profiles,
            include=("observer",),
            task_cards=cards,
        )

        self.assertEqual([task.id for task in workspace.tasks], ["01-api", "02-consumer"])
        self.assertEqual(workspace.repositories["service-a"].mode, "read-write")
        self.assertEqual(workspace.repositories["service-b"].mode, "read-write")
        self.assertEqual(workspace.repositories["observer"].mode, "read-only")

    def test_sdd_plan_is_portable_when_invoked_outside_the_feature_sponsor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            sponsor = RepositoryFixture(parent, "service-a")
            participant = RepositoryFixture(parent, "service-b")
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    repo_id: CatalogRepository(
                        id=repo_id,
                        remote=f"example-org/{repo_id}",
                    )
                    for repo_id in ("service-a", "service-b")
                },
            )
            feature = sponsor.root / "specs" / "area" / "sdd" / "portable"
            tasks = feature / "tasks"
            tasks.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "portable",
                    "sources": ["specs/area/sdd/portable/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            (tasks / "01-change.md").write_text(
                "# Make the change\n\n- Dependencies: none.\n", encoding="utf-8"
            )

            with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
                from_sponsor = build_sdd_plan(
                    feature="portable",
                    current_path=sponsor.root,
                    repo_overrides={"service-b": participant.root},
                )
                from_participant = build_sdd_plan(
                    feature="portable",
                    current_path=participant.root,
                    sponsor_id="service-a",
                    repo_overrides={"service-a": sponsor.root},
                )

            self.assertEqual(snapshot_plan(from_sponsor), snapshot_plan(from_participant))

    def test_sdd_plan_defers_task_and_interaction_repositories_until_stage_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            RepositoryFixture(parent, "contracts")
            producer = RepositoryFixture(parent, "producer")
            RepositoryFixture(parent, "consumer")
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "contracts": CatalogRepository(
                        id="contracts",
                        remote="example-org/contracts",
                    ),
                    "producer": CatalogRepository(id="producer", remote="example-org/producer"),
                    "consumer": CatalogRepository(id="consumer", remote="example-org/consumer"),
                },
            )
            feature = producer.root / "specs" / "area" / "sdd" / "event-change"
            tasks = feature / "tasks"
            tasks.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "event-change",
                    "sources": ["specs/area/sdd/event-change/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            (tasks / "01-contract.md").write_text(
                "---\nrepo: contracts\n---\n# Publish contract\n", encoding="utf-8"
            )
            (tasks / "02-producer.md").write_text(
                "---\nrepo: producer\ndepends_on: [01]\n---\n# Publish event\n",
                encoding="utf-8",
            )

            with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
                plan = build_sdd_plan(feature="event-change", current_path=producer.root)

            self.assertEqual(plan.phase, "discovery")
            self.assertEqual(plan.workspace.tasks, ())
            self.assertEqual(
                [(task.id, task.repo) for task in plan.proposed_tasks],
                [("01-contract", "contracts"), ("02-producer", "producer")],
            )
            self.assertTrue(
                all(repo.mode == "read-only" for repo in plan.workspace.repositories.values())
            )
            self.assertEqual(tuple(plan.workspace.repositories), ("producer",))
            self.assertEqual(
                {item["id"] for item in plan.catalog_repositories},
                {"contracts", "producer", "consumer"},
            )

    def test_surface_checks_honor_validated_not_applicable_derivation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory) / "state")
            state = {
                "workflow": {
                    "stages": {
                        "0": {
                            "gate": {"evidenceType": "specification"},
                            "evidence": {
                                "specification": {
                                    "affectedSurfaces": [
                                        {
                                            "id": "java-behavior",
                                            "ownerRepo": "service-a",
                                            "verification": {
                                                "requiredChecks": ["unit", "helmLint"],
                                            },
                                        }
                                    ]
                                }
                            },
                        }
                    }
                },
                "plan": {
                    "repositories": [
                        {
                            "id": "service-a",
                            "profile": {
                                "commands": {
                                    "unit": "./gradlew test",
                                    "helmLint": "./gradlew helmLint",
                                }
                            },
                        }
                    ]
                },
            }
            evidence = {
                "taskPlan": {
                    "checkDerivation": [
                        {
                            "task": "implementation",
                            "checks": [
                                {
                                    "id": "unit",
                                    "disposition": "required",
                                    "applicable": True,
                                    "executable": True,
                                },
                                {
                                    "id": "helmLint",
                                    "disposition": "not-applicable",
                                    "applicable": False,
                                    "executable": False,
                                    "reason": "The controlled snapshot has no Helm surface.",
                                },
                            ],
                        }
                    ]
                }
            }
            tasks = {
                "implementation": WorkspaceTask(
                    id="implementation",
                    repo="service-a",
                    required_checks=("unit",),
                    surfaces=("java-behavior",),
                )
            }

            store._assert_surface_required_checks(
                state,
                evidence,
                tasks,
                allow_derived_not_applicable=True,
            )

            with self.assertRaisesRegex(ConfigurationError, "omits surface-required checks"):
                store._assert_surface_required_checks(
                    state,
                    evidence,
                    tasks,
                    allow_derived_not_applicable=False,
                )

    def test_stage_one_materializes_owned_tasks_and_write_grants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            sponsor = RepositoryFixture(parent, "service-a")
            participant = RepositoryFixture(parent, "service-b")
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "service-a": CatalogRepository(
                        id="service-a",
                        remote="example-org/service-a",
                    ),
                    "service-b": CatalogRepository(
                        id="service-b", remote="example-org/service-b", author=True
                    ),
                },
            )
            feature = sponsor.root / "specs" / "area" / "sdd" / "new-feature"
            feature.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "new-feature",
                    "sources": ["specs/area/sdd/new-feature/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            run("git", "add", ".", cwd=sponsor.root)
            run("git", "commit", "-m", "feature seed", cwd=sponsor.root)
            with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
                plan = build_sdd_plan(
                    feature="new-feature",
                    current_path=sponsor.root,
                    repo_overrides={"service-b": participant.root},
                )

            store = RunStore(parent / "state")
            state = store.create(plan, feature / "manifest.yaml", "discovery-run")
            session_api.engage_repository(
                store,
                "discovery-run",
                "service-b",
                reason="The feature affects the service-b consumer entrypoint.",
                evidence=("service-a:AGENTS.md",),
                override=participant.root,
            )
            state = store.load("discovery-run")
            self.assertEqual(state["plan"]["phase"], "discovery")
            self.assertEqual(state["tasks"], {})
            self.assertTrue(
                all(repo["mode"] == "read-only" for repo in state["plan"]["repositories"])
            )

            evidence = merged_planning_evidence()
            evidence["specification"]["interfaceImpact"]["evaluatedRepositories"] = [
                "service-a",
                "service-b",
            ]
            evidence["specification"]["affectedSurfaces"][1]["verification"] = {
                "requiredChecks": ["unit"],
                "rationale": "The owning repository must prove the behavior.",
            }
            evidence["specification"]["callerMatrix"] = [
                {
                    "id": "consumer-entrypoint",
                    "repo": "service-b",
                    "path": "AGENTS.md",
                    "symbol": "fixture consumer entrypoint",
                    "surface": "consumer-behavior",
                    "behavior": "consume the changed behavior",
                    "errorHandling": "preserve explicit failures",
                    "observability": "retain the repository signal",
                    "evidence": [{"repo": "service-b", "path": "AGENTS.md"}],
                }
            ]
            store.clear_current_gate("discovery-run", evidence)
            (feature / "stage-0-decisions.md").write_text(
                "# Approved specification decisions\n", encoding="utf-8"
            )
            (feature / "stage-1-decisions.md").write_text(
                "# Approved task decomposition\n", encoding="utf-8"
            )
            run("git", "add", ".", cwd=sponsor.root)
            run("git", "commit", "-m", "merge planning documents", cwd=sponsor.root)
            planning_revision = head_sha(sponsor.root)
            evidence["taskPlan"]["tasks"] = [
                {
                    "id": "implementation",
                    "repo": "service-b",
                    "requirement": "implement the discovered owner behavior",
                    "dependsOn": [],
                    "surfaces": ["consumer-behavior"],
                    "callers": ["consumer-entrypoint"],
                    "testCases": [
                        {
                            "id": "case-1",
                            "kind": "delta",
                            "requirement": "the new behavior is observable",
                        }
                    ],
                    "requiredChecks": ["unit"],
                    "context": [{"repo": "service-b", "path": "AGENTS.md"}],
                    "ownership": {
                        "reason": "service-b owns the affected implementation",
                        "evidence": [{"repo": "service-b", "path": "AGENTS.md"}],
                    },
                }
            ]
            evidence["taskPlan"]["surfaceCoverage"] = [
                {
                    "surface": "producer-behavior",
                    "disposition": "no-change",
                    "reason": "sponsor owns specification only",
                    "evidence": [{"repo": "service-a", "path": "AGENTS.md"}],
                },
                {
                    "surface": "consumer-behavior",
                    "disposition": "task",
                    "task": "implementation",
                },
            ]
            evidence["taskPlan"]["callerCoverage"] = [
                {
                    "caller": "consumer-entrypoint",
                    "disposition": "task",
                    "task": "implementation",
                }
            ]
            incomplete = json.loads(json.dumps(evidence))
            incomplete["taskPlan"]["surfaceCoverage"] = [
                incomplete["taskPlan"]["surfaceCoverage"][1]
            ]
            with self.assertRaisesRegex(ConfigurationError, "surfaceCoverage mismatch"):
                store.clear_current_gate("discovery-run", incomplete)
            missing_caller = json.loads(json.dumps(evidence))
            missing_caller["taskPlan"]["callerCoverage"] = []
            with self.assertRaisesRegex(ConfigurationError, "callerCoverage mismatch"):
                store.clear_current_gate("discovery-run", missing_caller)
            missing_surface_check = json.loads(json.dumps(evidence))
            missing_surface_check["taskPlan"]["tasks"][0]["requiredChecks"] = ["compile"]
            with self.assertRaisesRegex(ConfigurationError, "omits surface-required checks"):
                store.clear_current_gate("discovery-run", missing_surface_check)
            workflow = store.clear_current_gate("discovery-run", evidence)
            state = store.load("discovery-run")

            self.assertEqual(workflow["currentStage"], "tasks")
            self.assertEqual(state["plan"]["phase"], "execution")
            self.assertEqual(list(state["tasks"]), ["implementation"])
            self.assertNotEqual(state["discoveryPlanHash"], state["executionPlanHash"])
            modes = {repo["id"]: repo["mode"] for repo in state["plan"]["repositories"]}
            self.assertEqual(modes, {"service-a": "read-only", "service-b": "read-write"})
            sponsor_record = next(
                repo for repo in state["plan"]["repositories"] if repo["id"] == "service-a"
            )
            self.assertEqual(sponsor_record["context"]["revision"], planning_revision)

            from signal_harness.session import acknowledge_task_context

            with self.assertRaisesRegex(ResolutionError, "context acknowledgement"):
                prepare_task(store, "discovery-run", "implementation")
            acknowledgement = acknowledge_task_context(
                store,
                "discovery-run",
                "implementation",
                ("service-b:AGENTS.md",),
            )
            self.assertTrue(acknowledgement["complete"])
            prepare_task(store, "discovery-run", "implementation")
            for phase, status in (("red", "fail"), ("green", "pass"), ("refactor", "pass")):
                record_tdd_phase(
                    store,
                    "discovery-run",
                    "implementation",
                    "case-1",
                    phase,
                    status=status,
                    failure_kind="assertion" if phase == "red" else None,
                    signal=f"signal://case-1/{phase}",
                )
            evidence = recorded_tdd_evidence(store.load("discovery-run"), "implementation")
            with self.assertRaisesRegex(ResolutionError, "required checks"):
                store.clear_current_gate("discovery-run", evidence, task_id="implementation")

    def test_stage_one_rejects_unsubstantiated_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            sponsor = RepositoryFixture(parent, "service-a")
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "service-a": CatalogRepository(
                        id="service-a", remote="example-org/service-a", author=True
                    )
                },
            )
            feature = sponsor.root / "specs" / "area" / "sdd" / "new-feature"
            feature.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "new-feature",
                    "sources": ["specs/area/sdd/new-feature/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            run("git", "add", ".", cwd=sponsor.root)
            run("git", "commit", "-m", "feature seed", cwd=sponsor.root)
            with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
                plan = build_sdd_plan(feature="new-feature", current_path=sponsor.root)
            store = RunStore(parent / "state")
            store.create(plan, feature / "manifest.yaml", "discovery-run")
            evidence = merged_planning_evidence()
            evidence["specification"]["interfaceImpact"]["evaluatedRepositories"] = ["service-a"]
            evidence["specification"]["affectedSurfaces"] = [
                evidence["specification"]["affectedSurfaces"][0]
            ]
            evidence["taskPlan"]["surfaceCoverage"] = [
                {"surface": "producer-behavior", "disposition": "task", "task": "implementation"}
            ]
            evidence["taskPlan"]["tasks"] = [
                {
                    "id": "implementation",
                    "repo": "service-a",
                    "requirement": "implement behavior",
                    "dependsOn": [],
                    "surfaces": ["producer-behavior"],
                    "testCases": [{"id": "case-1", "kind": "delta", "requirement": "behavior"}],
                    "requiredChecks": ["unit"],
                    "context": [{"repo": "service-a", "path": "AGENTS.md"}],
                }
            ]
            store.clear_current_gate("discovery-run", evidence)
            with self.assertRaisesRegex(ConfigurationError, "ownership reason"):
                store.clear_current_gate("discovery-run", evidence)

    def test_stage_one_audits_denied_authoring_with_actor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            sponsor = RepositoryFixture(parent, "service-a")
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "service-a": CatalogRepository(
                        id="service-a",
                        remote="example-org/service-a",
                        author=False,
                    )
                },
            )
            feature = sponsor.root / "specs" / "area" / "sdd" / "denied-feature"
            feature.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "denied-feature",
                    "sources": ["specs/area/sdd/denied-feature/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            run("git", "add", ".", cwd=sponsor.root)
            run("git", "commit", "-m", "feature seed", cwd=sponsor.root)
            with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
                plan = build_sdd_plan(feature="denied-feature", current_path=sponsor.root)
            store = RunStore(parent / "state")
            store.create(plan, feature / "manifest.yaml", "denied-run")
            evidence = merged_planning_evidence()
            evidence["specification"]["interfaceImpact"]["evaluatedRepositories"] = ["service-a"]
            evidence["specification"]["affectedSurfaces"] = [
                evidence["specification"]["affectedSurfaces"][0]
            ]
            evidence["taskPlan"]["surfaceCoverage"] = [
                {"surface": "producer-behavior", "disposition": "task", "task": "implementation"}
            ]
            evidence["taskPlan"]["tasks"] = [
                {
                    "id": "implementation",
                    "repo": "service-a",
                    "requirement": "implement behavior",
                    "dependsOn": [],
                    "surfaces": ["producer-behavior"],
                    "testCases": [{"id": "case-1", "kind": "delta", "requirement": "behavior"}],
                    "requiredChecks": ["unit"],
                    "context": [{"repo": "service-a", "path": "AGENTS.md"}],
                    "ownership": {
                        "reason": "service-a owns the implementation",
                        "evidence": [{"repo": "service-a", "path": "AGENTS.md"}],
                    },
                }
            ]
            store.clear_current_gate("denied-run", evidence, actor="reviewer@example.com")
            before_denial = store.load("denied-run")
            (feature / "source.md").write_text("approved planning update\n", encoding="utf-8")
            run("git", "add", ".", cwd=sponsor.root)
            run("git", "commit", "-m", "approved planning documents", cwd=sponsor.root)

            with self.assertRaisesRegex(ConfigurationError, "non-authorable repository"):
                store.clear_current_gate(
                    "denied-run",
                    evidence,
                    actor="reviewer@example.com",
                )

            after_denial = store.load("denied-run")
            self.assertEqual(after_denial["plan"], before_denial["plan"])
            self.assertEqual(after_denial["planHash"], before_denial["planHash"])
            denial = after_denial["events"][-1]
            self.assertEqual(denial["type"], "security.authorization.denied")
            self.assertEqual(denial["detail"]["actor"], "reviewer@example.com")
            self.assertEqual(denial["detail"]["repository"], "service-a")
            self.assertEqual(denial["detail"]["task"], "implementation")

    def test_standalone_catalog_authorizes_stage_one_task_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "service-a"
            root.mkdir()
            (root / "AGENTS.md").write_text("Standalone rules\n", encoding="utf-8")
            (root / "pyproject.toml").write_text(
                "[project]\nname = 'service-a'\nversion = '0.1.0'\n", encoding="utf-8"
            )
            from signal_harness.bootstrap import scaffold_standalone

            scaffold_standalone(root, "service-a", "example-org/service-a", detect_commands=True)
            run("git", "init", "-b", "main", cwd=root)
            run("git", "config", "user.email", "test@example.com", cwd=root)
            run("git", "config", "user.name", "Test", cwd=root)
            run("git", "remote", "add", "origin", "example-org/service-a", cwd=root)
            catalog = load_policy_catalog(root / ".signal-harness/policy")
            feature = root / "specs" / "area" / "sdd" / "denied-feature"
            feature.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "denied-feature",
                    "sources": ["specs/area/sdd/denied-feature/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            run("git", "add", ".", cwd=root)
            run("git", "commit", "-m", "feature seed", cwd=root)
            with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
                plan = build_sdd_plan(feature="denied-feature", current_path=root)
            store = RunStore(parent / "state")
            store.create(plan, feature / "manifest.yaml", "denied-run")
            evidence = merged_planning_evidence()
            evidence["specification"]["interfaceImpact"]["evaluatedRepositories"] = ["service-a"]
            evidence["specification"]["affectedSurfaces"] = [
                evidence["specification"]["affectedSurfaces"][0]
            ]
            evidence["taskPlan"]["surfaceCoverage"] = [
                {"surface": "producer-behavior", "disposition": "task", "task": "implementation"}
            ]
            evidence["taskPlan"]["tasks"] = [
                {
                    "id": "implementation",
                    "repo": "service-a",
                    "requirement": "implement behavior",
                    "dependsOn": [],
                    "surfaces": ["producer-behavior"],
                    "testCases": [{"id": "case-1", "kind": "delta", "requirement": "behavior"}],
                    "requiredChecks": ["unit"],
                    "context": [{"repo": "service-a", "path": "AGENTS.md"}],
                    "ownership": {
                        "reason": "service-a owns the implementation",
                        "evidence": [{"repo": "service-a", "path": "AGENTS.md"}],
                    },
                }
            ]
            store.clear_current_gate("denied-run", evidence, actor="reviewer@example.com")

            store.clear_current_gate("denied-run", evidence, actor="reviewer@example.com")
            state = store.load("denied-run")
            self.assertEqual(state["plan"]["phase"], "execution")
            self.assertEqual(state["tasks"]["implementation"]["repo"], "service-a")
            self.assertTrue(state["plan"]["repositories"][0]["author"])

    def test_stage_one_relock_rejects_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            sponsor = RepositoryFixture(parent, "service-a")
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "service-a": CatalogRepository(id="service-a", remote="example-org/service-a")
                },
            )
            feature = sponsor.root / "specs" / "area" / "sdd" / "new-feature"
            feature.mkdir(parents=True)
            (feature / "source.md").write_text("source\n", encoding="utf-8")
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "new-feature",
                    "sources": ["specs/area/sdd/new-feature/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            run("git", "add", ".", cwd=sponsor.root)
            run("git", "commit", "-m", "feature seed", cwd=sponsor.root)
            with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
                plan = build_sdd_plan(feature="new-feature", current_path=sponsor.root)
            store = RunStore(parent / "state")
            store.create(plan, feature / "manifest.yaml", "discovery-run")
            evidence = merged_planning_evidence()
            evidence["specification"]["interfaceImpact"]["evaluatedRepositories"] = ["service-a"]
            evidence["specification"]["affectedSurfaces"] = [
                evidence["specification"]["affectedSurfaces"][0]
            ]
            store.clear_current_gate("discovery-run", evidence)

            (sponsor.root / "application.py").write_text("changed = True\n", encoding="utf-8")
            run("git", "add", ".", cwd=sponsor.root)
            run("git", "commit", "-m", "unexpected source change", cwd=sponsor.root)
            with self.assertRaisesRegex(ResolutionError, "outside approved planning documents"):
                store.clear_current_gate("discovery-run", evidence)


if __name__ == "__main__":
    unittest.main()
