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
from pathlib import Path

import pytest

CHECKER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/check_license_headers.py")
)


@pytest.mark.parametrize("prefix", ["", "#!/usr/bin/env python3\n", "# coding: utf-8\n"])
def test_license_notice_preserves_interpreter_prefixes(prefix):
    assert CHECKER["has_notice"](prefix + CHECKER["NOTICE"] + "\nprint('hello')\n")


def test_missing_or_partial_license_notice_is_rejected():
    for text in ("print('hello')\n", "# SPDX-License-Identifier: Apache-2.0\n", ""):
        assert not CHECKER["has_notice"](text)
    assert not CHECKER["has_notice"]("print('hello')\n" + CHECKER["NOTICE"])


def test_source_selection_includes_nested_configs_and_excludes_json():
    for name in ("src/new.py", ".github/workflows/new.yml", "pyproject.toml", "CODEOWNERS"):
        assert CHECKER["requires_notice"](Path(name))
    assert not CHECKER["requires_notice"](Path("schemas/example.json"))
    assert not CHECKER["requires_notice"](Path("uv.lock"))
