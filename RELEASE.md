# Release process

SDD and AIAL share a repository but have independent release identities and
artifacts. An AIAL-only change must never increment the SDD version or publish
an `sdd-v*` tag; likewise, an SDD-only change does not publish `aial-v*`.

For SDD, update `pyproject.toml`, `src/signal_harness/__init__.py`, plugin skill
metadata, the version matrix, and `CHANGELOG.md` to the same version. Run the
full checks in `CONTRIBUTING.md`, merge the reviewed change, and create
`sdd-vMAJOR.MINOR.PATCH` from that `main` commit. `Release SDD` validates the
identity and publishes the Python wheel, source archive, and checksums.

For AIAL, update
`src/signal_harness/capabilities/aial/engine/manifest.json`, its Node package
metadata, and the AIAL changelog to the same engine version. Run the AIAL Python
and Node-backed acceptance tests, merge the reviewed change, and create
`aial-vVERSION` from that `main` commit. `Release AIAL` validates the identity
and publishes the deterministic, service-pack-free AIAL archive and checksums.

Never move or reuse either component tag. Consumers subscribe to exactly one
component prefix, so publishing one component cannot create dependency PRs for
the other.
