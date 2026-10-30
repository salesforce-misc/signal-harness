# Repository guidance

Signal Engineering Harness is an organization-neutral engine. Consumer policy,
catalogs, integrations, and service context belong in external distributions.

For ad hoc AI review, use `$harness-code-review` with this repository as target.
The shared skill is in `plugins/signal-harness/skills/harness-code-review/`.
Its helper is also available as `python scripts/review.py`.

Review context is explicitly indexed by [docs/review/profile.yaml](docs/review/profile.yaml).
Read its selected documents, especially [rules](docs/review/rules.md),
[contracts](docs/review/contracts.md), [testing](docs/review/testing.md), and
[severity](docs/review/severity-map.md). Use the base branch's rule pack when
reviewing a PR; changes to review policy are themselves reviewable content.

Do not run `/innerloop` unless the user explicitly requests it.
