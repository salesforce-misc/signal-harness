from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import load_yaml, require_list, require_string
from .errors import ConfigurationError, ResolutionError
from .forge import remote_location

_REQUIRED_LIST_FIELDS = ("sources", "constraints")
_OPTIONAL_LIST_FIELDS = ("prior_features",)
_OPTIONAL_REQUIRED_LIST_FIELDS = ("touchpoints",)
_OPTIONAL_PATH_FIELDS = ("resolutions", "interfaces", "learnings")
_OPTIONAL_DIRECTORY_FIELDS = ("tasks_dir",)


@dataclass(frozen=True)
class ExternalPriorFeature:
    remote: str
    ref: str
    path: str


def external_prior_features(
    root: Path, manifest: str, *, forge_by_remote: dict[str, str | None] | None = None
) -> tuple[ExternalPriorFeature, ...]:
    """Parse forge blob URLs for catalog-scoped resolution (never fetch arbitrary URLs)."""

    manifest_path = root / manifest
    if not manifest_path.is_file():
        raise ResolutionError(f"required feature manifest does not exist: {manifest}")
    data = load_yaml(manifest_path)
    references: list[ExternalPriorFeature] = []
    for value in _values(data, "prior_features"):
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            continue
        parts = tuple(part for part in parsed.path.split("/") if part)
        boundary = next(
            (
                index
                for index in range(2, len(parts) - 1)
                if parts[index : index + 2] == ("-", "blob")
            ),
            None,
        )
        github_format = len(parts) >= 3 and parts[2] == "blob"
        github_remote = (
            remote_location(f"https://{parsed.netloc}/{'/'.join(parts[:2])}").identity
            if github_format
            else None
        )
        known_forges = forge_by_remote or {}
        github_provider = parsed.hostname == "github.com" or (
            github_remote is not None
            and github_remote in known_forges
            and known_forges[github_remote] in {None, "github"}
        )
        if github_format and (github_provider or boundary is None):
            project_parts, blob_parts = parts[:2], parts[2:]
        elif boundary is not None:
            project_parts = parts[:boundary]
            blob_parts = parts[boundary + 1 :]
        elif parsed.netloc != "github.com":
            continue
        else:
            project_parts, blob_parts = (), ()
        if len(blob_parts) < 3 or blob_parts[0] != "blob":
            raise ConfigurationError(
                "cross-repository prior_features must use a forge blob URL: "
                "https://HOST/OWNER/REPO/blob/REF/PATH or "
                "https://HOST/GROUP/PROJECT/-/blob/REF/PATH"
            )
        _, ref, *path_parts = blob_parts
        path = Path(*path_parts)
        if path.is_absolute() or ".." in path.parts or not path_parts:
            raise ConfigurationError("cross-repository prior_features path is invalid")
        references.append(
            ExternalPriorFeature(
                remote=remote_location(
                    f"https://{parsed.netloc}/{'/'.join(project_parts)}"
                ).identity,
                ref=ref,
                path=path.as_posix(),
            )
        )
    return tuple(references)


def _local_path(value: str, field_name: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ConfigurationError(f"{field_name} must contain repo-relative paths")
    return path.as_posix()


def _prior_feature_path(root: Path, manifest_path: Path, value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        return None
    path = Path(value)
    if path.is_absolute():
        raise ConfigurationError("prior_features must contain repo-relative paths")
    candidate = (manifest_path.parent / path).resolve()
    try:
        return candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ConfigurationError("prior_features must resolve inside the repository") from exc


def _values(data: dict[str, Any], field_name: str) -> tuple[str, ...]:
    raw = data.get(field_name, [])
    return tuple(require_string(item, f"{field_name}[]") for item in require_list(raw, field_name))


def feature_context_patterns(root: Path, manifest: str) -> tuple[str, ...]:
    """Return repo-local context referenced by an SDD feature manifest.

    Source and constraint references are required. Generated artifacts are included
    when already present so a fresh feature and a resumed feature use the same API.
    """

    manifest_path = root / manifest
    if not manifest_path.is_file():
        raise ResolutionError(f"required feature manifest does not exist: {manifest}")
    data = load_yaml(manifest_path)
    selected: list[str] = [manifest]

    for field_name in _REQUIRED_LIST_FIELDS:
        if field_name not in data:
            raise ConfigurationError(f"feature manifest must declare {field_name}: {manifest}")
        for value in _values(data, field_name):
            local = _local_path(value, field_name)
            if local is not None:
                selected.append(local)

    for field_name in _OPTIONAL_LIST_FIELDS:
        for value in _values(data, field_name):
            local = _prior_feature_path(root, manifest_path, value)
            if local is not None and (root / local).exists():
                selected.append(local)

    for field_name in _OPTIONAL_REQUIRED_LIST_FIELDS:
        for value in _values(data, field_name):
            local = _local_path(value, field_name)
            if local is not None:
                selected.append(local)

    for field_name in _OPTIONAL_PATH_FIELDS:
        raw_value = data.get(field_name)
        if raw_value is None:
            continue
        local = _local_path(require_string(raw_value, field_name), field_name)
        if local is not None and (root / local).is_file():
            selected.append(local)

    for field_name in _OPTIONAL_DIRECTORY_FIELDS:
        raw_value = data.get(field_name)
        if raw_value is None:
            continue
        local = _local_path(require_string(raw_value, field_name), field_name)
        if local is not None and (root / local).is_dir():
            selected.append(f"{local.rstrip('/')}/**/*")

    if "tasks_dir" not in data:
        conventional_tasks = manifest_path.parent / "tasks"
        if conventional_tasks.is_dir():
            relative = conventional_tasks.relative_to(root).as_posix()
            selected.append(f"{relative}/**/*")

    # A resumed SDD run must see artifacts produced by earlier stages even when an
    # older manifest predates explicit `resolutions`/`learnings` fields.
    feature_directory = manifest_path.parent.relative_to(root).as_posix()
    selected.append(f"{feature_directory}/**/*")

    missing = [
        pattern for pattern in selected if "*" not in pattern and not (root / pattern).is_file()
    ]
    if missing:
        raise ResolutionError(f"feature manifest references missing required context: {missing}")
    return tuple(dict.fromkeys(selected))
