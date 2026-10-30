# GitHub review presentation

Use this presentation when the user explicitly requests publishing verified
review results. The analysis runner remains read-only; this document does not
authorize posting, approvals, changes requests, or merges by itself.

The presentation follows the severity badges, findings table, deployment-risk
section, test advisory, and reviewer checklist used by the reference review
distribution's PR output. Only presentation is reused: keep this harness's trusted repository
rules, confidence thresholds, executable-document review, LEFT/RIGHT anchors,
and PASS/BLOCK/INCOMPLETE semantics. Do not import another skill's scoring,
documentation-only early exits, service rules, or agent claims.

## Inline finding

```markdown
**[AI Review Pilot — {emoji} {SEVERITY} · {confidence}%]** **{title}**

{concrete failure and supporting evidence}

**Suggested fix**

{actionable correction; include a code block only when a verified fix is available}

<details>
<summary>Verification details</summary>

- Rule: `{rule}`
- Reviewed commit: `{head}`
- Verification: independently checked against supplied source.
- Test execution: {actual evidence, or "Not run in this review."}

</details>

*— Powered by `$harness-code-review` (pilot)*
{stable hidden finding marker}
```

Replace `{stable hidden finding marker}` with an HTML comment:

```html
<!-- harness-review:sha256:{finding-id} -->
```

For a new finding, `finding-id` is the lowercase SHA-256 hex digest of the
UTF-8 JSON array `[rule, file, side, line, title]`, serialized without extra
whitespace (`ensure_ascii=False`, `separators=(",", ":")`). Use the report's
exact values and repository-relative file path. For example, hash
`["GENERAL-CORRECTNESS","src/engine.py","RIGHT",2,"Guard removed"]`, which emits:

```html
<!-- harness-review:sha256:2b13694775d0bf6e7e0d62e88b9e1c30d94c3cf14ae35e77a6c761ee1d5575d1 -->
```

Do not include the commit SHA or presentation text. Preserve an existing
marker's literal value when editing/reformatting its finding, including legacy
`harness-review:` markers; do not generate a replacement ID for it.

| Stored severity | Emoji | Display label |
| --- | --- | --- |
| critical | 🔴 | CRITICAL |
| warning | 🟡 | WARNING |
| suggestion | 🔵 | SUGGESTION |

Severity describes impact; confidence describes evidence strength. A warning
with confidence 99 stays a yellow WARNING, not a red CRITICAL. Formatting must
never recompute a verdict or add/remove findings. Combine findings on the same
file/side/line into one comment, retaining a separate badge for each finding.

## Review summary

```markdown
## AI Code Review — PR #{number}

| Severity | Confidence | Finding | Location |
| --- | --- | --- | --- |
| 🔴 Critical | {confidence}% | [{title}]({inline_comment_url}) | `{file}:{line}` |

**Verdict: {✅ PASS / 🚨 BLOCK / ⚠️ INCOMPLETE}** — {severity counts}

### Deployment Risk

{one evidence-backed sentence about runtime, compatibility, workflow, or rollout risk}

### Test Advisory

Recommended checks; not executed by this review unless evidence says otherwise:

- Pre-merge: {specific regression checks tied to findings}
- Post-deploy: {relevant observable behavior; omit if inapplicable}
- Stress/chaos: {only if relevant; do not invent test results or a separate analysis}

### Reviewer Checklist

- [ ] {specific external assumption or owner decision the AI could not verify}

<details>
<summary>Review provenance and scope</summary>

Reviewed commit: `{head}`. Rules revision: `{rulesRevision}`.
{full/partial coverage and any limitations}
{actual analysis/verification method; do not invent agents, counts, or CI results}
Posted as a comment-only review; this is not a GitHub merge gate or approval.

</details>
```

Link the table to the actual inline comments. For new comments, add those links
after GitHub returns their URLs. Preserve the stored verdict: PASS can include
warnings; INCOMPLETE is not PASS. Explain any deliberately partial coverage.
Keep deployment risk specific. Omit the test advisory for documentation-only
reviews, and omit post-deploy/stress/chaos bullets and reviewer checklists when
they are inapplicable or unsupported. Never invent advisory results or claim
that the Documentation or Flag Coverage lenses ran.

## Publication safeguards

1. Validate report/bundle identity, coverage, and independent verification.
   Only finalized verified findings may be published as completed findings.
2. Check repository, PR, author identity, and reviewed head before writing.
   A changed head requires a new review; do not relabel stale results as current.
3. Fetch existing inline comments and match the stable hidden marker and
   file/side/line. Preserve legacy `harness-review:` markers when reformatting.
   Edit matching comments and the existing summary instead of duplicating them;
   never edit another reviewer's comments or replace unrelated conversation.
4. Use the report's exact RIGHT addition or LEFT deletion anchor. If GitHub
   cannot accept a mapped anchor, disclose it rather than silently claiming
   that every finding was posted.
5. New reviews use `event: COMMENT` only, never APPROVE or REQUEST_CHANGES.
   Update existing comment bodies with PATCH and review summaries with PUT.
   Keep replies, resolutions, locations, and review state unchanged.
6. Verify posted counts, links, and severity labels by reading them back. State
   which authenticated identity posted them; a skill is not a registered bot.
