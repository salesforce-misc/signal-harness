# Security

## Supported versions

The project is pre-1.0. Only the latest commit on the default branch and the
latest tagged release receive security fixes.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Report it through
[Salesforce's vulnerability submission portal](https://www.sfdc.co/SubmitVuln)
as soon as it is discovered.

Include the affected version, reproduction steps, impact, and any known
mitigation. Do not include production credentials, customer data, or other
sensitive material in the report.

This project limits its runtime dependencies to reduce its security and
maintenance footprint. Consumers should still have their security stakeholders
review this project and its third-party dependencies before deployment.

## Security-sensitive areas

Changes involving command execution, repository permissions, path resolution,
credentials, provider callbacks, logging, or evidence redaction require an
explicit security review. The enterprise hardening requirements are tracked in
[docs/roadmap.md](docs/roadmap.md#milestone-6--security-and-enterprise-hardening).
