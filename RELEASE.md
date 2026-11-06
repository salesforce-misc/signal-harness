# Release process

This checkout releases the SDD engine under `sdd-v*`, with provider-neutral
assurance interfaces. AIAL is planned; its engine, artifacts, and release
workflow are not included.

For SDD, update `pyproject.toml`, `src/signal_harness/__init__.py`, plugin skill
metadata, the version matrix, and `CHANGELOG.md` to the same version. Run the
full checks in `CONTRIBUTING.md`, merge the reviewed change, and create
`sdd-vMAJOR.MINOR.PATCH` from that `main` commit. `Release SDD` validates the
identity and publishes the Python wheel, source archive, and checksums.

Never move or reuse a published tag. A future AIAL implementation must introduce
its own version identity, acceptance tests, reproducible artifacts, and reviewed
release workflow before enabling `aial-v*` releases. SDD consumers should
continue to subscribe only to `sdd-v*`.
