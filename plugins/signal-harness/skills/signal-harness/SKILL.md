---
name: signal-harness
description: >-
  Signal-driven engineering entrypoint for code authoring and assurance. Use
  when planning work from a repository, including multi-repo features with
  explicit include/exclude selection, contract dependencies, signals, and
  per-repository change routing.
metadata:
  type: workflow
  version: 0.9.0
---

# Signal Engineering Harness

Use the standalone `signal-harness` CLI as the deterministic source of repository
identity, context, permissions, and task routing.

For initial adoption, use `signal-harness init distribution` to scaffold a
shared product policy, `signal-harness init repository` to seed a service-owned
profile and update that distribution's catalog, or `signal-harness init
standalone` for an independent repository. Treat detected commands and context
as a draft for repository-owner review; never overwrite a conflicting existing
profile.

## Preflight

1. Run `signal-harness discover` in the invoking repository. Stop if the repository
   has no `.signal-harness/repo.yaml` or required context is missing.
2. For an SDD feature, run `signal-harness sdd <feature> --distribution NAME`
   or supply an explicit external `--policy` path. The selected distribution
   supplies policy while repository profiles supply context and commands.
   Production is the safe default for distributions that declare it; use
   `--mode evaluation` only for explicitly labeled local/conformance runs.
3. For an explicitly routed workspace, locate or author a feature workspace
   manifest. A single-repo feature still uses a workspace with one repository.
4. Run `signal-harness validate <workspace>`, passing `--repo ID=PATH` for every
   additional local checkout. Stop on any validation error.
5. Run `signal-harness plan <workspace>` and treat its repository ids, modes,
   context locks, signal declarations, task ordering, and interaction edges as
   authoritative.
6. For a synthesized SDD run, treat `proposedTasks` as discovery hints only.
   Before Stage 1 clears, every included repository is read-only and `tasks` is
   empty. Do not prepare a worktree or infer a write target during Stage 0.
7. Read every file listed in each included repository's `context.files` before
   decomposing or implementing work. The file list includes each participant's
   feature manifest when one is declared. Search implementation code on demand;
   do not replace declared context with an indiscriminate whole-repo read.

## Work through the central runtime

After starting a run, use `signal-harness next <run-id> [--task TASK]` as the
authoritative handoff. It returns the current activity, locked context,
repository route, worktree, and read/write boundary. The current Codex or Claude
session performs the implementation; never launch a second coding agent.

Stage 0 starts with only the sponsor engaged; catalog peers are candidates, not
permission to read them. Use targeted source search and lock exact sponsor paths
before citing them or engaging another repository:

```shell
signal-harness lock-context RUN_ID SPONSOR_ID src/caller.py --reason "Stage 0 found the caller"
signal-harness engage-repository RUN_ID PEER_ID --reason "The caller uses this peer" --evidence SPONSOR_ID:src/caller.py
signal-harness lock-context RUN_ID PEER_ID src/handler.py --reason "Stage 0 found the peer handler"
signal-harness context RUN_ID
signal-harness next RUN_ID
```

Read the refreshed locked context before submitting evidence. Engage only peers
supported by already locked source references; lock discovered peer paths before
referencing them in specification evidence. If engagement records an unavailable
repository, resolve the access failure and retry engagement before advancing;
never substitute a no-impact claim for missing required access. Profileless
peers are discovery-only until onboarded with verification commands and included
in a new run.

Stage 0 is the specification audit. Inspect current source across engaged
repositories and resolve scope, behavior, errors, compatibility, rollout,
interface impact, affected delivery surfaces, and ownership questions. Its evidence must include a
`sourceAudit` listing consulted/missing sources and delegation findings; no
unresolved delegation may pass. It must also include structured
`interfaceDecisions` and `affectedSurfaces` discovered from current source.
When the selected workflow requires them, Stage 0 must also provide the exact
configured `surfaceAudit`, `callerAudit`/`callerMatrix`, and
`repositoryPreflight` checks. A strict surface audit also requires one
`precedentAudit` decision per affected surface. A surface's verification checks are repository
command ids, not prose; the runtime enforces them on the owning task.
Stage 1 must submit a task DAG where
each task declares `repo`, `dependsOn`, ordered `testCases`, `requiredChecks`,
covered `surfaces`, assigned `callers`, task-scoped locked `context`, and `ownership` with a reason
plus repo-relative source evidence. Every discovered surface must close through
a task, evidenced no-change decision, or governed defer. Every caller must close
the same way through `callerCoverage`. Cross-repository build
dependencies declare produced/consumed artifacts. The runtime materializes
those tasks and grants write scope only after that gate clears.

For a routed task:

1. Read every task-scoped required context file returned by `next`, record it
   with `ack-context`, then run `prepare`. Call `next` again and work only in its
   returned worktree.
2. Use `verify --command ID` for repository-owned checks and `record` for durable
   artifact references.
3. Record one phase at a time with `tdd-record`. Use `tdd-evidence` after all
   ordered RED, GREEN, and REFACTOR phases complete, then submit that exact
   evidence to `advance`; guard tests also require sensitivity evidence.
4. Commit in the session, then use `publish` and `open-pr` for the explicit
   repository route.
   After a producer is published, record each declared output through
   `handoff-record`; consumer preparation is blocked until provenance resolves.
5. After external approval and merge, use `resume` to refresh the PR and advance
   its gate exactly once.
6. When a task or run is terminal, use `cleanup`; it refuses dirty or unowned
   worktrees and retains the task branch.

If Stage 2 exposes a missing product decision, use `rewind --to-stage 0` with
the task, actor, and reason. After the revised specification gate clears, Core
resumes that same task at Stage 2. If Stage 3 review requires implementation
changes, rewind that task to Stage 2 and clear its evidence again. Do not mimic
either transition by editing state files.
When the workflow selects `review` evidence, reconcile every task test case and
affected surface and caller, include a passing privacy check, and satisfy every
configured production-readiness dimension. A review cannot clear after a waived
Stage 2.

For a workflow using `knowledge` evidence, submit persisted signals, artifact
references, and learning candidates. A run with task owners in multiple
repositories must also include a rollback owner, unwind order, deployment
order, and alerting procedure.

## Safety rules

- Never infer a write target from the current directory after planning.
- Before working on a task, run `signal-harness route <workspace> <task-id>` and use
  the returned repository root and remote.
- Never modify or open a PR for a read-only repository.
- Never read an excluded repository as background context.
- A task may use context from other included repos, but its diff and PR belong
  only to its assigned repo.
- Do not advance a consumer task until its declared producer dependencies pass.
- Do not prepare a cross-repository consumer until every consumed artifact has
  immutable URI, digest, source-revision, and provider provenance.
- Production runs cannot use waivers, local PR URLs, or local artifact results.
- Evaluation waivers are allowed only on gates that explicitly set
  `waivableInEvaluation`; PR gates and mandatory distribution work remain non-waivable.
- Evidence values such as `undefined`, `null`, or `n/a` never count as TDD signals.
- If a context SHA changes after planning, rebuild the plan before continuing.

## Current implementation status

The `0.9.x` engine implements evidence-driven lazy repository engagement,
immutable run-owned snapshots, versioned external distributions, the agent-driven
session protocol, repository
command signals, persisted one-case TDD, review regression evidence and targeted
reruns, safe branch publication and cleanup, GitHub finding ingestion,
idempotent PR creation, and pull-based PR resume. Event callbacks remain an
optional deployment extension.
