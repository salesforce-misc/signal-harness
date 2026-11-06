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
import re
from pathlib import Path
from typing import Any

import yaml

from .config import load_yaml, require_mapping
from .errors import ConfigurationError
from .git import normalize_remote

SAFE_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
SAFE_PACKAGE = re.compile(r"^[a-z][a-z0-9_]*$")
CORE_RANGE = ">=0.9,<0.10"

STANDARD_WORKFLOW = """apiVersion: signal-harness/v1alpha1
kind: Workflow
id: standard-sdd
version: "1.0"

stages:
  - id: "0"
    name: Specify
    objective: Produce an approved, self-contained specification.
    activities:
      - Resolve behavior, boundaries, compatibility, and rollout decisions.
      - Inspect selected repositories for affected interfaces and callers.
    gate:
      kind: pr
      evidenceType: specification
      clearsWhen: The specification is approved and merged.

  - id: "1"
    name: Decompose
    objective: Produce an approved repository-routed task graph.
    activities:
      - Assign every task, dependency, and affected surface to an explicit repository.
    gate:
      kind: pr
      evidenceType: task-plan
      clearsWhen: The task plan is approved and merged.

  - id: "2"
    name: Implement
    objective: Implement each task using repository-owned verification commands.
    perTask: true
    activities:
      - Record RED, GREEN, and REFACTOR evidence for each behavior.
    gate:
      kind: automatic
      evidenceType: tdd
      clearsWhen: Required task verification passes.

  - id: "3"
    name: Review
    objective: Resolve automated and human review findings.
    perTask: true
    activities:
      - Persist findings, resolutions, and rerun evidence.
    gate:
      kind: pr
      evidenceType: review
      clearsWhen: The task PR is approved and merged.

  - id: "4"
    name: Integrate
    objective: Verify affected repository and service boundaries.
    activities:
      - Run declared integration and compatibility verification.
    gate:
      kind: human
      evidenceType: integration
      waivableInEvaluation: true
      clearsWhen: A human records integration sign-off.

  - id: "5"
    name: Validate
    objective: Exercise representative behavior in a suitable environment.
    activities:
      - Record happy-path, failure-path, and rollout evidence.
    gate:
      kind: human
      evidenceType: smoke
      waivableInEvaluation: true
      clearsWhen: A human records validation sign-off.

  - id: "6"
    name: Learn
    objective: Preserve reusable decisions and follow-up work.
    activities:
      - Persist learning and architecture artifacts.
    gate:
      kind: automatic
      evidenceType: knowledge
      clearsWhen: Required learning artifacts are persisted.
"""


def _safe_name(value: str, field: str) -> str:
    normalized = value.strip()
    if not SAFE_NAME.fullmatch(normalized):
        raise ConfigurationError(f"{field} must match {SAFE_NAME.pattern}: {value!r}")
    return normalized


def _safe_package(value: str) -> str:
    normalized = value.strip()
    if not SAFE_PACKAGE.fullmatch(normalized):
        raise ConfigurationError(f"package name must match {SAFE_PACKAGE.pattern}: {value!r}")
    return normalized


def _write_new_or_same(path: Path, content: str) -> None:
    if path.exists():
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            raise ConfigurationError(f"refusing to overwrite existing file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _yaml(value: dict[str, Any]) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)


def scaffold_distribution(target: Path, name: str, package: str | None = None) -> list[Path]:
    name = _safe_name(name, "distribution name")
    package = _safe_package(package or name.replace("-", "_"))
    target = target.expanduser().resolve()
    package_root = target / "src" / package
    script = f"{name}-sdd"
    files: dict[Path, str] = {
        target / "README.md": (
            f"# {name} engineering harness\n\n"
            "Consumer distribution for the Signal Engineering Harness. Product policy lives "
            "here; repository context remains in each participating repository.\n"
        ),
        target / "pyproject.toml": f'''[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}-engineering-harness"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["signal-engineering-harness{CORE_RANGE}"]

[project.scripts]
{script} = "{package}.cli:main"

[project.entry-points."signal_harness.distributions"]
{name} = "{package}.distribution:manifest_path"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
{package} = ["distribution.yaml", "policy/*.yaml"]
''',
        package_root / "__init__.py": '__version__ = "0.1.0"\n',
        package_root / "distribution.py": """from importlib.resources import files


def manifest_path() -> str:
    return str(files(__package__).joinpath("distribution.yaml"))
""",
        package_root / "cli.py": """import sys

from signal_harness.cli import main as core_main

from .distribution import manifest_path


def main(argv=None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if not arguments:
        raise SystemExit("expected plan, start, implement, or core")
    command = arguments.pop(0)
    if command == "core":
        return core_main(arguments)
    if command not in {"plan", "start", "implement"} or not arguments:
        raise SystemExit("expected plan, start, or implement followed by a feature")
    feature = arguments.pop(0)
    delegated = ["sdd", feature, "--distribution", manifest_path(), *arguments]
    if command in {"start", "implement"}:
        delegated.append("--start")
    return core_main(delegated)
""",
        package_root / "distribution.yaml": _yaml(
            {
                "apiVersion": "signal-harness.io/v1alpha1",
                "kind": "Distribution",
                "metadata": {"name": name, "version": "0.1.0"},
                "spec": {
                    "requiresCore": CORE_RANGE,
                    "profileApi": "signal-harness/v1alpha1",
                    "policy": "policy",
                    "policyModes": {
                        "allowed": ["evaluation", "production"],
                        "default": "evaluation",
                    },
                },
            }
        ),
        package_root / "policy" / "catalog.yaml": _yaml(
            {
                "apiVersion": "signal-harness/v1alpha1",
                "kind": "RepositoryCatalog",
                "id": name,
                "workflow": "sdd.yaml",
                "repositories": {},
            }
        ),
        package_root / "policy" / "sdd.yaml": STANDARD_WORKFLOW,
        target / "templates" / "repository" / "repo.yaml": _yaml(
            {
                "apiVersion": "signal-harness/v1alpha1",
                "kind": "RepositoryProfile",
                "repo": {
                    "id": "replace-with-catalog-id",
                    "remote": "replace-with-org/repository",
                    "defaultBranch": "main",
                },
                "context": {
                    "required": ["AGENTS.md"],
                    "optional": ["README.md", "docs/**/*.md", "specs/**/*.md"],
                    "exclude": [".git/**", "build/**", "node_modules/**"],
                },
                "commands": {"unit": "replace-with-unit-test-command"},
                "signals": {
                    "sources": [{"id": "unit-tests", "kind": "test", "commandRef": "unit"}]
                },
            }
        ),
    }
    for path, content in files.items():
        _write_new_or_same(path, content)
    return sorted(files)


def _detected_commands(root: Path) -> dict[str, str]:
    if (root / "gradlew").is_file():
        return {"unit": "./gradlew test"}
    if (root / "mvnw").is_file():
        return {"unit": "./mvnw test"}
    if (root / "package.json").is_file():
        return {"unit": "npm test"}
    if (root / "pyproject.toml").is_file():
        return {"unit": "python3 -m pytest"}
    return {}


def _repository_profile(
    root: Path,
    repo_id: str,
    remote: str,
    *,
    detect_commands: bool,
    forge: str | None = None,
) -> dict[str, Any]:
    required: list[str]
    if (root / "AGENTS.md").is_file():
        required = ["AGENTS.md"]
    elif (root / "README.md").is_file():
        required = ["README.md"]
    else:
        _write_new_or_same(
            root / ".signal-harness/context.md",
            "# Repository context\n\n"
            "TODO: record service-specific architecture and engineering rules.\n",
        )
        required = [".signal-harness/context.md"]
    optional = [
        value
        for value, exists in (
            ("AGENTS.md", (root / "AGENTS.md").is_file() and "AGENTS.md" not in required),
            ("README.md", (root / "README.md").is_file() and "README.md" not in required),
            ("docs/**/*.md", (root / "docs").is_dir()),
            ("specs/**/*.md", (root / "specs").is_dir()),
        )
        if exists
    ]
    commands = _detected_commands(root) if detect_commands else {}
    profile: dict[str, Any] = {
        "apiVersion": "signal-harness/v1alpha1",
        "kind": "RepositoryProfile",
        "repo": {
            "id": repo_id,
            "remote": remote,
            "defaultBranch": "main",
            **({"forge": forge} if forge else {}),
        },
        "context": {
            "required": required,
            "optional": optional,
            "exclude": [".git/**", "build/**", "node_modules/**"],
        },
    }
    if commands:
        profile["commands"] = commands
        profile["signals"] = {
            "sources": [{"id": "unit-tests", "kind": "test", "commandRef": "unit"}]
        }
    return profile


def _catalog_path(distribution: Path) -> Path:
    requested = distribution.expanduser().resolve()
    if requested.is_file():
        data = load_yaml(requested)
        policy = require_mapping(data.get("spec"), "spec").get("policy")
        if not isinstance(policy, str) or policy.startswith("package://"):
            raise ConfigurationError(
                "onboarding requires a source distribution with a relative policy path"
            )
        return (requested.parent / policy / "catalog.yaml").resolve()
    direct = requested / "policy" / "catalog.yaml"
    if direct.is_file():
        return direct
    manifests = sorted(requested.glob("src/*/distribution.yaml"))
    if len(manifests) == 1:
        return _catalog_path(manifests[0])
    catalogs = sorted(requested.glob("src/*/policy/catalog.yaml"))
    if len(catalogs) == 1:
        return catalogs[0]
    raise ConfigurationError(
        f"could not resolve one source distribution policy catalog beneath {requested}"
    )


def _catalog_entry(repo_id: str, remote: str) -> str:
    lines = [
        f"  {repo_id}:",
        f"    remote: {json.dumps(remote)}",
        "    author: true",
    ]
    return "\n".join(lines) + "\n"


def _add_catalog_repository(
    catalog_path: Path,
    repo_id: str,
    remote: str,
) -> None:
    data = load_yaml(catalog_path)
    repositories = require_mapping(data.get("repositories"), "repositories")
    existing = repositories.get(repo_id)
    if existing is not None:
        if (
            not isinstance(existing, dict)
            or not isinstance(existing.get("remote"), str)
            or normalize_remote(existing["remote"]) != normalize_remote(remote)
        ):
            raise ConfigurationError(
                f"catalog repository {repo_id} already exists with different values"
            )
        if "interactsWith" in existing:
            raise ConfigurationError(
                "remove legacy interactsWith before registering this repository"
            )
        if "author" in existing and not isinstance(existing["author"], bool):
            raise ConfigurationError(f"catalog repository {repo_id} author must be boolean")
        # Registration must not silently grant write permission, replace clone
        # transport, or erase owner-maintained catalog metadata/comments.
        return
    source = catalog_path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    start = next(
        (index for index, line in enumerate(lines) if line.startswith("repositories:")),
        None,
    )
    if start is None:
        raise ConfigurationError(f"catalog has no repositories mapping: {catalog_path}")
    if lines[start].strip() == "repositories: {}":
        lines[start] = "repositories:\n" + _catalog_entry(repo_id, remote)
    else:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            stripped = lines[index].strip()
            if stripped and not lines[index][0].isspace() and not stripped.startswith("#"):
                end = index
                break
        prefix = "" if end == 0 or lines[end - 1].endswith("\n") else "\n"
        lines.insert(end, prefix + _catalog_entry(repo_id, remote))
    updated = "".join(lines)
    parsed = yaml.safe_load(updated)
    require_mapping(require_mapping(parsed, "catalog").get("repositories"), "repositories")
    catalog_path.write_text(updated, encoding="utf-8")


def register_catalog_repository(
    distribution: Path,
    repo_id: str,
    remote: str,
    *,
    interacts_with: tuple[str, ...] = (),
) -> Path:
    """Register one repository in a source distribution's policy catalog."""
    repo_id = _safe_name(repo_id, "repository id")
    remote = remote.strip()
    if not remote or any(character.isspace() for character in remote):
        raise ConfigurationError("repository remote must be a non-empty value without spaces")
    if interacts_with:
        raise ConfigurationError(
            "static repository interactions were removed; Stage 0 discovers and engages "
            "feature-specific repositories"
        )
    catalog = resolve_catalog_path(distribution)
    _add_catalog_repository(catalog, repo_id, remote)
    return catalog


def resolve_catalog_path(distribution: Path) -> Path:
    """Resolve the editable policy catalog for a source distribution checkout."""
    return _catalog_path(distribution)


def scaffold_repository(
    target: Path,
    repo_id: str,
    remote: str,
    *,
    distribution: Path | None = None,
    interacts_with: tuple[str, ...] = (),
    detect_commands: bool = False,
    forge: str | None = None,
) -> list[Path]:
    root = target.expanduser().resolve()
    if forge is not None and forge not in {"github", "gitlab"}:
        raise ConfigurationError("forge must be github or gitlab")
    if not root.is_dir():
        raise ConfigurationError(f"repository directory does not exist: {root}")
    repo_id = _safe_name(repo_id, "repository id")
    remote = remote.strip()
    if not remote or any(character.isspace() for character in remote):
        raise ConfigurationError("repository remote must be a non-empty value without spaces")
    if interacts_with:
        raise ConfigurationError(
            "static repository interactions were removed; Stage 0 discovers and engages "
            "feature-specific repositories"
        )
    profile_path = root / ".signal-harness/repo.yaml"
    _write_new_or_same(
        profile_path,
        _yaml(
            _repository_profile(
                root,
                repo_id,
                remote,
                detect_commands=detect_commands,
                forge=forge,
            )
        ),
    )
    written = [profile_path]
    if distribution is not None:
        catalog = register_catalog_repository(
            distribution,
            repo_id,
            remote,
        )
        written.append(catalog)
    return written


def scaffold_standalone(
    target: Path,
    repo_id: str,
    remote: str,
    *,
    detect_commands: bool = False,
    forge: str | None = None,
) -> list[Path]:
    root = target.expanduser().resolve()
    written = scaffold_repository(
        root,
        repo_id,
        remote,
        detect_commands=detect_commands,
        forge=forge,
    )
    local = root / ".signal-harness"
    files = {
        local / "distribution.yaml": _yaml(
            {
                "apiVersion": "signal-harness.io/v1alpha1",
                "kind": "Distribution",
                "metadata": {"name": repo_id, "version": "0.1.0"},
                "spec": {
                    "requiresCore": CORE_RANGE,
                    "profileApi": "signal-harness/v1alpha1",
                    "policy": "policy",
                    "policyModes": {"allowed": ["evaluation"], "default": "evaluation"},
                },
            }
        ),
        local / "policy" / "catalog.yaml": _yaml(
            {
                "apiVersion": "signal-harness/v1alpha1",
                "kind": "RepositoryCatalog",
                "id": repo_id,
                "workflow": "sdd.yaml",
                "repositories": {repo_id: {"remote": remote, "author": True}},
            }
        ),
        local / "policy" / "sdd.yaml": STANDARD_WORKFLOW,
    }
    for path, content in files.items():
        _write_new_or_same(path, content)
    return [*written, *sorted(files)]
