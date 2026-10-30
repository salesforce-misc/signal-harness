from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import load_yaml, require_string, string_list
from .errors import ConfigurationError


@dataclass(frozen=True)
class TaskCard:
    id: str
    repo: str
    depends_on: tuple[str, ...]
    description: str
    source: str
    declared_status: str | None = None
    deferred: bool = False
    test_cases: tuple[dict[str, Any], ...] = ()


def _frontmatter(text: str, path: Path) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ConfigurationError(f"unterminated task-card frontmatter: {path}")
    try:
        value = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid task-card frontmatter in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"task-card frontmatter must be a mapping: {path}")
    return value


def _description(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip().strip("`")
    return fallback


def _dependency_tokens(data: dict[str, Any], text: str) -> tuple[str, ...]:
    raw = data.get("depends_on")
    if raw is not None:
        if not isinstance(raw, list):
            raise ConfigurationError("task-card depends_on must be a list")
        return tuple(str(item).strip() for item in raw if str(item).strip())
    lines = text.splitlines()
    dependency_text = ""
    for index, line in enumerate(lines):
        match = re.match(r"(?i)^-?\s*(?:Delivery\s+)?Dependenc(?:y|ies):\s*(.*)$", line)
        if not match:
            continue
        parts = [match.group(1).strip()]
        for continuation in lines[index + 1 :]:
            if not continuation.strip():
                break
            if re.match(r"^\s*(?:#{1,6}\s|[-+*]\s)", continuation):
                break
            parts.append(continuation.strip())
        dependency_text = " ".join(parts)
        break
    if not dependency_text or dependency_text.lower().startswith("none"):
        inline = re.search(r"(?i)\bDepends on Tasks?\s+([^.\n]+)", text)
        if inline:
            return tuple(re.findall(r"\b\d{1,3}\b", inline.group(1)))
        return ()
    emphasized = tuple(re.findall(r"\*\*(\d{1,3})\*\*", dependency_text))
    if emphasized:
        return emphasized
    return tuple(re.findall(r"\b\d{1,3}\b", dependency_text))


def _normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    status = str(value).strip()
    lowered = status.lower()
    for canonical in ("complete", "deferred", "in progress", "pending", "blocked"):
        if lowered.startswith(canonical):
            return canonical
    return status or None


def _resolve_dependencies(
    tokens: tuple[str, ...], card_ids: tuple[str, ...], path: Path
) -> tuple[str, ...]:
    resolved: list[str] = []
    for token in tokens:
        candidates = [
            card_id
            for card_id in card_ids
            if card_id == token or card_id.startswith((f"{token}-", f"{token.zfill(2)}-"))
        ]
        if len(candidates) != 1:
            raise ConfigurationError(
                f"task dependency {token!r} in {path} must resolve once; found {candidates}"
            )
        resolved.append(candidates[0])
    return tuple(dict.fromkeys(resolved))


def _test_cases(data: dict[str, Any], path: Path) -> tuple[dict[str, Any], ...]:
    raw = data.get("testCases", [])
    if not isinstance(raw, list):
        raise ConfigurationError(f"task-card testCases must be a list: {path}")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ConfigurationError(f"task-card testCases[{index}] must be a mapping: {path}")
        case_id = require_string(value.get("id"), f"testCases[{index}].id")
        if case_id in seen:
            raise ConfigurationError(f"duplicate task-card test case {case_id}: {path}")
        seen.add(case_id)
        kind = require_string(value.get("kind"), f"testCases[{index}].kind")
        if kind not in {"delta", "guard"}:
            raise ConfigurationError(f"testCases[{index}].kind must be delta or guard")
        test_case: dict[str, Any] = {
            "id": case_id,
            "kind": kind,
            "requirement": require_string(
                value.get("requirement"), f"testCases[{index}].requirement"
            ),
        }
        if "traces" in value:
            test_case["traces"] = list(
                string_list(value.get("traces"), f"testCases[{index}].traces")
            )
        result.append(test_case)
    return tuple(result)


def discover_task_cards(
    root: Path,
    manifest: str,
    sponsor: str,
    resolve_repo: Any,
) -> tuple[TaskCard, ...]:
    manifest_path = root / manifest
    data = load_yaml(manifest_path)
    configured = data.get("tasks_dir")
    if configured is not None:
        configured = require_string(configured, "tasks_dir")
        tasks_dir = root / configured
    else:
        tasks_dir = manifest_path.parent / "tasks"
    if not tasks_dir.is_dir():
        return ()

    paths = tuple(
        path for path in sorted(tasks_dir.glob("[0-9]*.md")) if path.name != "task-list.md"
    )
    ids = tuple(path.stem for path in paths)
    cards: list[TaskCard] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        metadata = _frontmatter(text, path)
        raw_repo = str(metadata.get("repo", sponsor)).split("#", 1)[0].strip()
        repo = resolve_repo(raw_repo)
        status = _normalize_status(metadata.get("status"))
        dependencies = _resolve_dependencies(_dependency_tokens(metadata, text), ids, path)
        cards.append(
            TaskCard(
                id=path.stem,
                repo=repo,
                depends_on=dependencies,
                description=_description(text, path.stem),
                source=path.relative_to(root).as_posix(),
                declared_status=status,
                deferred=bool(status and "deferred" in status.lower()),
                test_cases=_test_cases(metadata, path),
            )
        )
    return tuple(cards)
