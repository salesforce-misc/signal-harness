# Code-host providers

The SDD stages are shared. GitHub PRs and GitLab MRs are provider-specific
implementations of the same publication, review, and resume boundary.

## Configuration

The repository profile selects a provider independently of Git transport:

```yaml
repo:
  id: widget-ui
  remote: git@gitlab.example.com:engineering/subgroup/widget-ui.git
  defaultBranch: main
  forge: gitlab
```

`github.com` and `gitlab.com` infer their provider. Self-hosted installations
must declare `repo.forge: github` or `gitlab`; an unknown host never silently
falls back to GitHub. Both standalone and distribution-backed bootstrap accept
`--forge gitlab`. Distribution owners should set this during their onboarding.
These additions require a core release containing the provider adapter; they
do not retrofit an already installed older release.

Keep the SSH/HTTPS clone URL in the catalog/profile. Identity comparison
normalizes GitHub to `owner/repo` and other hosts to
`https://host/namespace/project`, including nested GitLab groups. This does not
rewrite origins or clone catalog entries. Git access and API access remain
separate; neither is probed simply to select a provider.

## Runtime sequence

1. `git.py::normalize_remote` compares the invocation checkout with its profile
   and catalog without losing a non-GitHub host. Context locking/planning and
   repository rules continue to use the existing engine; no forge API is needed.
2. `hosting.py::publish_branch` validates a clean, session-owned commit and uses
   ordinary `git push`. SSH Git permission is sufficient for this operation.
3. `hosting.py::open_pull_request` calls `forge.py::provider_for`. The GitHub
   adapter uses `gh`; the GitLab adapter uses `glab api --hostname HOST`, with
   explicit encoded project identity and JSON request bodies, never a shell.
   The public CLI retains its `pr` terminology for backwards compatibility.
4. `gitlab.py::GitLabProvider.open` searches all pages for an existing MR before
   creating one. Source/target projects, branches, host, URL, and the clean
   published local head must match. Cross-project forks are not supported.
5. `GitLabProvider.refresh` reads authenticated MR approvals and approval rules,
   reset-on-push policy, every discussion page, and the MR head pipeline. It
   rechecks the MR at the end and rejects a changed snapshot. Failed/incomplete
   reads raise an actionable error without saving partial approval evidence.
6. `hosting.py` imports discussion findings into the common findings ledger and
   resumes the existing review gate only with a merged MR, independent human
   approval, passing checks, and complete task evidence. Repeated resume cannot
   advance a gate twice. Provider selection never bypasses a human gate.

GitHub's existing `signal_harness.github` public imports remain compatibility
exports. The CLI now calls `hosting`, not the GitHub implementation directly.

## GitLab authentication and conservative gates

Install `glab` using its supported installer, then authenticate for your host:

```shell
glab auth login --hostname gitlab.example.com --git-protocol ssh
glab auth status --hostname gitlab.example.com
```

Use the normal credential store, not repository config or run state. Git push
permission does **not** establish API permission. Missing CLI, expired login,
403/404, unavailable approval APIs, invalid responses, or timeouts stop the
affected MR operation and request access; they do not block local discovery.

Automatic GitLab resume deliberately requires:

- At least one approver other than the MR author, zero remaining approvals,
  and every returned approval rule approved. Empty approvers never pass.
- `reset_approvals_on_push: true` from the project approvals API. Without this
  policy the API does not establish that an existing approval covers the
  current head. Ask the project owner to enable the policy; the engine does not
  modify settings or approve on a user's behalf.
- A successful head pipeline with SHA exactly equal to the published task
  head. Missing/manual/skipped pipelines and synthetic merged-result SHA
  pipelines remain pending; this adapter does not infer source-head provenance
  for synthetic merge commits or substitute unrelated branch CI.
- All resolvable discussions resolved, a non-draft MR, no detected conflict or
  rebase requirement, and stable MR evidence across the refresh.

These restrictions may leave a legitimate MR pending; they must not turn
unknown evidence into a pass. Fix permission/policy/pipeline configuration and
refresh rather than overriding the gate. See the official
[GitLab approvals API](https://docs.gitlab.com/api/merge_request_approvals/) and
[glab API command](https://docs.gitlab.com/cli/api/).

## Validation and release boundary

`tests/test_forge.py` covers self-hosted/subgroup identities, profile seeding,
provider dispatch, MR creation/reuse, pagination, review evidence, access
failures, stale heads/pipelines, and idempotent gate resume. The existing
GitHub runtime suite remains a compatibility check. These are deterministic
local Git worktree plus mocked API trials, not a live GitLab certification.

After merging, publish a versioned SDD core release, update the distribution's
core pin through its normal CI, then validate authenticated MR reads and a
full test MR lifecycle against the intended GitLab instance. Do not pin a
distribution to an unpublished tag or mistake an unmerged adapter for support
in an older installed core.
