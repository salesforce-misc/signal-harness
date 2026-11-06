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

import runpy
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VALIDATE = runpy.run_path(str(ROOT / "scripts/validate_assets.py"))["validate_release_versions"]
VERSION_FILES = (
    "pyproject.toml",
    "src/signal_harness/__init__.py",
    "plugins/signal-harness/skills/signal-harness/SKILL.md",
    "plugins/signal-harness/.claude-plugin/plugin.json",
    "plugins/signal-harness/.codex-plugin/plugin.json",
)


@pytest.fixture
def release_tree(tmp_path):
    for name in VERSION_FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return tmp_path


def test_release_identities_agree(release_tree):
    assert VALIDATE(release_tree) == "0.9.0"


@pytest.mark.parametrize("name", VERSION_FILES)
def test_each_release_identity_must_agree(release_tree, name):
    path = release_tree / name
    path.write_text(path.read_text().replace("0.9.0", "0.4.0"))
    with pytest.raises(ValueError, match="must match"):
        VALIDATE(release_tree)


def test_skill_body_cannot_mask_wrong_front_matter(release_tree):
    path = release_tree / VERSION_FILES[2]
    path.write_text(
        path.read_text().replace("version: 0.9.0", "version: 0.4.0") + "\nversion: 0.9.0\n"
    )
    with pytest.raises(ValueError, match="metadata.version"):
        VALIDATE(release_tree)
