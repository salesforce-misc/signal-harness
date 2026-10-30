# Component releases

Signal Engineering Harness keeps SDD and AIAL in one repository while
publishing them as independently versioned components. Consumers select an
immutable component tag rather than `main` or the repository's newest release.

| Component | Version source | Tag | Artifact |
| --- | --- | --- | --- |
| SDD | `pyproject.toml` | `sdd-vVERSION` | Python wheel, source archive, checksums |
| AIAL | `src/signal_harness/capabilities/aial/engine/manifest.json` | `aial-vVERSION` | deterministic AIAL engine archive, checksums |

The shared repository is not a shared release train. An AIAL-only change can
publish `aial-v0.2.1` without changing the SDD package version. An SDD consumer
such as `acme-sdd` discovers only `sdd-v*`, so that AIAL release is invisible to
its dependency automation.

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

## Publish AIAL

1. Update the AIAL manifest, Node package metadata, and changelog version
   through a pull request.
2. Merge only after required AIAL quality, reproducibility, and Python
   3.11–3.13 acceptance tests pass.
3. Create and push the matching tag from the merged `main` commit:

   ```shell
   git fetch origin main
   git tag -a aial-vVERSION origin/main -m "Signal Harness AIAL VERSION"
   git push origin aial-vVERSION
   ```

4. `Release AIAL` checks that the tag matches the engine manifest and belongs
   to `main`.
5. The workflow builds the service-pack-free archive twice, rejects
   non-deterministic output, and publishes the archive with checksums.

Do not move or reuse a published tag. Correct a release by publishing the next
version of that component only.

## Core dependency changes

Routine dependency-update bots are intentionally disabled in this repository.
The core is consumed through immutable component releases, so dependency changes
must be proposed deliberately, reviewed with their compatibility impact, pass the
complete required CI matrix, and ship in a new `sdd-v*` or `aial-v*` release as
appropriate. Do not add a Dependabot or general-purpose Renovate schedule that
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
