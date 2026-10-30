# Version compatibility

The SDD CLI and agent entrypoints are released together under `sdd-v*` and use
the same package version. AIAL is released independently under `aial-v*` from
the same repository. Repository-owned profiles and workspaces use versioned API
identifiers so they do not need to copy either engine pipeline.

| SDD release | Python | State | Schemas/profile API | Policy workflow | Codex/Claude plugin |
| --- | --- | --- | --- | --- | --- |
| 0.9.x | 3.11–3.13 | 1 | `signal-harness/v1alpha1` | lazy repository engagement | exactly 0.9.x |
| 0.8.x | 3.11–3.13 | 1 | `signal-harness/v1alpha1` | versioned external distribution | exactly 0.8.x |
| 0.7.x | 3.11–3.13 | 1 | `signal-harness/v1alpha1` | external, extension-owned | exactly 0.7.x |
| 0.3–0.6.x | 3.11+ | 1 | `signal-harness/v1alpha1` | legacy embedded workflow | match the CLI minor |

Starting in 0.7, the core does not recognize product-specific profile API names,
paths, commands, or default policies. An extension may provide a wrapper during
its own migration, but it must invoke `signal-harness` with an explicit policy
pack.

Starting in 0.8, a distribution manifest declares `requiresCore`, `profileApi`,
its packaged policy, and approved provider names. The same compatibility range
should appear in the distribution package dependency metadata.

Starting in 0.9, catalogs define only the authorized repository universe and
authoring permission. Stage 0 resolves repositories lazily from evidence;
static `interactsWith` graphs are rejected. Omitted `author` defaults to `false`
for both catalog entries and workspace repository entries. When upgrading, add
`author: true` explicitly to each intended implementation task owner;
`mode: read-write` alone does not grant authoring or permit task routing.

Saved 0.8 discovery plans retain their original permissions until explicitly
migrated. Updating workspace/catalog files affects new plans only. Before Stage 1
materialization, review a JSON file keyed by engaged repository ID, for example:

```json
{"service-a": {"remote": "example-org/service-a", "author": true}}
```

Apply only reviewed grants with the saved repository remote:

```shell
signal-harness migrate-author-permissions RUN_ID --permissions reviewed-grants.json \
  --actor USER --reason "Reviewed saved-run upgrade" --state-root STATE_ROOT
```

This operation accepts discovery runs at Stage 0 or 1, fills only missing `author`
fields, preserves explicit permissions (including `false`), updates plan hashes,
and journals actor, reason, identities, grants, and prior hash. Omitted repositories
remain denied. It cannot grant permissions to unknown repositories or change
remotes. Already materialized execution plans require a new run for new grants.

New run snapshots include a hash of the original repository ID and normalized
remote in their filenames. Existing identity-matched legacy snapshots are reused
without moving them or following a newer remote tip.

Compatibility rules:

- Install the CLI and plugin from the same `sdd-v*` tag or commit with
  `scripts/install.py`; mixed plugin/CLI minor versions are unsupported.
- AIAL consumers select only `aial-v*`; SDD consumers select only `sdd-v*`.
  A release from the other component is not an available dependency update.
- State version 1 is readable across the listed releases. New optional fields
  are additive; a future incompatible state change requires a migration and a
  new state version.
- `v1alpha1` schemas may add optional fields in a minor release. Required-field
  or semantic changes require compatibility fixtures and an announced
  migration path.
- A policy pack declares its own workflow version and repository universe.
  Repositories retain only context, commands, signals, and rules;
  they do not pin or copy the workflow implementation or an interface inventory.
- `compat-snapshot` and `compat-run-snapshot` provide the portable artifacts for
  checking plans and completed results during an upgrade.

### Core 0.9 gate actor migration

`clear-gate` and its `advance` alias require an explicit `--actor USER`.
Update automation that previously omitted it, for example:

```shell
signal-harness clear-gate RUN_ID --evidence evidence.json --actor USER
```

Use the identity initiating the gate action. Unlike `lock-context`, these gate
commands do not default to `system`.
