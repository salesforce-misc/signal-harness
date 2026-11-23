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

import ast
import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "src/signal_harness/runtime").resolve()
SDD = (ROOT / "src/signal_harness/sdd").resolve()


@pytest.mark.parametrize(
    "path",
    sorted(p for folder in (RUNTIME, SDD) for p in folder.glob("*.py") if p.stem != "__init__"),
)
def test_legacy_modules_resolve_to_the_canonical_implementation(path):
    canonical = importlib.import_module(f"signal_harness.{path.parent.name}.{path.stem}")
    if path.stem in {"persistence", "profiles"}:
        return  # These are new shared interfaces, not legacy modules.
    legacy = importlib.import_module(f"signal_harness.{path.stem}")
    assert legacy is canonical


def test_runtime_imports_have_no_reverse_dependency_on_sdd_or_compatibility_modules():
    for path in RUNTIME.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = importlib.util.resolve_name(
                        "." * node.level + module, "signal_harness.runtime"
                    )
                if module.startswith("signal_harness"):
                    assert module.startswith(
                        ("signal_harness.runtime", "signal_harness.signals")
                    ), (path, module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("signal_harness"):
                        assert alias.name.startswith(
                            ("signal_harness.runtime", "signal_harness.signals")
                        ), (path, alias.name)


def test_shared_runtime_can_import_and_persist_without_loading_the_sdd_engine(tmp_path):
    script = """
import importlib
import json
import sys
from pathlib import Path

for name in json.loads(sys.argv[2]):
    importlib.import_module(name)
from signal_harness.runtime.persistence import PersistentRunStore
store = PersistentRunStore(Path(sys.argv[1]))
state = {"runId": "shared-run", "auditVersion": 1, "events": []}
store.append_event(state, "example.saved", {"token": "synthetic-value"})
store.save(state)
assert store.load("shared-run")["events"][0]["detail"]["token"] == "[REDACTED]"
assert not any(
    name == "signal_harness.sdd" or name.startswith("signal_harness.sdd.")
    for name in sys.modules
)
"""
    import json

    modules = [f"signal_harness.runtime.{path.stem}" for path in RUNTIME.glob("*.py")]
    subprocess.run([sys.executable, "-c", script, str(tmp_path), json.dumps(modules)], check=True)
