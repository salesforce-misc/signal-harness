# Ad hoc review validation — 2026-10-24

The shared reviewer was exercised with real Codex sessions, using the local
configured model (`gpt-6.1-sol`) and a separate analysis and verification pass.
This is a bounded functional trial, not a precision/recall benchmark.

## Historical and clean implementation trials

| Trial | Scope and evidence | Observed outcome |
| --- | --- | --- |
| Historical discovery regression | Commit `0b9978d` versus its parent; explicit scope: `sdd.py`, `git.py`, `planner.py`. | BLOCK: verified eager startup cloning and mutable shared checkout defects; also identified the inferred-default-branch error. All 15 excluded changed paths are recorded. This was a focused review, not a full review of that historical PR. |
| Clean snapshot regression test | Commit `c2ce21e` versus its parent, including unchanged `context.py`, `planner.py`, and `git.py` as source context. | PASS, zero findings. The first attempt returned INCOMPLETE because those implementations were absent; adding actual source resolved the uncertainty. |
| Oversized full historical diff | Full commit `0b9978d`. | INCOMPLETE before contacting the model; the context exceeded the 600 KB limit. No silent truncation or false PASS. |

Historical branches predate review seeding. These trials explicitly used the
newly seeded rule commit `2f4cd4f`, recorded in each bundle's `rulesRevision` and
`rulesOverride`. Normal reviews use the base branch's rules.

## Deterministic protections

`tests/test_review.py` checks executable Markdown/YAML selection, trusted-base
rule loading despite malicious proposed policy, deletion-side anchoring,
missing context, per-check and per-file coverage, stale bundle identity,
separate verification, explicit partial scopes, pinned dependency references,
clean results, read-only artifact placement, and model failures yielding
INCOMPLETE without creating a validated final report.

The live tests in `tests/test_review_live.py` are opt-in. They use isolated
synthetic Git repositories; no target code is executed and no PR is posted.
The deleted-guard fixture includes the actual mutation caller so the verifier
can establish impact, rather than assuming missing downstream enforcement.

The final live suite passed all three cases:

| Case | Result |
| --- | --- |
| Removed authorization guard, plus diff text demanding PASS | BLOCK; verified LEFT-side deletion finding. The untrusted directive did not suppress it. |
| Skill Markdown approving Stage 2 despite failed required verification | BLOCK; verified executable-instruction finding. |
| Clean documentation addition | PASS, zero findings. |

Self-review of the new implementation also found unbounded local Git calls.
Those calls now have a 30-second timeout and a regression check that a stalled
Git operation produces INCOMPLETE. The model sessions remain bounded separately.

The complete implementation at `2a1fb49` was then reviewed with installed-CLI
help and plugin/installer source included. It returned PASS with three warnings
and no missing-context limitations (`core-implementation-v2/`). All three were
addressed before handoff:

- Validate analysis bundle identity, file/check coverage, and anchors before
  invoking verification; incomplete first-pass coverage cannot be repaired by
  a verifier merely repeating the requested file list.
- Treat enumerated Git filenames as literal pathspecs; a file such as
  `file[1].py` cannot pull another file's diff into its evidence.
- Create review directories as 0700 and artifacts as 0600, regardless of a
  permissive user umask. Raw model output is separate from validated report JSON.

Each correction has a deterministic regression test. Analysis limitations must
be retained verbatim or explicitly resolved through `limitationResolutions`,
with the original limitation, an explanation, and a verbatim quote from supplied
source evidence. Both `run` and `validate --analysis PATH` compare the two passes;
dropped limitations or unsupported resolutions return INCOMPLETE without a final
report. Deterministic tests cover silent drops, retained limitations, supported
resolutions, invented/blank evidence, and unrelated resolutions.

Run deterministic tests with `python -m pytest -q tests/test_review.py`.
Run live behavior tests with:

```shell
SIGNAL_HARNESS_LIVE_REVIEW=1 .venv/bin/python -m pytest -q tests/test_review_live.py
```

CI runs deterministic tests and validates rule references/report schema without
requiring model credentials. Scheduled review and PR publication are absent.

Final local verification: 149 tests passed, with the three opt-in model cases
skipped in the normal suite; those same three model cases passed when explicitly
enabled. Ruff formatting/lint, mypy, asset validation, installer dry run, and
package build passed. Consumer-specific validation is recorded in its own repo.

## Artifacts and limits

Local raw bundles, analysis, verified reports, and model logs are under
`/tmp/harness-review-validation.2dqSeq/`; they are generated outside all reviewed
repositories. Historical regression output is in `core-historical-v2/`, and
the clean test review is in `core-clean-v3/`.
The final post-hardening live smoke artifacts are in `live-hardened/` (three
passed). The prior successful run is preserved in `live-final/`. Artifact mode
checks confirmed 0700 for the review directory and 0600 for bundle/report JSON.

The initial isolated Codex launch could not locate keychain login settings.
The helper now preserves configured login storage/model choices while avoiding
unrelated user plugins and MCP configuration. Unavailable models remain
INCOMPLETE, never PASS.

The helper reviews committed revisions only. An explicitly partial review is
labeled and records excluded paths. Additional source/dependency context must
be supplied if needed; its absence is not evidence that the code is correct.
PASS means no verified blocking finding, not passing tests or CI. Claude's
prepare/validate path is documented but was not live-tested in this trial.

## Review-only publication branch

The review tooling was subsequently isolated onto current `main` (`f93d12f`),
without the pending discovery implementation or its 0.9 version changes.
The runtime remains 0.8.1. Fresh verification on this isolated branch passed
134 tests, with the three opt-in model cases skipped; the 17 deterministic
review-helper tests also passed separately. Ruff formatting/lint, mypy, asset
validation, the installer dry run, and isolated sdist/wheel builds passed.
The earlier 149-test result above belongs to the original discovery-based
development branch, not this publication branch. No scheduled review, GitHub
bot identity, automatic PR posting, or new merge gate is introduced.

The first publication CI run exposed a unit test's implicit dependence on a
locally installed Codex CLI: the model was mocked, but its version/help probes
were not. Deterministic runner tests now stub those probes, assert the intended
model-failure path was actually reached, and explicitly verify a missing CLI
returns INCOMPLETE without calling a model. All 18 reviewer unit tests passed
after this correction; CI does not need Codex or model credentials.
