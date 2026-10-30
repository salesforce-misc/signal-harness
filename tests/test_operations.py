from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from signal_harness.control import (
    cancel_run,
    pause_run,
    recover_run,
    resume_run,
    run_summary,
)
from signal_harness.errors import ResolutionError
from signal_harness.findings import record_finding, resolve_finding
from signal_harness.integrations import (
    dispatch_outbox,
    enqueue_integration_event,
    record_external_id,
    request_human_input,
    resolve_human_input,
)
from signal_harness.leases import claim_task, release_task
from signal_harness.verification import run_verification
from signal_harness.worktrees import prepare_task
from tests.test_runtime import RuntimeFixture


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, event: dict) -> str:
        self.calls.append(event)
        return f"external-{event['id']}"


class FlakyProvider(RecordingProvider):
    def send(self, event: dict) -> str:
        self.calls.append(event)
        if len(self.calls) == 1:
            raise RuntimeError("temporary provider failure token=synthetic-private-value")
        return f"external-{event['id']}"


class RunControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = RuntimeFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_pause_resume_cancel_and_recovery_are_explicit(self) -> None:
        paused = pause_run(
            self.fixture.store, "runtime-run", actor="operator@example.com", reason="maintenance"
        )
        self.assertEqual(paused["status"], "paused")
        resumed = resume_run(self.fixture.store, "runtime-run", "operator@example.com")
        self.assertEqual(resumed["status"], "active")
        cancelled = cancel_run(
            self.fixture.store, "runtime-run", actor="operator@example.com", reason="superseded"
        )
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaisesRegex(ResolutionError, "cancelled"):
            resume_run(self.fixture.store, "runtime-run", "operator@example.com")
        diagnosis = recover_run(self.fixture.store, "runtime-run")
        self.assertFalse(diagnosis["recoverable"])
        self.assertIn("terminal", diagnosis["blockers"][0])

    def test_summary_exposes_stage_evidence_and_actionable_waits(self) -> None:
        request_human_input(
            self.fixture.store,
            "runtime-run",
            request_id="scope-choice",
            kind="question",
            prompt="Which compatibility mode should be used?",
            channels=("slack",),
        )
        summary = run_summary(self.fixture.store, "runtime-run")
        self.assertEqual(summary["status"], "waiting")
        self.assertEqual(summary["currentStage"], "0")
        self.assertEqual(summary["pendingHumanRequests"], ["scope-choice"])
        self.assertIn("resolve-human", summary["nextActions"])

    def test_summary_projects_completed_workflow_task_without_breaking_replay_state(self) -> None:
        state = self.fixture.store.load("runtime-run")
        state["tasks"]["implementation"]["status"] = "prepared"
        state["workflow"]["tasks"]["implementation"]["status"] = "complete"
        self.fixture.store.save(state)

        summary = run_summary(self.fixture.store, "runtime-run")

        self.assertEqual(summary["tasks"]["implementation"]["status"], "complete")
        self.assertEqual(
            self.fixture.store.load("runtime-run")["tasks"]["implementation"]["status"],
            "prepared",
        )


class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = RuntimeFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_human_request_and_external_updates_are_idempotent(self) -> None:
        created = request_human_input(
            self.fixture.store,
            "runtime-run",
            request_id="spec-approval",
            kind="approval",
            prompt="Approve the resolved specification?",
            channels=("slack", "work_items"),
        )
        repeated = request_human_input(
            self.fixture.store,
            "runtime-run",
            request_id="spec-approval",
            kind="approval",
            prompt="Approve the resolved specification?",
            channels=("slack", "work_items"),
        )
        self.assertEqual(created, repeated)

        slack = RecordingProvider()
        work_items = RecordingProvider()
        first = dispatch_outbox(
            self.fixture.store, "runtime-run", {"slack": slack, "work_items": work_items}
        )
        second = dispatch_outbox(
            self.fixture.store, "runtime-run", {"slack": slack, "work_items": work_items}
        )
        self.assertEqual(first["sent"], 2)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(len(slack.calls), 1)
        self.assertEqual(len(work_items.calls), 1)

        record_external_id(
            self.fixture.store, "runtime-run", "work_items", "work-item", "ITEM-12345678"
        )
        record_external_id(
            self.fixture.store, "runtime-run", "work_items", "work-item", "ITEM-12345678"
        )
        with self.assertRaisesRegex(ResolutionError, "already mapped"):
            record_external_id(
                self.fixture.store, "runtime-run", "work_items", "work-item", "ITEM-99999999"
            )

        resolved = resolve_human_input(
            self.fixture.store,
            "runtime-run",
            "spec-approval",
            actor="reviewer@example.com",
            decision="approved",
            response="Approved for implementation",
        )
        replay = resolve_human_input(
            self.fixture.store,
            "runtime-run",
            "spec-approval",
            actor="reviewer@example.com",
            decision="approved",
            response="Approved for implementation",
        )
        self.assertEqual(resolved, replay)
        self.assertEqual(self.fixture.store.load("runtime-run")["status"], "active")

    def test_disabled_integrations_leave_notifications_pending(self) -> None:
        request_human_input(
            self.fixture.store,
            "runtime-run",
            request_id="local-only",
            kind="question",
            prompt="Confirm local behavior",
            channels=("slack",),
        )
        result = dispatch_outbox(self.fixture.store, "runtime-run", {})
        self.assertEqual(result, {"sent": 0, "pending": 1, "failed": 0})

    def test_interrupted_callback_is_persisted_and_retry_is_idempotent(self) -> None:
        request_human_input(
            self.fixture.store,
            "runtime-run",
            request_id="retryable",
            kind="question",
            prompt="Confirm retry behavior",
            channels=("slack",),
        )
        provider = FlakyProvider()
        failed = dispatch_outbox(self.fixture.store, "runtime-run", {"slack": provider})
        persisted = self.fixture.store.state_path("runtime-run").read_text()
        self.assertNotIn("synthetic-private-value", persisted)
        self.assertIn("[REDACTED]", persisted)
        retried = dispatch_outbox(self.fixture.store, "runtime-run", {"slack": provider})
        replay = dispatch_outbox(self.fixture.store, "runtime-run", {"slack": provider})

        self.assertEqual(failed, {"sent": 0, "pending": 1, "failed": 1})
        self.assertEqual(retried, {"sent": 1, "pending": 0, "failed": 0})
        self.assertEqual(replay, {"sent": 0, "pending": 0, "failed": 0})
        self.assertEqual(len(provider.calls), 2)

    def test_slack_and_work_items_lifecycle_events_share_the_replay_safe_outbox(self) -> None:
        events = (
            ("slack-review-42", "slack", "pull-request.review-requested"),
            ("work_items-drive", "work_items", "work-item.feature.upsert"),
            ("work_items-stage-2", "work_items", "work-item.stage.transition"),
            ("work_items-task-1", "work_items", "work-item.task.upsert"),
            ("work_items-chatter-1", "work_items", "work-item.comment.post"),
        )
        for event_id, channel, kind in events:
            enqueue_integration_event(
                self.fixture.store,
                "runtime-run",
                event_id=event_id,
                channel=channel,
                kind=kind,
                payload={"feature": "fixture"},
            )
        enqueue_integration_event(
            self.fixture.store,
            "runtime-run",
            event_id="work_items-drive",
            channel="work_items",
            kind="work-item.feature.upsert",
            payload={"feature": "fixture"},
        )

        state = self.fixture.store.load("runtime-run")
        self.assertEqual(len(state["outbox"]), 5)


class LeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = RuntimeFixture(Path(self.temp.name))
        self.fixture.enter_task_phase()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_repository_lease_is_exclusive_replayable_and_releasable(self) -> None:
        lease = claim_task(
            self.fixture.store,
            "runtime-run",
            "implementation",
            owner="session-a",
            ttl_seconds=300,
        )
        replay = claim_task(
            self.fixture.store,
            "runtime-run",
            "implementation",
            owner="session-a",
            ttl_seconds=300,
        )
        self.assertEqual(lease["token"], replay["token"])
        with self.assertRaisesRegex(ResolutionError, "leased"):
            claim_task(
                self.fixture.store,
                "runtime-run",
                "implementation",
                owner="session-b",
                ttl_seconds=300,
            )
        release_task(
            self.fixture.store,
            "runtime-run",
            "implementation",
            owner="session-a",
            token=lease["token"],
        )
        claimed = claim_task(
            self.fixture.store,
            "runtime-run",
            "implementation",
            owner="session-b",
            ttl_seconds=300,
        )
        self.assertEqual(claimed["owner"], "session-b")


class FindingLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = RuntimeFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_review_findings_require_an_explicit_replay_safe_disposition(self) -> None:
        finding = record_finding(
            self.fixture.store,
            "runtime-run",
            "implementation",
            finding_id="review-1",
            source="github-inline-review",
            severity="high",
            summary="Authorization guard is missing",
            evidence="https://github.example/review/1",
        )
        replay = record_finding(
            self.fixture.store,
            "runtime-run",
            "implementation",
            finding_id="review-1",
            source="github-inline-review",
            severity="high",
            summary="Authorization guard is missing",
            evidence="https://github.example/review/1",
        )
        self.assertEqual(finding, replay)
        with self.assertRaisesRegex(ResolutionError, "evidence"):
            resolve_finding(
                self.fixture.store,
                "runtime-run",
                "implementation",
                "review-1",
                disposition="disagree",
                actor="developer@example.com",
                rationale="The guard is applied by the interceptor",
            )
        resolved = resolve_finding(
            self.fixture.store,
            "runtime-run",
            "implementation",
            "review-1",
            disposition="disagree",
            actor="developer@example.com",
            rationale="The guard is applied by the interceptor",
            evidence="src/security/AuthorizationInterceptor.java",
        )
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["resolution"]["disposition"], "disagree")

    def test_behavioral_fixes_require_regression_lifecycle_and_targeted_rerun(self) -> None:
        self.fixture.enter_task_phase()
        prepare_task(self.fixture.store, "runtime-run", "implementation")
        record_finding(
            self.fixture.store,
            "runtime-run",
            "implementation",
            finding_id="review-regression",
            source="github-inline-review",
            severity="high",
            summary="Null handling changes behavior",
            evidence="https://github.example/review/2",
            affected_commands=("unit",),
            behavioral_fix=True,
        )
        with self.assertRaisesRegex(ResolutionError, "regression TDD evidence"):
            resolve_finding(
                self.fixture.store,
                "runtime-run",
                "implementation",
                "review-regression",
                disposition="address",
                actor="developer@example.com",
                rationale="Added the missing behavior",
            )
        regression = {
            "status": "pass",
            "signals": ["signal://regression/green"],
            "testCases": [
                {
                    "id": "review-regression-case",
                    "findingId": "review-regression",
                    "kind": "delta",
                    "requirement": "Null input is handled",
                    "lifecycle": [
                        {
                            "phase": "red",
                            "status": "fail",
                            "failureKind": "assertion",
                            "signal": "signal://regression/red",
                        },
                        {
                            "phase": "green",
                            "status": "pass",
                            "signal": "signal://regression/green",
                        },
                        {
                            "phase": "refactor",
                            "status": "pass",
                            "signal": "signal://regression/refactor",
                        },
                    ],
                }
            ],
        }
        resolve_finding(
            self.fixture.store,
            "runtime-run",
            "implementation",
            "review-regression",
            disposition="address",
            actor="developer@example.com",
            rationale="Added the missing behavior",
            regression_evidence=regression,
        )
        from signal_harness.findings import review_readiness

        blocked = review_readiness(self.fixture.store.load("runtime-run"), "implementation")
        self.assertFalse(blocked["ready"])
        run_verification(self.fixture.store, "runtime-run", "implementation", ("unit",))
        ready = review_readiness(self.fixture.store.load("runtime-run"), "implementation")
        self.assertTrue(ready["ready"])


if __name__ == "__main__":
    unittest.main()
