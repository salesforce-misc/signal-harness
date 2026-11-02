#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return value


def validate(schema_name: str, document: Path) -> None:
    schema = load_json(ROOT / "schemas" / schema_name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(load_yaml(document))


def validate_release_versions(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    module = ast.parse((root / "src/signal_harness/__init__.py").read_text(encoding="utf-8"))
    module_versions = [
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    ]
    if module_versions != [version]:
        raise ValueError("module __version__ must match the Python package version")
    skill_path = root / "plugins/signal-harness/skills/signal-harness/SKILL.md"
    skill = skill_path.read_text(encoding="utf-8")
    sections = skill.split("---", 2)
    if len(sections) != 3 or sections[0].strip():
        raise ValueError("authoring skill must declare YAML front matter")
    front_matter = yaml.safe_load(sections[1])
    metadata = front_matter.get("metadata") if isinstance(front_matter, dict) else None
    if not isinstance(metadata, dict) or metadata.get("version") != version:
        raise ValueError("plugin skill metadata.version must match the Python package version")
    for runtime in ("claude", "codex"):
        manifest = root / f"plugins/signal-harness/.{runtime}-plugin/plugin.json"
        if load_json(manifest).get("version") != version:
            raise ValueError(f"{runtime} plugin version must match the Python package version")
    return version


def main() -> None:
    for schema_path in sorted((ROOT / "schemas").glob("*.json")):
        Draft202012Validator.check_schema(load_json(schema_path))
    review_skill = ROOT / "plugins/signal-harness/skills/harness-code-review"
    Draft202012Validator.check_schema(load_json(review_skill / "references/report.schema.json"))
    if not (review_skill / "references/github-review-format.md").is_file():
        raise ValueError("shared GitHub review format must ship with the review skill")
    review_profile = load_yaml(ROOT / "docs/review/profile.yaml")
    review_documents = review_profile.get("documents", [])
    if not review_documents or not all((ROOT / path).is_file() for path in review_documents):
        raise ValueError("review profile must resolve every required document")
    for check in review_profile.get("checks", []):
        if not set(check.get("documents", [])).issubset(review_documents):
            raise ValueError(f"review check has undeclared documents: {check.get('id')}")
    validate("workflow.schema.json", ROOT / "templates/policy-pack/sdd.yaml")
    validate(
        "repository-catalog.schema.json",
        ROOT / "templates/policy-pack/catalog.yaml",
    )
    validate("workspace.schema.json", ROOT / "templates/workspace.yaml")
    validate(
        "distribution.schema.json",
        ROOT / "tests/fixtures/distribution/distribution.yaml",
    )

    forbidden_policy_roots = (
        ROOT / "src/signal_harness/policies",
        ROOT / "distributions",
        ROOT / "examples",
    )
    embedded = [
        str(path.relative_to(ROOT))
        for policy_root in forbidden_policy_roots
        if policy_root.exists()
        for path in policy_root.rglob("*")
        if path.is_file()
    ]
    if embedded:
        raise ValueError(f"consumer policy or examples must not ship in core: {embedded}")

    version = validate_release_versions(ROOT)
    sdd_release = (ROOT / ".github/workflows/release-sdd.yml").read_text(encoding="utf-8")
    aial_release = (ROOT / ".github/workflows/release-aial.yml").read_text(encoding="utf-8")
    if '"sdd-v*"' not in sdd_release or '"aial-v*"' in sdd_release:
        raise ValueError("SDD releases must use only the sdd-v* tag namespace")
    if '"aial-v*"' not in aial_release or '"sdd-v*"' in aial_release:
        raise ValueError("AIAL releases must use only the aial-v* tag namespace")
    if (ROOT / ".github/workflows/release.yml").exists():
        raise ValueError("the legacy repository-wide v* release workflow must not exist")
    if (ROOT / ".github/dependabot.yml").exists() or (ROOT / ".github/dependabot.yaml").exists():
        raise ValueError(
            "routine core dependency bots must remain disabled; dependency changes require "
            "review and a versioned component release"
        )
    for manifest_path in (
        ROOT / ".claude-plugin/marketplace.json",
        ROOT / ".agents/plugins/marketplace.json",
    ):
        manifest = load_json(manifest_path)
        if manifest.get("name") != "signal-engineering-harness":
            raise ValueError(f"unexpected plugin marketplace name in {manifest_path}")
        plugins = manifest.get("plugins")
        if not isinstance(plugins, list) or not any(
            item.get("name") == "signal-harness" for item in plugins if isinstance(item, dict)
        ):
            raise ValueError(f"signal-harness plugin missing from {manifest_path}")
    print(f"validated schemas, neutral templates, core boundary, and plugins for {version}")


if __name__ == "__main__":
    main()
