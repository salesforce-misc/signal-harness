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

"""Refresh compatibility assets from the canonical SDD schemas and templates."""

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="refresh legacy copies; default is check"
    )
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    drift = []
    for kind in ("templates", "schemas"):
        canonical_root = root / kind / "sdd"
        for canonical in sorted(canonical_root.rglob("*")):
            if not canonical.is_file():
                continue
            legacy = root / kind / canonical.relative_to(canonical_root)
            if not legacy.is_file() or legacy.read_bytes() != canonical.read_bytes():
                if arguments.write:
                    legacy.parent.mkdir(parents=True, exist_ok=True)
                    legacy.write_bytes(canonical.read_bytes())
                else:
                    drift.append(str(legacy.relative_to(root)))
    for path in drift:
        print(f"Compatibility asset differs: {path}")
    return int(bool(drift))


if __name__ == "__main__":
    raise SystemExit(main())
