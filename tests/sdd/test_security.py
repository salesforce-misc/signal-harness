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
from pathlib import Path
from unittest.mock import Mock

from signal_harness.control import cancel_run
from signal_harness.errors import ConfigurationError, ResolutionError
from signal_harness.retention import delete_run
from signal_harness.security import approve_command, authorize_command, record_cost
from signal_harness.verification import run_verification
from signal_harness.worktrees import prepare_task
from tests.sdd.test_runtime import RuntimeFixture


class SecurityPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = RuntimeFixture(Path(self.temp.name))
        self.fixture.enter_task_phase()
        prepare_task(self.fixture.store, "runtime-run", "implementation")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_profile_signal_commands_are_trusted_and_other_commands_need_approval(self) -> None:
        signals = run_verification(
            self.fixture.store, "runtime-run", "implementation", ("unit",), timeout=10
        )
        self.assertEqual(signals[0]["status"], "pass")

        with self.assertRaisesRegex(ConfigurationError, "not trusted"):
            run_verification(
                self.fixture.store, "runtime-run", "implementation", ("fail",), timeout=10
            )
        approval = approve_command(
            self.fixture.store,
            "runtime-run",
            repository="service-a",
            command_id="fail",
            actor="security@example.com",
        )
        self.assertEqual(approval["actor"], "security@example.com")
        approved = run_verification(
            self.fixture.store, "runtime-run", "implementation", ("fail",), timeout=10
        )
        self.assertEqual(approved[0]["status"], "fail")

    def test_shell_wrappers_and_excessive_timeouts_are_rejected(self) -> None:
        state = self.fixture.store.load("runtime-run")
        with self.assertRaisesRegex(ConfigurationError, "shell wrapper"):
            authorize_command(state, "service-a", "unsafe", "sh -c 'echo unsafe'", 10)
        with self.assertRaisesRegex(ConfigurationError, "cannot be parsed"):
            authorize_command(state, "service-a", "unsafe", "python -c 'unterminated", 10)
        with self.assertRaisesRegex(ConfigurationError, "maximum"):
            authorize_command(state, "service-a", "unit", "python -c 'print(1)'", 9999)

    def test_verification_redacts_secrets_before_persistence(self) -> None:
        signals = run_verification(
            self.fixture.store,
            "runtime-run",
            "implementation",
            ("unit",),
            timeout=10,
            runner=Mock(
                return_value=subprocess.CompletedProcess(
                    [], 0, "Authorization: Bearer super-secret-token\n", ""
                )
            ),
        )
        self.assertIn("[REDACTED]", signals[0]["metadata"]["stdout"])
        persisted = self.fixture.store.state_path("runtime-run").read_text(encoding="utf-8")
        self.assertNotIn("super-secret-token", persisted)

    def test_audit_log_tampering_is_detected(self) -> None:
        path = self.fixture.store.state_path("runtime-run")
        value = json.loads(path.read_text(encoding="utf-8"))
        value["events"][0]["detail"] = "tampered"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ResolutionError, "audit log integrity"):
            self.fixture.store.load("runtime-run")

    def test_cost_budget_fails_closed_before_exceeding_the_run_limit(self) -> None:
        recorded = record_cost(
            self.fixture.store,
            "runtime-run",
            amount_usd=10.25,
            source="codex-session",
        )
        self.assertEqual(recorded["costUsd"], 10.25)
        with self.assertRaisesRegex(ConfigurationError, "cost budget"):
            record_cost(
                self.fixture.store,
                "runtime-run",
                amount_usd=100.0,
                source="codex-session",
            )


class RetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = RuntimeFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_deletion_requires_terminal_run_exact_confirmation_and_no_worktrees(self) -> None:
        with self.assertRaisesRegex(ResolutionError, "terminal"):
            delete_run(self.fixture.store, "runtime-run", confirmation="runtime-run")
        cancel_run(
            self.fixture.store,
            "runtime-run",
            actor="operator@example.com",
            reason="retention test",
        )
        with self.assertRaisesRegex(ConfigurationError, "confirmation"):
            delete_run(self.fixture.store, "runtime-run", confirmation="wrong")

        result = delete_run(self.fixture.store, "runtime-run", confirmation="delete runtime-run")
        self.assertEqual(result["status"], "deleted")
        self.assertFalse(self.fixture.store.run_dir("runtime-run").exists())


if __name__ == "__main__":
    unittest.main()
