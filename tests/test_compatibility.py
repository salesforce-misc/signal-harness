from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from signal_harness.compatibility import compare_snapshots, snapshot_plan, snapshot_run
from signal_harness.planner import build_plan
from tests import test_engine
from tests.test_runtime import RuntimeFixture


class CompatibilitySnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = test_engine.WorkspaceTests(
            "test_builds_multi_repo_plan_and_skips_excluded_repo"
        )
        self.fixture.setUp()
        self.a = self.fixture.a
        self.b = self.fixture.b

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def test_invocation_location_does_not_change_portable_plan(self) -> None:
        value = self.fixture.workspace()
        value["workflow"] = "sdd.yaml"
        workspace = self.fixture.write_workspace(value)
        from_sponsor = build_plan(workspace, self.a.root, {"service-b": self.b.root})
        from_participant = build_plan(
            workspace,
            self.b.root,
            {"service-a": self.a.root, "service-b": self.b.root},
        )

        self.assertEqual(snapshot_plan(from_sponsor), snapshot_plan(from_participant))

    def test_drift_report_names_exact_compatibility_paths(self) -> None:
        value = self.fixture.workspace()
        value["workflow"] = "sdd.yaml"
        workspace = self.fixture.write_workspace(value)
        expected = snapshot_plan(build_plan(workspace, self.a.root, {"service-b": self.b.root}))
        actual = json.loads(json.dumps(expected))
        actual["repositories"][0]["commands"]["unit"] = "./gradlew changedTest"
        actual["workflow"][0]["gate"]["kind"] = "human"

        differences = compare_snapshots(expected, actual)

        self.assertEqual(
            [item["path"] for item in differences],
            [
                "$.repositories[0].commands.unit",
                "$.workflow[0].gate.kind",
            ],
        )

    def test_checked_in_native_fixture_is_machine_readable(self) -> None:
        fixture = Path("tests/fixtures/compatibility/native-sdd-v1.json")
        value = json.loads(fixture.read_text(encoding="utf-8"))
        self.assertEqual(value["schemaVersion"], 1)
        self.assertEqual(
            sorted(source["repository"] for source in value["nativeSources"]),
            ["service-a", "service-b"],
        )
        self.assertEqual([stage["id"] for stage in value["workflow"]], [str(i) for i in range(7)])


class RunCompatibilityTests(unittest.TestCase):
    def test_run_snapshot_covers_portable_results_without_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RuntimeFixture(Path(directory))
            state = fixture.store.load("runtime-run")
            state["artifacts"] = [
                {"kind": "decision", "reference": "artifact://decision/1", "task": None}
            ]
            state["tasks"]["implementation"]["pullRequest"] = {
                "url": "https://github.example/service-a/pull/1",
                "head": "dev/example",
                "base": "main",
                "state": "OPEN",
                "updatedAt": "ignored",
            }
            state["tasks"]["implementation"]["signals"] = [
                {"kind": "test", "status": "pass", "summary": "unit passed"}
            ]
            first = snapshot_run(state)
            state["updatedAt"] = "a different timestamp"
            second = snapshot_run(state)

            self.assertEqual(first, second)
            self.assertEqual(first["artifacts"][0]["reference"], "artifact://decision/1")
            self.assertEqual(first["tasks"]["implementation"]["pullRequest"]["base"], "main")
            self.assertIn("0", first["gates"])


if __name__ == "__main__":
    unittest.main()
