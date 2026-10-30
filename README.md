# Signal Engineering Harness

Signal Engineering Harness is the product-neutral SDD engine behind team and
product engineering workflows. It supplies deterministic stages, repository
discovery, context locking, task routing, evidence contracts, gates, worktree
isolation, and PR routing. It intentionally does not contain a company's
service catalog, engineering policy, repository rules, or user-facing feature
command.

This README focuses on the SDD consumption model. The repository can also host
independently released assurance capabilities, but those do not change how an
SDD consumer is layered or invoked.

> **Feature developers do not invoke `signal-harness` directly.** Install and
> use the command or agent entrypoint supplied by your team distribution. The
> raw core CLI is for distribution authors, CI, conformance tests, and advanced
> diagnostics.

For ad hoc review of core or a consumer distribution, see
[AI review setup and commands](docs/review/README.md). The shared
`harness-code-review` skill loads the target repository's own rules.

- **Authoring** — repository discovery, context locking, multi-repo planning,
  task routing, guarded worktrees, and explicit per-repository PR plans.
- **Assurance** — a provider-neutral boundary for investigation, code review,
  test analysis, verification, and quality gates.
- **Learning** — a governed boundary for feedback, known patterns, signal
  learning, and policy promotion.
- **Engineering signals** — a shared evidence contract for tests, CI, static
  analysis, contracts, observability, incidents, and human feedback.

Version `0.9.0` makes repository discovery lazy and evidence-driven on top of
versioned distribution manifests and repository profiles. It also adds
runtime-discovered affected-surface closure, immutable cross-repository
artifact handoff, evaluation/production policy modes, persisted one-case TDD,
review-regression evidence, targeted verification reruns, abnormal-state
recovery, and ownership-checked worktree cleanup. Codex or Claude remains the
code executor; the harness is its deterministic control plane.

## Choose a consumption model

The engine supports two ownership models. Both keep the reusable lifecycle in
this repository and consume an immutable `sdd-v*` core release.

### Two-tier: engine + distribution

Use two tiers when one application or team can own its SDD policy, repository
configuration, and user entrypoint together.

```text
1. Signal Engineering Harness                  reusable core engine
                  │ released SDD dependency
                  ▼
2. Acme SDD distribution                       consumer-owned layer
   policy, repository configuration, adapters,
   agent entrypoint, and `acme-sdd` command
                  │
                  ▼
   feature developer runs `acme-sdd <subcommand> <request>`
```

Repository profiles may still exist because the runtime needs repository
identity, context, and verification commands. In the two-tier model they are
governed as part of the same consumer layer rather than as an independently
owned service extension.

For a two-tier single-repository consumer, `signal-harness init standalone`
can co-locate the policy and repository profile under `.signal-harness/` as a
starting point. The consumer should still expose its own service command or
agent entrypoint; feature developers should not depend on the raw core CLI.

Example end-user experience:

```shell
cd /path/to/acme-application
acme-sdd start audit-export
```

The exact syntax belongs to the distribution. It may expose a CLI, a Codex
skill, a Claude plugin, or all three.

### Three-tier: engine + distribution + repository

Use three tiers for a product with independently owned services or
multi-repository delivery. The distribution owns product-wide behavior; each
repository owns only the facts and rules that change with that service.

```text
1. Signal Engineering Harness                  reusable core engine
                  │ released SDD dependency
                  ▼
2. Acme Platform SDD                            product distribution
   product stages, catalog, integrations,
   compatibility tests, and `$acme-sdd`
                  │ loads selected profiles and context
                  ▼
3. account-svc / billing-svc / ...             repository layer
   .signal-harness/repo.yaml, local rules,
   specs, source, commands, and signals
                  │
                  ▼
   developer runs `$acme-sdd <request>` from a service checkout
```

For example:

```text
cd account-svc
Codex:       $acme-sdd create admin profiles CRUD
Claude Code: /acme-sdd create admin profiles CRUD
```

The entrypoint comes from the Acme distribution. Running it inside `account-svc`
selects the service profile and local context; product policy then selects related
repositories, and the core discovers current interfaces and routes work. The
service does not carry another copy of the SDD pipeline.

## What each layer owns

The table shows the three-tier ownership split. In the two-tier model, the
Distribution and Repository layer responsibilities are governed by the same
consumer owner; repository-specific commands remain in the repository profile.

| Layer | Owns | Must not own |
| --- | --- | --- |
| Core engine | Generic state machine, schemas, context locking, repository permissions, evidence validation, provider protocols, worktrees, and PR routing | Consumer repository names, organization policy, credentials, or a privileged default workflow |
| Distribution | Product/team stages and gates, repository catalog, integrations, compatibility range, agent adapters, and stable user command | Copies of the core state machine or service build rules |
| Repository layer | Repository identity, context seeds, architecture and brownfield rules, specs, build/test commands, and signal sources | A copied distribution or lifecycle implementation |

The catalog identifies participating repositories. It does not maintain an
API-by-API contract inventory. For each feature, the running workflow reads
the selected repositories' current source to find relevant APIs, events,
schemas, generated clients, configuration, callers, and compatibility impact.

## Distribution owners: onboard to the engine

The following commands are setup tools for distribution maintainers, not the
normal feature-development interface.

### 1. Create a distribution

From an installed SDD core release or a core development checkout:

```shell
signal-harness init distribution ../acme-sdd \
  --name acme --package acme_sdd
```

This creates a separately versioned Python distribution with:

- `pyproject.toml`, including a bounded core compatibility range;
- `src/acme_sdd/distribution.yaml`, the runtime contract;
- `src/acme_sdd/policy/sdd.yaml`, the consumer-owned SDD stages and gates;
- `src/acme_sdd/policy/catalog.yaml`, the participating repository catalog;
- `src/acme_sdd/cli.py`, a thin user command that delegates to the core; and
- `templates/repository/repo.yaml`, the repository-profile starting point.

The generated workflow is neutral. Replace it with the team's reviewed policy,
add thin Codex or Claude entrypoints, add consumer compatibility tests, and pin
an immutable `sdd-v*` release. Do not copy Python engine modules into the
distribution.

For a manual policy-pack starting point, copy and fill the
[extension templates](templates/README.md).

### 2. Register the distribution

Package the distribution and keep its generated
`signal_harness.distributions` entry point. At runtime the core discovers that
entry point, validates `requiresCore`, then loads the distribution manifest,
catalog, and workflow. See [component releases](docs/release.md) for versioning
and update automation.

### 3. Publish the consumer-facing entrypoint

The distribution should expose its own stable command and agent adapters, such
as `acme-sdd`, `$acme-sdd`, or `/acme-sdd`. Those adapters translate a feature
request into the compatible core API; they must not reimplement the lifecycle.

End-user installation and feature syntax belong in the distribution README.

## Repository owners: add the repository layer

Use `init repository` for the three-tier model, where repositories independently
own their context and verification rules.

The generic core scaffolder is:

```shell
signal-harness init repository ../widget-svc \
  --id widget-svc \
  --remote acme/widget-svc \
  --distribution ../acme-sdd \
  --detect-commands
```

It creates `.signal-harness/repo.yaml` in the service and adds a lightweight
entry to the distribution catalog. It reuses existing `AGENTS.md`, `README.md`,
`docs/`, and `specs/` context and conservatively detects Gradle, Maven, npm, or
Python unit-test commands. Generated values are drafts: service owners must
review their context, rules, commands, signals, and ownership.
Existing files with different content are never overwritten.

A distribution can provide a richer onboarding command that infers product
conventions. For example, it may distinguish existing and new services:

```shell
cd /path/to/acme-sdd
uv run acme-sdd onboard brownfield ../existing-svc

uv run acme-sdd onboard greenfield ../new-svc \
  --spec specs/service.md
```

That command prepares one service-repository change for local context and one
distribution change for the central catalog. The service team reviews the
first; the distribution owners review the second. After both merge, developers
use `$acme-sdd <request>` or `/acme-sdd <request>` from the service checkout.

## What happens during an SDD run

The distribution's entrypoint performs this sequence through the core:

1. Identify the invoking repository and load its profile and context.
2. Load and compatibility-check the installed distribution.
3. Resolve candidate repositories from the distribution catalog.
4. Keep all selected repositories read-only while Stage 0 audits the spec and
   discovers feature-relevant interfaces from current source.
5. Build and freeze the Stage 1 task DAG with explicit repository owners,
   evidence, dependencies, test cases, and required checks.
6. Grant write access only to routed task worktrees and enforce the
   distribution's remaining implementation, review, integration, validation,
   and learning gates.
7. Publish each change and PR to its owning repository.

Invocation location provides context; it does not silently grant write access.
The same distribution and core govern every participating repository.

## Core contributors and distribution diagnostics

Use Python 3.11 or newer:

```shell
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
```

Core and distribution maintainers can use the generic CLI to validate policy,
inspect resolution, and exercise conformance fixtures. Detailed commands and
state semantics are documented in [operations](docs/operations.md). They are
deliberately not presented as the feature-developer quick start.

The test suite creates temporary Git repositories and worktrees and covers
context exclusion, dependency ordering, read-only enforcement, state
integrity, TDD evidence, verification, publication, resume, and assurance
contracts.

GitHub and GitLab share the same SDD stages. Self-hosted repositories declare
`repo.forge` during onboarding; Git/API permissions are checked when needed,
not by cloning every catalog entry. See [code-host providers](docs/forge-providers.md)
for configuration, runtime files, and conservative GitLab review gates.

## Further reading

- [Architecture](docs/architecture.md)
- [Core/distribution/repository design](docs/core-distribution-extension-design.md)
- [Operations](docs/operations.md)
- [Security](docs/security.md)
- [Compatibility and cutover](docs/compatibility-cutover.md)
- [Component releases](docs/release.md)
- [Delivery roadmap](docs/roadmap.md)

## Project status and license

This project is pre-1.0 and under active development. The repository is
licensed under the Apache License 2.0. See [LICENSE.txt](LICENSE.txt).
