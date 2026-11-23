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

"""GitLab change-request adapter. Unknown or stale review evidence never passes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from signal_harness.runtime.errors import ResolutionError
from signal_harness.runtime.forge import CommandRunner, RemoteLocation
from signal_harness.runtime.git import head_sha, is_dirty
from signal_harness.runtime.persistence import utc_now
from signal_harness.runtime.security import redact_text


class GitLabProvider:
    name = "gitlab"

    def __init__(self, location: RemoteLocation):
        self.location = location
        self.project = f"projects/{quote(location.project, safe='')}"

    def _api(
        self,
        endpoint: str,
        task: dict[str, Any],
        runner: CommandRunner,
        *,
        payload: dict[str, Any] | None = None,
        pages: bool = False,
    ) -> Any:
        command = [
            "glab",
            "api",
            "--hostname",
            self.location.host,
            "--method",
            "POST" if payload is not None else "GET",
            endpoint,
        ]
        kwargs: dict[str, Any] = {}
        if payload is not None:
            command.extend(["--input", "-"])
            kwargs["input"] = json.dumps(payload)
        if pages:
            command.append("--paginate")
        try:
            completed = runner(
                command,
                cwd=Path(task["worktree"]),
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
                **kwargs,
            )
        except FileNotFoundError as exc:
            raise ResolutionError(
                "Install glab and authenticate with "
                f"glab auth login --hostname {self.location.host}. "
                "Local discovery does not require glab; this MR operation does."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ResolutionError("GitLab API timed out; no review gate was advanced") from exc
        if completed.returncode != 0:
            detail = redact_text(completed.stderr.strip() or completed.stdout.strip())
            raise ResolutionError(
                f"GitLab API failed on {self.location.host}: {detail}. "
                "Check glab authentication and project/API permissions; "
                "no review gate was advanced."
            )
        try:
            if not pages:
                value = json.loads(completed.stdout)
                if not isinstance(value, dict):
                    raise ValueError("expected an object")
                return value
            # glab --paginate emits consecutive JSON arrays, not one JSON document.
            remaining = completed.stdout.strip()
            result: list[dict[str, Any]] = []
            while remaining:
                page, end = json.JSONDecoder().raw_decode(remaining)
                if not isinstance(page, list) or not all(isinstance(row, dict) for row in page):
                    raise ValueError("expected a page of objects")
                result.extend(page)
                remaining = remaining[end:].strip()
            if not completed.stdout.strip():
                raise ValueError("empty paginated response")
            return result
        except (ValueError, TypeError) as exc:
            raise ResolutionError(f"GitLab returned invalid JSON for {endpoint}") from exc

    def _project_id(self, task: dict[str, Any], runner: CommandRunner) -> int:
        project = self._api(self.project, task, runner)
        if (
            type(project.get("id")) is not int
            or project.get("path_with_namespace") != self.location.project
        ):
            raise ResolutionError("GitLab project identity does not match the routed repository")
        return int(project["id"])

    def _iid(self, url: str) -> int:
        parsed = urlsplit(url)
        prefix = f"/{self.location.project}/-/merge_requests/"
        suffix = parsed.path.removeprefix(prefix)
        if (
            parsed.scheme != "https"
            or parsed.netloc != self.location.host
            or parsed.username
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith(prefix)
            or not suffix.isdigit()
            or int(suffix) < 1
        ):
            raise ResolutionError("GitLab MR URL does not belong to the routed repository")
        return int(suffix)

    def _validate(
        self, value: dict[str, Any], repo: dict[str, Any], task: dict[str, Any], project_id: int
    ) -> dict[str, Any]:
        iid = self._iid(str(value.get("web_url", "")))
        if (
            value.get("iid") != iid
            or value.get("source_project_id") != project_id
            or value.get("target_project_id") != project_id
            or value.get("source_branch") != task["branch"]
            or value.get("target_branch") != repo["profile"]["defaultBranch"]
        ):
            raise ResolutionError(
                "GitLab MR project/branch identity does not match the routed task"
            )
        revision = head_sha(Path(task["worktree"]))
        if (
            is_dirty(Path(task["worktree"]))
            or task.get("publishedHead") != revision
            or value.get("sha") != revision
        ):
            raise ResolutionError(
                "GitLab MR head is stale or differs from the clean published task head"
            )
        state = {"opened": "OPEN", "merged": "MERGED", "closed": "CLOSED"}.get(
            str(value.get("state"))
        )
        if state is None or (state == "MERGED" and not value.get("merged_at")):
            raise ResolutionError("GitLab MR has an unverified state")
        return {
            "url": value["web_url"],
            "state": state,
            "reviewDecision": "REVIEW_REQUIRED",
            "head": value["source_branch"],
            "base": value["target_branch"],
            "sha": revision,
            "provider": self.name,
            "iid": iid,
            "projectId": project_id,
            "mergedAt": value.get("merged_at"),
            "updatedAt": utc_now(),
        }

    def open(
        self,
        repo: dict[str, Any],
        task: dict[str, Any],
        title: str,
        body_file: Path | None,
        draft: bool,
        runner: CommandRunner,
    ) -> dict[str, Any]:
        project_id = self._project_id(task, runner)
        query = urlencode(
            {
                "state": "all",
                "source_branch": task["branch"],
                "target_branch": repo["profile"]["defaultBranch"],
                "per_page": 100,
            }
        )
        matches = self._api(f"{self.project}/merge_requests?{query}", task, runner, pages=True)
        matches = [row for row in matches if row.get("source_project_id") == project_id]
        if len(matches) > 1:
            raise ResolutionError("Multiple GitLab MRs match this task; select one explicitly")
        if matches:
            iid = self._iid(str(matches[0].get("web_url", "")))
            value = self._api(f"{self.project}/merge_requests/{iid}", task, runner)
        else:
            revision = head_sha(Path(task["worktree"]))
            if is_dirty(Path(task["worktree"])) or task.get("publishedHead") != revision:
                raise ResolutionError("Publish the clean task head before creating a GitLab MR")
            value = self._api(
                f"{self.project}/merge_requests",
                task,
                runner,
                payload={
                    "source_branch": task["branch"],
                    "target_branch": repo["profile"]["defaultBranch"],
                    "title": f"Draft: {title.strip()}" if draft else title.strip(),
                    "description": body_file.expanduser().resolve().read_text()
                    if body_file
                    else "",
                },
            )
        return self._validate(value, repo, task, project_id)

    def refresh(
        self,
        repo: dict[str, Any],
        task: dict[str, Any],
        recorded: dict[str, Any],
        runner: CommandRunner,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        iid = self._iid(recorded["url"])
        project_id = self._project_id(task, runner)
        endpoint = f"{self.project}/merge_requests/{iid}"
        value = self._api(endpoint, task, runner)
        result = self._validate(value, repo, task, project_id)
        checks: list[dict[str, Any]] = []

        def check(name: str, passed: bool, failed: bool = False) -> None:
            checks.append(
                {"name": name, "status": "pass" if passed else "fail" if failed else "pending"}
            )

        approvals = self._api(f"{endpoint}/approvals", task, runner)
        rules = self._api(f"{endpoint}/approval_state", task, runner)
        policy = self._api(f"{self.project}/approvals", task, runner)
        author = value.get("author") or {}
        approvers = approvals.get("approved_by")
        verified_approvers: set[int] = set()
        if isinstance(approvers, list) and type(author.get("id")) is int:
            for row in approvers:
                if not isinstance(row, dict) or not isinstance(row.get("user"), dict):
                    continue
                approver_id = row["user"].get("id")
                if type(approver_id) is not int or approver_id == author["id"]:
                    continue
                identity = self._api(f"users/{approver_id}", task, runner)
                if (
                    type(identity.get("id")) is int
                    and identity["id"] == approver_id
                    and identity.get("bot") is False
                ):
                    verified_approvers.add(approver_id)
        rule_rows = rules.get("rules")
        rules_pass = isinstance(rule_rows, list) and all(
            isinstance(rule, dict) and rule.get("approved") is True for rule in rule_rows
        )
        # Without reset-on-push, an approval can refer to an older source head.
        approval_conditions = {
            "independent-human": bool(verified_approvers),
            "approval-rules": rules_pass,
            "reset-on-push": policy.get("reset_approvals_on_push") is True,
            "required-approvals": type(approvals.get("approvals_left")) is int
            and approvals["approvals_left"] == 0,
            "provider-approved": approvals.get("approved", True) is True,
            "approval-sync-complete": value.get("detailed_merge_status") != "approvals_syncing",
        }
        approved = all(approval_conditions.values())
        check("gitlab:current-head-approvals", approved)
        if approved:
            result["reviewDecision"] = "APPROVED"

        discussions = self._api(f"{endpoint}/discussions?per_page=100", task, runner, pages=True)
        entries = []
        all_resolved = True
        for thread in discussions:
            notes = thread.get("notes")
            if (
                not isinstance(notes, list)
                or not notes
                or not all(isinstance(n, dict) for n in notes)
            ):
                raise ResolutionError("GitLab returned an incomplete discussion")
            resolvable = [note for note in notes if note.get("resolvable") is True]
            resolved = bool(resolvable) and all(note.get("resolved") is True for note in resolvable)
            all_resolved = all_resolved and (not resolvable or resolved)
            bodies = [str(note.get("body", "")).strip() for note in notes if not note.get("system")]
            if any(bodies):
                if not thread.get("id"):
                    raise ResolutionError("GitLab discussion has no stable identity")
                entries.append(
                    {
                        "kind": "thread",
                        "id": f"{project_id}:{iid}:{thread['id']}",
                        "body": "\n\n".join(body for body in bodies if body),
                        "resolved": resolved,
                        "url": f"{result['url']}#note_{notes[-1].get('id', '')}",
                    }
                )
        check("gitlab:resolved-discussions", all_resolved)

        pipeline = value.get("head_pipeline")
        if isinstance(pipeline, dict) and type(pipeline.get("id")) is int:
            pipeline = self._api(f"{self.project}/pipelines/{pipeline['id']}", task, runner)
            exact = pipeline.get("sha") == result["sha"]
            check(
                "gitlab:head-pipeline",
                exact and pipeline.get("status") == "success",
                exact and pipeline.get("status") in {"failed", "canceled"},
            )
        else:
            check("gitlab:head-pipeline", False)
        status = value.get("detailed_merge_status")
        result["mergeStateStatus"] = (
            "DIRTY"
            if value.get("has_conflicts") is True or status == "conflict"
            else "BEHIND"
            if status in {"need_rebase", "not_open"} and result["state"] == "OPEN"
            else "UNKNOWN"
        )
        check(
            "gitlab:merge-readiness",
            not (
                value.get("draft")
                or value.get("work_in_progress")
                or result["mergeStateStatus"] in {"DIRTY", "BEHIND"}
                or status in {"checking", "unchecked", "preparing", "approvals_syncing"}
            ),
        )
        # Refuse a mixed snapshot if a push, merge, or pipeline changed during API reads.
        latest = self._api(endpoint, task, runner)
        self._validate(latest, repo, task, project_id)
        if any(
            latest.get(key) != value.get(key)
            for key in (
                "sha",
                "state",
                "updated_at",
                "head_pipeline",
                "detailed_merge_status",
                "draft",
            )
        ):
            raise ResolutionError(
                "GitLab MR changed during refresh; retry with fresh review evidence"
            )
        # Persist only allowlisted evidence after the complete snapshot validates.
        # The journal timestamp is observation time, not provider approval time.
        result["approvalEvidence"] = {
            "provider": self.name,
            "host": self.location.host,
            "projectId": project_id,
            "iid": iid,
            "url": result["url"],
            "sha": result["sha"],
            "approverIds": sorted(verified_approvers),
            "decision": result["reviewDecision"],
            "pendingReasons": [name for name, passed in approval_conditions.items() if not passed],
        }
        result["checks"] = checks
        return result, entries
