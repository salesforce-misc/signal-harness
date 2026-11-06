# Contributing to Signal Harness

Thank you for contributing. Signal Harness uses a Salesforce-sponsored
governance model: community contributions are welcome, while Salesforce
maintainers make the final decisions about accepted changes and releases.

Before starting a substantial change, open a GitHub issue to describe the
problem and proposed direction. Bug reports should include a minimal
reproduction. Feature requests should explain the use case without including
customer data, credentials, or other confidential information.

Contributions should preserve the product boundary described in
[docs/architecture.md](docs/architecture.md): the core
engine owns deterministic routing, permissions, state, evidence, and mutation
boundaries; policy packs and repository profiles supply domain behavior and
repository knowledge.

## Development setup

Python 3.11 or newer is required. With `uv`:

```shell
uv sync --extra dev
uv run signal-harness --help
```

With `pip`:

```shell
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Required checks

Run the same checks as CI before opening a pull request:

```shell
ruff format --check src tests scripts plugins/signal-harness/skills/harness-code-review/scripts
ruff check src tests scripts plugins/signal-harness/skills/harness-code-review/scripts
mypy src/signal_harness
python scripts/check_license_headers.py
python scripts/validate_assets.py
python scripts/install.py --runtime both --dry-run
python -m pytest -q
python -m build
```

Salesforce-authored source and configuration files that support comments must
carry the copyright and Apache license notice described in
[the licensing guide](how_to_license.md). Keep executable shebangs first.
`scripts/check_license_headers.py` checks the currently used Python, YAML, TOML,
shell, and named configuration formats. Extend its file selection when adding
another source language; JSON files do not support comments.

Changes to schemas, policy files, state formats, CLI behavior, or plugin
manifests must include compatibility coverage and an entry in `CHANGELOG.md`.

## Pull requests

- Keep engine behavior repository-neutral.
- Put organization- and product-specific defaults in a downstream distribution.
- Keep service-specific rules, context, contracts, and commands in the service
  repository's `.signal-harness/repo.yaml` and referenced files.
- Include tests for fail-closed behavior and unsafe inputs.
- Do not merge your own pull request without the required independent review.
- Do not commit credentials, production data, or unsanitized provider output.

Create changes in a focused branch, keep commits descriptive, reference the
corresponding GitHub issue, and submit a pull request against `main`. All
contributors must sign the
[Salesforce Contributor License Agreement](https://cla.salesforce.com/sign-cla)
before their contribution can be accepted. All participation is governed by
the [Code of Conduct](CODE_OF_CONDUCT.md).

See [docs/roadmap.md](docs/roadmap.md) for milestone ownership and cutover
criteria, and [publication readiness](docs/publication.md) for administrator
setup and the final release checks.
