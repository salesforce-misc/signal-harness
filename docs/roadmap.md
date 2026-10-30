# Core Engine Roadmap

This roadmap covers only the organization-neutral engine. Consumer catalogs,
policies, integrations, migrations, and golden runs belong in downstream
distribution repositories.

## Completed foundation

- [x] Repository-owned context profiles and SHA locking.
- [x] External policy packs with no embedded default consumer policy.
- [x] Catalog-wide repository and runtime contract discovery with author controls.
- [x] Multi-repository planning, task DAGs, guarded worktrees, and PR routing.
- [x] Persisted lifecycle gates, evidence, TDD, findings, and replay-safe resume.
- [x] Command policy, repository permissions, retention, and abnormal recovery.
- [x] Portable plan and run compatibility snapshots.
- [x] Versioned distribution manifests and installed distribution discovery.
- [x] Neutral templates and synthetic conformance fixtures.
- [x] Policy-configured caller, precedent, delivery-surface, repository
      preflight, production-readiness, and fail-closed waiver evidence contracts.
- [x] Non-overwriting distribution, repository, and standalone bootstrap
      commands with lightweight catalog onboarding.

## Public distribution readiness

- [ ] Publish the first immutable core package and checksums.
- [ ] Select an approved open-source license.
- [ ] Add contribution, governance, security-response, and release policies.
- [ ] Add artifact inspection that rejects consumer identities and embedded
      default policies from wheels and source distributions.
- [ ] Exercise the SDK against at least one independently released distribution.

## Extension SDK

- [x] Define the distribution entry-point group and compatibility contract.
- [x] Define a product-neutral produced/consumed artifact handoff contract for
      dependency-ordered cross-repository tasks; ecosystem-specific resolvers
      and publishers remain distribution extensions.
- [ ] Stabilize signal, work-item, assurance, and lifecycle-hook entry-point
      groups with explicit capability and permission declarations.
- [ ] Publish an extension conformance suite.
- [ ] Add signed or checksummed extension provenance verification.
- [ ] Document API deprecation and compatibility windows before core 1.0.

## Operational maturity

- [ ] Add canary/stable release channels and automated downstream dependency PRs.
- [ ] Add state migration rehearsal and rollback fixtures.
- [ ] Add scale tests for large catalogs and concurrent multi-repository runs.
- [ ] Add reference provider implementations using only public SDK contracts.
