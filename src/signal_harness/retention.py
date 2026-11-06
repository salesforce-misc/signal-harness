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

import shutil
from typing import Any

from .errors import ConfigurationError, ResolutionError
from .state import RunStore

_TERMINAL_STATUSES = {"cancelled", "complete", "evaluation-complete-with-waivers"}


def delete_run(store: RunStore, run_id: str, *, confirmation: str) -> dict[str, Any]:
    state = store.load(run_id)
    if state.get("status") not in _TERMINAL_STATUSES:
        raise ResolutionError("run deletion requires a terminal run")
    expected = f"delete {run_id}"
    if confirmation != expected:
        raise ConfigurationError(f"deletion confirmation must exactly equal {expected!r}")
    attached = [task_id for task_id, task in state.get("tasks", {}).items() if task.get("worktree")]
    if attached:
        raise ResolutionError(
            "run deletion requires worktree cleanup first: " + ", ".join(sorted(attached))
        )
    run_dir = store.run_dir(run_id)
    shutil.rmtree(run_dir)
    return {"runId": run_id, "status": "deleted"}
