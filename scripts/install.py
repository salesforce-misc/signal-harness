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

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(value: list[str], dry_run: bool) -> None:
    print("+", " ".join(value))
    if not dry_run:
        subprocess.run(value, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install a version-matched Signal Harness CLI and agent plugin"
    )
    parser.add_argument("--runtime", choices=("codex", "claude", "both"), default="both")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--agents-home", type=Path, default=Path.home() / ".agents")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    command([args.python, "-m", "pip", "install", str(ROOT)], args.dry_run)
    if args.runtime in {"codex", "both"}:
        source = ROOT / "plugins/signal-harness/skills/signal-harness"
        target = args.agents_home.expanduser() / "skills" / "signal-harness"
        print("+", f"install skill {source} -> {target}")
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise SystemExit(f"refusing to overwrite existing skill: {target}")
            shutil.copytree(source, target)
    if args.runtime in {"claude", "both"}:
        claude = shutil.which("claude")
        if claude is None and not args.dry_run:
            raise SystemExit("claude executable is required for --runtime claude/both")
        executable = claude or "claude"
        command([executable, "plugin", "marketplace", "add", str(ROOT)], args.dry_run)
        command(
            [
                executable,
                "plugin",
                "install",
                "signal-harness@signal-engineering-harness",
                "--yes",
            ],
            args.dry_run,
        )


if __name__ == "__main__":
    main()
