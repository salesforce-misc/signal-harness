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
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import yaml

from signal_harness.control import cancel_run
from signal_harness.errors import ConfigurationError, ResolutionError
from signal_harness.findings import record_finding
from signal_harness.github import (
    open_pull_request,
    publish_branch,
    refresh_pull_request,
    resume_from_pull_request,
)
from signal_harness.lifecycle import validate_gate_evidence
from signal_harness.planner import build_plan
from signal_harness.session import context_bundle, next_action, record_artifact
from signal_harness.signals import EngineeringSignal
from signal_harness.state import SAFE_ID, RunStore, new_run_id
from signal_harness.task_cards import discover_task_cards
from signal_harness.tdd import record_tdd_phase, recorded_tdd_evidence
from signal_harness.verification import run_verification
from signal_harness.workflow import WorkflowDefinition, load_workflow_reference
from signal_harness.worktrees import cleanup_task, prepare_task

EXAMPLE_WORKFLOW = str((Path(__file__).resolve().parent / "fixtures/policy/sdd.yaml").resolve())


class RunIdTest(unittest.TestCase):
    def test_generated_run_id_satisfies_store_validator(self) -> None:
        run_id = new_run_id("Admin Profile Cache")

        self.assertRegex(run_id, SAFE_ID)
        self.assertTrue(run_id.startswith("admin-profile-cache-"))


def run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


class RuntimeFixture:
    def __init__(self, base: Path, remote: str = "example-org/service-a", forge: str | None = None):
        self.root = base / "service-a"
        self.root.mkdir()
        self.remote = remote
        run("git", "init", "-b", "main", cwd=self.root)
        run("git", "config", "user.email", "test@example.com", cwd=self.root)
        run("git", "config", "user.name", "Signal Harness Test", cwd=self.root)
        origin = remote if ":" in remote else f"git@github.com:{remote}.git"
        run("git", "remote", "add", "origin", origin, cwd=self.root)
        (self.root / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
        write_yaml(
            self.root / ".signal-harness" / "repo.yaml",
            {
                "apiVersion": "signal-harness/v1alpha1",
                "kind": "RepositoryProfile",
                "repo": {
                    "id": "service-a",
                    "remote": self.remote,
                    "defaultBranch": "main",
                    **({"forge": forge} if forge else {}),
                },
                "context": {"required": ["AGENTS.md"]},
                "commands": {
                    "unit": f"{sys.executable} -c \"print('verification passed')\"",
                    "fail": f'{sys.executable} -c "raise SystemExit(7)"',
                },
                "signals": {
                    "sources": [{"id": "unit-tests", "kind": "test", "commandRef": "unit"}]
                },
            },
        )
        run("git", "add", ".", cwd=self.root)
        run("git", "commit", "-m", "fixture", cwd=self.root)
        self.workspace = base / "workspace.yaml"
        (base / "sdd.yaml").write_text(
            Path(EXAMPLE_WORKFLOW).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        write_yaml(
            self.workspace,
            {
                "apiVersion": "signal-harness/v1alpha1",
                "kind": "Workspace",
                "feature": "agent-driven-runtime",
                "sponsor": "service-a",
                "workflow": "sdd.yaml",
                "repositories": {
                    "service-a": {
                        "remote": self.remote,
                        "mode": "read-write",
                        "author": True,
                    }
                },
                "tasks": [{"id": "implementation", "repo": "service-a"}],
            },
        )
        self.store = RunStore(base / "state")
        self.store.create(build_plan(self.workspace, self.root), self.workspace, "runtime-run")

    def enter_task_phase(self, test_cases: list[str] | None = None) -> None:
        case_ids = test_cases or ["case-1"]
        evidence = {
            "state": "MERGED",
            "reviewDecision": "APPROVED",
            "url": "https://github.example/spec/pull/1",
            "specification": {
                "artifact": "artifact://spec/1",
                "scope": "fixture scope",
                "behavior": "fixture behavior",
                "errorHandling": "fail closed",
                "repositoryBoundaries": ["service-a"],
                "interfaceImpact": {
                    "evaluatedRepositories": ["service-a"],
                    "affectedInterfaces": [],
                    "summary": "No cross-repository interface impact.",
                },
                "interfaceDecisions": [],
                "affectedSurfaces": [
                    {
                        "id": "service-behavior",
                        "kind": "behavior",
                        "ownerRepo": "service-a",
                        "evidence": [{"repo": "service-a", "path": "AGENTS.md"}],
                    }
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
                        "id": "implementation",
                        "repo": "service-a",
                        "requirement": "implement behavior",
                        "dependsOn": [],
                        "surfaces": ["service-behavior"],
                        "testCases": [
                            {
                                "id": case_id,
                                "kind": "delta",
                                "requirement": f"behavior for {case_id}",
                            }
                            for case_id in case_ids
                        ],
                    }
                ],
                "surfaceCoverage": [
                    {
                        "surface": "service-behavior",
                        "disposition": "task",
                        "task": "implementation",
                    }
                ],
            },
        }
        self.store.clear_current_gate("runtime-run", evidence)
        self.store.clear_current_gate("runtime-run", evidence)

    def complete_tdd(self) -> dict:
        record_tdd_phase(
            self.store,
            "runtime-run",
            "implementation",
            "case-1",
            "red",
            status="fail",
            failure_kind="assertion",
            signal="signal://unit/red",
        )
        record_tdd_phase(
            self.store,
            "runtime-run",
            "implementation",
            "case-1",
            "green",
            status="pass",
            signal="signal://unit/green",
        )
        record_tdd_phase(
            self.store,
            "runtime-run",
            "implementation",
            "case-1",
            "refactor",
            status="pass",
            signal="signal://unit/refactor",
        )
        return recorded_tdd_evidence(self.store.load("runtime-run"), "implementation")


def valid_tdd_evidence() -> dict:
    return {
        "status": "pass",
        "signals": ["signal://unit/green"],
        "testCases": [
            {
                "id": "case-1",
                "requirement": "The new behavior is observable",
                "kind": "delta",
                "lifecycle": [
                    {
                        "phase": "red",
                        "status": "fail",
                        "failureKind": "assertion",
                        "signal": "signal://unit/red",
                    },
                    {"phase": "green", "status": "pass", "signal": "signal://unit/green"},
                    {
                        "phase": "refactor",
                        "status": "pass",
                        "signal": "signal://unit/refactor",
                    },
                ],
            }
        ],
    }


class AgentDrivenRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = RuntimeFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_next_action_scopes_context_and_write_target(self) -> None:
        first = next_action(self.fixture.store, "runtime-run")
        self.assertEqual(first["action"], "complete-stage")
        self.assertEqual(first["stage"]["id"], "0")
        self.assertEqual(first["permissions"]["write"], [])

        self.fixture.enter_task_phase()
        task_action = next_action(self.fixture.store, "runtime-run", "implementation")
        self.assertEqual(task_action["action"], "prepare")
        prepared = prepare_task(self.fixture.store, "runtime-run", "implementation")
        implement = next_action(self.fixture.store, "runtime-run", "implementation")
        self.assertEqual(implement["action"], "implement")
        self.assertEqual(implement["worktree"], prepared["worktree"])
        self.assertEqual(implement["permissions"]["write"], [prepared["worktree"]])
        expected_reads = {
            str(Path(item["root"]) / item["path"]) for item in implement["context"]["files"]
        }
        self.assertEqual(
            set(implement["permissions"]["read"]),
            expected_reads,
        )

        bundle = context_bundle(self.fixture.store, "runtime-run", "implementation")
        self.assertEqual(bundle["writeTarget"]["repo"], "service-a")
        self.assertTrue(any(item["path"] == "AGENTS.md" for item in bundle["files"]))

    def test_records_artifacts_and_verification_signals(self) -> None:
        self.fixture.enter_task_phase()
        prepare_task(self.fixture.store, "runtime-run", "implementation")

        artifact = record_artifact(
            self.fixture.store,
            "runtime-run",
            kind="decision",
            reference="artifact://decision/1",
            task_id="implementation",
        )
        signals = run_verification(
            self.fixture.store, "runtime-run", "implementation", ("unit",), timeout=10
        )

        self.assertEqual(artifact["reference"], "artifact://decision/1")
        self.assertEqual(signals[0]["status"], "pass")
        self.assertEqual(signals[0]["metadata"]["commandId"], "unit")
        self.assertEqual(EngineeringSignal.from_dict(signals[0]).status, "pass")
        repeated = run_verification(
            self.fixture.store, "runtime-run", "implementation", ("unit",), timeout=10
        )
        self.assertNotEqual(signals[0]["id"], repeated[0]["id"])
        self.assertTrue(repeated[0]["id"].endswith(":2"))
        saved = self.fixture.store.load("runtime-run")
        self.assertEqual(saved["artifacts"][0]["task"], "implementation")
        self.assertEqual(saved["tasks"]["implementation"]["signals"][0]["status"], "pass")

    def test_baseline_delta_verification_accepts_only_preexisting_pmd_findings(self) -> None:
        self.fixture.enter_task_phase()
        prepared = prepare_task(self.fixture.store, "runtime-run", "implementation")

        def pmd_runner(command, *, cwd, **_kwargs):
            root = Path(cwd)
            report = root / "build/reports/pmd/main.xml"
            report.parent.mkdir(parents=True, exist_ok=True)
            findings = [
                ("src/main/java/example/Legacy.java", "10", "CloseResource"),
            ]
            if root == Path(prepared["worktree"]):
                findings.append(
                    ("src/main/java/example/NewCode.java", "20", "AvoidDuplicateLiterals")
                )
            files = "".join(
                f'<file name="{root / path}"><violation beginline="{line}" '
                f'endline="{line}" begincolumn="1" endcolumn="2" rule="{rule}" '
                'ruleset="fixture" priority="3">finding</violation></file>'
                for path, line, rule in findings
            )
            report.write_text(
                '<?xml version="1.0"?><pmd xmlns="http://pmd.sourceforge.net/report/2.0.0">'
                f"{files}</pmd>",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 1, "PMD findings", "")

        failed = run_verification(
            self.fixture.store,
            "runtime-run",
            "implementation",
            ("unit",),
            timeout=10,
            runner=pmd_runner,
            baseline_reports=("build/reports/pmd/main.xml", "build/reports/pmd/test.xml"),
            report_format="pmd",
        )
        self.assertEqual(failed[0]["status"], "fail")
        self.assertEqual(failed[0]["metadata"]["baselineDelta"]["newFindingCount"], 1)

        def unchanged_runner(command, *, cwd, **_kwargs):
            root = Path(cwd)
            report = root / "build/reports/pmd/main.xml"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                '<?xml version="1.0"?><pmd xmlns="http://pmd.sourceforge.net/report/2.0.0">'
                f'<file name="{root / "src/main/java/example/Legacy.java"}">'
                '<violation beginline="10" endline="10" begincolumn="1" endcolumn="2" '
                'rule="CloseResource" ruleset="fixture" priority="3">finding</violation>'
                "</file></pmd>",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 1, "PMD findings", "")

        passed = run_verification(
            self.fixture.store,
            "runtime-run",
            "implementation",
            ("unit",),
            timeout=10,
            runner=unchanged_runner,
            baseline_reports=("build/reports/pmd/main.xml", "build/reports/pmd/test.xml"),
            report_format="pmd",
        )
        self.assertEqual(passed[0]["status"], "pass")
        self.assertEqual(passed[0]["source"], "repository-profile-baseline-delta")
        self.assertEqual(passed[0]["metadata"]["baselineDelta"]["newFindingCount"], 0)
        self.assertEqual(passed[0]["metadata"]["baselineDelta"]["baselineFindingCount"], 1)
        self.assertEqual(
            passed[0]["metadata"]["baselineDelta"]["symmetricallyAbsentReports"],
            ["build/reports/pmd/test.xml"],
        )

        def asymmetric_runner(command, *, cwd, **_kwargs):
            result = unchanged_runner(command, cwd=cwd)
            root = Path(cwd)
            if root == Path(prepared["worktree"]):
                test_report = root / "build/reports/pmd/test.xml"
                test_report.write_text(
                    '<?xml version="1.0"?><pmd xmlns="http://pmd.sourceforge.net/report/2.0.0"/>',
                    encoding="utf-8",
                )
            return result

        asymmetric = run_verification(
            self.fixture.store,
            "runtime-run",
            "implementation",
            ("unit",),
            timeout=10,
            runner=asymmetric_runner,
            baseline_reports=("build/reports/pmd/main.xml", "build/reports/pmd/test.xml"),
            report_format="pmd",
        )
        self.assertEqual(asymmetric[0]["status"], "fail")
        self.assertEqual(
            asymmetric[0]["metadata"]["baselineDelta"]["asymmetricReports"],
            ["build/reports/pmd/test.xml"],
        )

    def test_verification_rejects_locked_context_drift(self) -> None:
        self.fixture.enter_task_phase()
        prepare_task(self.fixture.store, "runtime-run", "implementation")
        (self.fixture.root / "AGENTS.md").write_text("changed after planning\n", encoding="utf-8")
        with self.assertRaisesRegex(ResolutionError, "became dirty"):
            run_verification(
                self.fixture.store, "runtime-run", "implementation", ("unit",), timeout=10
            )

    def test_tdd_runtime_enforces_the_active_case_and_phase(self) -> None:
        self.fixture.enter_task_phase(["case-1", "case-2"])
        action = next_action(self.fixture.store, "runtime-run", "implementation")
        self.assertEqual(action["tdd"]["activeCase"]["id"], "case-1")
        self.assertEqual(action["tdd"]["activeCase"]["currentPhase"], "red")
        with self.assertRaisesRegex(ResolutionError, "expects red"):
            record_tdd_phase(
                self.fixture.store,
                "runtime-run",
                "implementation",
                "case-1",
                "green",
                status="pass",
                signal="signal://unit/green",
            )
        with self.assertRaisesRegex(ResolutionError, "next TDD case is case-1"):
            record_tdd_phase(
                self.fixture.store,
                "runtime-run",
                "implementation",
                "case-2",
                "red",
                status="fail",
                failure_kind="assertion",
                signal="signal://case-2/red",
            )
        with self.assertRaisesRegex(ResolutionError, "incomplete TDD"):
            self.fixture.store.clear_current_gate(
                "runtime-run", valid_tdd_evidence(), "implementation"
            )
        for case_id in ("case-1", "case-2"):
            for phase, status in (("red", "fail"), ("green", "pass"), ("refactor", "pass")):
                record_tdd_phase(
                    self.fixture.store,
                    "runtime-run",
                    "implementation",
                    case_id,
                    phase,
                    status=status,
                    failure_kind="assertion" if phase == "red" else None,
                    signal=f"signal://{case_id}/{phase}",
                )

            if case_id == "case-1":
                current = next_action(self.fixture.store, "runtime-run", "implementation")
                self.assertEqual(current["tdd"]["activeCase"]["id"], "case-2")
                with self.assertRaisesRegex(ResolutionError, "incomplete TDD"):
                    recorded_tdd_evidence(self.fixture.store.load("runtime-run"), "implementation")
        evidence = recorded_tdd_evidence(self.fixture.store.load("runtime-run"), "implementation")
        self.fixture.store.clear_current_gate("runtime-run", evidence, "implementation")

    def test_tdd_waiver_closes_pending_case_state_without_marking_it_passed(self) -> None:
        self.fixture.enter_task_phase()
        waiver = {
            "outcome": "waived",
            "waiver": {
                "reason": "dependency service is unavailable in the evaluation environment",
                "authorizedBy": "evaluation-owner@example.com",
                "scope": "task implementation verification",
            },
        }

        self.fixture.store.clear_current_gate("runtime-run", waiver, task_id="implementation")
        state = self.fixture.store.load("runtime-run")
        task = state["workflow"]["tasks"]["implementation"]

        self.assertEqual(task["stages"]["2"]["status"], "waived")
        self.assertEqual(task["tdd"]["status"], "waived")
        self.assertIsNone(task["tdd"]["activeCase"])
        self.assertEqual(task["tdd"]["cases"][0]["status"], "waived")
        self.assertEqual(task["currentStage"], "3")

    def test_tdd_waiver_preserves_completed_lifecycle_when_checks_are_unavailable(self) -> None:
        self.fixture.enter_task_phase()
        self.fixture.complete_tdd()
        waiver = {
            "outcome": "waived",
            "waiver": {
                "reason": "Docker integration checks are unavailable",
                "authorizedBy": "evaluation-owner@example.com",
                "scope": "required integration check only",
            },
        }

        self.fixture.store.clear_current_gate("runtime-run", waiver, task_id="implementation")
        state = self.fixture.store.load("runtime-run")
        task = state["workflow"]["tasks"]["implementation"]

        self.assertEqual(task["stages"]["2"]["status"], "waived")
        self.assertEqual(task["tdd"]["status"], "complete")
        self.assertEqual(task["tdd"]["cases"][0]["status"], "complete")
        self.assertEqual(task["currentStage"], "3")

    def test_cleanup_is_owned_terminal_and_replay_safe(self) -> None:
        self.fixture.enter_task_phase()
        task = prepare_task(self.fixture.store, "runtime-run", "implementation")
        branch = task["branch"]
        with self.assertRaisesRegex(ResolutionError, "terminal run or completed task"):
            cleanup_task(self.fixture.store, "runtime-run", "implementation")
        cancel_run(
            self.fixture.store,
            "runtime-run",
            actor="operator@example.com",
            reason="test cleanup",
        )
        cleanup = cleanup_task(self.fixture.store, "runtime-run", "implementation")
        replay = cleanup_task(self.fixture.store, "runtime-run", "implementation")
        self.assertEqual(cleanup, replay)
        self.assertFalse(Path(cleanup["path"]).exists())
        self.assertEqual(cleanup["branchRetained"], branch)

    def test_cleanup_refuses_dirty_worktree(self) -> None:
        self.fixture.enter_task_phase()
        task = prepare_task(self.fixture.store, "runtime-run", "implementation")
        (Path(task["worktree"]) / "uncommitted.txt").write_text("keep me\n", encoding="utf-8")
        cancel_run(
            self.fixture.store,
            "runtime-run",
            actor="operator@example.com",
            reason="test dirty refusal",
        )
        with self.assertRaisesRegex(ResolutionError, "dirty worktree"):
            cleanup_task(self.fixture.store, "runtime-run", "implementation")


class EvidenceContractTests(unittest.TestCase):
    def test_task_plan_rejects_string_test_cases_before_materialization(self) -> None:
        stage = load_workflow_reference(EXAMPLE_WORKFLOW, Path.cwd()).stages[1]
        evidence = {
            "state": "MERGED",
            "reviewDecision": "APPROVED",
            "url": "local://task-plan",
            "taskPlan": {
                "artifact": "artifact://task-plan/1",
                "tasks": [
                    {
                        "id": "implementation",
                        "repo": "service-a",
                        "requirement": "implement behavior",
                        "dependsOn": [],
                        "surfaces": ["service-behavior"],
                        "testCases": ["case-1"],
                    }
                ],
                "surfaceCoverage": [
                    {
                        "surface": "service-behavior",
                        "disposition": "task",
                        "task": "implementation",
                    }
                ],
            },
        }

        with self.assertRaisesRegex(ConfigurationError, "must be an object"):
            validate_gate_evidence(stage, evidence)

    def test_waivers_are_auditable_and_cannot_replace_pr_approval(self) -> None:
        workflow = load_workflow_reference(EXAMPLE_WORKFLOW, Path.cwd())
        waiver = {
            "outcome": "waived",
            "waiver": {
                "reason": "test environment is unavailable",
                "authorizedBy": "release-owner@example.com",
                "scope": "integration verification for this evaluation run",
            },
        }
        validate_gate_evidence(workflow.stages[4], waiver)
        with self.assertRaisesRegex(ConfigurationError, "PR gate cannot be waived"):
            validate_gate_evidence(workflow.stages[0], waiver)
        del waiver["waiver"]["authorizedBy"]
        with self.assertRaisesRegex(ConfigurationError, "authorizedBy"):
            validate_gate_evidence(workflow.stages[4], waiver)

    def test_production_mode_rejects_waivers_and_local_pr_evidence(self) -> None:
        workflow = load_workflow_reference(EXAMPLE_WORKFLOW, Path.cwd())
        waiver = {
            "outcome": "waived",
            "waiver": {
                "reason": "environment unavailable",
                "authorizedBy": "release-owner@example.com",
                "scope": "integration",
            },
        }
        with self.assertRaisesRegex(ConfigurationError, "production mode"):
            validate_gate_evidence(workflow.stages[4], waiver, policy_mode="production")

        evidence = {
            "state": "MERGED",
            "reviewDecision": "APPROVED",
            "url": "local://specification",
            "specification": {
                "artifact": "artifact://spec/1",
                "scope": "bounded change",
                "behavior": "observable behavior",
                "errorHandling": "fail closed",
                "repositoryBoundaries": ["service-a"],
                "interfaceImpact": {
                    "evaluatedRepositories": ["service-a"],
                    "affectedInterfaces": [],
                    "summary": "No interface impact.",
                },
                "interfaceDecisions": [],
                "affectedSurfaces": [
                    {
                        "id": "service-behavior",
                        "kind": "behavior",
                        "ownerRepo": "service-a",
                        "evidence": [{"repo": "service-a", "path": "AGENTS.md"}],
                    }
                ],
                "rollout": "guarded rollout",
                "decisions": ["bounded implementation"],
                "openQuestions": [],
                "sourceAudit": {
                    "consulted": ["service-a:AGENTS.md"],
                    "missing": [],
                    "delegationFindings": [],
                    "unresolvedDelegations": [],
                },
            },
        }
        with self.assertRaisesRegex(ConfigurationError, "hosted provider"):
            validate_gate_evidence(workflow.stages[0], evidence, policy_mode="production")

    def test_workflow_rejects_unsupported_evidence_contract(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "evidenceType"):
            WorkflowDefinition.from_dict(
                {
                    "apiVersion": "signal-harness/v1alpha1",
                    "kind": "Workflow",
                    "id": "invalid",
                    "version": "1",
                    "stages": [
                        {
                            "id": "0",
                            "name": "Invalid",
                            "objective": "Reject invalid evidence",
                            "activities": [],
                            "gate": {
                                "kind": "automatic",
                                "evidenceType": "unknown",
                                "clearsWhen": "never",
                            },
                        }
                    ],
                }
            )

    def test_specification_gate_requires_a_complete_decision_artifact(self) -> None:
        stage = load_workflow_reference(EXAMPLE_WORKFLOW, Path.cwd()).stages[0]
        evidence = {
            "state": "MERGED",
            "reviewDecision": "APPROVED",
            "url": "https://github.example/spec/pull/1",
        }
        with self.assertRaisesRegex(ConfigurationError, "specification"):
            validate_gate_evidence(stage, evidence)
        evidence["specification"] = {
            "artifact": "artifact://spec/1",
            "scope": "bounded change",
            "behavior": "observable behavior",
            "errorHandling": "fail closed",
            "repositoryBoundaries": ["service-a"],
            "interfaceImpact": {
                "evaluatedRepositories": ["service-a"],
                "affectedInterfaces": [],
                "summary": "No cross-repository interface impact.",
            },
            "interfaceDecisions": [],
            "affectedSurfaces": [
                {
                    "id": "service-behavior",
                    "kind": "behavior",
                    "ownerRepo": "service-a",
                    "evidence": [{"repo": "service-a", "path": "AGENTS.md"}],
                }
            ],
            "rollout": "guarded rollout",
            "decisions": ["use the versioned contract"],
            "openQuestions": [],
            "sourceAudit": {
                "consulted": ["service-a:AGENTS.md"],
                "missing": [],
                "delegationFindings": [],
                "unresolvedDelegations": [],
            },
        }
        validate_gate_evidence(stage, evidence)

        evidence["specification"]["sourceAudit"]["unresolvedDelegations"] = [
            "See another service for authorization behavior"
        ]
        with self.assertRaisesRegex(ConfigurationError, "unresolvedDelegations"):
            validate_gate_evidence(stage, evidence)

    def test_integration_and_smoke_gates_require_structured_evidence(self) -> None:
        workflow = load_workflow_reference(EXAMPLE_WORKFLOW, Path.cwd())
        human = {"verifiedBy": "engineer@example.com", "marker": "human verified"}
        with self.assertRaisesRegex(ConfigurationError, "integration"):
            validate_gate_evidence(workflow.stages[4], human)
        integration = {
            **human,
            "signals": ["signal://integration/1"],
            "repositories": ["service-a", "service-b"],
            "interfaces": ["event-v2"],
        }
        validate_gate_evidence(workflow.stages[4], integration)

        with self.assertRaisesRegex(ConfigurationError, "smoke"):
            validate_gate_evidence(workflow.stages[5], human)
        smoke = {
            **human,
            "scenarios": [
                {"kind": kind, "status": "pass", "evidence": f"signal://smoke/{kind}"}
                for kind in ("happy", "failure", "compatibility", "rollout")
            ],
        }
        validate_gate_evidence(workflow.stages[5], smoke)

    def test_tdd_gate_rejects_final_green_without_lifecycle(self) -> None:
        stage = load_workflow_reference(EXAMPLE_WORKFLOW, Path.cwd()).stages[2]
        with self.assertRaisesRegex(ConfigurationError, "testCases"):
            validate_gate_evidence(stage, {"status": "pass", "signals": ["green"]})

    def test_tdd_gate_rejects_environment_failure_as_red(self) -> None:
        stage = load_workflow_reference(EXAMPLE_WORKFLOW, Path.cwd()).stages[2]
        evidence = valid_tdd_evidence()
        evidence["testCases"][0]["lifecycle"][0]["failureKind"] = "environment"
        with self.assertRaisesRegex(ConfigurationError, "behavioral assertion"):
            validate_gate_evidence(stage, evidence)

    def test_tdd_gate_requires_guard_sensitivity_and_accepts_valid_delta(self) -> None:
        stage = load_workflow_reference(EXAMPLE_WORKFLOW, Path.cwd()).stages[2]
        validate_gate_evidence(stage, valid_tdd_evidence())

        guard = valid_tdd_evidence()
        guard["testCases"][0]["kind"] = "guard"
        with self.assertRaisesRegex(ConfigurationError, "sensitivity"):
            validate_gate_evidence(stage, guard)
        guard["testCases"][0]["sensitivity"] = {
            "mutation": "invert expected authorization",
            "status": "fail",
            "signal": "signal://unit/sensitivity",
        }
        validate_gate_evidence(stage, guard)

    def test_tdd_gate_rejects_placeholder_signal_and_sensitivity_values(self) -> None:
        stage = load_workflow_reference(EXAMPLE_WORKFLOW, Path.cwd()).stages[2]
        evidence = valid_tdd_evidence()
        evidence["testCases"][0]["lifecycle"][1]["signal"] = "undefined"
        with self.assertRaisesRegex(ConfigurationError, "signal evidence"):
            validate_gate_evidence(stage, evidence)

        guard = valid_tdd_evidence()
        guard["testCases"][0]["kind"] = "guard"
        guard["testCases"][0]["sensitivity"] = {
            "mutation": "invert expected authorization",
            "status": "fail",
            "signal": "undefined",
        }
        with self.assertRaisesRegex(ConfigurationError, "sensitivity"):
            validate_gate_evidence(stage, guard)

    def test_task_card_preserves_ordered_test_case_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_yaml(root / "specs/feature/manifest.yaml", {"feature": "fixture"})
            card = root / "specs/feature/tasks/01-change.md"
            write_yaml(
                card,
                {
                    "repo": "service-a",
                    "testCases": [
                        {
                            "id": "case-1",
                            "kind": "delta",
                            "requirement": "first",
                            "traces": ["literal:flag"],
                        },
                        {"id": "case-2", "kind": "guard", "requirement": "second"},
                    ],
                },
            )
            # write_yaml is valid YAML but task cards require frontmatter.
            metadata = card.read_text(encoding="utf-8")
            card.write_text(f"---\n{metadata}---\n# Change\n", encoding="utf-8")
            cards = discover_task_cards(
                root, "specs/feature/manifest.yaml", "service-a", lambda value: value
            )
            self.assertEqual([item["id"] for item in cards[0].test_cases], ["case-1", "case-2"])
            self.assertEqual(cards[0].test_cases[0]["traces"], ["literal:flag"])


class GitHubResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = RuntimeFixture(Path(self.temp.name))
        self.fixture.enter_task_phase()
        prepare_task(self.fixture.store, "runtime-run", "implementation")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _open_recorded_pr(self, number: int) -> dict:
        return open_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            "Implement behavior",
            runner=Mock(
                side_effect=[
                    subprocess.CompletedProcess([], 0, "[]\n", ""),
                    subprocess.CompletedProcess(
                        [],
                        0,
                        f"https://github.com/example-org/service-a/pull/{number}\n",
                        "",
                    ),
                ]
            ),
        )

    def test_pr_creation_is_idempotent_and_resume_advances_once(self) -> None:
        create_runner = Mock(
            side_effect=[
                subprocess.CompletedProcess([], 0, "[]\n", ""),
                subprocess.CompletedProcess(
                    [], 0, "https://github.com/example-org/service-a/pull/42\n", ""
                ),
            ]
        )
        created = open_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            "Implement behavior",
            runner=create_runner,
        )
        repeated = open_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            "Implement behavior",
            runner=Mock(side_effect=AssertionError("must not create twice")),
        )
        self.assertEqual(created["url"], repeated["url"])

        self.fixture.store.clear_current_gate(
            "runtime-run", self.fixture.complete_tdd(), "implementation"
        )
        refresh_runner = Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "url": created["url"],
                        "state": "MERGED",
                        "reviewDecision": "APPROVED",
                        "headRefName": created["head"],
                        "baseRefName": "main",
                        "mergedAt": "2026-10-05T12:00:00Z",
                    }
                ),
                "",
            )
        )
        resumed = resume_from_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            runner=refresh_runner,
        )
        replay = resume_from_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            runner=refresh_runner,
        )

        self.assertTrue(resumed["advanced"])
        self.assertFalse(replay["advanced"])
        state = self.fixture.store.load("runtime-run")
        self.assertEqual(state["workflow"]["currentStage"], "4")

    def test_existing_pull_request_must_match_the_routed_base(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.example/service-a/pull/1",
                            "state": "OPEN",
                            "reviewDecision": "",
                            "headRefName": self.fixture.store.load("runtime-run")["tasks"][
                                "implementation"
                            ]["branch"],
                            "baseRefName": "release",
                        }
                    ]
                ),
                "",
            )
        )
        with self.assertRaisesRegex(ResolutionError, "base"):
            open_pull_request(
                self.fixture.store,
                "runtime-run",
                "implementation",
                "Implement behavior",
                runner=runner,
            )

    def test_closed_unmerged_pull_request_has_an_actionable_recovery_reason(self) -> None:
        created = open_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            "Implement behavior",
            runner=Mock(
                side_effect=[
                    subprocess.CompletedProcess([], 0, "[]\n", ""),
                    subprocess.CompletedProcess(
                        [],
                        0,
                        "https://github.com/example-org/service-a/pull/43\n",
                        "",
                    ),
                ]
            ),
        )
        self.fixture.store.clear_current_gate(
            "runtime-run", self.fixture.complete_tdd(), "implementation"
        )
        result = resume_from_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            runner=Mock(
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        {
                            "url": created["url"],
                            "state": "CLOSED",
                            "reviewDecision": "APPROVED",
                            "headRefName": created["head"],
                            "baseRefName": "main",
                            "mergedAt": None,
                        }
                    ),
                    "",
                )
            ),
        )

        self.assertFalse(result["advanced"])
        self.assertEqual(result["reason"], "closed-unmerged")

    def test_failed_ci_is_normalized_and_blocks_pr_gate_resume(self) -> None:
        created = open_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            "Implement behavior",
            runner=Mock(
                side_effect=[
                    subprocess.CompletedProcess([], 0, "[]\n", ""),
                    subprocess.CompletedProcess(
                        [],
                        0,
                        "https://github.com/example-org/service-a/pull/44\n",
                        "",
                    ),
                ]
            ),
        )
        self.fixture.store.clear_current_gate(
            "runtime-run", self.fixture.complete_tdd(), "implementation"
        )
        result = resume_from_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            runner=Mock(
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        {
                            "url": created["url"],
                            "state": "OPEN",
                            "reviewDecision": "APPROVED",
                            "headRefName": created["head"],
                            "baseRefName": "main",
                            "mergedAt": None,
                            "statusCheckRollup": [
                                {
                                    "name": "unit",
                                    "status": "COMPLETED",
                                    "conclusion": "FAILURE",
                                    "detailsUrl": "https://ci.example/unit",
                                }
                            ],
                        }
                    ),
                    "",
                )
            ),
        )

        self.assertFalse(result["advanced"])
        self.assertEqual(result["reason"], "ci-failed")
        self.assertEqual(result["pullRequest"]["checks"][0]["status"], "fail")

    def test_failed_publish_redacts_diagnostics_without_marking_published(self) -> None:
        task = self.fixture.store.load("runtime-run")["tasks"]["implementation"]
        worktree = Path(task["worktree"])
        (worktree / "change.txt").write_text("implemented\n", encoding="utf-8")
        run("git", "add", "change.txt", cwd=worktree)
        run("git", "commit", "-m", "implement behavior", cwd=worktree)
        diagnostic = "denied https://user:url-credential@host/repo token=assignment-credential"
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                runner = Mock(
                    return_value=subprocess.CompletedProcess(
                        [],
                        1,
                        diagnostic if stream == "stdout" else "",
                        diagnostic if stream == "stderr" else "",
                    )
                )
                with self.assertRaises(ResolutionError) as raised:
                    publish_branch(
                        self.fixture.store, "runtime-run", "implementation", runner=runner
                    )
                self.assertIn("git push failed", str(raised.exception))
                self.assertIn("[REDACTED]", str(raised.exception))
                self.assertNotIn("url-credential", str(raised.exception))
                self.assertNotIn("assignment-credential", str(raised.exception))
                saved_task = self.fixture.store.load("runtime-run")["tasks"]["implementation"]
                self.assertNotIn("publishedHead", saved_task)

    def test_publish_requires_a_clean_session_commit_and_is_idempotent(self) -> None:
        task = self.fixture.store.load("runtime-run")["tasks"]["implementation"]
        worktree = Path(task["worktree"])
        (worktree / "change.txt").write_text("implemented\n", encoding="utf-8")
        run("git", "add", "change.txt", cwd=worktree)
        run("git", "commit", "-m", "implement behavior", cwd=worktree)
        push_runner = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))

        published = publish_branch(
            self.fixture.store, "runtime-run", "implementation", runner=push_runner
        )
        repeated = publish_branch(
            self.fixture.store,
            "runtime-run",
            "implementation",
            runner=Mock(side_effect=AssertionError("must not push twice")),
        )

        self.assertEqual(published, repeated)
        self.assertEqual(push_runner.call_count, 1)

    def test_refresh_ingests_pr_comments_and_inline_reviews_as_findings(self) -> None:
        created = open_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            "Implement behavior",
            runner=Mock(
                side_effect=[
                    subprocess.CompletedProcess([], 0, "[]\n", ""),
                    subprocess.CompletedProcess(
                        [],
                        0,
                        "https://github.com/example-org/service-a/pull/45\n",
                        "",
                    ),
                ]
            ),
        )
        runner = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        {
                            "url": created["url"],
                            "state": "OPEN",
                            "reviewDecision": "CHANGES_REQUESTED",
                            "headRefName": created["head"],
                            "baseRefName": "main",
                            "comments": [
                                {
                                    "id": "C1",
                                    "body": "Please add a guard.",
                                    "url": "https://github.example/comment/1",
                                }
                            ],
                        }
                    ),
                    "",
                ),
                subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        {
                            "data": {
                                "repository": {
                                    "pullRequest": {
                                        "reviewThreads": {
                                            "nodes": [
                                                {
                                                    "id": "RT91",
                                                    "isResolved": False,
                                                    "comments": {
                                                        "nodes": [
                                                            {
                                                                "databaseId": 91,
                                                                "body": "Handle the null case.",
                                                                "url": "https://github.example/inline/91",
                                                            }
                                                        ]
                                                    },
                                                }
                                            ]
                                        }
                                    }
                                }
                            }
                        }
                    ),
                    "",
                ),
            ]
        )
        refresh_pull_request(self.fixture.store, "runtime-run", "implementation", runner=runner)
        findings = self.fixture.store.load("runtime-run")["findings"]
        self.assertIn("github:comment:C1", findings)
        self.assertIn("github:thread:RT91", findings)
        resolved_thread = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "RT91",
                                    "isResolved": True,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "databaseId": 91,
                                                "body": "Handle the null case.",
                                                "url": "https://github.example/inline/91",
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }
        refresh_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            runner=Mock(
                side_effect=[
                    subprocess.CompletedProcess(
                        [],
                        0,
                        json.dumps(
                            {
                                "url": created["url"],
                                "state": "OPEN",
                                "reviewDecision": "CHANGES_REQUESTED",
                                "headRefName": created["head"],
                                "baseRefName": "main",
                            }
                        ),
                        "",
                    ),
                    subprocess.CompletedProcess([], 0, json.dumps(resolved_thread), ""),
                ]
            ),
        )
        resolved = self.fixture.store.load("runtime-run")["findings"]["github:thread:RT91"]
        self.assertEqual(resolved["status"], "resolved")

    def test_deleted_branch_and_stale_base_have_specific_recovery_reasons(self) -> None:
        created = self._open_recorded_pr(46)
        self.fixture.store.clear_current_gate(
            "runtime-run", self.fixture.complete_tdd(), "implementation"
        )
        deleted = resume_from_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            runner=Mock(
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        {
                            "url": created["url"],
                            "state": "OPEN",
                            "reviewDecision": "",
                            "headRefName": "",
                            "baseRefName": "main",
                        }
                    ),
                    "",
                )
            ),
        )
        self.assertEqual(deleted["reason"], "branch-deleted")

        state = self.fixture.store.load("runtime-run")
        state["tasks"]["implementation"]["pullRequest"]["headDeleted"] = False
        self.fixture.store.save(state)
        stale = resume_from_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            runner=Mock(
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        {
                            "url": created["url"],
                            "state": "OPEN",
                            "reviewDecision": "APPROVED",
                            "headRefName": created["head"],
                            "baseRefName": "main",
                            "mergeStateStatus": "BEHIND",
                        }
                    ),
                    "",
                )
            ),
        )
        self.assertEqual(stale["reason"], "stale-base")

    def test_merged_pr_with_open_findings_reports_incomplete_evidence(self) -> None:
        created = self._open_recorded_pr(47)
        self.fixture.store.clear_current_gate(
            "runtime-run", self.fixture.complete_tdd(), "implementation"
        )
        record_finding(
            self.fixture.store,
            "runtime-run",
            "implementation",
            finding_id="review-open",
            source="github-inline-review",
            severity="high",
            summary="Unresolved behavioral concern",
            evidence="https://github.example/review/open",
        )
        result = resume_from_pull_request(
            self.fixture.store,
            "runtime-run",
            "implementation",
            runner=Mock(
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        {
                            "url": created["url"],
                            "state": "MERGED",
                            "reviewDecision": "APPROVED",
                            "headRefName": created["head"],
                            "baseRefName": "main",
                            "mergedAt": "2026-10-06T12:00:00Z",
                        }
                    ),
                    "",
                )
            ),
        )
        self.assertFalse(result["advanced"])
        self.assertEqual(result["reason"], "merged-incomplete-evidence")
        self.assertIn("finding review-open is open", result["blockers"])


if __name__ == "__main__":
    unittest.main()
