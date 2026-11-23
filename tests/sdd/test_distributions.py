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
from unittest.mock import Mock, patch

import yaml

from signal_harness import __version__
from signal_harness.distributions import (
    Distribution,
    load_distribution,
    load_distribution_manifest,
)
from signal_harness.errors import ConfigurationError


def write_distribution(root: Path, requires_core: str = ">=0.9,<0.10") -> Path:
    policy = root / "policy"
    policy.mkdir(parents=True)
    (policy / "catalog.yaml").write_text(
        (Path(__file__).parent / "fixtures/policy/catalog.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (policy / "sdd.yaml").write_text(
        (Path(__file__).parent / "fixtures/policy/sdd.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest = {
        "apiVersion": "signal-harness.io/v1alpha1",
        "kind": "Distribution",
        "metadata": {"name": "fixture", "version": "1.2.0"},
        "spec": {
            "requiresCore": requires_core,
            "profileApi": "signal-harness/v1alpha1",
            "policy": "policy",
            "providers": {"workItems": "fixture-work-items"},
        },
    }
    path = root / "distribution.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return path


class DistributionTests(unittest.TestCase):
    def test_loads_path_and_resolves_relative_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            distribution = load_distribution_manifest(write_distribution(root))

            self.assertEqual(distribution.name, "fixture")
            self.assertEqual(distribution.version, "1.2.0")
            self.assertEqual(distribution.policy, (root / "policy").resolve())
            self.assertEqual(distribution.providers["workItems"], "fixture-work-items")
            self.assertEqual(distribution.as_dict()["requiresCore"], ">=0.9,<0.10")

    def test_rejects_incompatible_core_before_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_distribution(Path(directory), ">=99")
            with self.assertRaisesRegex(ConfigurationError, f"installed version is {__version__}"):
                load_distribution_manifest(path)

    def test_loads_installed_distribution_entry_point(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = load_distribution_manifest(write_distribution(Path(directory)))
            entry_point = Mock()
            entry_point.name = "fixture"
            entry_point.load.return_value = lambda: expected
            with patch(
                "signal_harness.distributions.installed_distributions",
                return_value={"fixture": entry_point},
            ):
                actual = load_distribution("fixture")

            self.assertIsInstance(actual, Distribution)
            self.assertEqual(actual, expected)

    def test_unknown_distribution_fails_closed(self) -> None:
        with (
            patch("signal_harness.distributions.installed_distributions", return_value={}),
            self.assertRaisesRegex(ConfigurationError, "unknown harness distribution"),
        ):
            load_distribution("missing")


if __name__ == "__main__":
    unittest.main()
