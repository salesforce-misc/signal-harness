# Component releases

This checkout publishes the SDD engine and provider-neutral assurance interfaces.
Consumers select an immutable `sdd-v*` tag rather than `main` or the repository's
newest release. The AIAL implementation and releases are planned, not available.

| Component | Version source | Tag | Artifact |
| --- | --- | --- | --- |
| SDD | `pyproject.toml` | `sdd-vVERSION` | Python wheel, source archive, checksums |

## Publish SDD

1. Update the SDD package, module, plugin, compatibility, and changelog versions
   through a pull request.
2. Merge only after required quality and Python 3.11–3.13 tests pass.
3. Create and push the matching tag from the merged `main` commit:

   ```shell
   git fetch origin main
   git tag -a sdd-vVERSION origin/main -m "Signal Harness SDD VERSION"
   git push origin sdd-vVERSION
   ```

4. `Release SDD` checks that the tag matches `pyproject.toml`, belongs to
   `main`, and passes the complete SDD gate again.
5. The workflow publishes the checksummed Python wheel and source archive.

## Planned AIAL releases

There is no AIAL release command or workflow in this checkout. Before enabling
one, a reviewed implementation must supply its own version metadata, acceptance
tests, reproducible artifact builder, and release workflow. Reserve `aial-v*`
for that future component; do not publish placeholder tags.

The intended model keeps component releases independent. A future AIAL-only
release must not increment the SDD package version or trigger SDD consumer
updates. Assurance interfaces in the current Python package remain part of the
SDD package's compatibility contract.

Do not move or reuse a published tag. Correct a release by publishing the next
version of that component only.

## Core dependency changes

Routine dependency-update bots are intentionally disabled in this repository.
The core is consumed through immutable component releases, so dependency changes
must be proposed deliberately, reviewed with their compatibility impact, pass the
complete required CI matrix, and ship in a new `sdd-v*` release. Do not add a Dependabot or general-purpose Renovate schedule that
can create or auto-merge dependency changes independently of that release process.

## Consumer updates

Each consumer combines a compatibility range with one exact component tag.
For example, Acme SDD accepts `>=0.9,<0.10` and pins `sdd-v0.9.0`. Its Renovate
configuration extracts versions only from tags matching `^sdd-v`, updates the
runtime contract, Git source tag, and lockfile together, and ignores every
`aial-v*` release.

Compatible SDD patch updates may auto-merge only after the consumer's complete
required CI and branch protections pass. Minor or major compatibility changes
require an explicit compatibility-range update and owner review.
