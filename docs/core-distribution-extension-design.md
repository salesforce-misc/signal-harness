# Core, Distribution, and Service Extension Design

Status: proposed target architecture
Last updated: 2026-10-08

## Purpose

The Signal Engineering Harness is intended to be an organization-neutral,
open-source engineering control plane. Any company, team, or individual should
be able to adopt it without inheriting another consumer's repositories,
policies, service names, credentials, integrations, or workflow decisions.

The core coordinates software-development workflows. A downstream distribution
supplies organization or product policy, while each participating repository
retains authority over its own context and commands. Codex, Claude, or another
developer-controlled agent performs the authoring work; the harness provides
deterministic planning, routing, permissions, evidence, gates, and publication.

This document defines that boundary, the recommended consumption model, and
the extension path from an individual service through a product distribution
to the open-source core.

## Decision summary

1. The core repository contains only generic runtime code, public schemas,
   neutral templates, an extension SDK, and synthetic conformance fixtures.
2. Team and product configuration lives in a separately versioned distribution
   repository, such as `acme-engineering-harness`.
3. Repository facts, source context, build commands, and service-specific
   signals remain in each service repository.
4. Consumers install a team distribution. The distribution depends on a
   compatible released version of the core and exposes a stable team command.
5. Declarative extension is preferred. Executable extensions use installed,
   versioned, permission-declared plugins; the core never executes arbitrary
   code merely because it exists in a checked-out repository.
6. Generic capabilities are contributed upstream. Product-wide behavior stays
   in the product distribution. Single-service behavior stays with the service.
7. Repository catalogs describe the authorized participation universe and
   authoring permission, not interaction edges. Stage 0 lazily engages only
   evidence-relevant repositories and discovers contracts from current source.

## Architecture

```text
signal-engineering-harness                         open-source upstream
  runtime, schemas, SDK, security, lifecycle
                         │
                         │ released semantic-version dependency
                         ▼
acme-engineering-harness                           product distribution
  Acme catalog, SDD policy, approved providers,
  agent entrypoints, compatibility and golden tests
                         │
                         │ invoked from a participating checkout
                         ▼
service-a / service-b / web-app / ...             service repositories
  local profile, code, specs, context, commands,
  signals, and feature artifacts
```

Dependencies point inward: services consume a product distribution, and the
product distribution consumes the core. The core has no dependency on Acme or
on any other consumer.

## Ownership boundaries

### Open-source core repository

The core owns reusable mechanisms:

- lifecycle and gate state machines;
- repository discovery and context locking;
- multi-repository planning and DAG validation;
- permission, mutation, and worktree boundaries;
- command execution and engineering-signal contracts;
- evidence persistence and replay-safe resume;
- provider and extension protocols;
- explicit PR routing and provider-neutral publication contracts;
- version, schema, state, and plugin compatibility checks;
- generic CLI and agent-runtime adapters;
- neutral templates and fully synthetic conformance fixtures.

The core must not contain:

- real consumer repository names or remotes;
- organization- or product-specific workflow policy;
- product-specific service relationships;
- consumer credentials, endpoints, team names, or provider configuration;
- product-specific migration wrappers or golden-run evidence;
- a hidden default policy that makes one consumer privileged;
- copies of downstream pipeline definitions.

Neutral examples are permitted only when they use fictional identities and are
testing the public contract. An executable Acme example is consumer material,
even if the runtime does not select it by default.

### Product or team distribution repository

A distribution turns the generic engine into an opinionated product workflow.
For Acme, the recommended repository name is `acme-engineering-harness`. This is
broader and more durable than `acme-sdd` if authoring and AIAL assurance are
eventually delivered through the same product surface. The initial distribution
may expose only SDD.

The distribution owns:

- the authoritative participating-repository catalog;
- per-repository authoring permissions;
- product-wide SDD stages, gates, and defaults;
- approved provider selection and product integration adapters;
- a stable user command such as `acme-sdd`;
- Codex and Claude entrypoints that delegate to the installed core;
- product compatibility tests, golden plans, and golden completed runs;
- migration shims from legacy product workflows;
- a lockfile and supported core compatibility range;
- canary and stable rollout policy.

The distribution should not copy the core state machine or duplicate
service-owned commands and documentation.

### Participating service repositories

Each service remains authoritative for the facts that naturally change with
its code. A typical service owns:

```text
.signal-harness/repo.yaml
AGENTS.md and/or CLAUDE.md
specs/
architecture documentation
build, test, lint, and verification commands
service-specific signal declarations
feature artifacts
```

The repository profile declares identity, context seeds, commands, and signal
sources. It does not contain another copy of the SDD pipeline.

The service profile also does not need an exhaustive inventory of API calls.
The distribution catalog identifies repositories that may interact. During a
specific feature run, the agent and engine inspect the selected repositories'
current source to discover the relevant HTTP APIs, events, schemas, generated
clients, configuration surfaces, and compatibility implications.

### Operator and environment configuration

Credentials, personal preferences, local checkout paths, and transient state
belong outside versioned policy:

- environment variables or an approved secret store for credentials;
- user configuration for personal preferences;
- command-line overrides for local checkout paths;
- `$XDG_STATE_HOME/signal-harness`, or its documented fallback, for run state.

Portable manifests use repository identities rather than developer-specific
absolute paths.

## Consumption model

### Installation

The core is released as a normal versioned package. A product distribution is
also released as a package and declares a bounded dependency on the core:

```toml
[project]
name = "acme-engineering-harness"
version = "2.1.0"
dependencies = [
  "signal-engineering-harness>=0.9,<0.10",
]

[project.scripts]
acme-sdd = "acme_harness.cli:main"
```

A consumer installs the distribution, not independent copies of both layers:

```shell
uv tool install acme-engineering-harness==2.1.0
```

The product package resolver installs a compatible core. Git submodules,
copied workflow files, mutable `main` downloads, and independent core pins in
every service are intentionally avoided.

### Invocation

From a participating service checkout:

```shell
cd account-svc
acme-sdd implement lifecycle-events
```

The thin product entrypoint:

1. identifies the invocation repository;
2. resolves its repository-owned profile;
3. loads the installed product distribution manifest, catalog, and SDD policy;
4. invokes the compatible core runtime;
5. lets the core select related repositories and lock their context;
6. discovers feature-relevant contracts from current source;
7. routes tasks, verification, commits, and PRs to explicit repositories.

The equivalent generic interface may remain available:

```shell
signal-harness sdd lifecycle-events --distribution acme
```

The product command is the stable daily interface; the generic command is
useful for debugging and other distributions.

### Bootstrap and onboarding

The core exposes neutral scaffolding commands; generated values remain owned
by the consumer:

```shell
signal-harness init distribution ../product-harness \
  --name product --package product_harness
signal-harness init repository ../service-a \
  --id service-a --remote example/service-a \
  --distribution ../product-harness --detect-commands
```

`init repository` seeds only repository facts, context, commands, and signals.
It does not copy the lifecycle into the service. `init standalone` is the
explicit exception for independent repositories: it co-locates a local
distribution and policy with the service while preserving the same runtime
contracts.

### Agent entrypoints

Codex and Claude skills should be thin interaction adapters. They translate the
agent's request into the stable distribution CLI and render the next governed
activity. They must not embed a copy of the lifecycle or product policy.

This permits an agent session started in any participating repository to retain
that repository's context while using the same installed engine and product
policy as every other service.

## Distribution contract

The core should define and validate a generic distribution manifest. For
example:

```yaml
apiVersion: signal-harness.io/v1alpha1
kind: Distribution

metadata:
  name: acme
  version: 2.1.0

spec:
  requiresCore: ">=0.9,<0.10"
  profileApi: signal-harness/v1alpha1
  policy: package://acme_harness/policy

  providers:
    workItems: work_items
    assurance: aial
```

Provider names resolve through installed package entry points. The manifest
does not name arbitrary import paths and cannot expand permissions by itself.

Before planning or mutation, the core validates:

- distribution and core version compatibility;
- supported policy, profile, workspace, and state schema versions;
- provider availability and declared capabilities;
- requested permissions against core-enforced limits;
- catalog and profile consistency.

An incompatible combination fails closed with an actionable error.

## Extension model

### Declarative extensions

Declarative configuration is the default extension mechanism:

- distributions define catalogs, workflow policies, provider selection, and
  product governance;
- services define context, commands, and signal sources;
- run inputs define the requested feature and explicit overrides.

Declarative inputs are schema-validated and cannot replace core enforcement.

### Executable extensions

When configuration is insufficient, the core exposes a small, versioned SDK
and package entry-point groups. Candidate groups include:

```text
signal_harness.distributions
signal_harness.signal_providers
signal_harness.work_item_providers
signal_harness.assurance_providers
signal_harness.lifecycle_hooks
```

Every executable extension must:

- be installed as a versioned package;
- declare its required core SDK range;
- declare capabilities and requested permissions;
- be selected and allowlisted by the product distribution;
- pass the core extension conformance suite;
- preserve explicit repository and mutation boundaries.

The engine does not automatically import code from `.signal-harness/` or from
another path in an untrusted checkout. A service may develop an extension in
its repository, but the extension is packaged and approved through the product
distribution before execution.

### Extension placement decision

Use the narrowest correct ownership scope:

| Question | Destination |
| --- | --- |
| Is this a repository fact, command, context seed, or signal for one service? | Service repository |
| Is this policy or an integration shared across the product? | Product distribution |
| Is this executable behavior used by one service? | Versioned service plugin, pinned and allowlisted by the product distribution |
| Would the capability make sense unchanged for unrelated companies? | Core proposal and upstream contribution |
| Is it a credential, local path, or personal preference? | Secret store or user/environment configuration |

An implementation should not move upstream merely because it is code. A core
contribution must be consumer-neutral, preserve backward compatibility, and
avoid product identifiers. Conversely, a missing generic extension point
should be added to core rather than worked around through a long-lived fork.

### Fork policy

Long-lived product forks of the core are discouraged because they recreate the
original repeated-update problem. A temporary fork is acceptable for incubating
a generic upstream change, but the intended outcomes are:

1. contribute the generic abstraction and tests upstream;
2. release the new core version;
3. update the product distribution dependency;
4. delete the product patch.

Product-specific implementation remains downstream as a plugin.

## Proposed Acme distribution layout

```text
acme-engineering-harness/
├── pyproject.toml
├── uv.lock
├── src/acme_harness/
│   ├── cli.py
│   ├── distribution.yaml
│   ├── policy/
│   │   ├── catalog.yaml
│   │   └── sdd.yaml
│   ├── providers/
│   │   ├── work_items.py
│   │   └── aial.py
│   ├── hooks/
│   └── migrations/
├── skills/
│   └── automated-sdd/
├── compatibility/
│   ├── golden-plans/
│   └── golden-runs/
└── tests/
```

Generic GitHub publication can remain in core if it is provider-neutral and
useful to unrelated consumers. work-item integration, Acme conventions, Salesforce-specific
identity, and AIAL bindings belong downstream.

Although the distribution is named for the broader engineering harness, its
first release can contain only SDD policy and authoring integration. AIAL can
be added later without renaming the repository or weakening the core boundary.

## Repository and contract discovery model

The product catalog is intentionally lightweight:

```yaml
repositories:
  user-svc:
    remote: example-org/user-svc
    author: true
  billing-svc:
    remote: example-org/billing-svc
    author: false
```

It answers which repositories the workflow is allowed to consider. It does not
attempt to mirror every endpoint such as `GET /api/v1/users/{id}`.

For each feature, the SDD run:

1. locks only the sponsor at its initial revision and persists catalog entries as
   authorized candidates without acquiring them; selected peers are resolved and
   SHA-locked only upon evidence-backed Stage 0 engagement;
2. reads only allowed repository context and current source;
3. identifies feature-relevant APIs, events, schemas, clients, configuration,
   and compatibility constraints;
4. records discovered impact in the feature artifacts and task graph;
5. routes required changes and PRs to their owning repositories.

Explicit governed contract metadata is reserved for contracts whose version,
generation, or compatibility policy is itself an organizational control. It is
not a prerequisite inventory of every service-to-service call.

## Versioning and updates

Core, distributions, schemas, and executable plugins are versioned separately.

- The core uses semantic versioning and publishes immutable artifacts.
- A distribution declares a bounded compatible core range and commits a
  lockfile for reproducible testing.
- Plugins declare an SDK compatibility range.
- Manifests carry explicit `apiVersion` values.
- State migrations are owned by core; product migration shims are owned by the
  distribution.

The recommended core update flow is:

1. publish a signed or checksummed core release;
2. open an automated dependency PR in each distribution;
3. run core conformance tests plus distribution-specific golden trials;
4. test the candidate in a canary channel;
5. promote the distribution release to stable;
6. let service users receive the tested core through the distribution.

Services should not each negotiate a different core version. The product
distribution is the tested upgrade and rollback unit.

## Security and trust model

The core retains final authority for:

- repository inclusion and exclusion;
- read-only versus mutable repository scope;
- command and provider capability limits;
- worktree ownership and path validation;
- explicit target repository and base branch;
- secrets redaction and evidence retention;
- state integrity and replay behavior.

Downstream policy may narrow these limits but cannot silently broaden them.
Executable plugins are supply-chain inputs and therefore require pinned
versions, approval, provenance, and conformance testing. A checkout is context,
not an implicit executable plugin source.

## Required cleanup of the current core repository

The runtime is already substantially organization-neutral, but the repository
does not yet meet the strict public-core boundary. Before calling it a clean
open-source core:

1. move `examples/acme/`, Acme mappings, Acme parity reports, and Acme golden-run
   evidence into the Acme distribution;
2. move product acceptance fixtures to the distribution, replacing any core
   fixtures with fictional repositories and data;
3. stop including consumer examples in core source distributions;
4. retain only generic templates and synthetic schema/conformance fixtures;
5. neutralize product-specific README links, badges, and commands;
6. implement the distribution manifest and package entry-point loader;
7. publish an extension SDK and compatibility/conformance suite;
8. add CI that inspects built wheel and source artifacts for unexpected
   consumer configuration or identifiers;
9. choose an approved open-source license and add contribution, security, and
   release documentation.

## Boundary tests

Core CI should make architectural drift visible:

- build and inspect wheels and source distributions;
- assert that no default consumer catalog or workflow is installed;
- deny known product identifiers in shipped code and artifacts;
- validate all public schemas and neutral templates;
- run a synthetic distribution against two or more fictional services;
- run plugin compatibility and permission-escalation tests;
- verify invocation from different fictional services produces equivalent
  portable plans while preserving the invocation repository's context.

Product distribution CI separately owns real repository parity, migration, and
golden-run evidence.

## Acceptance criteria

The target architecture is satisfied when:

- a non-Acme adopter can install and use the core without receiving Acme data;
- the core package has no privileged consumer or embedded default policy;
- Acme can upgrade the core once in its distribution and validate the change
  before services consume it;
- a service can add context, commands, and signals without copying the SDD
  pipeline;
- a service can add executable behavior through a reviewed plugin without
  forking the core;
- generic improvements can be contributed upstream without importing Acme
  policy;
- SDD runs dynamically discover feature-relevant cross-service contracts and
  route work to the correct repositories;
- incompatible core, distribution, schema, or plugin versions fail before
  mutation.

## Consequences

This model introduces a separately released product distribution and a formal
plugin API, but it removes repeated pipeline maintenance from every service.
It also gives consumers a clear choice: configure their service, extend their
product distribution, or improve the generic engine. That separation is the
mechanism that allows the core to evolve as open source while Acme remains free
to apply its own repositories, governance, integrations, and release cadence.
