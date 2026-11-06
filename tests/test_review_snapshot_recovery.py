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

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tests.test_review_planning_lifecycle as planning_fixtures
import tests.test_review_regressions as review_fixtures
from signal_harness import git
from signal_harness.cli import main
from signal_harness.control import _next_actions
from signal_harness.errors import ResolutionError
from signal_harness.sdd import build_sdd_plan
from signal_harness.security import record_cost
from signal_harness.session import lock_discovery_context
from signal_harness.state import RunStore
from tests.test_engine import RepositoryFixture, run


class SnapshotRecoveryTests(unittest.TestCase):
    def remote(self, base):
        source = RepositoryFixture(base, "service-b")
        remote = base / "remote.git"
        run("git", "clone", "--bare", str(source.root), str(remote), cwd=base)
        run("git", "remote", "set-url", "origin", str(remote), cwd=source.root)
        return source.root, remote

    def test_timed_out_registered_missing_snapshot_retries_original_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, remote = self.remote(base)
            original_revision = git.head_sha(source)
            snapshots = base / "run"
            calls = []
            original_run_git = git._run_git

            def interrupted(*args, **kwargs):
                calls.append(args)
                result = original_run_git(*args, **kwargs)
                if args[:2] == ("worktree", "add"):
                    shutil.rmtree(Path(args[-2]))
                    raise git._GitTimeout("Git operation timed out after 120 seconds")
                return result

            with patch.dict("os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}):
                with (
                    patch("signal_harness.git._run_git", side_effect=interrupted),
                    self.assertRaisesRegex(
                        ResolutionError, "timed out.*original revision retained"
                    ),
                ):
                    git.managed_repository(str(remote), "peer", snapshot_root=snapshots)
                descriptor = next(snapshots.glob(".*.snapshot.json"))
                identity = descriptor.read_bytes()
                cache = next(path for path in (base / "cache").glob("peer-*") if path.is_dir())
                unrelated = base / "unrelated"
                git._run_git(
                    "worktree", "add", "--detach", str(unrelated), original_revision, cwd=cache
                )
                sentinel = unrelated / "operator-data"
                sentinel.write_bytes(b"preserve unrelated")
                (source / "README.md").write_text("advanced\n")
                run("git", "add", ".", cwd=source)
                run("git", "commit", "-m", "advance", cwd=source)
                run("git", "push", "origin", "main", cwd=source)
                with patch("signal_harness.git._run_git", wraps=git._run_git) as process:
                    snapshot = git.managed_repository(str(remote), "peer", snapshot_root=snapshots)
                self.assertEqual(git.head_sha(snapshot), original_revision)
                self.assertEqual(descriptor.read_bytes(), identity)
                self.assertEqual(sentinel.read_bytes(), b"preserve unrelated")
                self.assertTrue(unrelated.is_dir())
                adds = [
                    call.args
                    for call in process.call_args_list
                    if call.args[:2] == ("worktree", "add")
                ]
                self.assertEqual(len(adds), 1)
                self.assertEqual(adds[0].count("--force"), 1)

    def test_missing_locked_or_mismatched_registration_is_preserved(self):
        for mutation in ("locked", "revision"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                _, remote = self.remote(base)
                with patch.dict(
                    "os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}
                ):
                    snapshot = git.managed_repository(
                        str(remote), "peer", snapshot_root=base / "run"
                    )
                    cache = Path(git._run_git("rev-parse", "--git-common-dir", cwd=snapshot)).parent
                    descriptor = next(snapshot.parent.glob(".*.snapshot.json"))
                    if mutation == "locked":
                        git._run_git(
                            "worktree", "lock", "--reason", "operator", str(snapshot), cwd=cache
                        )
                    else:
                        identity = json.loads(descriptor.read_text())
                        identity["revision"] = "0" * 40
                        descriptor.write_text(json.dumps(identity))
                    shutil.rmtree(snapshot)
                    registration = git._run_git("worktree", "list", "--porcelain", cwd=cache)
                    identity_bytes = descriptor.read_bytes()
                    with self.assertRaisesRegex(ResolutionError, "recovery required"):
                        git.managed_repository(str(remote), "peer", snapshot_root=base / "run")
                    self.assertEqual(
                        git._run_git("worktree", "list", "--porcelain", cwd=cache), registration
                    )
                    self.assertEqual(descriptor.read_bytes(), identity_bytes)
                    self.assertFalse(snapshot.exists())

    def test_partial_snapshot_and_symlinks_are_preserved(self):
        for mutation in ("partial", "dirty", "symlink", "identity-symlink"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                _, remote = self.remote(base)
                with patch.dict(
                    "os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}
                ):
                    snapshot = git.managed_repository(
                        str(remote), "peer", snapshot_root=base / "run"
                    )
                    descriptor = next(snapshot.parent.glob(".*.snapshot.json"))
                    if mutation in {"partial", "symlink"}:
                        shutil.rmtree(snapshot)
                    if mutation == "partial":
                        snapshot.mkdir()
                    if mutation == "symlink":
                        other = base / "operator-data"
                        other.mkdir()
                        snapshot.symlink_to(other, target_is_directory=True)
                    if mutation == "identity-symlink":
                        other = base / "operator-identity.json"
                        descriptor.rename(other)
                        descriptor.symlink_to(other)
                    sentinel = snapshot / "operator-data.bin"
                    sentinel.write_bytes(b"preserve\x00\xff")
                    before = descriptor.read_bytes()
                    with self.assertRaisesRegex(ResolutionError, "recovery required|symlink|dirty"):
                        git.managed_repository(str(remote), "peer", snapshot_root=base / "run")
                    self.assertEqual(sentinel.read_bytes(), b"preserve\x00\xff")
                    self.assertEqual(descriptor.read_bytes(), before)

    def test_sponsor_acquisition_diagnostics_are_redacted_and_profile_error_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            invoking, _, catalog = planning_fixtures.PlanningLifecycleTests().fixture(base)
            with (
                patch("signal_harness.sdd.load_policy_catalog", return_value=catalog),
                patch.dict("os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}),
            ):
                for diagnostic in (
                    "Git operation timed out after 120 seconds",
                    "authentication failed for https://user:credential@host/repo.git token=private",
                ):
                    with (
                        patch(
                            "signal_harness.sdd.managed_repository",
                            side_effect=ResolutionError(diagnostic),
                        ),
                        self.assertRaisesRegex(ResolutionError, "acquisition failed") as raised,
                    ):
                        build_sdd_plan(
                            feature="review", current_path=invoking.root, sponsor_id="service-b"
                        )
                    message = str(raised.exception)
                    self.assertIn(
                        "timed out" if "timed out" in diagnostic else "authentication failed",
                        message,
                    )
                    self.assertNotIn("credential", message)
                    self.assertNotIn("private", message)
                with (
                    patch(
                        "signal_harness.sdd.discover_catalog_roots",
                        return_value={"service-b": invoking.root},
                    ),
                    patch("signal_harness.sdd._load_profiles", return_value={}),
                    self.assertRaisesRegex(ResolutionError, "no local checkout/profile") as raised,
                ):
                    build_sdd_plan(
                        feature="review", current_path=invoking.root, sponsor_id="service-b"
                    )
                self.assertNotIn("acquisition failed", str(raised.exception))

    def test_context_movement_denials_audit_without_plan_changes(self):
        for movement in ("before", "during"):
            with self.subTest(movement=movement), tempfile.TemporaryDirectory() as directory:
                _, _, _, _, store = review_fixtures.ReviewRegressionTests().discovery(
                    Path(directory)
                )
                before = store.load("review")
                from signal_harness.session import collect_context

                def moved(*args, **kwargs):
                    from dataclasses import replace

                    return replace(collect_context(*args, **kwargs), revision="0" * 40)

                target = (
                    "signal_harness.session.head_sha"
                    if movement == "before"
                    else "signal_harness.session.collect_context"
                )
                options = (
                    {"return_value": "0" * 40} if movement == "before" else {"side_effect": moved}
                )
                with patch(target, **options), self.assertRaisesRegex(ResolutionError, "moved"):
                    lock_discovery_context(
                        store,
                        "review",
                        "service-a",
                        paths=("implementation.py",),
                        reason="inspect",
                        actor="operator",
                    )
                after = store.load("review")
                self.assertEqual(after["plan"], before["plan"])
                detail = after["events"][-1]["detail"]
                self.assertEqual(detail["actor"], "operator")
                self.assertEqual(detail["action"], "lock-context")
                self.assertEqual(detail["outcome"], "denied")
                self.assertIn("repository-moved", detail["reason"])

    def test_peer_diagnostics_are_sanitized_before_cli_and_state_sinks(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = review_fixtures.ReviewRegressionTests()
            _, peer, _, _, store = fixture.discovery(Path(directory))
            diagnostic = (
                "authentication failed https://user:url-secret@host/repo.git "
                "token=assignment-secret " + "x" * 600 + " token=trailing-secret"
            )
            output = io.StringIO()
            with (
                patch(
                    "signal_harness.session.managed_repository",
                    side_effect=ResolutionError(diagnostic),
                ),
                patch("sys.stdout", output),
            ):
                self.assertEqual(
                    main(
                        [
                            "engage-repository",
                            "review",
                            "service-b",
                            "--reason",
                            "Source calls peer",
                            "--evidence",
                            "service-a:AGENTS.md",
                            "--state-root",
                            str(store.root),
                        ]
                    ),
                    0,
                )
            result = json.loads(output.getvalue())
            state = store.load("review")
            error = result["error"]
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["action"], "obtain-repository-access")
            self.assertEqual(len(error), 500)
            self.assertIn("[remote]", error)
            self.assertIn("[REDACTED]", error)
            self.assertEqual(state["plan"]["unavailableRepositories"][0]["reason"], error)
            self.assertEqual(state["repositoryEngagement"]["service-b"]["error"], error)
            serialized = output.getvalue() + store.state_path("review").read_text()
            for secret in ("url-secret", "assignment-secret", "trailing-secret"):
                self.assertNotIn(secret, serialized)
            self.assertEqual(state["events"][-1]["type"], "discovery.repository.unavailable")
            self.assertEqual(fixture.engage(store, override=peer.root)["status"], "engaged")
            self.assertEqual(store.load("review")["plan"]["unavailableRepositories"], [])

    def test_context_denial_preserves_cost_during_collection_and_cas_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, _, store = review_fixtures.ReviewRegressionTests().discovery(Path(directory))
            before = store.load("review")
            from dataclasses import replace

            from signal_harness.session import collect_context

            def moved(*args, **kwargs):
                record_cost(store, "review", amount_usd=0.1, source="during collection")
                return replace(collect_context(*args, **kwargs), revision="0" * 40)

            original_save = RunStore.save
            attempts = 0

            def save(current_store, state):
                nonlocal attempts
                if state["events"][-1]["type"] == "security.authorization.denied":
                    attempts += 1
                    if attempts == 1:
                        record_cost(store, "review", amount_usd=0.2, source="before audit save")
                return original_save(current_store, state)

            with (
                patch("signal_harness.session.collect_context", side_effect=moved),
                patch.object(RunStore, "save", save),
                self.assertRaisesRegex(ResolutionError, "moved while locking"),
            ):
                lock_discovery_context(
                    store, "review", "service-a", paths=("implementation.py",), reason="inspect"
                )
            after = store.load("review")
            self.assertEqual(attempts, 2)
            self.assertEqual(after["plan"], before["plan"])
            self.assertEqual(after["planHash"], before["planHash"])
            self.assertEqual(after["security"]["usage"]["costUsd"], 0.3)
            new_events = after["events"][len(before["events"]) :]
            self.assertEqual(
                [event["type"] for event in new_events],
                [
                    "security.cost.recorded",
                    "security.cost.recorded",
                    "security.authorization.denied",
                ],
            )

    def test_context_denial_retries_only_stale_saves_and_is_bounded(self):
        for error, attempts in (
            ("stale run state for review; reload before retrying the operation", 3),
            ("unrelated persistence failure", 1),
        ):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                _, _, _, _, store = review_fixtures.ReviewRegressionTests().discovery(
                    Path(directory)
                )
                with (
                    patch("signal_harness.session.head_sha", return_value="0" * 40),
                    patch.object(RunStore, "save", side_effect=ResolutionError(error)) as save,
                    self.assertRaisesRegex(ResolutionError, error),
                ):
                    lock_discovery_context(
                        store, "review", "service-a", paths=("implementation.py",), reason="inspect"
                    )
                self.assertEqual(save.call_count, attempts)

    def test_optional_access_assessment_remains_available(self):
        state = {
            "status": "active",
            "workflow": {"currentStage": "0"},
            "plan": {"unavailableRepositories": [{"id": "optional"}]},
        }
        self.assertEqual(_next_actions(state, [])[0], "assess-repository-access")
