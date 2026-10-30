# Core verification expectations

The authoritative automated checks are `.github/workflows/ci.yml` and
`pyproject.toml`. Existing checks include Ruff format/lint, mypy, asset validation,
installer dry run, package build, and all Python tests on 3.11–3.13.

Use `python -m pytest -q` for the suite. Tests must establish behavior, not only
assert prompt wording or mirror the implementation.

| Changed behavior | Useful regression evidence |
| --- | --- |
| Discovery or context | Sponsor-only startup with inaccessible peers; engage one peer; exact context additions; unchanged earlier snapshot after another fetch; drift rejection. |
| Permissions, commands, paths | Default deny; rejected unauthorized target; traversal/symlink escape; hostile command; audit/redaction. |
| Gates or state | Missing/invalid evidence rejected; successful path; retry/resume; rewind; production versus evaluation; corrupted journal. |
| CLI, schemas, extensions | Parser/model/schema agreement; compatible old consumer; deliberate incompatible rejection; provider failure/retry. |
| Skills, templates, packaging | References resolve in packaged form; instructions match CLI and gates; version identities agree; executable Markdown is reviewed. |

An ad hoc review is read-only. Cite existing CI evidence or explicitly state
that tests were not run; never execute code from a reviewed PR merely to verify
a finding in a credentialed review session. Required regression tests depend on
behavioral risk, not an arbitrary coverage percentage or test per method.
