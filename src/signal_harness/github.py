from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import ResolutionError
from .forge import RemoteLocation, remote_location
from .hosting import (
    open_pull_request as open_pull_request,
)
from .hosting import (
    publish_branch as publish_branch,
)
from .hosting import (
    refresh_pull_request as refresh_pull_request,
)
from .hosting import (
    resume_from_pull_request as resume_from_pull_request,
)
from .state import utc_now

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

_PASSING_CHECK_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_FAILING_CHECK_CONCLUSIONS = {
    "ACTION_REQUIRED",
    "CANCELLED",
    "FAILURE",
    "STALE",
    "STARTUP_FAILURE",
    "TIMED_OUT",
}


def _run_gh(
    command: list[str], cwd: Path, runner: CommandRunner
) -> subprocess.CompletedProcess[str]:
    completed = runner(command, cwd=cwd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ResolutionError(f"GitHub command failed: {detail}")
    return completed


def _json_output(completed: subprocess.CompletedProcess[str], description: str) -> Any:
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ResolutionError(f"GitHub returned invalid JSON for {description}: {exc}") from exc


def _checks(value: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, check in enumerate(value.get("statusCheckRollup") or []):
        if not isinstance(check, dict):
            continue
        name = check.get("name") or check.get("context") or f"check-{index + 1}"
        provider_status = str(check.get("status") or check.get("state") or "").upper()
        conclusion = str(check.get("conclusion") or "").upper()
        if conclusion in _PASSING_CHECK_CONCLUSIONS or provider_status == "SUCCESS":
            status = "pass"
        elif conclusion in _FAILING_CHECK_CONCLUSIONS or provider_status in {
            "ERROR",
            "FAILURE",
        }:
            status = "fail"
        else:
            status = "pending"
        result.append(
            {
                "name": name,
                "status": status,
                "providerStatus": provider_status,
                "conclusion": conclusion,
                "url": check.get("detailsUrl") or check.get("targetUrl"),
            }
        )
    return result


def _review_entries(value: dict[str, Any], threads: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for comment in value.get("comments") or []:
        if isinstance(comment, dict) and str(comment.get("body", "")).strip():
            entries.append(
                {
                    "kind": "comment",
                    "id": comment.get("id") or comment.get("databaseId"),
                    "body": comment["body"],
                    "url": comment.get("url"),
                }
            )
    for review in value.get("reviews") or []:
        if (
            isinstance(review, dict)
            and str(review.get("body", "")).strip()
            and str(review.get("state", "")).upper() in {"COMMENTED", "CHANGES_REQUESTED"}
        ):
            entries.append(
                {
                    "kind": "review",
                    "id": review.get("id") or review.get("databaseId"),
                    "body": review["body"],
                    "url": review.get("url"),
                }
            )
    thread_nodes = (
        (threads.get("data") or {})
        .get("repository", {})
        .get("pullRequest", {})
        .get("reviewThreads", {})
        .get("nodes", [])
        if isinstance(threads, dict)
        else []
    )
    for thread in thread_nodes:
        if isinstance(thread, dict):
            comments = (thread.get("comments") or {}).get("nodes") or []
            bodies = [
                str(comment.get("body", "")).strip()
                for comment in comments
                if isinstance(comment, dict) and str(comment.get("body", "")).strip()
            ]
            if bodies:
                last = comments[-1]
                entries.append(
                    {
                        "kind": "thread",
                        "id": thread.get("id"),
                        "body": "\n\n".join(bodies),
                        "url": last.get("url"),
                        "resolved": bool(thread.get("isResolved")),
                    }
                )
    return [entry for entry in entries if entry.get("id") is not None]


def _open(
    repo: dict[str, Any],
    task: dict[str, Any],
    title: str,
    *,
    body_file: Path | None = None,
    draft: bool = False,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    worktree = Path(task["worktree"])
    lookup = [
        "gh",
        "pr",
        "list",
        "--repo",
        _github_repo(repo),
        "--head",
        task["branch"],
        "--state",
        "all",
        "--limit",
        "1",
        "--json",
        "url,state,reviewDecision,headRefName,baseRefName",
    ]
    lookup_result = _run_gh(lookup, worktree, runner)
    existing = _json_output(lookup_result, "pull request lookup") if lookup_result.stdout else []
    if not isinstance(existing, list) or not all(isinstance(item, dict) for item in existing):
        raise ResolutionError("GitHub pull request lookup returned an unexpected payload")
    if existing:
        value = existing[0]
        if value.get("headRefName") != task["branch"]:
            raise ResolutionError("existing pull request head does not match the routed branch")
        if value.get("baseRefName") != repo["profile"]["defaultBranch"]:
            raise ResolutionError(
                "existing pull request base does not match the repository profile"
            )
        pull_request = {
            "url": value["url"],
            "state": value.get("state", "OPEN"),
            "reviewDecision": value.get("reviewDecision", ""),
            "head": value.get("headRefName", task["branch"]),
            "base": value.get("baseRefName", repo["profile"]["defaultBranch"]),
            "updatedAt": utc_now(),
        }
        return pull_request

    command = [
        "gh",
        "pr",
        "create",
        "--repo",
        _github_repo(repo),
        "--base",
        repo["profile"]["defaultBranch"],
        "--head",
        task["branch"],
        "--title",
        title.strip(),
    ]
    if not title.strip():
        raise ResolutionError("pull request title must not be empty")
    if body_file is not None:
        resolved_body = body_file.expanduser().resolve()
        if not resolved_body.is_file():
            raise ResolutionError(f"pull request body file does not exist: {resolved_body}")
        command.extend(["--body-file", str(resolved_body)])
    else:
        command.extend(["--body", ""])
    if draft:
        command.append("--draft")
    url = _run_gh(command, worktree, runner).stdout.strip().splitlines()[-1]
    if not url.startswith("https://"):
        raise ResolutionError(f"GitHub did not return a pull request URL: {url}")
    pull_request = {
        "url": url,
        "state": "OPEN",
        "reviewDecision": "",
        "head": task["branch"],
        "base": repo["profile"]["defaultBranch"],
        "updatedAt": utc_now(),
    }
    return pull_request


def _refresh(
    repo: dict[str, Any],
    task: dict[str, Any],
    recorded: dict[str, Any],
    *,
    runner: CommandRunner = subprocess.run,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    command = [
        "gh",
        "pr",
        "view",
        recorded["url"],
        "--repo",
        _github_repo(repo),
        "--json",
        "url,state,reviewDecision,headRefName,baseRefName,mergedAt,mergeStateStatus,"
        "statusCheckRollup,comments,reviews",
    ]
    value = _json_output(_run_gh(command, Path(task["worktree"]), runner), "pull request refresh")
    if not isinstance(value, dict):
        raise ResolutionError("GitHub pull request refresh returned an unexpected payload")
    head = value.get("headRefName")
    head_deleted = not bool(head)
    if head and head != task["branch"]:
        raise ResolutionError("pull request head does not match the routed task branch")
    if value.get("baseRefName") != repo["profile"]["defaultBranch"]:
        raise ResolutionError("pull request base does not match the repository profile")
    pull_request = {
        "url": value["url"],
        "state": value["state"],
        "reviewDecision": value.get("reviewDecision", ""),
        "head": head or task["branch"],
        "base": value["baseRefName"],
        "mergedAt": value.get("mergedAt"),
        "mergeStateStatus": value.get("mergeStateStatus", ""),
        "headDeleted": head_deleted,
        "checks": _checks(value),
        "updatedAt": utc_now(),
    }
    number = recorded["url"].rstrip("/").rsplit("/", 1)[-1]
    threads: Any = {}
    if number.isdigit():
        location = remote_location(repo["remote"])
        owner, separator, name = location.project.partition("/")
        if not separator:
            raise ResolutionError("GitHub repository remote must be owner/name")
        thread_result = _run_gh(
            [
                "gh",
                "api",
                *(["--hostname", location.host] if location.host != "github.com" else []),
                "graphql",
                "-f",
                "query=query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100){nodes{id,isResolved,comments(first:100){nodes{databaseId,body,url}}}}}}}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
                "-F",
                f"number={number}",
            ],
            Path(task["worktree"]),
            runner,
        )
        threads = _json_output(thread_result, "review threads")
    return pull_request, _review_entries(value, threads)


def _github_repo(repo: dict[str, Any]) -> str:
    location = remote_location(repo["remote"])
    return (
        location.project if location.host == "github.com" else f"{location.host}/{location.project}"
    )


class GitHubProvider:
    name = "github"

    def __init__(self, location: RemoteLocation) -> None:
        if len(location.project.split("/")) != 2:
            raise ResolutionError("GitHub repositories must be owner/name")
        self.location = location

    def open(
        self,
        repo: dict[str, Any],
        task: dict[str, Any],
        title: str,
        body_file: Path | None,
        draft: bool,
        runner: Any,
    ) -> dict[str, Any]:
        return _open(repo, task, title, body_file=body_file, draft=draft, runner=runner)

    def refresh(
        self, repo: dict[str, Any], task: dict[str, Any], recorded: dict[str, Any], runner: Any
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return _refresh(repo, task, recorded, runner=runner)
