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

import json
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from signal_harness.bootstrap import scaffold_repository
from signal_harness.catalog import CatalogRepository, RepositoryCatalog
from signal_harness.errors import ConfigurationError, ResolutionError
from signal_harness.feature_context import external_prior_features
from signal_harness.forge import provider_for, remote_location
from signal_harness.git import head_sha, normalize_remote
from signal_harness.hosting import (
    open_pull_request,
    publish_branch,
    refresh_pull_request,
    resume_from_pull_request,
)
from signal_harness.models import RepositoryProfile, WorkspaceRepository
from signal_harness.planner import resolve_workspace
from signal_harness.sdd import build_sdd_plan
from signal_harness.session import engage_repository, lock_discovery_context
from signal_harness.state import RunStore
from signal_harness.worktrees import prepare_task
from tests.sdd.test_engine import EXAMPLE_WORKFLOW, RepositoryFixture, profile, write_yaml
from tests.sdd.test_runtime import RuntimeFixture, run


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@github.com:org/repo.git", "org/repo"),
        ("https://github.com/org/repo.git", "org/repo"),
        ("org/repo", "org/repo"),
        ("ssh://git@github.com:2222/org/repo.git", "org/repo"),
        ("git@gitlab.example:team/subgroup/repo.git", "https://gitlab.example/team/subgroup/repo"),
        ("ssh://git@gitlab.example/team/repo.git", "https://gitlab.example/team/repo"),
        ("ssh://git@gitlab.example:22/team/repo.git", "https://gitlab.example/team/repo"),
        ("ssh://git@gitlab.example:2222/team/repo.git", "https://gitlab.example/team/repo"),
        (
            "ssh://git@gitlab.example:2222/team/subgroup/repo.git",
            "https://gitlab.example/team/subgroup/repo",
        ),
        ("https://gitlab.example/team/repo.git", "https://gitlab.example/team/repo"),
        ("https://gitlab.example:443/team/repo.git", "https://gitlab.example/team/repo"),
        ("https://gitlab.example:8443/team/repo", "https://gitlab.example:8443/team/repo"),
    ],
)
def test_remote_identity(remote, expected):
    assert normalize_remote(remote) == expected


@pytest.mark.parametrize(
    "remote",
    [
        "https://user:secret@gitlab.example/a/b",
        "file:///tmp/a/b",
        "https://gitlab.example/a/../b",
        "https://gitlab.example/a/b?token=x",
        "https://gitlab.example:bad/a/b",
        "ssh://git@gitlab.example:bad/a/b",
        "ssh://git@gitlab.example:65536/a/b",
        "not-a-project",
    ],
)
def test_invalid_remote(remote):
    with pytest.raises(ConfigurationError):
        remote_location(remote)


def test_self_host_requires_explicit_provider_without_authentication():
    repo = {"remote": "git@gitlab.example:team/repo.git", "profile": {}}
    with pytest.raises(ResolutionError, match="repo.forge"):
        provider_for(repo)
    repo["profile"]["forge"] = "gitlab"
    assert provider_for(repo).name == "gitlab"


def test_scaffold_self_host_and_prior_feature(tmp_path):
    scaffold_repository(
        tmp_path, "service-a", "git@gitlab.example:team/service-a.git", forge="gitlab"
    )
    from signal_harness.config import load_yaml

    profile = RepositoryProfile.from_dict(load_yaml(tmp_path / ".signal-harness/repo.yaml"))
    assert profile.forge == "gitlab"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "prior_features:\n  - https://gitlab.example/team/subgroup/peer/-/blob/main/specs/feature.md\n"
    )
    refs = external_prior_features(tmp_path, "manifest.yaml")
    assert refs[0].remote == "https://gitlab.example/team/subgroup/peer"
    assert refs[0].path == "specs/feature.md"


@pytest.mark.parametrize(
    ("url", "remote", "path"),
    [
        ("https://github.com/org/repo/blob/main/docs/-/design.md", "org/repo", "docs/-/design.md"),
        (
            "https://github.com/org/repo/blob/main/docs/-/blob/design.md",
            "org/repo",
            "docs/-/blob/design.md",
        ),
        (
            "https://gitlab.example/team/subgroup/blob/peer/-/blob/main/specs/design.md",
            "https://gitlab.example/team/subgroup/blob/peer",
            "specs/design.md",
        ),
        (
            "https://gitlab.example/team/subgroup/peer/-/blob/main/docs/-/design.md",
            "https://gitlab.example/team/subgroup/peer",
            "docs/-/design.md",
        ),
        (
            "https://gitlab.example/team/-/peer/-/blob/main/docs/design.md",
            "https://gitlab.example/team/-/peer",
            "docs/design.md",
        ),
    ],
)
def test_prior_feature_dash_components(tmp_path, url, remote, path):
    write_yaml(tmp_path / "manifest.yaml", {"prior_features": [url]})
    (reference,) = external_prior_features(tmp_path, "manifest.yaml")
    assert (reference.remote, reference.ref, reference.path) == (remote, "main", path)


@pytest.mark.parametrize(
    ("ssh_remote", "https_remote", "forge", "separator", "prior_path"),
    [
        (
            "ssh://git@gitlab.example:2222/team/subgroup/service-b.git",
            "https://gitlab.example/team/subgroup/service-b",
            "gitlab",
            "-/blob",
            "specs/shared/sdd/foundation/manifest.yaml",
        ),
        (
            "ssh://git@gitlab.example:2222/team/subgroup/blob/peer.git",
            "https://gitlab.example/team/subgroup/blob/peer",
            "gitlab",
            "-/blob",
            "specs/design.md",
        ),
        (
            "git@github.enterprise.example:team/service-b.git",
            "https://github.enterprise.example/team/service-b",
            "github",
            "blob",
            "docs/-/blob/design.md",
        ),
    ],
)
def test_ssh_port_catalog_locks_https_prior_feature(
    tmp_path, ssh_remote, https_remote, forge, separator, prior_path
):
    sponsor = RepositoryFixture(tmp_path, "service-a")
    peer = RepositoryFixture(tmp_path, "service-b")
    run("git", "remote", "set-url", "origin", ssh_remote, cwd=peer.root)
    peer_profile = profile(peer.id, https_remote)
    peer_profile["repo"]["forge"] = forge
    write_yaml(peer.root / ".signal-harness/repo.yaml", peer_profile)
    prior = peer.root / prior_path
    prior.parent.mkdir(parents=True)
    prior.write_text("feature: foundation\n", encoding="utf-8")
    run("git", "add", ".", cwd=peer.root)
    run("git", "commit", "-m", "add prior feature", cwd=peer.root)
    feature = sponsor.root / "specs/area/sdd/change"
    feature.mkdir(parents=True)
    (feature / "source.md").write_text("source\n", encoding="utf-8")
    write_yaml(
        feature / "manifest.yaml",
        {
            "feature": "change",
            "sources": ["specs/area/sdd/change/source.md"],
            "constraints": ["AGENTS.md"],
            "prior_features": [f"{https_remote}/{separator}/main/{prior_path}"],
        },
    )
    run("git", "add", ".", cwd=sponsor.root)
    run("git", "commit", "-m", "add feature", cwd=sponsor.root)
    catalog = RepositoryCatalog(
        id="fixture",
        workflow=EXAMPLE_WORKFLOW,
        repositories={
            "service-a": CatalogRepository(id="service-a", remote=sponsor.remote),
            "service-b": CatalogRepository(id="service-b", remote=ssh_remote),
        },
    )
    with patch("signal_harness.sdd.load_policy_catalog", return_value=catalog):
        plan = build_sdd_plan(
            feature="change", current_path=sponsor.root, repo_overrides={"service-b": peer.root}
        )
    assert [item.workspace.id for item in plan.repositories] == ["service-a"]
    selected_plan = resolve_workspace(
        replace(
            plan.workspace,
            workflow=None,
            repositories={
                **plan.workspace.repositories,
                "service-b": WorkspaceRepository(
                    id="service-b", remote=ssh_remote, mode="read-only"
                ),
            },
            include=("service-a", "service-b"),
        ),
        sponsor.root,
        sponsor.root,
        {"service-b": peer.root},
    )
    selected_peer = next(
        item for item in selected_plan.repositories if item.workspace.id == "service-b"
    )
    assert prior_path in [item.path for item in selected_peer.context.files]
    store = RunStore(tmp_path / "state")
    store.create(plan, feature / "manifest.yaml", "forge-prior-run")
    engage_repository(
        store,
        "forge-prior-run",
        "service-b",
        override=peer.root,
        reason="The source manifest names a hosted prior feature.",
        evidence=("service-a:specs/area/sdd/change/manifest.yaml",),
    )
    lock_discovery_context(
        store,
        "forge-prior-run",
        "service-b",
        paths=(prior_path,),
        reason="Lock only the exact prior feature found by targeted source inspection.",
    )
    peer_record = next(
        item
        for item in store.load("forge-prior-run")["plan"]["repositories"]
        if item["id"] == "service-b"
    )
    assert prior_path in [item["path"] for item in peer_record["context"]["files"]]
    assert peer_record["profile"]["forge"] == forge
    assert (
        subprocess.check_output(
            ["git", "remote", "get-url", "origin"], cwd=peer.root, text=True
        ).strip()
        == ssh_remote
    )


class GitLabAPI:
    def __init__(self, repo, task):
        self.calls = []
        self.created = []
        self.fail = None
        self.bad_json = None
        self.race = False
        self.reads = 0
        self.mr = {
            "iid": 42,
            "web_url": "https://gitlab.example/team/subgroup/service-a/-/merge_requests/42",
            "state": "merged",
            "merged_at": "2026-10-26T00:00:00Z",
            "updated_at": "now",
            "source_project_id": 10,
            "target_project_id": 10,
            "source_branch": task["branch"],
            "target_branch": repo["profile"]["defaultBranch"],
            "sha": head_sha(Path(task["worktree"])),
            "author": {"id": 1},
            "head_pipeline": {"id": 99},
            "detailed_merge_status": "not_open",
            "draft": False,
        }
        self.approvals = {"approved_by": [{"user": {"id": 2}}], "approvals_left": 0}
        self.users = {2: {"id": 2, "bot": False}}
        self.rules = {"rules": [{"approved": True}]}
        self.policy = {"reset_approvals_on_push": True}
        self.pipeline = {"id": 99, "sha": self.mr["sha"], "status": "success"}
        self.discussions = []
        self.existing = []

    def __call__(self, command, **kwargs):
        assert command[:2] == ["glab", "api"]
        assert command[command.index("--hostname") + 1] == "gitlab.example"
        endpoint = command[6]
        self.calls.append(endpoint)
        assert endpoint.startswith("projects/team%2Fsubgroup%2Fservice-a") or endpoint.startswith(
            "users/"
        )
        if self.fail and self.fail in endpoint:
            return subprocess.CompletedProcess(command, 1, "", "403 forbidden glpat-do-not-expose")
        if self.bad_json and self.bad_json in endpoint:
            return subprocess.CompletedProcess(command, 0, "not json", "")
        if endpoint.startswith("users/"):
            value = self.users[int(endpoint.removeprefix("users/"))]
        elif endpoint.endswith("/approval_state"):
            value = self.rules
        elif endpoint.endswith("/merge_requests/42/approvals"):
            value = self.approvals
        elif endpoint.endswith("/approvals"):
            value = self.policy
        elif "/discussions?" in endpoint:
            value = self.discussions
        elif "/pipelines/" in endpoint:
            value = self.pipeline
        elif "merge_requests?" in endpoint:
            value = self.existing
        elif endpoint.endswith("/merge_requests"):
            assert command[5] == "POST"
            self.created.append(json.loads(kwargs["input"]))
            value = self.mr
        elif endpoint.endswith("/merge_requests/42"):
            self.reads += 1
            value = deepcopy(self.mr)
            if self.race and self.reads > 1:
                value["updated_at"] = "changed"
        else:
            value = {"id": 10, "path_with_namespace": "team/subgroup/service-a"}
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")


@pytest.fixture
def gitlab_task(tmp_path):
    fixture = RuntimeFixture(tmp_path, "git@gitlab.example:team/subgroup/service-a.git", "gitlab")
    fixture.enter_task_phase()
    prepare_task(fixture.store, "runtime-run", "implementation")
    state = fixture.store.load("runtime-run")
    task = state["tasks"]["implementation"]
    repo = state["plan"]["repositories"][0]
    worktree = Path(task["worktree"])
    (worktree / "change.txt").write_text("implementation\n")
    run("git", "add", ".", cwd=worktree)
    run("git", "commit", "-m", "implementation", cwd=worktree)
    publish_branch(
        fixture.store,
        "runtime-run",
        "implementation",
        runner=Mock(
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ),
    )
    task = fixture.store.load("runtime-run")["tasks"]["implementation"]
    return fixture, repo, task, GitLabAPI(repo, task)


def open_mr(fixture, api):
    return open_pull_request(fixture.store, "runtime-run", "implementation", "Feature", runner=api)


def resume(fixture, api):
    return resume_from_pull_request(fixture.store, "runtime-run", "implementation", runner=api)


def test_gitlab_end_to_end_idempotent_resume(gitlab_task):
    fixture, repo, task, api = gitlab_task
    created = open_mr(fixture, api)
    assert created["provider"] == "gitlab"
    assert len(api.created) == 1
    assert open_mr(fixture, api) == created
    fixture.store.clear_current_gate("runtime-run", fixture.complete_tdd(), "implementation")
    assert resume(fixture, api)["advanced"]
    assert not resume(fixture, api)["advanced"]
    assert fixture.store.load("runtime-run")["workflow"]["currentStage"] == "4"


@pytest.mark.parametrize(
    "identity",
    [
        {"id": 2, "bot": True},
        {"id": 2},
        {"id": 2, "bot": None},
        {"id": 2, "bot": "false"},
        {"id": 2, "bot": 0},
        {"id": 3, "bot": False},
        {"id": "2", "bot": False},
        {"id": True, "bot": False},
    ],
)
def test_gitlab_requires_verified_human_identity(gitlab_task, identity):
    fixture, repo, task, api = gitlab_task
    open_mr(fixture, api)
    fixture.store.clear_current_gate("runtime-run", fixture.complete_tdd(), "implementation")
    # Approval-list claims cannot replace the authenticated identity response.
    api.approvals["approved_by"][0]["user"]["bot"] = False
    api.users[2] = identity
    assert not resume(fixture, api)["advanced"]
    assert "users/2" in api.calls


def test_gitlab_bot_and_independent_human_approval(gitlab_task):
    fixture, repo, task, api = gitlab_task
    open_mr(fixture, api)
    fixture.store.clear_current_gate("runtime-run", fixture.complete_tdd(), "implementation")
    api.users[2]["bot"] = True
    api.users[3] = {"id": 3, "bot": False}
    api.approvals["approved_by"] = [{"user": {"id": 2}}, {"user": {"id": 3}}]
    assert resume(fixture, api)["advanced"]
    assert "users/2" in api.calls and "users/3" in api.calls


@pytest.mark.parametrize(
    "case",
    [
        "missing-pipeline",
        "stale-pipeline",
        "failed-pipeline",
        "pending-pipeline",
        "self-approval",
        "approval-rule",
        "no-reset",
        "unresolved-thread",
        "draft",
        "no-approvers",
        "missing-rules",
    ],
)
def test_gitlab_never_advances_with_incomplete_evidence(gitlab_task, case):
    fixture, repo, task, api = gitlab_task
    open_mr(fixture, api)
    fixture.store.clear_current_gate("runtime-run", fixture.complete_tdd(), "implementation")
    if case == "missing-pipeline":
        api.mr["head_pipeline"] = None
    elif case == "stale-pipeline":
        api.pipeline["sha"] = "old"
    elif case == "failed-pipeline":
        api.pipeline["status"] = "failed"
    elif case == "pending-pipeline":
        api.pipeline["status"] = "manual"
    elif case == "self-approval":
        api.approvals["approved_by"] = [{"user": {"id": 1}}]
    elif case == "approval-rule":
        api.rules["rules"][0]["approved"] = False
    elif case == "no-reset":
        api.policy["reset_approvals_on_push"] = False
    elif case == "unresolved-thread":
        api.discussions = [
            {"id": "t1", "notes": [{"id": 3, "body": "Fix", "resolvable": True, "resolved": False}]}
        ]
    elif case == "draft":
        api.mr["draft"] = True
    elif case == "no-approvers":
        api.approvals["approved_by"] = []
    else:
        api.rules = {}
    assert not resume(fixture, api)["advanced"]


@pytest.mark.parametrize(
    "case",
    [
        "access",
        "invalid-json",
        "stale-head",
        "foreign-url",
        "foreign-project",
        "race",
        "identity-access",
        "identity-json",
        "identity-array",
    ],
)
def test_gitlab_errors_preserve_gate_and_state(gitlab_task, case):
    fixture, repo, task, api = gitlab_task
    open_mr(fixture, api)
    fixture.store.clear_current_gate("runtime-run", fixture.complete_tdd(), "implementation")
    if case == "access":
        api.fail = "/approval_state"
    elif case == "identity-access":
        api.fail = "users/2"
    elif case == "identity-json":
        api.bad_json = "users/2"
    elif case == "identity-array":
        api.users[2] = []
    elif case == "invalid-json":
        api.bad_json = "/discussions?"
    elif case == "stale-head":
        api.mr["sha"] = "different"
    elif case == "foreign-url":
        api.mr["web_url"] = "https://evil.example/team/subgroup/service-a/-/merge_requests/42"
    elif case == "foreign-project":
        api.mr["source_project_id"] = 12
    else:
        api.race = True
        api.reads = 0
    before = fixture.store.load("runtime-run")
    with pytest.raises(ResolutionError) as error:
        resume(fixture, api)
    assert "glpat-do-not-expose" not in str(error.value)
    assert fixture.store.load("runtime-run") == before


def test_gitlab_pagination_and_draft_body(gitlab_task, tmp_path):
    fixture, repo, task, api = gitlab_task
    body = tmp_path / "body.md"
    body.write_text("## Design\nBody with quotes and Unicode: ✓\n")
    created = open_pull_request(
        fixture.store,
        "runtime-run",
        "implementation",
        "Feature",
        body_file=body,
        draft=True,
        runner=api,
    )
    assert api.created[0]["title"] == "Draft: Feature"
    assert api.created[0]["description"] == body.read_text()
    provider = provider_for(repo)
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, '[]\n[{"id":1}]\n', ""))
    assert provider._api(
        "projects/team%2Fsubgroup%2Fservice-a/discussions", task, runner, pages=True
    ) == [{"id": 1}]
    assert created["url"].endswith("/42")


def test_missing_glab_actionable(gitlab_task):
    fixture, repo, task, api = gitlab_task
    with pytest.raises(ResolutionError, match="Install glab"):
        open_mr(fixture, Mock(side_effect=FileNotFoundError()))


def test_invalid_profile_provider_is_configuration_error():
    with pytest.raises(ConfigurationError, match="repo.forge"):
        RepositoryProfile.from_dict(
            {
                "apiVersion": "signal-harness/v1alpha1",
                "kind": "RepositoryProfile",
                "repo": {"forge": []},
            }
        )


def test_gitlab_approval_decision_journal_transitions(gitlab_task):
    fixture, repo, task, api = gitlab_task
    open_mr(fixture, api)

    def refresh():
        return refresh_pull_request(fixture.store, "runtime-run", "implementation", runner=api)

    def decisions():
        return [
            event
            for event in fixture.store.load("runtime-run")["events"]
            if event["type"] == "pull-request.approval-decision"
        ]

    approved = refresh()
    evidence = approved["approvalEvidence"]
    assert evidence == {
        "provider": "gitlab",
        "host": "gitlab.example",
        "projectId": 10,
        "iid": 42,
        "url": api.mr["web_url"],
        "sha": api.mr["sha"],
        "approverIds": [2],
        "decision": "APPROVED",
        "pendingReasons": [],
    }
    assert decisions()[0]["detail"] == {"task": "implementation", **evidence}
    assert decisions()[0]["at"]
    refresh()
    assert len(decisions()) == 1  # refreshed timestamps are not semantic transitions

    api.users[3] = {"id": 3, "bot": False}
    api.approvals["approved_by"] = [{"user": {"id": 3}}, {"user": {"id": 2}}]
    refresh()
    assert decisions()[-1]["detail"]["approverIds"] == [2, 3]
    assert len(decisions()) == 2
    api.approvals["approved_by"].reverse()
    refresh()
    assert len(decisions()) == 2

    api.approvals["approvals_left"] = 1
    assert refresh()["reviewDecision"] == "REVIEW_REQUIRED"
    assert decisions()[-1]["detail"]["pendingReasons"] == ["required-approvals"]
    assert len(decisions()) == 3

    worktree = Path(task["worktree"])
    (worktree / "change.txt").write_text("new head\n")
    run("git", "add", ".", cwd=worktree)
    run("git", "commit", "-m", "new head", cwd=worktree)
    state = fixture.store.load("runtime-run")
    state["tasks"]["implementation"]["publishedHead"] = head_sha(worktree)
    fixture.store.save(state)
    api.mr["sha"] = api.pipeline["sha"] = head_sha(worktree)
    refresh()
    assert len(decisions()) == 4
    assert decisions()[-1]["detail"]["sha"] == head_sha(worktree)

    before = fixture.store.load("runtime-run")
    api.race, api.reads = True, 0
    with pytest.raises(ResolutionError):
        refresh()
    assert fixture.store.load("runtime-run") == before


def test_gitlab_pending_approval_audit_is_not_denial(gitlab_task):
    fixture, repo, task, api = gitlab_task
    open_mr(fixture, api)
    api.approvals["approved_by"] = []
    value = refresh_pull_request(fixture.store, "runtime-run", "implementation", runner=api)
    assert value["approvalEvidence"]["approverIds"] == []
    assert value["approvalEvidence"]["decision"] == "REVIEW_REQUIRED"
    assert value["approvalEvidence"]["pendingReasons"] == ["independent-human"]
