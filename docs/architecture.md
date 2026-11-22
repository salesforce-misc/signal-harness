# Architecture

For the SDD-specific seven-stage lifecycle, layer responsibilities, and execution
flow, start with the [SDD architecture README](../sdd/README.md).

## Product boundary

The Signal Engineering Harness is a governed control layer around repositories,
models, tools, engineering signals, and review systems. Models may propose work;
deterministic engine code owns repository selection, permissions, state,
evidence, and mutation boundaries.

```text
Repository context ─┐
Service contracts ──┤
CI and test results ├──► Context + evidence ──► Authoring ──► Change sets
Production signals ┤             │                │              │
Human feedback ─────┘             └──► Assurance ─┴──► Gates ───┘
                                          │
                                      Learning
```

## Current package boundaries

```text
src/signal_harness/
├── config.py, context.py, git.py, graph.py, models.py, state.py
│   └── shared deterministic runtime
├── planner.py, worktrees.py, session.py, verification.py
│   └── agent-session authoring and repository command execution
├── pull_requests.py, hosting.py, forge.py, github.py, gitlab.py
│   └── explicit PR/MR planning, provider dispatch, guarded publication and resume
├── control.py, integrations.py, leases.py, findings.py
│   └── operator controls, durable waits, provider outbox, task claims, and findings
├── security.py, retention.py, compatibility.py
│   └── command policy, limits, redaction, deletion, and parity evidence
├── catalog.py, sdd.py, workflow.py, lifecycle.py
│   └── central policy loading, repository interaction scope, and gate state
├── capabilities/
│   ├── authoring.py
│   ├── assurance.py
│   └── learning.py
└── signals/
    ├── models.py
    └── providers.py
```

Assurance and learning remain extension contracts. The current runtime work
focuses on SDD. Ad hoc repository review is development tooling with separate
repository-owned rule packs; it does not change the authoring lifecycle.

## Invariants

1. A mutating operation always carries an explicit repository id.
2. Read-only repositories may provide context and signals but never own a task,
   branch, commit, or PR.
3. Excluded repositories are not opened, indexed, or queried.
4. Local paths are runtime configuration. Portable manifests store repository
   identity as `<org>/<repo>`.
5. Repository context is declarative and SHA-locked.
6. Cross-repository task ordering is a DAG; cycles fail before mutation.
7. PR routing always names an explicit repository and base branch.
8. Quality decisions cite engineering signals and preserve their evidence.
9. Learning output is advisory until a governed policy-promotion step accepts it.

## Repository profile

`.signal-harness/repo.yaml` holds stable repository facts:

- identity and default branch;
- required, optional, and excluded context;
- build and verification commands;
- engineering-signal sources.

It contains no active run state. Product-specific compatibility paths and API
identifiers belong in an extension adapter, not in the core engine.

## Feature workspace

`signal-harness sdd` synthesizes the workspace from the external repository
catalog plus repository-owned profiles. The catalog defines the authorized
candidate universe and explicit author permission; startup SHA-locks only the
sponsor. Task cards are proposed hints, not executable tasks or repository
selection rules. During Stage 0, locked source evidence justifies
`engage-repository` for one catalog peer at a time, and `lock-context` locks exact
evidence paths after targeted search. Unengaged candidates are not opened or
fetched. Engaged repositories remain read-only until the approved Stage 1 plan
assigns task ownership and the catalog explicitly permits authoring.
Manually authored workspaces remain available when explicit routing is useful.

The engine loads a catalog and workflow from an external policy-pack path. It
does not embed any product's repositories, interfaces, lifecycle choices, or
providers. Participating repositories contribute knowledge and commands, not a
copy of the pipeline state machine. A product extension owns its policy pack;
the core repository keeps only neutral templates and non-installed examples.

## Runtime state

Transient state lives under `$XDG_STATE_HOME/signal-harness/<run-id>/`, falling
back to `~/.local/state/signal-harness/<run-id>/`. It does not dirty participant
repositories. Reviewable outcomes remain in feature artifacts and PRs.

## Mutation flow

1. `sdd` builds a discovery plan: only the sponsor is SHA-locked and read-only,
   the catalog candidate universe is retained, prior task cards are proposed
   hints, and no executable task exists yet.
2. Stage 0 audits the feature specification and discovers affected interfaces,
   behavior, failure handling, compatibility, rollout, and repository boundaries
   from current source. Specification evidence includes a source/delegation
   audit and cannot clear with unresolved delegation. Policy may additionally
   require a caller matrix, complete delivery-surface category audit,
   one precedent decision per affected surface, repository preflight, and
   surface-owned verification commands. Locked source evidence permits lazy
   engagement of catalog peers; selected inaccessible peers require human access.
3. Stage 1 freezes an execution plan. Every task must have a repository owner,
   source-backed ownership evidence, ordered test cases, required repository
   checks, dependencies, and an explicit base strategy. Only task-owner
   repositories with explicit catalog author permission then become writable.
   The discovery and execution plan hashes
   are both retained for audit. If approved Stage 0/1 artifacts were committed
   after discovery, the engine advances the sponsor lock only when every commit
   is a descendant on the same branch and every changed file is a planning
   document beneath the active feature directory. Any source change or change
   in another participant fails closed.
   With strict check derivation, every repository-profile command is audited.
   A validated, evidence-backed `not-applicable` disposition can narrow a
   Stage 0 surface's default checks for that task; merely omitting the check
   cannot.
4. The session reads and explicitly acknowledges every Stage 1 task-scoped
   locked context file. `prepare` refuses to create a worktree before this and
   then revalidates the source repository. A task may explicitly use a published
   same-repository dependency head as its base; otherwise it uses the locked
   repository revision.
5. `next` gives the current Codex or Claude session its task, worktree, locked
   context, and exact read/write boundary.
6. Cross-repository dependencies resolve declared artifacts with immutable URI,
   digest, producer task, source revision, and provider provenance before the
   consumer worktree can be prepared. Evaluation runs may locally freeze a clean
   committed producer SHA without a push; production rejects that evidence and
   requires a published head.
7. `verify` runs repository-owned commands without a shell and persists their
   outcomes as engineering signals.
   Brownfield static-analysis debt can use baseline-delta verification: Core
   executes the identical command at the locked base and task head, compares
   declared structured reports, and passes only when there are no new findings.
8. A TDD gate cannot clear until every Stage 1 `requiredChecks` command has a
   latest passing engine-produced signal.
9. Before a write or PR, the engine rechecks task ownership and repository root.
10. `publish` pushes the session-owned commit; idempotent PR creation targets the
   explicit repository and persists returned evidence.
11. `resume` refreshes external PR state and advances a merge gate exactly once.
12. `rewind` allow-lists and audits Stage 2→1 re-decomposition, Stage 2→0
    clarification, and Stage 3→2 review-fix transitions. Re-decomposition
    snapshots and invalidates the old task graph before Stage 1 materializes
    its replacement; a clarified Stage 0 gate resumes the same task rather than
    replaying unrelated stages.
13. A workflow can select structured `knowledge` evidence. When multiple
    repositories own tasks, Core additionally requires rollback ownership,
    unwind/deployment order, and alerting before completion.
14. Human/provider waits, task claims, approvals, budgets, and operator controls
    are persisted and replay-safe.
15. Portable plan and run snapshots make native/central compatibility drift
    machine-readable.

In evaluation mode, only gates explicitly declaring `waivableInEvaluation` may
record a waiver with a reason, authorizer, and scope. A waiver never becomes a passing result: the final run
status is `evaluation-complete-with-waivers`. PR approval/merge gates cannot be
waived, and Stage 3 cannot approve a task after a waived Stage 2. At a workflow
that explicitly permits a TDD waiver, it closes unfinished cases as waived; if the
RED/GREEN/REFACTOR lifecycle already completed and only a required environment
check is unavailable, that completed TDD evidence remains intact.
Production mode rejects waivers and local-only PR or artifact evidence.

The harness controls workflow and evidence but never launches the coding agent.
The developer-owned session makes code changes and commits; the harness enforces
their route, verification, publication, and gate transitions.
