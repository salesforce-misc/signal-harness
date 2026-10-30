# SDD operations

This guide is for core and distribution maintainers, CI, conformance tests,
and advanced diagnostics. Feature developers should use their distribution's
command or agent entrypoint, as described in the [consumption model](../README.md).

The harness is a persistent control plane for a Codex or Claude session. It
does not require the initiating terminal to remain attached. Run state lives
under `$XDG_STATE_HOME/signal-harness` (or `~/.local/state/signal-harness`).

Repository fetch caches default to `$XDG_CACHE_HOME/signal-harness/repositories`,
or `~/.cache/signal-harness/repositories` when `XDG_CACHE_HOME` is unset. Set
`SIGNAL_HARNESS_REPOSITORY_CACHE` to override that shared cache location.
Remotely fetched peer engagements create detached, run-owned snapshots under
`RUN_DIR/repositories/<snapshot-key>`. Explicit or saved checkout overrides are
used directly at their provided paths. A remotely acquired sponsor used for
planning gets a distinct snapshot under
`REPOSITORY_CACHE/plans/<unique-id>/<snapshot-key>`; this path is selected
before the run directory is reserved and persists in the plan. New snapshot
keys combine the sanitized, truncated repository ID with a SHA-256 digest of
the original ID and normalized remote. Matching legacy snapshots retain their
existing paths. Shared fetch-cache refreshes do not move managed snapshots;
persisted runs using those snapshots read context from their owned paths.

Interrupted provisioning keeps the ownership descriptor and original revision.
A retry repairs only an unlocked, detached registration at that revision whose
worktree still has its valid metadata. If it is missing, moved, dirty, on a
branch, or otherwise inconsistent, recovery stops with operator guidance rather
than deleting or repointing user content.

Persisted runs enforce the sponsor and engaged repository HEAD recorded at start,
including during Stage 0. If a local sponsor checkout advances, the error names
the expected SHA. Start a new run from the current revision to collect additional
evidence; preserve the old run and checkout work. Re-engaging an existing
repository does not relock it. Approved sponsor planning-document commits are
refreshed only when the engine materializes the task plan; this does not allow
arbitrary Stage 0 relocking.

Context locks hash on-disk working-tree content and record HEAD and dirty state
separately. Stage 0 accepts modified and untracked files, including as engagement
evidence; a file hash does not establish that its bytes exist in the recorded
commit. Later locking preserves earlier hashes, so editing, removing, or
discarding locked content causes a drift error. Preserve the original evidence
or start a new run to collect changed content. Before task worktree preparation,
the engine rejects recorded or current dirty state and verifies the locked file
hashes. Commit or stash changes and start a new run before implementation.

## Install CLI and agent entrypoints

From an `sdd-v*` tagged checkout, one command installs the version-matched CLI
and both the Codex skill and Claude plugin:

```shell
python3 scripts/install.py --runtime both
```

Use `--runtime codex` or `--runtime claude` when only one agent runtime is
needed. The installer refuses to overwrite an existing Codex skill; remove or
archive the old version explicitly before upgrading. Claude plugin installation is
delegated to the Claude CLI; this script adds no equivalent overwrite check.

Use Python 3.11 or newer:

```shell
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

The canonical core CLI is `signal-harness`. Product-specific entrypoints belong
in the product distribution; feature developers should use those entrypoints.

## Standalone setup

For an independent repository that does not need a shared distribution:

```shell
signal-harness init standalone . \
  --id widget-svc --remote acme/widget-svc --detect-commands
signal-harness sdd my-feature \
  --distribution .signal-harness/distribution.yaml
```

Standalone mode co-locates a local distribution and repository profile under
`.signal-harness/`. Promote that policy into a shared distribution when more
than one repository needs the same governance.

## Repository-local context

Each participating repository owns `.signal-harness/repo.yaml`. The profile
declares context seeds, commands, and signal sources without moving
repository-specific knowledge into the central engine.

```shell
signal-harness discover
```

`discover` reads only declared context and records the current Git revision,
branch, dirty state, file sizes, and content hashes. A run can therefore detect
context drift before writing code.

## Trigger the engine from a service

No repository-local pipeline definition is required. Point the core engine at
an installed distribution or an explicit policy pack maintained by the product
or organization:

```shell
signal-harness sdd lifecycle-events --distribution example-product
signal-harness sdd lifecycle-events --policy /path/to/product-policy
```

The engine discovers the feature manifest, loads the external catalog and
workflow, finds sibling checkouts by catalog remote, and creates a SHA-locked,
read-only discovery plan. Existing task cards appear only as `proposedTasks`;
the initial executable `tasks` list is empty. The catalog's lightweight
`interactsWith` graph selects discovery participants. During Stage 0 and Stage
1, the running agent inspects current source in those repositories to discover
the actual APIs, events, schemas, generated clients, configuration surfaces,
compatibility impact, and repository owners for this change. Use `--repo
ID=PATH` when a checkout is not a sibling.

Stage 0 records structured interface decisions and an affected-surface matrix
from current source; this is run evidence, not a maintained contract catalog.
Strict workflows can also require a source-derived caller matrix, an explicit
affected/no-impact audit across delivery-surface categories, and repository
preflight results. The surface audit also requires an evidenced precedent
decision for every affected surface. Each affected surface can declare criticality and exact
repository command checks.
Stage 1 materializes the executable DAG. Each task must identify its owner with
repo-relative source evidence, ordered test cases, required repository-profile
checks, covered surfaces, assigned callers, dependencies, and optional same-repository
dependency-head base. Every discovered surface must resolve to a task,
evidenced no-change decision, or governed defer; callers use the same exact
coverage rule. Cross-repository dependencies
declare produced/consumed artifacts. The
engine then freezes a second plan hash and grants write access only to task
owners.

When strict check derivation is enabled, Stage 1 audits every command in the
owning repository profile. A surface check may be omitted from a task only when
that audited entry is explicitly `not-applicable` with executable/applicability
facts, a reason, sources, and repository evidence. Core reconciles that decision
with the broader Stage 0 surface defaults; an unaudited omission still fails
closed.

The invocation checkout and feature owner can differ. For example, this reads
the service-a feature context and produces the same portable plan while invoked
from service-b:

```shell
signal-harness sdd lifecycle-events --cwd ../service-b \
  --sponsor service-a --distribution example-product
```

Additional context-only repositories can be added with `--include REPO_ID`; all
selected repositories and their direct neighbors remain read-only until the
approved Stage 1 task plan explicitly assigns ownership.
`--exclude REPO_ID` removes optional interaction neighbors and fails if it
attempts to remove the sponsor or a task repository.

Add `--start` to persist the resolved plan and initialize Stage 0. Gate evidence
is fail-closed and advances only when it satisfies the centrally declared gate:

```shell
signal-harness sdd lifecycle-events --policy /path/to/product-policy \
  --mode evaluation --start --run-id lifecycle-events-eval
signal-harness clear-gate lifecycle-events-eval --evidence merged-spec-pr.json
```

## Plan a multi-repository feature

Workspace manifests use stable repository ids. Local checkout paths are runtime
arguments and are never committed:

```shell
signal-harness validate /path/to/workspace.yaml \
  --cwd ../service-a \
  --repo service-b=../service-b

signal-harness plan /path/to/workspace.yaml \
  --cwd ../service-a \
  --repo service-b=../service-b
```

Resolve the safe target for one task:

```shell
signal-harness route /path/to/workspace.yaml service-b-change \
  --cwd ../service-a \
  --repo service-b=../service-b
```

## Persist and prepare a run

State defaults to `$XDG_STATE_HOME/signal-harness`, or
`~/.local/state/signal-harness` when `XDG_STATE_HOME` is unset.

```shell
signal-harness start <workspace.yaml> --cwd <sponsor> --repo service-b=<path>
signal-harness ack-context <run-id> <task-id> --file service-a:AGENTS.md
signal-harness prepare <run-id> <task-id>
signal-harness freeze <run-id> <task-id>
signal-harness next <run-id> --task <task-id>
signal-harness verify <run-id> <task-id> --command unit
signal-harness verify <run-id> <task-id> --command staticAnalysis \
  --baseline-report build/reports/pmd/main.xml \
  --baseline-report build/reports/pmd/test.xml --report-format pmd
signal-harness record <run-id> --task <task-id> \
  --kind decision --reference artifact://decision/1
signal-harness advance <run-id> --task <task-id> --evidence tdd-evidence.json
signal-harness rewind <run-id> --task <task-id> --to-stage 1 \
  --actor developer@example.com --reason "task needs further decomposition"
signal-harness rewind <run-id> --task <task-id> --to-stage 0 \
  --actor developer@example.com --reason "missing authorization decision"
signal-harness publish <run-id> <task-id>
signal-harness handoff-record <run-id> --task <producer-task> \
  --artifact <artifact-id> --uri <immutable-uri> --digest sha256:<digest> \
  --source-revision <published-head> --provider <provider-id>
signal-harness open-pr <run-id> <task-id> --title "Implement feature"
signal-harness resume <run-id> <task-id>
signal-harness status <run-id>
signal-harness summary <run-id>
signal-harness pr-plan <run-id> <task-id> --title "Implement feature"
```

`ack-context` records that the current session consumed the exact Stage 1
task-scoped locked files; `prepare` refuses to run until all are acknowledged.
It then rechecks the assigned repository's remote, cleanliness, HEAD, and
locked context before creating a task-specific worktree. `next` returns the
activity and permission boundary to the current agent session. Repository
commands execute without a shell and become persisted signals. `publish`,
`open-pr`, and `resume` revalidate the routed worktree and are idempotent for an
unchanged branch or provider event. `pr-plan` remains available as a dry run.
`rewind` supports audited Stage 2→1 re-decomposition, Stage 2→0 clarification,
and Stage 3→2 review-fix loops; it records actor/reason and rejects other
backward jumps. Re-decomposition preserves the invalidated execution graph in
the audit trail, returns the run to discovery, and replaces it only after the
corrected Stage 1 evidence passes.
`prepare` refuses a dependent task until every declared consumed artifact has
immutable URI, digest, producer, source-revision, and provider provenance.
In evaluation mode, `freeze` records a clean committed task SHA without pushing
it so dependency-head and local artifact handoffs can be exercised without
remote mutation. Production mode rejects local freezes and still requires a
published head plus remotely resolvable artifact evidence.
Production mode rejects waivers, local PR evidence, and local artifact results.
Evaluation waivers are also fail-closed unless the workflow gate explicitly
declares `waivableInEvaluation: true`.
For brownfield PMD debt, baseline-delta verification executes the same
repository-owned command at the locked base and task head, parses both declared
reports, and emits a passing engine signal only when the task introduces no new
findings. Both exit codes, finding sets, and the locked baseline revision remain
in the run ledger; this is not a waiver and cannot hide a new violation.
Workflows that select `knowledge` evidence require structured learning
artifacts, and multi-repository runs additionally require a rollback owner,
unwind order, deployment order, and alerting procedure.

## Operator controls

```shell
signal-harness summary RUN_ID
signal-harness pause RUN_ID --actor USER --reason REASON
signal-harness resume-run RUN_ID --actor USER
signal-harness cancel RUN_ID --actor USER --reason REASON
signal-harness recover RUN_ID
```

`summary` reports the current stage, task/PR state, signal counts, pending human
requests, pending notifications, and next actions. `recover` explains terminal
state, unresolved questions, and lease problems without mutating the run.

## Human gates and provider events

Human waits are durable and replay-safe:

```shell
signal-harness ask RUN_ID spec-approval --kind approval \
  --prompt "Approve the resolved specification?" --channel slack --channel work_items
signal-harness resolve-human RUN_ID spec-approval --actor USER \
  --decision approved --response "Approved for implementation"
```

Slack, work-item, and future integrations consume the same outbox contract. Queue a
structured event with a stable id:

```shell
signal-harness notify RUN_ID review-42 --channel slack \
  --kind pull-request.review-requested --payload review.json
signal-harness notify RUN_ID feature-123 --channel work_items \
  --kind work-item.feature.upsert --payload feature.json
```

Work-item providers can define `work-item.feature.*`, `work-item.stage.*`,
`work-item.task.*`, and `work-item.comment.*` event kinds. These are examples;
the distribution owns its event vocabulary. Provider adapters
return an external id after a successful update. The engine records attempts,
errors, delivery state, and external ids; replay never resends an already sent
event. With no adapter configured, events remain pending and workflow semantics
do not change.

Provider credentials are deliberately outside run state. A host process injects
one provider per enabled channel and calls `dispatch_outbox`; the engine never
loads Slack or work-item credentials itself.

## Parallel sessions

Only active tasks can be claimed. Leases are exclusive per repository and the
run-wide parallel limit is enforced:

```shell
signal-harness claim RUN_ID TASK_ID --owner SESSION_ID --ttl 3600
signal-harness release RUN_ID TASK_ID --owner SESSION_ID --token LEASE_TOKEN
```

The task dependency graph remains authoritative; a lease cannot make a pending
task runnable.

## CI and review findings

The selected code-host adapter normalizes review/CI evidence and imports
discussion findings into the shared ledger. GitHub and GitLab use the same
resume gate; see [provider configuration and GitLab safeguards](forge-providers.md).
Additional review comments can also be recorded explicitly:

```shell
signal-harness finding RUN_ID TASK_ID review-1 --source github-inline-review \
  --severity high --summary "Authorization guard is missing" \
  --evidence https://github.example/review/1
signal-harness resolve-finding RUN_ID TASK_ID review-1 --disposition disagree \
  --actor USER --rationale "Applied by the interceptor" \
  --evidence src/security/AuthorizationInterceptor.java
```

Every finding must be addressed, disagreed with using evidence, or explicitly
deferred. Replaying either operation with the same content is idempotent.

## Retention

Only complete or cancelled runs with no attached worktrees can be deleted. The
confirmation is intentionally exact:

```shell
signal-harness delete-run RUN_ID --confirm "delete RUN_ID"
```
