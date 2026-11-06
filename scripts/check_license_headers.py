#!/usr/bin/env python3
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

"""Check license notices in tracked and unignored source/configuration files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

NOTICE = """# Copyright (c) 2026 Salesforce, Inc.
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
"""
SUFFIXES = {".py", ".yaml", ".yml", ".toml", ".sh"}
NAMES = {".editorconfig", ".gitignore", "CODEOWNERS", "MANIFEST.in"}


def requires_notice(path: Path) -> bool:
    return path.suffix in SUFFIXES or path.name in NAMES


def has_notice(text: str) -> bool:
    lines = text.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        lines.pop(0)
    if lines and re.match(r"#.*coding[:=]\s*[-\w.]+", lines[0]):
        lines.pop(0)
    # Preserve the original copyright year when future contributions arrive.
    header = re.escape(NOTICE).replace("2026", r"\d{4}(?:-\d{4})?")
    return re.match(header, "".join(lines)) is not None


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = sorted({Path(name) for name in result.stdout.split("\0") if name})
    selected = [path for path in paths if requires_notice(path) and (root / path).is_file()]
    missing = [path for path in selected if not has_notice((root / path).read_text("utf-8"))]
    for path in missing:
        print(f"Missing Salesforce Apache license notice: {path}")
    if missing:
        return 1
    print(f"validated license notices in {len(selected)} source/configuration files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
