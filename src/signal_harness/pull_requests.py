from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from .errors import ResolutionError
from .forge import provider_for
from .state import RunStore
from .worktrees import verify_prepared_task


def pull_request_plan(
    store: RunStore,
    run_id: str,
    task_id: str,
    title: str,
    body_file: Path | None = None,
    draft: bool = False,
) -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise ResolutionError("pull request title must not be empty")
    task, repo = verify_prepared_task(store, run_id, task_id)

    provider = provider_for(repo)
    command = [
        "gh",
        "pr",
        "create",
        "--repo",
        repo["remote"],
        "--base",
        repo["profile"]["defaultBranch"],
        "--head",
        task["branch"],
        "--title",
        title,
    ]
    if body_file is not None:
        body_file = body_file.expanduser().resolve()
        if not body_file.is_file():
            raise ResolutionError(f"pull request body file does not exist: {body_file}")
        command.extend(["--body-file", str(body_file)])
    else:
        command.extend(["--body", ""])
    if draft:
        command.append("--draft")

    if provider.name == "gitlab":
        location = provider.location
        command = [
            "glab",
            "mr",
            "create",
            "--repo",
            f"{location.host}/{location.project}",
            "--source-branch",
            task["branch"],
            "--target-branch",
            repo["profile"]["defaultBranch"],
            "--title",
            title,
            "--description",
            body_file.read_text(encoding="utf-8") if body_file else "",
            "--yes",
        ]
        if draft:
            command.append("--draft")
    else:
        location = provider.location
        command[command.index("--repo") + 1] = (
            location.project
            if location.host == "github.com"
            else f"{location.host}/{location.project}"
        )

    return {
        "task": task_id,
        "repo": repo["id"],
        "remote": repo["remote"],
        "cwd": task["worktree"],
        "worktree": task["worktree"],
        "branch": task["branch"],
        "base": repo["profile"]["defaultBranch"],
        "command": command,
        "shell": shlex.join(command),
        "executed": False,
    }
