# Core interfaces and compatibility

- CLI: `src/signal_harness/sdd/cli.py`; preserve arguments, output meaning, routing,
  exit status, and agent-facing next actions or document a versioned migration.
- Config: `schemas/`, `models.py`, `catalog.py`, `workflow.py`, and
  `distributions.py` must agree on required fields, defaults, rejected values,
  and compatible core ranges. Schema-valid must not imply unsafe permission.
- State: `state.py`, `session.py`, `control.py`, and consumers of `run.json`
  must agree on stage status, plan hashes, context, and evidence provenance.
  State version is a persistence contract, separate from package version.
- Extensions: providers are installed, allowlisted interfaces. Consumer policy
  remains external; core contains neither a live service interaction graph nor
  an API inventory. Discovery is sponsor-first and incremental in core 0.9.
- Releases: `pyproject.toml`, module version, skill metadata, and `uv.lock`
  agree. SDD uses `sdd-v*`. AIAL is planned and has no implementation or release
  workflow in this checkout; a future implementation must establish its own
  independent release contract.

For changes to rules, distinguish current contract from a proposed migration.
A PR editing this document does not authorize itself to bypass current rules.
