# SDD security model

## Trust boundaries

The centrally versioned workflow and schemas are trusted engine code.
Repository profiles are trusted configuration only after normal repository
review: they may name context files, stable commands, contracts, and signal
sources, but cannot change the central workflow, state policy, repository
route, or another repository's permissions.

A command referenced by a declared engineering-signal source is allowlisted by
the reviewed profile. Any other profile command requires a per-run approval
that records the actor. Commands execute as an argument vector in the routed
worktree, never through a shell. Shell interpreters (`sh`, `bash`, `zsh`,
`fish`, `cmd`, and PowerShell) are rejected even when declared in a profile.

```shell
signal-harness approve-command RUN_ID REPOSITORY COMMAND_ID --actor USER
```

Repository identity, remote, locked revision, clean state, context hashes,
write mode, task route, branch, and worktree root are checked before mutation.
Branch names, run ids, external ids, and provider event keys use bounded safe
formats; provider payloads remain structured data rather than generated command
arguments.

## Credentials and least privilege

Use separate credentials or applications for these capabilities:

| Capability | Minimum permission |
|---|---|
| Context discovery | read selected repositories |
| Task publication | push only the routed repository branch |
| Pull requests | create/read PRs; no merge or self-approval |
| Slack | post only to configured review channels |
| work-item/work items | create/update only configured work-item types |

Credentials must be supplied by the host credential store or provider adapter.
They must never appear in profiles, workspace manifests, provider payloads, or
run state. Bearer tokens, common GitHub/AWS token shapes, and secret assignments
are redacted from command output and audit event details before persistence.

## AI providers and local artifacts

The optional AI review runner sends the prepared source bundle to the configured
Codex service using the operator's existing account. The bundle includes source
and review context. Use an account and provider approved for the repositories
being reviewed; the harness does not establish a separate data-processing
agreement or a network isolation boundary. See the
[review runner's data flow and isolation limits](review/README.md).

Run state, context snapshots, worktrees, source bundles, and model logs can
contain proprietary source or sensitive engineering information even after
credential-pattern redaction. Keep them out of public commits, restrict access,
and apply your organization's retention policy. Redaction is not a guarantee
that arbitrary confidential content has been removed.

## Limits and audit

Each run defaults to 1,800 seconds per command, 100 command executions, four
parallel repository leases, 20,000 persisted output bytes per stream, and a
USD 100 reported cost budget. Agent hosts report usage with:

```shell
signal-harness record-cost RUN_ID --amount 1.25 --source codex-session
```

The event journal is sequence-numbered and hash-chained. Loading a run fails if
an event was edited, removed, inserted, or reordered. This is tamper-evident
local state, not a substitute for exporting audit events to an access-controlled
append-only enterprise log.

## Threat and recovery cases

Automated tests cover shell-wrapper injection, unapproved commands, timeout and
cost overrun, output-secret leakage, audit corruption, dirty or drifted context,
repository-lease conflicts, interrupted provider delivery, replayed events, and
unsafe run deletion. GitHub recovery distinguishes failed/pending CI, approval
waits, merge waits, requested changes, closed-unmerged PRs,
merged-without-approval, and stages that are not at the PR gate.

For an incident: pause the run; revoke the affected provider credential; export
the run id and audit state to the incident record; inspect `recover`; cancel if
integrity or routing is uncertain; clean owned worktrees separately; and delete
state only after the retention owner authorizes it. Default retention is 30
days, but the host environment owns archival and legal-hold policy.
