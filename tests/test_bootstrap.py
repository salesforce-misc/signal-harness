from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from signal_harness.bootstrap import register_catalog_repository
from signal_harness.cli import main
from signal_harness.distributions import load_distribution_manifest
from signal_harness.errors import ConfigurationError


class BootstrapCliTests(unittest.TestCase):
    @patch("signal_harness.cli.RunStore.migrate_author_permissions", return_value={"ok": True})
    def test_permission_migration_cli_requires_and_forwards_reviewed_mapping(self, migrate):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "permissions.json"
            permissions = {"service-a": {"remote": "example/service-a", "author": True}}
            path.write_text(json.dumps(permissions))
            result = main(
                [
                    "migrate-author-permissions",
                    "run-1",
                    "--permissions",
                    str(path),
                    "--actor",
                    "operator",
                    "--reason",
                    "Reviewed grants",
                    "--state-root",
                    directory,
                ]
            )
            self.assertEqual(result, 0)
            self.assertEqual(migrate.call_args.args, ("run-1", permissions))
            self.assertEqual(
                migrate.call_args.kwargs, {"actor": "operator", "reason": "Reviewed grants"}
            )
            path.write_text("invalid json")
            self.assertEqual(
                main(
                    [
                        "migrate-author-permissions",
                        "run-1",
                        "--permissions",
                        str(path),
                        "--actor",
                        "operator",
                        "--reason",
                        "Reviewed grants",
                        "--state-root",
                        directory,
                    ]
                ),
                2,
            )

    @patch(
        "signal_harness.cli.lock_discovery_context",
        create=True,
        return_value={"repository": "service-b", "added": ["src/consumer.py"]},
    )
    def test_lock_context_command_delegates_exact_stage_zero_paths(self, lock_context) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = main(
                [
                    "lock-context",
                    "run-1",
                    "service-b",
                    "src/consumer.py",
                    "src/contract.py",
                    "--reason",
                    "targeted code search evidence",
                    "--actor",
                    "operator",
                    "--state-root",
                    temporary,
                ]
            )

        self.assertEqual(result, 0)
        call = lock_context.call_args
        self.assertEqual(call.args[1:3], ("run-1", "service-b"))
        self.assertEqual(call.kwargs["paths"], ("src/consumer.py", "src/contract.py"))
        self.assertEqual(call.kwargs["actor"], "operator")

    @patch(
        "signal_harness.cli.engage_repository",
        create=True,
        return_value={"repository": "service-b", "status": "engaged"},
    )
    def test_engage_repository_command_delegates_stage_zero_evidence(self, engage) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = main(
                [
                    "engage-repository",
                    "run-1",
                    "service-b",
                    "--reason",
                    "sponsor calls service-b",
                    "--evidence",
                    "service-a:AGENTS.md",
                    "--actor",
                    "developer@example.com",
                    "--override",
                    "/tmp/service-b",
                    "--state-root",
                    temporary,
                ]
            )

        self.assertEqual(result, 0)
        call = engage.call_args
        self.assertEqual(call.args[1:3], ("run-1", "service-b"))
        self.assertEqual(call.kwargs["evidence"], ("service-a:AGENTS.md",))
        self.assertEqual(call.kwargs["actor"], "developer@example.com")
        self.assertEqual(call.kwargs["override"], Path("/tmp/service-b").resolve())

    def test_init_distribution_creates_external_policy_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "acme-harness"

            result = main(
                [
                    "init",
                    "distribution",
                    str(target),
                    "--name",
                    "acme",
                    "--package",
                    "acme_harness",
                ]
            )

            self.assertEqual(result, 0)
            self.assertTrue((target / "pyproject.toml").is_file())
            self.assertTrue((target / "src/acme_harness/distribution.yaml").is_file())
            self.assertTrue((target / "src/acme_harness/policy/catalog.yaml").is_file())
            self.assertTrue((target / "src/acme_harness/policy/sdd.yaml").is_file())
            self.assertTrue((target / "templates/repository/repo.yaml").is_file())
            self.assertNotIn("product-specific", (target / "pyproject.toml").read_text().lower())

    def test_init_repository_preserves_catalog_comments_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            distribution = root / "distribution"
            policy = distribution / "policy"
            policy.mkdir(parents=True)
            catalog = policy / "catalog.yaml"
            catalog.write_text(
                """apiVersion: signal-harness/v1alpha1
kind: RepositoryCatalog
id: acme
workflow: sdd.yaml

# Keep this product-owner comment.
repositories:
  peer-svc:
    remote: acme/peer-svc
""",
                encoding="utf-8",
            )
            repository = root / "widget-svc"
            repository.mkdir()
            (repository / "AGENTS.md").write_text("# Widget rules\n", encoding="utf-8")
            (repository / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")

            arguments = [
                "init",
                "repository",
                str(repository),
                "--id",
                "widget-svc",
                "--remote",
                "acme/widget-svc",
                "--distribution",
                str(distribution),
                "--detect-commands",
            ]
            self.assertEqual(main(arguments), 0)
            first_catalog = catalog.read_text(encoding="utf-8")
            self.assertEqual(main(arguments), 0)

            profile = yaml.safe_load(
                (repository / ".signal-harness/repo.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(profile["repo"]["id"], "widget-svc")
            self.assertEqual(profile["context"]["required"], ["AGENTS.md"])
            self.assertEqual(profile["commands"]["unit"], "./gradlew test")
            self.assertEqual(catalog.read_text(encoding="utf-8"), first_catalog)
            self.assertIn("# Keep this product-owner comment.", first_catalog)
            self.assertIn("widget-svc:", first_catalog)
            self.assertIn("author: true", first_catalog)

    def test_catalog_registration_preserves_existing_permissions_and_metadata(self) -> None:
        for permission in ({}, {"author": False}, {"author": True}):
            with self.subTest(permission=permission), tempfile.TemporaryDirectory() as temporary:
                distribution = Path(temporary)
                policy = distribution / "policy"
                policy.mkdir()
                catalog = policy / "catalog.yaml"
                entry = {"remote": "acme/widget-svc", **permission, "mode": "read-only"}
                source = "# Keep owner policy.\n" + yaml.safe_dump(
                    {"repositories": {"widget-svc": entry}}
                )
                catalog.write_text(source, encoding="utf-8")

                register_catalog_repository(distribution, "widget-svc", "acme/widget-svc")
                self.assertEqual(catalog.read_text(encoding="utf-8"), source)
                with self.assertRaisesRegex(
                    ConfigurationError, "already exists with different values"
                ):
                    register_catalog_repository(distribution, "widget-svc", "acme/other")
                self.assertEqual(catalog.read_text(encoding="utf-8"), source)

    def test_init_standalone_creates_a_local_distribution_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "solo-svc"
            repository.mkdir()
            (repository / "README.md").write_text("# Solo\n", encoding="utf-8")
            (repository / "pyproject.toml").write_text(
                "[project]\nname = 'solo'\nversion = '0.1.0'\n", encoding="utf-8"
            )

            result = main(
                [
                    "init",
                    "standalone",
                    str(repository),
                    "--id",
                    "solo-svc",
                    "--remote",
                    "acme/solo-svc",
                    "--detect-commands",
                ]
            )

            self.assertEqual(result, 0)
            local = repository / ".signal-harness"
            self.assertTrue((local / "repo.yaml").is_file())
            self.assertTrue((local / "distribution.yaml").is_file())
            self.assertTrue((local / "policy/catalog.yaml").is_file())
            self.assertTrue((local / "policy/sdd.yaml").is_file())
            distribution = load_distribution_manifest(local / "distribution.yaml")
            self.assertEqual(distribution.name, "solo-svc")
            catalog = yaml.safe_load((local / "policy/catalog.yaml").read_text())
            self.assertEqual(catalog["repositories"]["solo-svc"]["remote"], "acme/solo-svc")
            self.assertTrue(catalog["repositories"]["solo-svc"]["author"])


if __name__ == "__main__":
    unittest.main()
