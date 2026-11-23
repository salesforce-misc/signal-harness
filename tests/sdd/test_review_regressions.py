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

import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

from signal_harness.catalog import CatalogRepository, RepositoryCatalog
from signal_harness.compatibility import compare_snapshots, snapshot_plan
from signal_harness.context import collect_context
from signal_harness.control import cancel_run
from signal_harness.errors import ConfigurationError, ResolutionError
from signal_harness.git import _run_git, head_sha, managed_repository
from signal_harness.planner import build_plan, infer_repository_profile
from signal_harness.sdd import build_sdd_plan
from signal_harness.security import record_cost
from signal_harness.session import engage_repository, lock_discovery_context
from signal_harness.state import RunStore, _canonical_hash
from signal_harness.worktrees import _run_git as worktree_git
from signal_harness.worktrees import cleanup_task, prepare_task, verify_run_context
from tests.sdd.fixtures.legacy_08_serializer import legacy_discovery_plan
from tests.sdd.test_engine import (
    EXAMPLE_WORKFLOW,
    RepositoryFixture,
    merged_planning_evidence,
    profile,
    run,
    write_yaml,
)
from tests.sdd.test_runtime import RuntimeFixture


class ReviewRegressionTests(unittest.TestCase):
    def discovery(self, base, *, overrides=None, exclude=()):
        sponsor = RepositoryFixture(base, "service-a")
        peer = RepositoryFixture(base, "service-b")
        feature = sponsor.root / "specs/area/sdd/review"
        feature.mkdir(parents=True)
        (feature / "source.md").write_text("source\n", encoding="utf-8")
        (sponsor.root / "implementation.py").write_text("value = 1\n", encoding="utf-8")
        write_yaml(
            feature / "manifest.yaml",
            {
                "feature": "review",
                "sources": ["specs/area/sdd/review/source.md"],
                "constraints": ["AGENTS.md"],
            },
        )
        run("git", "add", ".", cwd=sponsor.root)
        run("git", "commit", "-m", "feature", cwd=sponsor.root)
        catalog = RepositoryCatalog(
            id="fixture",
            workflow=EXAMPLE_WORKFLOW,
            repositories={
                name: CatalogRepository(id=name, remote=f"example-org/{name}", author=True)
                for name in ("service-a", "service-b")
            },
        )
        with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
            plan = build_sdd_plan(
                feature="review",
                current_path=sponsor.root,
                repo_overrides=overrides,
                exclude=exclude,
            )
        store = RunStore(base / "state")
        store.create(plan, feature / "manifest.yaml", "review")
        return sponsor, peer, feature, plan, store

    def engage(self, store, **kwargs):
        return engage_repository(
            store,
            "review",
            "service-b",
            reason="Source calls peer",
            evidence=("service-a:AGENTS.md",),
            **kwargs,
        )

    def test_snapshot_slug_collisions_remain_independent_and_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first_base, second_base = base / "one", base / "two"
            first_base.mkdir()
            second_base.mkdir()
            _, first_remote = self.remote(first_base)
            _, second_remote = self.remote(second_base)
            with patch.dict("os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}):
                first = managed_repository(
                    str(first_remote), "team/api", snapshot_root=base / "run"
                )
                second = managed_repository(
                    str(second_remote), "team-api", snapshot_root=base / "run"
                )
                self.assertNotEqual(first, second)
                long_id = "repository-" + "x" * 180
                long_snapshot = managed_repository(
                    str(first_remote), long_id, snapshot_root=base / "run"
                )
                self.assertLess(len(long_snapshot.name), 200)
                self.assertEqual(
                    managed_repository(str(first_remote), long_id, snapshot_root=base / "run"),
                    long_snapshot,
                )
                for remote, repo_id, expected in (
                    (first_remote, "team/api", first),
                    (second_remote, "team-api", second),
                ):
                    self.assertEqual(
                        managed_repository(str(remote), repo_id, snapshot_root=base / "run"),
                        expected,
                    )
                # A pre-upgrade immutable path is still reused by its original identity.
                identity_path = next((base / "run").glob(f".{first.name}.snapshot.json"))
                legacy = base / "run" / "team-api"
                _run_git("worktree", "move", str(first), str(legacy), cwd=first)
                identity_path.rename(base / "run" / ".team-api.snapshot.json")
                self.assertEqual(
                    managed_repository(str(first_remote), "team/api", snapshot_root=base / "run"),
                    legacy.resolve(),
                )
                self.assertEqual(
                    managed_repository(str(second_remote), "team-api", snapshot_root=base / "run"),
                    second,
                )

    def test_cleanup_merges_concurrent_cost_and_recovers_interrupted_removal(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), tempfile.TemporaryDirectory() as directory:
                fixture = RuntimeFixture(Path(directory))
                fixture.enter_task_phase()
                task = prepare_task(fixture.store, "runtime-run", "implementation")
                cancel_run(fixture.store, "runtime-run", actor="operator", reason="cleanup")

                def remove(root, *args, fixture=fixture, interrupted=interrupted, **kwargs):
                    result = worktree_git(root, *args, **kwargs)
                    if args[:2] == ("worktree", "remove"):
                        record_cost(fixture.store, "runtime-run", amount_usd=0.25, source="host")
                        if interrupted:
                            raise OSError("interrupted after removal")
                    return result

                with patch("signal_harness.worktrees._run_git", side_effect=remove):
                    if interrupted:
                        with self.assertRaisesRegex(OSError, "interrupted"):
                            cleanup_task(fixture.store, "runtime-run", "implementation")
                    else:
                        cleanup_task(fixture.store, "runtime-run", "implementation")
                cleanup = cleanup_task(fixture.store, "runtime-run", "implementation")
                latest = fixture.store.load("runtime-run")
                self.assertFalse(Path(task["worktree"]).exists())
                self.assertEqual(latest["security"]["usage"]["costUsd"], 0.25)
                self.assertEqual(latest["tasks"]["implementation"]["worktreeCleanup"], cleanup)
                self.assertEqual(
                    len([e for e in latest["events"] if e["type"] == "task.worktree.cleaned"]), 1
                )

    def test_cleanup_retries_completion_conflict_without_erasing_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RuntimeFixture(Path(directory))
            fixture.enter_task_phase()
            prepare_task(fixture.store, "runtime-run", "implementation")
            cancel_run(fixture.store, "runtime-run", actor="operator", reason="cleanup")
            original_save = RunStore.save
            conflicted = False

            def save(store, state):
                nonlocal conflicted
                if state["events"][-1]["type"] == "task.worktree.cleaned" and not conflicted:
                    conflicted = True
                    record_cost(fixture.store, "runtime-run", amount_usd=0.5, source="host")
                original_save(store, state)

            with patch.object(RunStore, "save", save):
                cleanup_task(fixture.store, "runtime-run", "implementation")
            self.assertTrue(conflicted)
            self.assertEqual(fixture.store.load("runtime-run")["security"]["usage"]["costUsd"], 0.5)

    def test_denied_permission_migrations_persist_one_event_without_changing_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, _, store = self.discovery(Path(directory))
            original = store.load("review")
            remote = original["plan"]["repositories"][0]["remote"]
            cases = [
                (
                    {},
                    "operator",
                    "Reviewed grants",
                    None,
                    ConfigurationError,
                    "permission migration requires actor, reason and permissions",
                ),
                (
                    {"service-a": {"remote": remote, "author": True}},
                    "",
                    "Reviewed grants",
                    None,
                    ConfigurationError,
                    "permission migration requires actor, reason and permissions",
                ),
                (
                    {"service-a": {"remote": remote, "author": True}},
                    "operator",
                    "",
                    None,
                    ConfigurationError,
                    "permission migration requires actor, reason and permissions",
                ),
                (
                    [],
                    "operator",
                    "Reviewed grants",
                    None,
                    ConfigurationError,
                    "permission migration requires actor, reason and permissions",
                ),
                (
                    {"service-a": {"remote": remote, "author": True}},
                    "operator",
                    "Reviewed",
                    "paused",
                    ResolutionError,
                    "permission migration requires an active discovery plan",
                ),
                (
                    {"unknown": {"remote": remote, "author": True}},
                    "operator",
                    "Reviewed",
                    None,
                    ConfigurationError,
                    "unknown engaged repository: unknown",
                ),
                (
                    {"service-a": {"remote": remote, "author": "true"}},
                    "operator",
                    "Reviewed",
                    None,
                    ConfigurationError,
                    "permissions require explicit remote and boolean author",
                ),
                (
                    {"service-a": {"remote": "wrong/remote", "author": True}},
                    "operator",
                    "Reviewed",
                    None,
                    ConfigurationError,
                    "permission remote mismatch: service-a",
                ),
                (
                    {"service-a": {"remote": remote, "author": False}},
                    "operator",
                    "Reviewed",
                    None,
                    ConfigurationError,
                    "cannot replace explicit author permission: service-a",
                ),
                (
                    {"x" * 600: {"remote": remote, "author": True}},
                    "operator",
                    "Reviewed",
                    None,
                    ConfigurationError,
                    "unknown engaged repository: " + "x" * 600,
                ),
            ]
            for permissions, actor, reason, status, error_type, message in cases:
                with self.subTest(message=message, actor=actor, reason=reason):
                    before = store.load("review")
                    before["status"] = status or original["status"]
                    store.save(before)
                    with self.assertRaises(error_type) as raised:
                        store.migrate_author_permissions(
                            "review", permissions, actor=actor, reason=reason
                        )
                    self.assertEqual(str(raised.exception), message)
                    after = RunStore(store.root).load("review")
                    for field in ("plan", "planHash", "discoveryPlanHash"):
                        self.assertEqual(after[field], before[field])
                    self.assertEqual(after["events"][:-1], before["events"])
                    event = after["events"][-1]
                    self.assertEqual(event["type"], "security.authorization.denied")
                    self.assertTrue(event["at"])
                    self.assertEqual(
                        event["detail"],
                        {
                            "actor": actor,
                            "action": "migrate-author-permissions",
                            "repositories": sorted(permissions)
                            if isinstance(permissions, dict)
                            else [],
                            "outcome": "denied",
                            "reason": message[:500],
                        },
                    )

    def test_frozen_legacy_discovery_continues_context_lock_without_origin_head(self):
        with tempfile.TemporaryDirectory() as directory:
            sponsor, _, _, _, store = self.discovery(Path(directory))
            run(
                "git",
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/main",
                cwd=sponsor.root,
            )
            run("git", "symbolic-ref", "-d", "refs/remotes/origin/HEAD", cwd=sponsor.root)
            legacy = store.load("review")
            legacy["plan"] = legacy_discovery_plan(legacy["plan"])
            legacy["planHash"] = _canonical_hash(legacy["plan"])
            legacy["discoveryPlanHash"] = legacy["planHash"]
            store.save(legacy)
            repository = legacy["plan"]["repositories"][0]
            self.assertNotIn("profileSource", repository)
            self.assertTrue((sponsor.root / repository["profilePath"]).is_file())
            with self.assertRaisesRegex(ResolutionError, "cannot infer repository default branch"):
                infer_repository_profile(sponsor.root, "service-a", repository["remote"])
            reloaded = RunStore(store.root)
            result = lock_discovery_context(
                reloaded,
                "review",
                "service-a",
                paths=("implementation.py",),
                reason="Continue saved 0.8 discovery",
                actor="operator",
            )
            after = reloaded.load("review")
            context = after["plan"]["repositories"][0]["context"]
            self.assertEqual(context["revision"], repository["context"]["revision"])
            self.assertEqual(result["added"], ["implementation.py"])
            files = {item["path"]: item for item in context["files"]}
            for item in repository["context"]["files"]:
                self.assertEqual(files[item["path"]], item)
            self.assertEqual(after["planHash"], _canonical_hash(after["plan"]))
            self.assertEqual(after["discoveryPlanHash"], after["planHash"])

    def test_legacy_permission_migration_is_explicit_and_enables_materialization(self):
        with tempfile.TemporaryDirectory() as directory:
            _, peer, _, _, store = self.discovery(Path(directory))
            self.engage(store, override=peer.root)
            legacy = store.load("review")
            legacy["plan"] = legacy_discovery_plan(legacy["plan"])
            legacy["planHash"] = _canonical_hash(legacy["plan"])
            legacy["discoveryPlanHash"] = legacy["planHash"]
            store.save(legacy)
            evidence = merged_planning_evidence()
            for task in evidence["taskPlan"]["tasks"]:
                ref = {"repo": task["repo"], "path": "AGENTS.md"}
                task["requiredChecks"] = ["unit"]
                task["context"] = [ref]
                task["ownership"] = {"reason": "Owns behavior", "evidence": [ref]}
            store.clear_current_gate("review", evidence)
            with self.assertRaisesRegex(ConfigurationError, "non-authorable"):
                store.clear_current_gate("review", evidence)
            repos = {r["id"]: r for r in store.load("review")["plan"]["repositories"]}

            def migrate(permissions):
                return store.migrate_author_permissions(
                    "review", permissions, actor="operator", reason="Reviewed upgrade grants"
                )

            for invalid in (
                {"unknown": {"remote": "example/unknown", "author": True}},
                {"service-a": {"remote": "wrong/remote", "author": True}},
                {"service-a": {"remote": repos["service-a"]["remote"], "author": "true"}},
            ):
                with self.assertRaises(ConfigurationError):
                    migrate(invalid)
            migrated = migrate(
                {
                    repo_id: {"remote": repo["remote"], "author": repo_id == "service-a"}
                    for repo_id, repo in repos.items()
                }
            )
            self.assertEqual(migrated["planHash"], _canonical_hash(migrated["plan"]))
            self.assertEqual(migrated["discoveryPlanHash"], migrated["planHash"])
            self.assertEqual(migrated["events"][-1]["detail"]["actor"], "operator")
            with self.assertRaisesRegex(ConfigurationError, "explicit author"):
                migrate({"service-b": {"remote": repos["service-b"]["remote"], "author": True}})
            with self.assertRaisesRegex(ConfigurationError, "non-authorable repository service-b"):
                store.clear_current_gate("review", evidence)
            self.assertTrue(store.load("review")["plan"]["repositories"][0]["author"])

    def test_legacy_permission_grants_allow_stage_one_continuation(self):
        with tempfile.TemporaryDirectory() as directory:
            _, peer, _, _, store = self.discovery(Path(directory))
            self.engage(store, override=peer.root)
            legacy = store.load("review")
            legacy["plan"] = legacy_discovery_plan(legacy["plan"])
            legacy["planHash"] = _canonical_hash(legacy["plan"])
            legacy["discoveryPlanHash"] = legacy["planHash"]
            store.save(legacy)
            evidence = merged_planning_evidence()
            for task in evidence["taskPlan"]["tasks"]:
                ref = {"repo": task["repo"], "path": "AGENTS.md"}
                task["requiredChecks"] = ["unit"]
                task["context"] = [ref]
                task["ownership"] = {"reason": "Owns behavior", "evidence": [ref]}
            store.clear_current_gate("review", evidence)
            with self.assertRaisesRegex(ConfigurationError, "non-authorable"):
                store.clear_current_gate("review", evidence)
            store.migrate_author_permissions(
                "review",
                {
                    repo["id"]: {"remote": repo["remote"], "author": True}
                    for repo in legacy["plan"]["repositories"]
                },
                actor="operator",
                reason="Reviewed 0.8 continuation",
            )
            store.clear_current_gate("review", evidence)
            self.assertEqual(store.load("review")["plan"]["phase"], "execution")
            self.assertEqual(set(store.load("review")["tasks"]), {"producer", "consumer"})
            with self.assertRaisesRegex(ResolutionError, "active discovery"):
                store.migrate_author_permissions(
                    "review",
                    {"service-a": {"remote": "example-org/service-a", "author": True}},
                    actor="operator",
                    reason="Cannot change execution permission",
                )

    def test_saved_peer_override_survives_reload_without_entering_portable_plan(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                saved = base / "service-b"
                sponsor, peer, _, plan, store = self.discovery(base, overrides={"service-b": saved})
                self.assertNotIn("checkoutOverrides", plan.as_dict())
                self.assertNotIn(str(saved), json.dumps(snapshot_plan(plan)))
                self.assertEqual(
                    store.load("review")["checkoutOverrides"], {"service-b": str(saved.resolve())}
                )
                target = peer.root
                if explicit:
                    other = base / "other"
                    other.mkdir()
                    target = RepositoryFixture(other, "service-b").root
                with patch("signal_harness.session.managed_repository") as fetch:
                    result = self.engage(
                        RunStore(store.root), **({"override": target} if explicit else {})
                    )
                fetch.assert_not_called()
                self.assertEqual(result["root"], str(target.resolve()))
                self.assertEqual(result["status"], "engaged")
                self.assertEqual(sponsor.id, "service-a")

    def test_excluded_and_unknown_overrides_are_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            _, _, _, plan, store = self.discovery(
                base,
                overrides={"service-b": base / "missing", "outside": base / "outside"},
                exclude=("service-b",),
            )
            self.assertEqual(plan.checkout_overrides, ())
            self.assertEqual(store.load("review")["checkoutOverrides"], {})
            with self.assertRaisesRegex(ConfigurationError, "not in the run catalog"):
                self.engage(store)

    def test_peer_profile_identity_is_validated_before_persistence(self):
        for identity, remote, error in (
            ("wrong", "example-org/service-b", "profile id mismatch"),
            ("service-b", "example-org/wrong", "profile/workspace remote mismatch"),
            ("service-b", "https://github.com/example-org/service-b.git", None),
        ):
            with (
                self.subTest(identity=identity, remote=remote),
                tempfile.TemporaryDirectory() as directory,
            ):
                _, peer, _, _, store = self.discovery(Path(directory))
                write_yaml(peer.root / ".signal-harness/repo.yaml", profile(identity, remote))
                if error:
                    before = store.load("review")
                    with self.assertRaisesRegex(ConfigurationError, error):
                        self.engage(store, override=peer.root)
                    after = store.load("review")
                    self.assertEqual(after["plan"], before["plan"])
                    self.assertEqual(after["tasks"], before["tasks"])
                else:
                    self.assertEqual(self.engage(store, override=peer.root)["status"], "engaged")

    def test_clarification_rewind_locks_new_evidence_and_preserves_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            _, peer, _, _, store = self.discovery(Path(directory))
            self.engage(store, override=peer.root)
            evidence = merged_planning_evidence()
            for task in evidence["taskPlan"]["tasks"]:
                reference = {"repo": task["repo"], "path": "AGENTS.md"}
                task["requiredChecks"] = ["unit"]
                task["context"] = [reference]
                task["ownership"] = {"reason": "Owns behavior", "evidence": [reference]}
            store.clear_current_gate("review", evidence)
            store.clear_current_gate("review", evidence)
            store.rewind_workflow(
                "review", "0", actor="developer", reason="Clarify behavior", task_id="producer"
            )
            before = store.load("review")
            self.assertNotIn(
                "implementation.py",
                [item["path"] for item in before["plan"]["repositories"][0]["context"]["files"]],
            )
            revised = copy.deepcopy(evidence)
            revised["specification"]["affectedSurfaces"][0]["evidence"] = [
                {"repo": "service-a", "path": "implementation.py"}
            ]
            with self.assertRaisesRegex(ConfigurationError, "not SHA-locked"):
                store.clear_current_gate("review", revised)
            locked = lock_discovery_context(
                store,
                "review",
                "service-a",
                paths=("implementation.py",),
                reason="Clarification needs source evidence",
                actor="reviewer",
            )
            self.assertEqual(locked["added"], ["implementation.py"])
            after = store.load("review")
            event = after["events"][-1]
            self.assertEqual(event["type"], "discovery.context.locked")
            self.assertEqual(event["detail"]["actor"], "reviewer")
            self.assertEqual(event["detail"]["action"], "lock-context")
            self.assertEqual(event["detail"]["outcome"], "success")
            self.assertEqual(event["detail"]["repository"], "service-a")
            self.assertTrue(event["at"])
            self.assertEqual(after["plan"]["phase"], "execution")
            self.assertEqual(after["plan"]["tasks"], before["plan"]["tasks"])
            self.assertEqual(after["tasks"], before["tasks"])
            self.assertEqual(after["workflow"], before["workflow"])
            self.assertEqual(after["discoveryPlanHash"], before["discoveryPlanHash"])
            self.assertEqual(after["executionPlanHash"], before["executionPlanHash"])
            self.assertNotEqual(after["planHash"], before["planHash"])
            self.assertEqual(after["planHash"], _canonical_hash(after["plan"]))
            for previous, current in zip(
                before["plan"]["repositories"], after["plan"]["repositories"], strict=True
            ):
                self.assertEqual(previous["mode"], current["mode"])
                self.assertEqual(previous["author"], current["author"])
                for file in previous["context"]["files"]:
                    self.assertIn(file, current["context"]["files"])
            resumed = store.clear_current_gate("review", revised)
            self.assertEqual(resumed["currentStage"], "tasks")
            self.assertEqual(resumed["tasks"]["producer"]["currentStage"], "2")
            self.assertNotIn("pendingReturn", resumed)
            self.assertEqual(store.load("review")["tasks"], before["tasks"])

    def test_execution_context_lock_requires_valid_clarification_return(self):
        with tempfile.TemporaryDirectory() as directory:
            _, peer, _, _, store = self.discovery(Path(directory))
            self.engage(store, override=peer.root)
            evidence = merged_planning_evidence()
            for task in evidence["taskPlan"]["tasks"]:
                reference = {"repo": task["repo"], "path": "AGENTS.md"}
                task["requiredChecks"] = ["unit"]
                task["context"] = [reference]
                task["ownership"] = {"reason": "Owns behavior", "evidence": [reference]}
            store.clear_current_gate("review", evidence)
            store.clear_current_gate("review", evidence)
            store.rewind_workflow(
                "review", "0", actor="developer", reason="Clarify behavior", task_id="producer"
            )
            original = store.load("review")
            for invalid in ("missing", "target", "resume", "index", "task", "record", "stage"):
                with self.subTest(invalid=invalid):
                    state = store.load("review")
                    state["workflow"] = copy.deepcopy(original["workflow"])
                    workflow = state["workflow"]
                    if invalid == "missing":
                        workflow.pop("pendingReturn")
                    elif invalid == "target":
                        workflow["pendingReturn"]["targetStage"] = "1"
                    elif invalid == "resume":
                        workflow["pendingReturn"]["resumeStage"] = "3"
                    elif invalid == "index":
                        workflow["pendingReturn"]["rewindIndex"] = -1
                    elif invalid == "task":
                        workflow["tasks"]["producer"]["status"] = "complete"
                    elif invalid == "record":
                        workflow["rewinds"][0]["task"] = "consumer"
                    else:
                        workflow["currentStage"] = "tasks"
                    store.save(state)
                    with self.assertRaisesRegex(ResolutionError, "only during Stage 0"):
                        lock_discovery_context(
                            store,
                            "review",
                            "service-a",
                            paths=("implementation.py",),
                            reason="Not an active clarification",
                        )
                    self.assertEqual(store.load("review")["plan"], original["plan"])

    def test_planning_relock_preserves_exact_source_for_materialized_task(self):
        with tempfile.TemporaryDirectory() as directory:
            sponsor, _, feature, _, store = self.discovery(Path(directory))
            lock_discovery_context(
                store,
                "review",
                "service-a",
                paths=("implementation.py",),
                reason="Task implementation evidence",
            )
            locked = next(
                item
                for item in store.load("review")["plan"]["repositories"][0]["context"]["files"]
                if item["path"] == "implementation.py"
            )
            evidence = merged_planning_evidence()
            evidence["specification"]["interfaceImpact"]["evaluatedRepositories"] = ["service-a"]
            evidence["specification"]["affectedSurfaces"] = evidence["specification"][
                "affectedSurfaces"
            ][:1]
            evidence["taskPlan"]["surfaceCoverage"] = [
                {"surface": "producer-behavior", "disposition": "task", "task": "implementation"}
            ]
            evidence["taskPlan"]["tasks"] = [
                {
                    "id": "implementation",
                    "repo": "service-a",
                    "requirement": "behavior",
                    "dependsOn": [],
                    "surfaces": ["producer-behavior"],
                    "testCases": [{"id": "case-1", "kind": "delta", "requirement": "behavior"}],
                    "requiredChecks": ["unit"],
                    "context": [{"repo": "service-a", "path": "implementation.py"}],
                    "ownership": {
                        "reason": "owns source",
                        "evidence": [{"repo": "service-a", "path": "implementation.py"}],
                    },
                }
            ]
            store.clear_current_gate("review", evidence)
            (feature / "source.md").write_text("approved changes\n", encoding="utf-8")
            run("git", "add", ".", cwd=sponsor.root)
            run("git", "commit", "-m", "planning documents", cwd=sponsor.root)
            store.clear_current_gate("review", evidence)
            state = store.load("review")
            self.assertEqual(state["plan"]["phase"], "execution")
            self.assertIn(locked, state["plan"]["repositories"][0]["context"]["files"])

    def test_planning_relock_allows_only_no_longer_required_document_removal(self):
        for update_manifest in (True, False):
            with (
                self.subTest(update_manifest=update_manifest),
                tempfile.TemporaryDirectory() as directory,
            ):
                sponsor, _, feature, _, store = self.discovery(Path(directory))
                lock_discovery_context(
                    store, "review", "service-a", paths=("implementation.py",), reason="source"
                )
                state = store.load("review")
                previous = state["plan"]["repositories"][0]["context"]["files"]
                (feature / "source.md").rename(feature / "specification.md")
                if update_manifest:
                    manifest = feature / "manifest.yaml"
                    manifest.write_text(
                        manifest.read_text().replace("source.md", "specification.md")
                    )
                run("git", "add", ".", cwd=sponsor.root)
                run("git", "commit", "-m", "rename planning source", cwd=sponsor.root)
                if not update_manifest:
                    with self.assertRaisesRegex(ResolutionError, "missing required context"):
                        store._refresh_context_after_planning_docs(state)
                    continue
                store._refresh_context_after_planning_docs(state)
                files = state["plan"]["repositories"][0]["context"]["files"]
                paths = {item["path"] for item in files}
                self.assertIn("specs/area/sdd/review/specification.md", paths)
                self.assertNotIn("specs/area/sdd/review/source.md", paths)
                self.assertIn(
                    next(item for item in previous if item["path"] == "implementation.py"), files
                )

    def test_engagement_serializes_success_unavailable_denial_and_context_lock(self):
        for outcome in ("unavailable", "denied", "context"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                _, peer, _, _, store = self.discovery(Path(directory))
                state = store.load("review")
                state["plan"]["catalogRepositories"].append(
                    {"id": "service-c", "remote": "example-org/service-c", "author": False}
                )
                state["planHash"] = _canonical_hash(state["plan"])
                store.save(state)
                collecting, release, second_started, second_loaded = (
                    Event(),
                    Event(),
                    Event(),
                    Event(),
                )
                original_load = store.load

                def collect(
                    root, profile, *, peer=peer, collecting=collecting, release=release, **kwargs
                ):
                    if root.resolve() == peer.root.resolve():
                        collecting.set()
                        if not release.wait(5):
                            raise AssertionError("engagement was not released")
                    return collect_context(root, profile, **kwargs)

                def load(
                    _store,
                    run_id,
                    *,
                    collecting=collecting,
                    second_loaded=second_loaded,
                    original_load=original_load,
                ):
                    if collecting.is_set():
                        second_loaded.set()
                    return original_load(run_id)

                def second_operation(
                    *, second_started=second_started, outcome=outcome, store=store
                ):
                    second_started.set()
                    if outcome == "context":
                        return lock_discovery_context(
                            store,
                            "review",
                            "service-a",
                            paths=("implementation.py",),
                            reason="source",
                        )
                    return engage_repository(
                        store,
                        "review",
                        "unknown" if outcome == "denied" else "service-c",
                        reason="source calls peer",
                        evidence=("service-a:AGENTS.md",),
                    )

                with (
                    patch("signal_harness.session.collect_context", side_effect=collect),
                    patch(
                        "signal_harness.session.managed_repository",
                        side_effect=ResolutionError("no access"),
                    ),
                    patch.object(RunStore, "load", new=load),
                    ThreadPoolExecutor(max_workers=2) as pool,
                ):
                    first = pool.submit(self.engage, store, override=peer.root)
                    try:
                        self.assertTrue(collecting.wait(5))
                        second = pool.submit(second_operation)
                        self.assertTrue(second_started.wait(5))
                        self.assertFalse(second_loaded.wait(0.2))
                    finally:
                        release.set()
                    self.assertEqual(first.result(timeout=5)["status"], "engaged")
                    if outcome == "denied":
                        with self.assertRaisesRegex(ConfigurationError, "not in the run catalog"):
                            second.result(timeout=5)
                    else:
                        second.result(timeout=5)
                state = store.load("review")
                self.assertEqual(
                    {item["id"] for item in state["plan"]["repositories"]},
                    {"service-a", "service-b"},
                )
                types = [item["type"] for item in state["events"]]
                self.assertEqual(types.count("discovery.repository.engaged"), 1)
                expected = {
                    "unavailable": "discovery.repository.unavailable",
                    "denied": "security.authorization.denied",
                    "context": "discovery.context.locked",
                }
                self.assertEqual(types.count(expected[outcome]), 1)
                if outcome == "unavailable":
                    self.assertTrue(
                        state["plan"]["unavailableRepositories"][0]["requiredByDiscovery"]
                    )
                if outcome == "context":
                    self.assertIn(
                        "implementation.py",
                        {
                            item["path"]
                            for item in state["plan"]["repositories"][0]["context"]["files"]
                        },
                    )

    def test_gate_clearing_serializes_with_discovery_updates(self):
        for outcome in ("unavailable", "engaged", "context"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                _, peer, _, _, store = self.discovery(Path(directory))
                evidence = merged_planning_evidence()
                evidence["specification"]["interfaceImpact"]["evaluatedRepositories"] = [
                    "service-a"
                ]
                evidence["specification"]["affectedSurfaces"] = evidence["specification"][
                    "affectedSurfaces"
                ][:1]
                updating, release, gate_started, gate_loaded = Event(), Event(), Event(), Event()
                original_load = store.load

                def pause(*, updating=updating, release=release):
                    updating.set()
                    if not release.wait(5):
                        raise AssertionError("discovery update was not released")

                def resolve(*args, pause=pause, **kwargs):
                    pause()
                    raise ResolutionError("no access")

                def collect(root, profile, *, pause=pause, **kwargs):
                    pause()
                    return collect_context(root, profile, **kwargs)

                def load(
                    _store,
                    run_id,
                    *,
                    original_load=original_load,
                    gate_started=gate_started,
                    gate_loaded=gate_loaded,
                ):
                    if gate_started.is_set():
                        gate_loaded.set()
                    return original_load(run_id)

                def update(*, outcome=outcome, store=store, peer=peer):
                    if outcome == "context":
                        return lock_discovery_context(
                            store,
                            "review",
                            "service-a",
                            paths=("implementation.py",),
                            reason="source",
                        )
                    return self.engage(
                        store, **({"override": peer.root} if outcome == "engaged" else {})
                    )

                def clear(*, gate_started=gate_started, store=store, evidence=evidence):
                    gate_started.set()
                    return store.clear_current_gate("review", evidence)

                with (
                    patch("signal_harness.session.managed_repository", side_effect=resolve),
                    patch("signal_harness.session.collect_context", side_effect=collect),
                    patch.object(RunStore, "load", new=load),
                    ThreadPoolExecutor(max_workers=2) as pool,
                ):
                    first = pool.submit(update)
                    try:
                        self.assertTrue(updating.wait(5))
                        gate = pool.submit(clear)
                        self.assertTrue(gate_started.wait(5))
                        self.assertFalse(gate_loaded.wait(0.2))
                    finally:
                        release.set()
                    result = first.result(timeout=5)
                    after_discovery = original_load("review")
                    if outcome == "context":
                        self.assertEqual(gate.result(timeout=5)["currentStage"], "1")
                    else:
                        self.assertEqual(result["status"], outcome)
                        with self.assertRaisesRegex(
                            ConfigurationError,
                            "unavailableRepositories"
                            if outcome == "unavailable"
                            else "evaluatedRepositories",
                        ):
                            gate.result(timeout=5)
                state = store.load("review")
                self.assertEqual(state["plan"], after_discovery["plan"])
                self.assertEqual(state["planHash"], after_discovery["planHash"])
                types = [item["type"] for item in state["events"]]
                event = {
                    "unavailable": "discovery.repository.unavailable",
                    "engaged": "discovery.repository.engaged",
                    "context": "discovery.context.locked",
                }[outcome]
                self.assertEqual(types.count(event), 1)
                if outcome == "unavailable":
                    unavailable = state["plan"]["unavailableRepositories"]
                    self.assertEqual([item["id"] for item in unavailable], ["service-b"])
                    self.assertTrue(unavailable[0]["requiredByDiscovery"])
                elif outcome == "engaged":
                    self.assertEqual(
                        {item["id"] for item in state["plan"]["repositories"]},
                        {"service-a", "service-b"},
                    )
                else:
                    locked = state["plan"]["repositories"][0]["context"]["files"]
                    self.assertIn("implementation.py", {item["path"] for item in locked})
                self.assertEqual(
                    state["workflow"]["currentStage"], "1" if outcome == "context" else "0"
                )
                self.assertEqual(types.count("workflow.gate.cleared"), int(outcome == "context"))

    def test_overlapping_context_locks_preserve_both_files_and_events(self):
        with tempfile.TemporaryDirectory() as directory:
            sponsor, _, _, _, store = self.discovery(Path(directory))
            (sponsor.root / "second.py").write_text("second = 2\n", encoding="utf-8")
            run("git", "add", ".", cwd=sponsor.root)
            run("git", "commit", "-m", "second source", cwd=sponsor.root)
            state = store.load("review")
            state["plan"]["repositories"][0]["context"]["revision"] = head_sha(sponsor.root)
            state["planHash"] = _canonical_hash(state["plan"])
            store.save(state)
            first_collecting, release_first, second_started, second_collecting = (
                Event(),
                Event(),
                Event(),
                Event(),
            )

            def collect(root, profile, *, required_files):
                if "implementation.py" in required_files and "second.py" not in required_files:
                    first_collecting.set()
                    if not release_first.wait(5):
                        raise AssertionError("first collection was not released")
                else:
                    second_collecting.set()
                return collect_context(root, profile, required_files=required_files)

            def lock(path):
                if path == "second.py":
                    second_started.set()
                return lock_discovery_context(
                    store, "review", "service-a", paths=(path,), reason=path
                )

            with (
                patch("signal_harness.session.collect_context", side_effect=collect),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                first = pool.submit(lock, "implementation.py")
                try:
                    self.assertTrue(first_collecting.wait(5))
                    second = pool.submit(lock, "second.py")
                    self.assertTrue(second_started.wait(5))
                    self.assertFalse(second_collecting.wait(0.2))
                finally:
                    release_first.set()
                first.result(timeout=5)
                second.result(timeout=5)
            state = store.load("review")
            paths = {item["path"] for item in state["plan"]["repositories"][0]["context"]["files"]}
            self.assertTrue({"implementation.py", "second.py"} <= paths)
            events = [
                item for item in state["events"] if item["type"] == "discovery.context.locked"
            ]
            self.assertEqual(len(events), 2)

    def test_interrupted_snapshot_provisioning_is_retryable(self):
        for interruption in ("identity", "before-worktree", "after-worktree"):
            with (
                self.subTest(interruption=interruption),
                tempfile.TemporaryDirectory() as directory,
            ):
                base = Path(directory)
                source, remote = self.remote(base)
                original = head_sha(source)
                snapshots = base / "run"

                def provision(*args, interruption=interruption, **kwargs):
                    if args[:2] == ("worktree", "add"):
                        if interruption == "after-worktree":
                            _run_git(*args, **kwargs)
                        raise OSError("interrupted worktree provisioning")
                    return _run_git(*args, **kwargs)

                with patch.dict(
                    "os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}
                ):
                    target = (
                        "signal_harness.git.os.replace"
                        if interruption == "identity"
                        else "signal_harness.git._run_git"
                    )
                    effect = (
                        OSError("interrupted identity publication")
                        if interruption == "identity"
                        else provision
                    )
                    with patch(target, side_effect=effect), self.assertRaises(OSError):
                        managed_repository(str(remote), "service-b", snapshot_root=snapshots)
                    identity_path = next(
                        snapshots.glob(".service-b-*.snapshot.json"), snapshots / "absent"
                    )
                    if interruption == "identity":
                        self.assertFalse(identity_path.exists())
                        self.assertFalse(list(snapshots.glob("service-b-*")))
                    else:
                        self.assertEqual(
                            json.loads(identity_path.read_text())["revision"], original
                        )
                    (source / "README.md").write_text("advanced\n", encoding="utf-8")
                    run("git", "add", ".", cwd=source)
                    run("git", "commit", "-m", "advance", cwd=source)
                    run("git", "push", "origin", "main", cwd=source)
                    snapshot = managed_repository(str(remote), "service-b", snapshot_root=snapshots)
                    expected = head_sha(source) if interruption == "identity" else original
                    self.assertEqual(head_sha(snapshot), expected)
                    if interruption == "before-worktree":
                        run(
                            "git",
                            "worktree",
                            "remove",
                            str(snapshot),
                            cwd=next((base / "cache").glob("service-b-*")),
                        )
                        identity = json.loads(identity_path.read_text())
                        identity["remote"] = "wrong/remote"
                        identity_path.write_text(json.dumps(identity))
                        with self.assertRaisesRegex(ResolutionError, "identity mismatch"):
                            managed_repository(str(remote), "service-b", snapshot_root=snapshots)

    def remote(self, base):
        source = RepositoryFixture(base, "service-b")
        remote = base / "remote.git"
        run("git", "clone", "--bare", str(source.root), str(remote), cwd=base)
        run("git", "remote", "set-url", "origin", str(remote), cwd=source.root)
        return source.root, remote

    def test_remote_sponsor_planning_commits_are_isolated_between_persisted_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, remote = self.remote(base)
            write_yaml(source / ".signal-harness/repo.yaml", profile("service-b", str(remote)))
            feature = source / "specs/area/sdd/review"
            write_yaml(
                feature / "manifest.yaml",
                {
                    "feature": "review",
                    "sources": ["specs/area/sdd/review/source.md"],
                    "constraints": ["AGENTS.md"],
                },
            )
            (feature / "source.md").write_text("original source\n", encoding="utf-8")
            run("git", "add", ".", cwd=source)
            run("git", "commit", "-m", "sponsor feature", cwd=source)
            run("git", "push", "origin", "main", cwd=source)
            invoking = RepositoryFixture(base, "service-a")
            catalog = RepositoryCatalog(
                id="fixture",
                workflow=EXAMPLE_WORKFLOW,
                repositories={
                    "service-a": CatalogRepository(
                        id="service-a", remote=invoking.remote, author=True
                    ),
                    "service-b": CatalogRepository(id="service-b", remote=str(remote), author=True),
                },
            )
            store = RunStore(base / "state")
            with (
                patch.dict("os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}),
                patch("signal_harness.sdd.load_policy_catalog", return_value=catalog),
            ):

                def start(run_id):
                    plan = build_sdd_plan(
                        feature="review", current_path=invoking.root, sponsor_id="service-b"
                    )
                    sponsor = plan.repositories[0]
                    self.assertFalse(store.run_dir(run_id).exists())
                    store.create(plan, sponsor.root / sponsor.workspace.feature_manifest, run_id)
                    return sponsor.root

                first, second = start("first"), start("second")
                self.assertNotEqual(first, second)
                before = store.load("second")
                original = head_sha(second)
                (first / "specs/area/sdd/review/source.md").write_text("approved plan\n")
                run(
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-am",
                    "planning documents",
                    cwd=first,
                )
                state = store.load("first")
                store._refresh_context_after_planning_docs(state)
                state["planHash"] = _canonical_hash(state["plan"])
                store.save(state)
                self.assertEqual(
                    store.load("first")["plan"]["repositories"][0]["context"]["revision"],
                    head_sha(first),
                )
                self.assertEqual(store.load("second"), before)
                self.assertEqual(head_sha(second), original)
                self.assertEqual(
                    (second / "specs/area/sdd/review/source.md").read_text(), "original source\n"
                )
                verify_run_context(store, "second")
                third = start("third")
                self.assertNotIn(third, (first, second))
                self.assertEqual(head_sha(third), original)
                verify_run_context(store, "third")

    def test_cached_remote_default_switch_with_old_default_retained_or_removed(self):
        for remove in (False, True):
            with self.subTest(remove=remove), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                source, remote = self.remote(base)
                with patch.dict(
                    "os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}
                ):
                    first = managed_repository(
                        str(remote), "service-b", snapshot_root=base / "run-a"
                    )
                    run("git", "checkout", "-b", "develop", cwd=source)
                    (source / "README.md").write_text("develop\n", encoding="utf-8")
                    run("git", "add", ".", cwd=source)
                    run("git", "commit", "-m", "develop", cwd=source)
                    run("git", "push", "origin", "develop", cwd=source)
                    run("git", "symbolic-ref", "HEAD", "refs/heads/develop", cwd=remote)
                    if remove:
                        run("git", "push", "origin", "--delete", "main", cwd=source)
                    second = managed_repository(
                        str(remote), "service-b", snapshot_root=base / "run-b"
                    )
                    self.assertEqual(head_sha(second), head_sha(source))
                    self.assertNotEqual(head_sha(first), head_sha(second))

    def test_missing_remote_head_falls_back_to_main_for_profileless_inference(self):
        for cached in (False, True):
            with self.subTest(cached=cached), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                source, remote = self.remote(base)
                with patch.dict(
                    "os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}
                ):
                    if cached:
                        run("git", "checkout", "-b", "develop", cwd=source)
                        (source / "README.md").write_text("old default\n", encoding="utf-8")
                        run("git", "add", ".", cwd=source)
                        run("git", "commit", "-m", "old default", cwd=source)
                        run("git", "push", "origin", "develop", cwd=source)
                        run("git", "symbolic-ref", "HEAD", "refs/heads/develop", cwd=remote)
                        old = managed_repository(
                            str(remote), "service-b", snapshot_root=base / "run-a"
                        )
                        self.assertEqual(head_sha(old), head_sha(source))
                        run("git", "checkout", "main", cwd=source)
                    # Keep main readable while the advertised HEAD is dangling.
                    run("git", "symbolic-ref", "HEAD", "refs/heads/missing", cwd=remote)
                    snapshot = managed_repository(
                        str(remote), "service-b", snapshot_root=base / "run-b"
                    )
                    self.assertEqual(head_sha(snapshot), head_sha(source))
                    inferred = infer_repository_profile(snapshot, "service-b", str(remote))
                    self.assertEqual(inferred.default_branch, "main")
                    if cached:
                        self.assertNotEqual(head_sha(old), head_sha(snapshot))

    def test_partial_engagement_retry_reuses_original_snapshot_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            _, peer, _, _, store = self.discovery(base)
            remote = base / "remote.git"
            run("git", "clone", "--bare", str(peer.root), str(remote), cwd=base)
            run("git", "remote", "set-url", "origin", str(remote), cwd=peer.root)
            # Use a profileless remote so its catalog identity can be a local test URL.
            run("git", "rm", ".signal-harness/repo.yaml", cwd=peer.root)
            run("git", "commit", "-m", "profileless", cwd=peer.root)
            run("git", "push", "origin", "main", cwd=peer.root)
            state = store.load("review")
            state["plan"]["catalogRepositories"][1]["remote"] = str(remote)
            state["planHash"] = _canonical_hash(state["plan"])
            store.save(state)
            original = head_sha(peer.root)
            with patch.dict("os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}):
                with (
                    patch(
                        "signal_harness.session.collect_context",
                        side_effect=ConfigurationError("context unavailable"),
                    ),
                    self.assertRaisesRegex(ConfigurationError, "context unavailable"),
                ):
                    self.engage(store)
                self.assertEqual(len(store.load("review")["plan"]["repositories"]), 1)
                (peer.root / "README.md").write_text("new tip\n", encoding="utf-8")
                run("git", "add", ".", cwd=peer.root)
                run("git", "commit", "-m", "advance", cwd=peer.root)
                run("git", "push", "origin", "main", cwd=peer.root)
                self.assertEqual(self.engage(store)["revision"], original)
                snapshot_root = store.run_dir("review") / "repositories"
                snapshot = managed_repository(str(remote), "service-b", snapshot_root=snapshot_root)
                (snapshot / "untracked").write_text("dirty", encoding="utf-8")
                with self.assertRaisesRegex(ResolutionError, "snapshot is dirty"):
                    managed_repository(str(remote), "service-b", snapshot_root=snapshot_root)
                (snapshot / "untracked").unlink()
                run("git", "checkout", "--detach", "HEAD~1", cwd=snapshot)
                with self.assertRaisesRegex(ResolutionError, "snapshot moved"):
                    managed_repository(str(remote), "service-b", snapshot_root=snapshot_root)
                run("git", "checkout", "--detach", original, cwd=snapshot)
                next(snapshot_root.glob(".service-b-*.snapshot.json")).unlink()
                with self.assertRaisesRegex(ResolutionError, "identity unavailable"):
                    managed_repository(str(remote), "service-b", snapshot_root=snapshot_root)

    def test_delayed_host_cost_save_cannot_erase_unavailable_engagement(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, _, store = self.discovery(Path(directory))
            saving, release = Event(), Event()
            original_save = store.save

            def save(_store, state):
                if state.get("costEvents"):
                    saving.set()
                    if not release.wait(5):
                        raise AssertionError("cost save was not released")
                return original_save(state)

            with (
                patch.object(RunStore, "save", new=save),
                patch(
                    "signal_harness.session.managed_repository",
                    side_effect=ResolutionError("no access"),
                ),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                cost = pool.submit(record_cost, store, "review", amount_usd=0.1, source="host")
                try:
                    self.assertTrue(saving.wait(5))
                    self.assertEqual(self.engage(store)["status"], "unavailable")
                finally:
                    release.set()
                with self.assertRaisesRegex(ResolutionError, "stale run state"):
                    cost.result(timeout=5)
            state = store.load("review")
            self.assertTrue(state["plan"]["unavailableRepositories"][0]["requiredByDiscovery"])
            self.assertEqual(state["repositoryEngagement"]["service-b"]["status"], "unavailable")
            self.assertIn("discovery.repository.unavailable", [e["type"] for e in state["events"]])
            evidence = merged_planning_evidence()
            evidence["specification"]["interfaceImpact"]["evaluatedRepositories"] = ["service-a"]
            evidence["specification"]["affectedSurfaces"] = evidence["specification"][
                "affectedSurfaces"
            ][:1]
            with self.assertRaisesRegex(ConfigurationError, "unavailableRepositories"):
                store.clear_current_gate("review", evidence)
            record_cost(store, "review", amount_usd=0.1, source="host retry")
            self.assertEqual(store.load("review")["security"]["usage"]["costUsd"], 0.1)

    def test_prepare_conflict_preserves_cost_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RuntimeFixture(Path(directory))
            fixture.enter_task_phase()
            created = []

            def interleave(root, *args, **kwargs):
                result = worktree_git(root, *args, **kwargs)
                if args[:2] == ("worktree", "add"):
                    created.append((Path(args[4]), args[3]))
                    record_cost(fixture.store, "runtime-run", amount_usd=0.1, source="host")
                return result

            with (
                patch("signal_harness.worktrees._run_git", side_effect=interleave),
                self.assertRaisesRegex(ResolutionError, "stale run state"),
            ):
                prepare_task(fixture.store, "runtime-run", "implementation")
            target, branch = created[0]
            self.assertFalse(target.exists())
            self.assertNotEqual(
                worktree_git(
                    fixture.root, "show-ref", "--verify", f"refs/heads/{branch}", check=False
                ).returncode,
                0,
            )
            state = fixture.store.load("runtime-run")
            self.assertEqual(state["security"]["usage"]["costUsd"], 0.1)
            self.assertEqual(len(state["costEvents"]), 1)
            self.assertIn("security.cost.recorded", [e["type"] for e in state["events"]])
            self.assertNotIn("task.prepared", [e["type"] for e in state["events"]])
            self.assertFalse(state["tasks"]["implementation"].get("worktree"))
            self.assertEqual(
                prepare_task(fixture.store, "runtime-run", "implementation")["status"], "prepared"
            )
            self.assertEqual(fixture.store.load("runtime-run")["security"]["usage"]["costUsd"], 0.1)

    def test_prepare_conflict_preserves_ignored_bytes_branch_and_concurrent_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RuntimeFixture(Path(directory))
            fixture.enter_task_phase()
            # A repository-local exclude applies to all linked worktrees.
            (fixture.root / ".git/info/exclude").write_text("credentials.bin\n")
            created = []
            payload = b"ignored local data\x00\xff\n"

            def interleave(root, *args, **kwargs):
                result = worktree_git(root, *args, **kwargs)
                if args[:2] == ("worktree", "add"):
                    target, branch = Path(args[4]), args[3]
                    created.append((target, branch))
                    (target / "credentials.bin").write_bytes(payload)
                    record_cost(fixture.store, "runtime-run", amount_usd=0.1, source="host")
                return result

            with (
                patch("signal_harness.worktrees._run_git", side_effect=interleave),
                self.assertRaisesRegex(ResolutionError, "preparation recovery required"),
            ):
                prepare_task(fixture.store, "runtime-run", "implementation")
            target, branch = created[0]
            self.assertEqual((target / "credentials.bin").read_bytes(), payload)
            self.assertEqual(
                worktree_git(target, "branch", "--show-current").stdout.strip(), branch
            )
            self.assertEqual(
                worktree_git(
                    fixture.root, "show-ref", "--verify", f"refs/heads/{branch}"
                ).returncode,
                0,
            )
            state = fixture.store.load("runtime-run")
            self.assertEqual(state["security"]["usage"]["costUsd"], 0.1)
            self.assertEqual(len(state["costEvents"]), 1)
            self.assertIn("security.cost.recorded", [event["type"] for event in state["events"]])
            self.assertNotIn("task.prepared", [event["type"] for event in state["events"]])
            self.assertFalse(state["tasks"]["implementation"].get("worktree"))

    def test_prepare_conflict_preserves_changed_or_recorded_resources(self):
        for change in ("dirty", "head", "branch", "recorded"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                fixture = RuntimeFixture(Path(directory))
                fixture.enter_task_phase()
                created = []

                def interleave(
                    root, *args, fixture=fixture, change=change, created=created, **kwargs
                ):
                    result = worktree_git(root, *args, **kwargs)
                    if args[:2] == ("worktree", "add"):
                        target = Path(args[4])
                        created.append(target)
                        if change in {"dirty", "head"}:
                            (target / "user-work.txt").write_text("preserve me")
                        if change == "head":
                            run("git", "add", ".", cwd=target)
                            run("git", "commit", "-m", "user work", cwd=target)
                        if change == "branch":
                            run("git", "checkout", "-b", "user-work", cwd=target)
                        record_cost(fixture.store, "runtime-run", amount_usd=0.1, source="host")
                        if change == "recorded":
                            state = fixture.store.load("runtime-run")
                            state["tasks"]["implementation"]["worktree"] = str(target)
                            fixture.store.save(state)
                    return result

                with (
                    patch("signal_harness.worktrees._run_git", side_effect=interleave),
                    self.assertRaisesRegex(ResolutionError, "preparation recovery required"),
                ):
                    prepare_task(fixture.store, "runtime-run", "implementation")
                self.assertTrue(created[0].is_dir())
                self.assertEqual(
                    fixture.store.load("runtime-run")["security"]["usage"]["costUsd"], 0.1
                )
                if change in {"dirty", "head"}:
                    self.assertEqual((created[0] / "user-work.txt").read_text(), "preserve me")

    def test_stale_saves_fail_closed_and_legacy_runs_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, _, store = self.discovery(Path(directory))
            path = store.state_path("review")
            legacy = store.load("review")
            del legacy["stateRevision"]
            path.write_text(json.dumps(legacy), encoding="utf-8")
            first, stale = store.load("review"), store.load("review")
            store.save(first)
            self.assertEqual(first["stateRevision"], 1)
            store.save(first)
            self.assertEqual(first["stateRevision"], 2)
            before = path.read_bytes()
            with self.assertRaisesRegex(ResolutionError, "stale run state"):
                store.save(stale)
            self.assertEqual(path.read_bytes(), before)

    def test_peer_profile_exclusions_are_locked_during_engagement(self):
        with tempfile.TemporaryDirectory() as directory:
            _, peer, _, _, store = self.discovery(Path(directory))
            self.engage(store, override=peer.root)
            before = store.load("review")
            locked = before["plan"]["repositories"][1]["context"]["files"]
            self.assertIn(".signal-harness/repo.yaml", {item["path"] for item in locked})
            original_head = head_sha(peer.root)
            value = profile(peer.id, peer.remote)
            value["context"]["exclude"] = []
            write_yaml(peer.root / ".signal-harness/repo.yaml", value)
            self.assertEqual(head_sha(peer.root), original_head)
            with self.assertRaisesRegex(ResolutionError, "previously locked context changed"):
                lock_discovery_context(
                    store,
                    "review",
                    peer.id,
                    paths=("docs/private/secret.md",),
                    reason="inspect peer",
                    actor="reviewer",
                )
            after = store.load("review")
            self.assertEqual(after["plan"], before["plan"])
            event = after["events"][-1]
            self.assertEqual(event["type"], "security.authorization.denied")
            self.assertEqual(event["detail"]["actor"], "reviewer")
            self.assertEqual(event["detail"]["reason"], "previous-context-changed")

    def test_manual_execution_rejects_profileless_task_owner_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            sponsor, peer, _, _, store = self.discovery(Path(directory))
            (peer.root / ".signal-harness/repo.yaml").unlink()
            run("git", "add", ".", cwd=peer.root)
            run("git", "commit", "-m", "profileless peer", cwd=peer.root)
            workspace = sponsor.root / "manual.yaml"
            write_yaml(
                workspace,
                {
                    "apiVersion": "signal-harness/v1alpha1",
                    "kind": "Workspace",
                    "feature": "manual",
                    "sponsor": sponsor.id,
                    "selection": {"include": [sponsor.id, peer.id]},
                    "repositories": {
                        sponsor.id: {"remote": sponsor.remote, "mode": "read-only"},
                        peer.id: {"remote": peer.remote, "mode": "read-write", "author": True},
                    },
                    "tasks": [{"id": "implementation", "repo": peer.id}],
                },
            )
            with self.assertRaisesRegex(ResolutionError, "executable task owner service-b"):
                plan = build_plan(workspace, sponsor.root, {peer.id: peer.root})
                store.create(plan, workspace, "manual")
            self.assertFalse(store.run_dir("manual").exists())

    def test_portable_snapshot_detects_discovery_permission_and_blocker_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, plan, _ = self.discovery(Path(directory))
            expected = snapshot_plan(plan)
            sponsor = plan.repositories[0]
            changed = replace(
                plan,
                repositories=(
                    replace(sponsor, workspace=replace(sponsor.workspace, author=False)),
                ),
            )
            self.assertIn(
                "$.repositories[0].author",
                [item["path"] for item in compare_snapshots(expected, snapshot_plan(changed))],
            )
            for changed, path in (
                (replace(plan, required_engagements=("service-b",)), "$.requiredEngagements[0]"),
                (
                    replace(
                        plan,
                        unavailable_repositories=(
                            {
                                "id": "service-b",
                                "remote": "example-org/service-b",
                                "requiredByDiscovery": True,
                                "reason": "/local/private/path",
                            },
                        ),
                    ),
                    "$.unavailableRepositories[0]",
                ),
                (
                    replace(
                        plan,
                        catalog_repositories=tuple(
                            {**item, "author": False} for item in plan.catalog_repositories
                        ),
                    ),
                    "$.catalogRepositories[0].author",
                ),
            ):
                snapshot = snapshot_plan(changed)
                self.assertIn(
                    path, [item["path"] for item in compare_snapshots(expected, snapshot)]
                )
                self.assertNotIn("/local/private/path", json.dumps(snapshot))
