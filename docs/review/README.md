# Ad hoc AI review

Install the shared skill from this source checkout with the core development
environment already installed (`python -m pip install -e '.[dev]'`):

```shell
ln -s /path/to/core/plugins/signal-harness/skills/harness-code-review ~/.agents/skills/harness-code-review
```

Use a new skill name/path only if an installation already exists; do not
overwrite another version. The plugin also exposes the skill through its
`skills/` directory. The standalone core CLI installer currently installs only
the authoring skill, so use the explicit link above for this review setup.

From a core development checkout with `.[dev]` installed:

```shell
.venv/bin/python scripts/review.py run --repo . --base origin/main --output /tmp/core-review-1
```

The same helper reviews a distribution with its own rules:

```shell
.venv/bin/python scripts/review.py run --repo /path/to/acme-sdd --base origin/main --output /tmp/harness-review-1
```

Use a new output directory each time. The command uses the existing Codex login
and sends the prepared source bundle to that account's configured Codex service.
The automatic runner currently supports the OpenAI Codex provider. For other
agents or model providers, use the local `prepare`/`validate` flow described
below and run the analysis with your chosen agent.

The automatic command uses the existing Codex login
and its default model; it starts two ephemeral read-only sessions, one for
analysis and one for independent verification. It does not post or run tests.
For agent-directed review, invoke `$harness-code-review` from either repository
after installing the skill. Claude can use the skill's prepare/validate flow.

The runner requests the installed Codex CLI's read-only filesystem sandbox;
its exact enforcement depends on that CLI and platform. User configuration
and project rules are disabled, and the prompt forbids tools, commands, and
network access. Those prompt instructions are not an isolation boundary. The
helper adds no separate network policy or allowlist of readable local files,
so read-only does not establish hermetic isolation from unrelated readable
files or all side effects. The bundle records the CLI version and exec help
under `runtimeContract`.

`--head REF` selects a committed revision. Working-tree edits and untracked
files are excluded. `--base REF` defines the comparison branch and trusted rule
pack; the actual diff starts at its merge base with head. During initial rule
seeding only, explicitly use `--rules-ref SEED_COMMIT` after reviewing that seed.
Subsequent reviews load rules from the base branch by default.

For a deliberately partial review, repeat `--path GLOB`. The bundle and terminal
result record the selected scope and every excluded changed path; do not call
that result a full PR review. Use `--context REPO_RELATIVE_PATH` to include an
unchanged caller or dependency at the reviewed head. The profile's `context`
list supplies default source context, such as the distribution's core pin.
For a dependency API, repeat `--reference /path/to/checkout@REF:FILE`; the
helper locks it to the exact commit without cloning, importing, or running it.
For distribution review, select the actual pinned core tag rather than local
core HEAD. Missing dependency access yields INCOMPLETE.

Outputs are `bundle.json`, candidate `analysis.json`, verified `report.json`,
and model logs. The bundle records exact head, base, merge-base, rules revision,
selected checks, both file versions, and addition/deletion anchors. Read-only
`prepare` builds this context without contacting a model; `validate --bundle
FILE --analysis FILE --report FILE` checks report identity, coverage, rule IDs, anchors, and verdict.

Raw second-pass output is `verification.json`; `report.json` is written only
after validation succeeds. Model/validation failures write an INCOMPLETE
`result.json`. Artifact directories use mode 0700 and files 0600.

Exit 0 is PASS; 1 is BLOCK; 2 is INCOMPLETE. PASS means no verified blocking
finding, not passing CI. Model outage, invalid output, missing docs, binary-only
changes, and oversized context cannot become PASS. New PR policy instructions
are reviewed as data and cannot change the review's trusted policy.

This is opt-in development tooling. No scheduled workflow, required status
check, PR posting, or consumer runtime dependency is installed.

If the user explicitly asks the agent to publish a completed review, follow
the shared [GitHub review format](../../plugins/signal-harness/skills/harness-code-review/references/github-review-format.md):
severity/confidence badges inline, a linked summary table, verdict, deployment
risk, relevant test advice, and reviewer checklist. Existing posts are updated
in place. This optional agent action does not make the runner a posting bot.
