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
