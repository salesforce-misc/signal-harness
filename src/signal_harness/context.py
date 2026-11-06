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

import fnmatch
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .errors import ResolutionError
from .git import current_branch, head_sha, is_dirty
from .models import RepositoryProfile


@dataclass(frozen=True)
class ContextFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ContextLock:
    repo: str
    root: str
    revision: str
    branch: str
    dirty: bool
    files: tuple[ContextFile, ...]


def _matches_exclude(relative_path: str, excludes: tuple[str, ...]) -> bool:
    path = relative_path.removeprefix("./")
    return any(
        fnmatch.fnmatch(path, pattern)
        or fnmatch.fnmatch(path, pattern.removesuffix("/**"))
        or (pattern.endswith("/**") and path.startswith(pattern[:-3] + "/"))
        for pattern in excludes
    )


def _expand(root: Path, pattern: str) -> list[Path]:
    if Path(pattern).is_absolute():
        raise ResolutionError(f"context patterns must be repo-relative: {pattern}")
    matches = []
    for candidate in root.glob(pattern):
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ResolutionError(f"context path escapes repository: {candidate}") from exc
        if resolved.is_file():
            matches.append(resolved)
    return sorted(matches)


def collect_context(
    root: Path,
    profile: RepositoryProfile,
    additional_required: tuple[str, ...] = (),
    *,
    required_files: tuple[str, ...] = (),
) -> ContextLock:
    root = root.resolve()
    selected: dict[str, Path] = {}

    for pattern in (*profile.context.required, *additional_required):
        matches = _expand(root, pattern)
        if not matches:
            raise ResolutionError(
                f"required context pattern matched no files in {profile.id}: {pattern}"
            )
        effective_matches = 0
        for match in matches:
            relative = match.relative_to(root).as_posix()
            if not _matches_exclude(relative, profile.context.exclude):
                selected[relative] = match
                effective_matches += 1
        if effective_matches == 0:
            raise ResolutionError(
                f"required context pattern is fully excluded in {profile.id}: {pattern}"
            )

    for pattern in profile.context.optional:
        for match in _expand(root, pattern):
            relative = match.relative_to(root).as_posix()
            if not _matches_exclude(relative, profile.context.exclude):
                selected[relative] = match

    for name in required_files:
        if Path(name).is_absolute():
            raise ResolutionError(f"context files must be repo-relative: {name}")
        resolved = (root / name).resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ResolutionError(f"context path escapes repository: {name}") from exc
        if not resolved.is_file():
            raise ResolutionError(f"required context file does not exist in {profile.id}: {name}")
        # Path.resolve() retains supplied casing on case-insensitive filesystems.
        # Reject aliases before exclusions so their spelling cannot bypass policy.
        parent = root
        for part in Path(relative).parts:
            if part not in {entry.name for entry in parent.iterdir()}:
                raise ResolutionError(f"context file must use on-disk spelling: {name}")
            parent = parent / part
        if _matches_exclude(relative, profile.context.exclude):
            raise ResolutionError(f"required context file is excluded in {profile.id}: {name}")
        selected[relative] = resolved

    files = tuple(
        ContextFile(
            path=relative,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size=path.stat().st_size,
        )
        for relative, path in sorted(selected.items())
    )
    return ContextLock(
        repo=profile.id,
        root=str(root),
        revision=head_sha(root),
        branch=current_branch(root),
        dirty=is_dirty(root),
        files=files,
    )
