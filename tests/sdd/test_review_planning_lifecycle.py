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
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from signal_harness import git
from signal_harness.catalog import CatalogRepository, RepositoryCatalog
from signal_harness.cli import main
from signal_harness.errors import ResolutionError
from signal_harness.sdd import build_sdd_plan, release_sdd_plan
from signal_harness.state import RunStore
from tests.sdd.test_engine import EXAMPLE_WORKFLOW, RepositoryFixture, profile, run, write_yaml


class PlanningLifecycleTests(unittest.TestCase):
    def fixture(self, base):
        source = RepositoryFixture(base, "service-b")
        remote = base / "remote.git"
        run("git", "clone", "--bare", str(source.root), str(remote), cwd=base)
        run("git", "remote", "set-url", "origin", str(remote), cwd=source.root)
        write_yaml(source.root / ".signal-harness/repo.yaml", profile("service-b", str(remote)))
        feature = source.root / "specs/area/sdd/review"
        write_yaml(
            feature / "manifest.yaml",
            {
                "feature": "review",
                "sources": ["specs/area/sdd/review/source.md"],
                "constraints": ["AGENTS.md"],
            },
        )
        (feature / "source.md").write_text("source\n")
        run("git", "add", ".", cwd=source.root)
        run("git", "commit", "-m", "feature", cwd=source.root)
        run("git", "push", "origin", "main", cwd=source.root)
        invoking = RepositoryFixture(base, "service-a")
        catalog = RepositoryCatalog(
            id="fixture",
            workflow=EXAMPLE_WORKFLOW,
            repositories={
                "service-a": CatalogRepository(id="service-a", remote=invoking.remote, author=True),
                "service-b": CatalogRepository(id="service-b", remote=str(remote), author=True),
            },
        )
        return invoking, remote, catalog

    def assert_released(self, base):
        self.assertEqual(list((base / "cache/plans").iterdir()), [])
        cache = next(path for path in (base / "cache").glob("service-b-*") if path.is_dir())
        worktrees = git._run_git("worktree", "list", "--porcelain", cwd=cache)
        self.assertEqual(worktrees.count("worktree "), 1)

    def test_repeated_cli_previews_release_only_their_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            invoking, _, catalog = self.fixture(base)
            with (
                patch.dict("os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}),
                patch("signal_harness.sdd.load_policy_catalog", return_value=catalog),
            ):
                # Keep a persisted snapshot alive while multiple previews come and go.
                persisted = build_sdd_plan(
                    feature="review", current_path=invoking.root, sponsor_id="service-b"
                )
                sponsor = persisted.repositories[0]
                store = RunStore(base / "state")
                store.create(persisted, sponsor.root / sponsor.workspace.feature_manifest, "saved")
                before = store.load("saved")
                for _ in range(3):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        result = main(
                            [
                                "sdd",
                                "review",
                                "--cwd",
                                str(invoking.root),
                                "--sponsor",
                                "service-b",
                                "--policy",
                                "fixture",
                            ]
                        )
                    self.assertEqual(result, 0)
                    preview = json.loads(output.getvalue())
                    preview_root = Path(preview["repositories"][0]["root"])
                    self.assertNotEqual(preview_root, sponsor.root)
                    self.assertFalse(preview_root.exists())
                    self.assertTrue(sponsor.root.is_dir())
                    self.assertEqual(store.load("saved"), before)
                self.assertEqual(len(list((base / "cache/plans").iterdir())), 1)

    def test_build_and_create_failures_release_owned_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            invoking, _, catalog = self.fixture(base)
            with (
                patch.dict("os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}),
                patch("signal_harness.sdd.load_policy_catalog", return_value=catalog),
            ):
                with self.assertRaisesRegex(ResolutionError, "exactly one manifest"):
                    build_sdd_plan(
                        feature="missing", current_path=invoking.root, sponsor_id="service-b"
                    )
                self.assert_released(base)
                original = git._run_git

                def fail_add(*args, **kwargs):
                    if args[:2] == ("worktree", "add"):
                        raise ResolutionError("worktree add failed after identity publication")
                    return original(*args, **kwargs)

                with (
                    patch("signal_harness.git._run_git", side_effect=fail_add),
                    self.assertRaisesRegex(ResolutionError, "no local checkout/profile"),
                ):
                    build_sdd_plan(
                        feature="review", current_path=invoking.root, sponsor_id="service-b"
                    )
                self.assert_released(base)
                with (
                    patch.object(RunStore, "create", side_effect=ResolutionError("create failed")),
                    redirect_stderr(io.StringIO()),
                ):
                    result = main(
                        [
                            "sdd",
                            "review",
                            "--cwd",
                            str(invoking.root),
                            "--sponsor",
                            "service-b",
                            "--policy",
                            "fixture",
                            "--start",
                        ]
                    )
                self.assertEqual(result, 2)
                self.assert_released(base)

    def test_api_release_is_idempotent_and_preserves_changed_resources(self):
        for change in ("clean", "dirty", "ignored", "head", "branch", "identity"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                invoking, _, catalog = self.fixture(base)
                with (
                    patch.dict(
                        "os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}
                    ),
                    patch("signal_harness.sdd.load_policy_catalog", return_value=catalog),
                ):
                    plan = build_sdd_plan(
                        feature="review", current_path=invoking.root, sponsor_id="service-b"
                    )
                    root = plan.repositories[0].root
                    if change in {"dirty", "ignored", "head"}:
                        if change == "ignored":
                            common = Path(
                                git._run_git(
                                    "rev-parse",
                                    "--path-format=absolute",
                                    "--git-common-dir",
                                    cwd=root,
                                )
                            )
                            (common / "info/exclude").write_text("user-data\n")
                        (root / "user-data").write_bytes(b"preserve\x00\xff")
                    if change == "head":
                        run("git", "add", ".", cwd=root)
                        run(
                            "git",
                            "-c",
                            "user.name=Test",
                            "-c",
                            "user.email=test@example.com",
                            "commit",
                            "-m",
                            "user work",
                            cwd=root,
                        )
                    if change == "branch":
                        run("git", "checkout", "-b", "user-work", cwd=root)
                    if change == "identity":
                        identity = next(root.parent.glob(".*.snapshot.json"))
                        identity.write_text(identity.read_text() + "\n")
                    if change == "clean":
                        release_sdd_plan(plan)
                        release_sdd_plan(plan)
                        self.assert_released(base)
                    else:
                        with self.assertRaisesRegex(ResolutionError, "recovery required"):
                            release_sdd_plan(plan)
                        self.assertTrue(root.is_dir())
                        self.assertTrue(list(root.parent.glob(".*.snapshot.json")))
                        if change in {"dirty", "ignored", "head"}:
                            self.assertEqual((root / "user-data").read_bytes(), b"preserve\x00\xff")

    def test_remote_options_rejected_before_any_git_execution(self):
        for remote in ("--upload-pack=evil", "--upload-pack=/tmp/a/b", "-x/repo"):
            with self.subTest(remote=remote), patch("signal_harness.git.subprocess.run") as process:
                with self.assertRaisesRegex(ResolutionError, "hyphen"):
                    git.managed_repository(remote, "peer")
                process.assert_not_called()
        for remote, expected in (
            ("owner/repo", "git@github.com:owner/repo.git"),
            ("https://host/owner/repo.git", "https://host/owner/repo.git"),
            ("git@host:owner/repo.git", "git@host:owner/repo.git"),
            ("/tmp/repo.git", "/tmp/repo.git"),
        ):
            self.assertEqual(git.remote_clone_url(remote), expected)

    def test_git_processes_are_bounded_noninteractive_and_timeout_is_sanitized(self):
        completed = subprocess.CompletedProcess(["git"], 0, "ok", "")
        with patch("signal_harness.git.subprocess.run", return_value=completed) as process:
            self.assertEqual(git._run_git("fetch", "origin"), "ok")
            git.head_sha(Path("."))
            git.is_ancestor(Path("."), "a", "b")
        for invocation in process.call_args_list:
            self.assertEqual(invocation.kwargs["timeout"], 120)
            self.assertEqual(invocation.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(invocation.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        with patch(
            "signal_harness.git.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git", "SECRET"], 120),
        ):
            with self.assertRaisesRegex(ResolutionError, "timed out") as raised:
                git._run_git("fetch", "SECRET")
            self.assertNotIn("SECRET", str(raised.exception))

    def test_acquisition_timeout_releases_lock_and_does_not_use_default_fallback(self):
        for operation in ("clone", "fetch", "remote", "symbolic-ref", "show-ref"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                _, remote, _ = self.fixture(base)
                original = subprocess.run
                calls = []

                def process(
                    command, *, calls=calls, operation=operation, original=original, **kwargs
                ):
                    calls.append(command)
                    actual = command[3:] if command[1:2] == ["-C"] else command[1:]
                    if actual[0] == operation:
                        raise subprocess.TimeoutExpired(command, 120)
                    return original(command, **kwargs)

                with patch.dict(
                    "os.environ", {"SIGNAL_HARNESS_REPOSITORY_CACHE": str(base / "cache")}
                ):
                    with (
                        patch("signal_harness.git.subprocess.run", side_effect=process),
                        self.assertRaisesRegex(ResolutionError, "timed out"),
                    ):
                        git.managed_repository(str(remote), "peer")
                    # The retry would block if the timed-out invocation retained its lock.
                    snapshot = git.managed_repository(str(remote), "peer")
                    self.assertTrue(snapshot.is_dir())
                clone = next(command for command in calls if command[1] == "clone")
                self.assertEqual(clone[clone.index("--filter=blob:none") + 1], "--")
