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

import tempfile
import unittest
from pathlib import Path

import yaml

from signal_harness.errors import ConfigurationError
from signal_harness.lifecycle import validate_gate_evidence
from signal_harness.state import RunStore
from signal_harness.workflow import WorkflowDefinition


def stage(evidence_type: str, requirements: dict) -> object:
    kind = "automatic" if evidence_type == "knowledge" else "pr"
    return WorkflowDefinition.from_dict(
        {
            "apiVersion": "signal-harness/v1alpha1",
            "kind": "Workflow",
            "id": "enforcement-test",
            "version": "1",
            "stages": [
                {
                    "id": "0",
                    "name": "Enforcement",
                    "objective": "Exercise opt-in evidence requirements.",
                    "activities": [],
                    "gate": {
                        "kind": kind,
                        "evidenceType": evidence_type,
                        "requirements": requirements,
                        "clearsWhen": "evidence is complete",
                    },
                }
            ],
        }
    ).stages[0]


def source(path: str = "src/main.py") -> list[dict[str, str]]:
    return [{"repo": "service-a", "path": path}]


def specification_evidence() -> dict:
    return {
        "state": "MERGED",
        "reviewDecision": "APPROVED",
        "url": "https://github.example/pull/1",
        "specification": {
            "artifact": "artifact://specification",
            "scope": "bounded feature",
            "behavior": "adds the requested behavior",
            "errorHandling": "preserves explicit failures",
            "repositoryBoundaries": ["service-a"],
            "interfaceImpact": {
                "evaluatedRepositories": ["service-a"],
                "affectedInterfaces": [],
                "summary": "No versioned interface changes.",
            },
            "interfaceDecisions": [],
            "affectedSurfaces": [
                {
                    "id": "service-behavior",
                    "kind": "behavior",
                    "ownerRepo": "service-a",
                    "evidence": source(),
                }
            ],
            "rollout": "feature flag",
            "decisions": ["preserve existing callers"],
            "openQuestions": [],
            "sourceAudit": {
                "consulted": ["service-a:spec.md"],
                "missing": [],
                "delegationFindings": [],
                "unresolvedDelegations": [],
            },
            "callerAudit": {
                "searches": ["rg 'shared_method' src"],
                "conclusion": "One caller was found and classified.",
                "inventory": [
                    {
                        "caller": "api-entrypoint",
                        "repo": "service-a",
                        "path": "src/controller.py",
                        "symbol": "fetch",
                        "evidence": source("src/controller.py"),
                    }
                ],
            },
            "callerMatrix": [
                {
                    "id": "api-entrypoint",
                    "repo": "service-a",
                    "path": "src/controller.py",
                    "symbol": "fetch",
                    "surface": "service-behavior",
                    "behavior": "calls the shared method",
                    "errorHandling": "returns a typed failure",
                    "observability": "logs the request id",
                    "evidence": source("src/controller.py"),
                }
            ],
            "literalAudit": {
                "searches": ["rg 'FeatureFlagName|X-Request-Id' specs src"],
                "literals": [
                    {
                        "id": "feature-flag",
                        "kind": "configuration",
                        "value": "FeatureFlagName",
                        "disposition": "preserved",
                        "evidence": source("spec.md"),
                    }
                ],
                "unaccounted": [],
                "conclusion": "Every named literal is frozen or superseded.",
            },
            "placementAudit": [
                {
                    "id": "shared-call-placement",
                    "repo": "service-a",
                    "path": "src/service.py",
                    "symbol": "shared_method",
                    "effect": "performs the new remote lookup",
                    "reusable": True,
                    "candidateCallers": ["api-entrypoint"],
                    "affectedCallers": ["api-entrypoint"],
                    "excludedCallers": [],
                    "rationale": "The only caller requires the new behavior.",
                    "evidence": source("src/service.py"),
                }
            ],
        },
    }


class EnforcementRequirementTests(unittest.TestCase):
    def test_profile_check_derivation_audits_every_available_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            state = {
                "plan": {
                    "repositories": [
                        {
                            "id": "service-a",
                            "profile": {
                                "commands": {
                                    "unit": "./gradlew test",
                                    "component": "./gradlew componentTest",
                                }
                            },
                        }
                    ]
                }
            }
            evidence = {
                "taskPlan": {
                    "checkDerivation": [
                        {
                            "task": "implementation",
                            "checks": [
                                {
                                    "id": "unit",
                                    "command": "./gradlew test",
                                    "executable": True,
                                    "disposition": "required",
                                    "sources": ["repository-profile"],
                                    "evidence": source(".signal-harness/repo.yaml"),
                                }
                            ],
                        }
                    ]
                }
            }
            tasks = [{"id": "implementation", "repo": "service-a"}]

            with self.assertRaisesRegex(ConfigurationError, "profile commands mismatch"):
                store._assert_profile_check_derivation(state, evidence, tasks)

    def test_manifest_closure_must_match_repository_file_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "specs" / "feature" / "manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                yaml.safe_dump({"feature": "fixture", "status": "stage-6"}),
                encoding="utf-8",
            )
            store = RunStore(root / "state")
            state = {
                "plan": {
                    "sponsor": "service-a",
                    "repositories": [
                        {
                            "id": "service-a",
                            "root": str(root),
                            "featureManifest": "specs/feature/manifest.yaml",
                        }
                    ],
                }
            }
            evidence = {
                "manifestClosure": {
                    "manifest": "specs/feature/manifest.yaml",
                    "status": "complete",
                    "evidence": ["git://manifest"],
                }
            }

            with self.assertRaisesRegex(ConfigurationError, "repository status=complete"):
                store._assert_manifest_closure(state, evidence)

    def test_specification_requirements_are_enforced_and_round_trip(self) -> None:
        workflow_stage = stage(
            "specification",
            {
                "callerMatrix": True,
                "literalAudit": True,
                "callerReconciliation": True,
                "placementAudit": True,
            },
        )
        self.assertEqual(
            workflow_stage.gate.requirements.as_dict(),
            {
                "callerMatrix": True,
                "literalAudit": True,
                "callerReconciliation": True,
                "placementAudit": True,
            },
        )
        evidence = specification_evidence()
        validate_gate_evidence(workflow_stage, evidence)

        del evidence["specification"]["literalAudit"]
        with self.assertRaisesRegex(ConfigurationError, "literalAudit"):
            validate_gate_evidence(workflow_stage, evidence)

    def test_caller_inventory_must_exactly_reconcile_with_matrix(self) -> None:
        workflow_stage = stage(
            "specification", {"callerMatrix": True, "callerReconciliation": True}
        )
        evidence = specification_evidence()
        evidence["specification"]["callerAudit"]["inventory"] = []

        with self.assertRaisesRegex(ConfigurationError, "caller inventory mismatch"):
            validate_gate_evidence(workflow_stage, evidence)

    def test_reusable_placement_must_classify_every_candidate_caller(self) -> None:
        workflow_stage = stage("specification", {"callerMatrix": True, "placementAudit": True})
        evidence = specification_evidence()
        evidence["specification"]["placementAudit"][0]["candidateCallers"].append(
            "unclassified-caller"
        )

        with self.assertRaisesRegex(ConfigurationError, "unknown caller"):
            validate_gate_evidence(workflow_stage, evidence)

    def test_task_plan_requires_check_derivation_and_scenario_coverage(self) -> None:
        workflow_stage = stage(
            "task-plan",
            {
                "checkDerivation": True,
                "testTraceability": True,
                "testCaseCategories": ["failure", "boundary", "observability"],
            },
        )
        evidence = {
            "state": "MERGED",
            "reviewDecision": "APPROVED",
            "url": "https://github.example/pull/2",
            "taskPlan": {
                "artifact": "artifact://task-plan",
                "tasks": [
                    {
                        "id": "implementation",
                        "repo": "service-a",
                        "requirement": "implement the behavior",
                        "dependsOn": [],
                        "surfaces": ["service-behavior"],
                        "requiredChecks": ["unit", "component"],
                        "testCases": [
                            {
                                "id": "failure",
                                "kind": "delta",
                                "requirement": "failure",
                                "traces": ["literal:flag", "surface:service-behavior"],
                            },
                            {
                                "id": "boundary",
                                "kind": "guard",
                                "requirement": "boundary",
                                "traces": ["caller:api-entrypoint"],
                            },
                            {
                                "id": "logging",
                                "kind": "guard",
                                "requirement": "log level and fields",
                                "traces": ["surface:service-behavior"],
                            },
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
                "checkDerivation": [
                    {
                        "task": "implementation",
                        "checks": [
                            {
                                "id": "unit",
                                "command": "./gradlew test",
                                "executable": True,
                                "disposition": "required",
                                "sources": ["repository-profile", "affected-surface"],
                                "evidence": source(".signal-harness/repo.yaml"),
                            },
                            {
                                "id": "component",
                                "command": "./gradlew componentTest",
                                "executable": True,
                                "disposition": "required",
                                "sources": ["changed-dependency"],
                                "evidence": source("build.gradle"),
                            },
                        ],
                    }
                ],
                "testCoverage": [
                    {
                        "task": "implementation",
                        "categories": [
                            {"id": "failure", "testCases": ["failure"]},
                            {"id": "boundary", "testCases": ["boundary"]},
                            {"id": "observability", "testCases": ["logging"]},
                        ],
                    }
                ],
            },
        }
        validate_gate_evidence(workflow_stage, evidence)

        missing_traces = {
            **evidence,
            "taskPlan": {
                **evidence["taskPlan"],
                "tasks": [
                    {
                        **evidence["taskPlan"]["tasks"][0],
                        "testCases": [
                            {
                                key: value
                                for key, value in evidence["taskPlan"]["tasks"][0]["testCases"][
                                    0
                                ].items()
                                if key != "traces"
                            },
                            *evidence["taskPlan"]["tasks"][0]["testCases"][1:],
                        ],
                    }
                ],
            },
        }
        with self.assertRaisesRegex(ConfigurationError, "requires traces"):
            validate_gate_evidence(workflow_stage, missing_traces)

        evidence["taskPlan"]["checkDerivation"][0]["checks"][1]["disposition"] = "not-applicable"
        with self.assertRaisesRegex(ConfigurationError, "locally executable"):
            validate_gate_evidence(workflow_stage, evidence)

    def test_traceability_must_cover_every_stage_zero_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            state = {
                "workflow": {
                    "stages": {
                        "0": {
                            "gate": {"evidenceType": "specification"},
                            "evidence": {
                                "specification": {
                                    "literalAudit": {"literals": [{"id": "flag"}]},
                                    "affectedSurfaces": [{"id": "behavior"}],
                                    "callerMatrix": [{"id": "entrypoint"}],
                                    "interfaceImpact": {"affectedInterfaces": []},
                                }
                            },
                        }
                    }
                }
            }
            evidence = {
                "taskPlan": {
                    "tasks": [
                        {
                            "id": "implementation",
                            "testCases": [
                                {
                                    "id": "case-1",
                                    "traces": [
                                        "surface:behavior",
                                        "caller:entrypoint",
                                    ],
                                }
                            ],
                        }
                    ]
                }
            }

            with self.assertRaisesRegex(ConfigurationError, "missing=.*literal:flag"):
                store._assert_test_traceability(state, evidence)

    def test_review_provenance_caps_self_review_claims(self) -> None:
        workflow_stage = stage("review", {"reviewProvenance": True})
        evidence = {
            "state": "MERGED",
            "reviewDecision": "APPROVED",
            "url": "https://github.example/pull/3",
            "review": {
                "reviewer": "agent-1",
                "criteria": [{"id": "criterion", "status": "pass", "evidence": ["diff"]}],
                "surfaces": [
                    {"id": "service-behavior", "disposition": "changed", "evidence": ["diff"]}
                ],
                "privacy": {"status": "pass", "evidence": ["privacy-review"]},
                "provenance": {
                    "kind": "self",
                    "reviewer": "agent-1",
                    "implementationAuthor": "agent-1",
                    "independent": False,
                    "evidence": ["session://agent-1"],
                },
                "assuranceClaim": "verified",
            },
        }

        with self.assertRaisesRegex(ConfigurationError, "self review cannot claim verified"):
            validate_gate_evidence(workflow_stage, evidence)
        evidence["review"]["assuranceClaim"] = "candidate"
        validate_gate_evidence(workflow_stage, evidence)

    def test_knowledge_requires_repository_manifest_closure(self) -> None:
        workflow_stage = stage("knowledge", {"manifestClosure": True})
        evidence = {
            "status": "pass",
            "signals": ["signal://knowledge"],
            "artifacts": ["docs/learnings.md"],
            "learningCandidates": [],
        }
        with self.assertRaisesRegex(ConfigurationError, "manifestClosure"):
            validate_gate_evidence(workflow_stage, evidence)

        evidence["manifestClosure"] = {
            "manifest": "specs/feature/sdd/change/manifest.yaml",
            "status": "complete",
            "evidence": ["git://manifest-commit"],
        }
        validate_gate_evidence(workflow_stage, evidence)


if __name__ == "__main__":
    unittest.main()
