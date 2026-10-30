# Changelog

All notable changes to Signal Engineering Harness are recorded here. The
project follows semantic versioning once its public API reaches 1.0.

## [Unreleased]

See the [delivery roadmap](docs/roadmap.md) for planned work.

### Changed

- `clear-gate` and its `advance` alias now require `--actor USER` for explicit
  gate attribution. Update existing invocations to supply the initiating actor.
- Exact context file paths must use on-disk spelling so case aliases cannot
  bypass exclusions on case-insensitive filesystems.

- Omitted `author` now defaults to `false` in both catalogs and workspace
  repositories. Upgrades must set `author: true` on intended task owners;
  `mode: read-write` alone does not grant authoring permission.

### Fixed

- Sanitize and bound peer acquisition diagnostics before returning or persisting them.
- Retry context-lock denial audit saves against fresh state on bounded revision conflicts,
  preserving concurrent host costs and the locked plan.

- Recover missing run snapshot registrations at their original revision without
  deleting partial or changed resources; report preserved recovery paths.
- Use `obtain-repository-access` consistently for required discovery access blockers.
- Preserve sanitized sponsor acquisition diagnostics and record bounded context-lock
  integrity denial events without changing the locked plan.

- Planning previews and failed plans release only their owned, unchanged sponsor
  worktrees; started runs retain isolated snapshots and changed resources require recovery.
- Catalog clone remotes reject leading hyphens and use a Git option terminator.
  Git acquisition and probes run noninteractively with a 120-second timeout.

- Persist denied author-permission migrations in the local run audit journal,
  preserving plan hashes and grants on rejection.
- Continue frozen 0.8 discovery context locking with saved repository profiles
  when the additive `profileSource` field is absent.

- Attribute context-lock events to the initiating actor, with an explicit action
  and outcome; `lock-context --actor` defaults to `system` for compatibility.
- Use repository-ID and remote hashes for collision-resistant snapshot names;
  existing matching legacy snapshot identities retain their original paths.
- Persist cleanup intent before deleting task worktrees and reconcile completion
  against fresh state, preserving concurrent costs and interrupted cleanup retries.
- Add `migrate-author-permissions` for reviewed, remote-bound legacy discovery
  grants without overriding explicit permissions or inferring grants from write mode.

- Allow exact evidence locking during active Stage 0 clarification rewinds while
  preserving materialized tasks and the return to Stage 2.

- Preserve ignored files and recovery guidance when task preparation conflicts
  with a concurrent state update.
- Isolate remotely acquired sponsor snapshots per SDD plan so planning-document
  commits cannot alter another run or prevent later acquisitions.

- Compensate pristine task worktrees and branches after a conflicting state save,
  preserving concurrent updates and allowing preparation to retry; preserve changed
  or newly recorded resources with explicit recovery guidance.

- Serialize repository engagement with other run mutations to preserve records,
  required access blockers, and events under concurrent Stage 0 operations.
- Allow obsolete planning-document paths to be removed during approved relocks,
  while still requiring current profile and feature-manifest context.

- Preserve exact sponsor evidence across approved planning-document relocks.
- Refresh cached remote defaults and validate original run snapshot identity on retry.
- Retain authorized checkout overrides in run-local state for lazy engagement,
  without adding local paths to portable plans, and validate peer profile identity.

### Added

- Evidence-driven Stage 0 repository engagement: startup locks only the sponsor,
  `engage-repository` resolves one relevant peer, and `lock-context` records only
  exact source evidence found by targeted search.
- Run-owned immutable repository snapshots backed by shared fetch caches, with
  partial clones and explicit human access gates only for engaged repositories.
- Actor-attributed authorization-denial audit events for Stage 1 task routing.
- Verified GitLab approval actor and decision evidence in the existing run journal,
  emitted only on semantic transitions after a complete validated refresh.
- Catalog-profile-aware prior-feature URL parsing for GitLab namespaces named
  `blob`, preserving GitHub Enterprise paths containing `-/blob`.

- Provider-neutral PR/MR lifecycle dispatch with GitHub compatibility exports
  and a GitLab adapter for MR creation, paginated review findings, independent
  approvals, exact-head CI, and fail-closed, replay-safe resume.
- Optional repository-profile `repo.forge` and bootstrap `--forge` for explicit
  provider selection on self-hosted code hosts, without discovery-time API calls.

- Versioned distribution manifests, installed distribution entry-point
  discovery, and fail-closed core compatibility checks.
- `signal-harness sdd --distribution NAME_OR_PATH` and portable distribution
  identity in plans and runs.
- A product-neutral core boundary enforced by asset validation.
- Repository-local SDD planning without a declared cross-service contract.
- Explicit feature sponsorship so a central run can be invoked from any
  cataloged participant while retaining the sponsor's manifest and context.
- Persisted one-case-at-a-time RED, GREEN, and REFACTOR execution.
- Regression evidence and post-resolution targeted verification for findings.
- GitHub PR comment and inline review ingestion with abnormal-state recovery.
- Ownership-checked, replay-safe worktree cleanup that retains task branches.
- Unified CLI/plugin installer, asset validation, type checking, support,
  release, and version-compatibility documentation.
- Runtime-discovered interface decisions and affected-surface closure from
  specification through task planning and review reconciliation.
- Product-neutral produced/consumed artifact handoff with immutable provenance
  and distribution-owned provider entry points.
- Evaluation and production policy modes; production rejects waivers and local
  PR or artifact evidence.
- Same-repository dependency-head enforcement and cross-repository artifact
  dependency validation.
- Workflow-configured caller matrices, complete delivery-surface audits,
  repository preflight checks, and production-readiness review dimensions.
- Surface-owned verification requirements and exact caller coverage from
  specification through task planning and review.
- Explicit `waivableInEvaluation` policy with a fail-closed default.
- Access-aware lazy discovery that gates an evidence-selected repository on
  human access without probing every catalog entry at startup.
- Product-neutral `init distribution`, `init repository`, and `init standalone`
  scaffolding with conservative command detection and non-overwriting writes.
- Independent `sdd-v*` and `aial-v*` release channels so an AIAL-only release
  cannot advance an SDD consumer pin.

### Fixed

- Repository engagement rejected outside Stage 0 discovery now records an
  actor-attributed denial while preserving the existing error and plan.

- Repository-catalog `author` permissions now default to deny, and static
  `interactsWith` graphs are rejected in favor of runtime Stage 0 discovery.
- Profileless peer inference avoids recursive application-source globs and
  invented verification commands. Documentation, specification, workflow and
  build-file seed globs remain collected on engagement, and inferred default
  branches follow the remote.

- Manifest-relative prior-feature paths are locked locally. Execution workspaces
  lock catalog-authorized GitHub prior references from selected peer checkouts;
  lazy Stage 0 discovery defers external references until explicit engagement
  and exact-path context locking.
- Host-preserving SSH/HTTPS repository identity and catalog-scoped prior-feature
  blob references for GitLab subgroups; no GitHub mirror substitution.
- Reopened provider-resolved discussion findings return to the open ledger.
- Opt-in literal, caller-inventory, side-effect-placement, repository-check,
  test-category/traceability, review-provenance, and manifest-closure evidence
  requirements for distribution policies.
- Legacy task-card dependency forms used by existing consumer SDD artifacts.
- Generated run IDs now satisfy the engine's lowercase-safe ID contract.
- Stage 1 validates structured test-case objects before execution-plan
  materialization, eliminating a validator/materializer contract mismatch.
- Explicit TDD waivers now close pending case state as waived instead of
  leaving an apparently completed task with an active RED lifecycle.
- Stage 1 can safely re-lock a sponsor after merged planning-document commits;
  non-document, non-sponsor, dirty-tree, branch, and history drift still fail
  closed.
- TDD evidence rejects placeholder values such as `undefined`, `null`, and
  `n/a`, including guard-sensitivity records.
- Stage 3 cannot approve a task whose Stage 2 gate was waived.

## [0.3.0] - 2026-10-05

### Added

- Centrally packaged Stage 0–6 SDD workflow prototype.
- Contract-driven multi-repository discovery and task planning.
- Repository-owned context, command, contract, and signal profiles.
- Recursive feature-context locking and drift detection.
- Persisted gate state and dependency-ordered per-task progression.
- Guarded task worktrees and explicit non-executing PR plans.
- Provider-neutral assurance, learning, and engineering-signal contracts.
- Claude and Codex plugin manifests and a legacy migration alias.

[Unreleased]: https://github.com/salesforce-misc/signal-harness/commits/main/
[0.3.0]: #030---2026-10-05
