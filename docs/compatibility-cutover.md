# Distribution Compatibility and Cutover

The core, consumer distributions, public schemas, and executable extensions are
versioned independently. A distribution declares `requiresCore` and
`profileApi` in `distribution.yaml`; package metadata should declare the same
core range. The core validates both before planning or mutation.

## Adoption sequence

1. Package the consumer catalog and workflow in a downstream distribution.
2. Register it through the `signal_harness.distributions` entry-point group.
3. Keep service-owned profiles and context in participating repositories.
4. Compare portable plans from the legacy entrypoint and the distribution.
5. Run representative completed workflows and compare stages, gates, evidence,
   commands, repository routes, and PR outcomes.
6. Switch the consumer entrypoint to the distribution package.
7. Remove duplicated engine definitions only after the rollback window closes.

## Compatibility evidence

Consumer-specific golden plans, run snapshots, reports, and migration wrappers
belong to the consumer distribution. Core retains only synthetic conformance
fixtures for its public contracts.

An incompatibility fails before repository mutation. A consumer can roll back
by reinstalling its previous distribution release, which restores its tested
core dependency range and policy as one unit.
