# Publication readiness

The [Salesforce OSS template](https://github.com/salesforce/oss-template) is the
source for the root governance files and licensing instructions. Preserve
`CODEOWNERS`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `LICENSE.txt`, `README.md`,
`SECURITY.md`, and `how_to_license.md` at the repository root. Keep the public
ECCN and GUS metadata lines in `CODEOWNERS`. For this repository's approval,
`#GUSINFO:` must contain the actual owning GUS Scrum Team and Product Tag,
separated by a comma; do not substitute the template's Open Source placeholders.
Keep the approval record itself in the approval system.

## Source validation

Run the [contribution checks](../CONTRIBUTING.md#required-checks) and confirm CI
passed on the exact revision proposed for publication. CI and SDD release
validation enforce source/configuration license notices, aligned release
identities, schemas, neutral templates, and the absence of an unavailable AIAL
release workflow. This release contains SDD plus assurance interfaces; AIAL is
planned.

Review the current tree and reachable Git history for credentials, internal
service code or configuration, private infrastructure references, and customer
data. Treat scanner results as candidates requiring inspection, including
synthetic credentials in negative tests. Pattern scans do not establish legal
clearance or prove that no confidential material exists. Review the
[security/data-handling guidance](security.md) as part of release preparation.

## Administrator setup

Repository protection requires administrator permissions; a maintainer can
prepare code and PRs but cannot configure it. Before accepting public
contributions, have an administrator protect `main` with the `Required` CI
status check, one independent approval, code-owner review, and resolved review
conversations. Preserve earlier approvals after pushes and allow merging
without a forced branch update when required CI passes.

An administrator can apply those settings with:

```shell
gh api --hostname github.com --method PUT \
  repos/salesforce-misc/signal-harness/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {"strict": false, "contexts": ["Required"]},
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1,
    "require_last_push_approval": false
  },
  "restrictions": null,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

Ensure `CODEOWNERS` includes an eligible independent reviewer before requiring
code-owner approval on a maintainer-authored PR. Public community contributors
do not automatically receive maintainer or code-owner permissions.

After the reviewed changes are merged, verify the final revision and settings,
obtain the required OSPO approval, and complete the publication task in its
original approval system. Changing visibility or publishing a release is a
separate action from preparing these changes.
