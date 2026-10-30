# Review severity and confidence

Impact and confidence are independent. Confidence is evidence strength, not a
way to promote style suggestions into critical defects.

- `critical`: demonstrated unauthorized mutation, lost/corrupted persisted
  evidence, unsafe execution or credential exposure, required gate bypass, or
  broken public behavior that prevents a supported workflow.
- `warning`: concrete regression with bounded impact, a supported compatibility
  failure, or a missing regression test tied to a specific plausible defect.
- `suggestion`: optional improvement; never blocks a verdict.

Only independently verified critical findings with confidence at least 85
produce `BLOCK`. Analysis candidates and final verified findings both require
confidence at least 70; each pass must omit findings below this floor.
Missing required context, unreviewed changes, failed model execution, invalid
output, or incomplete verification produce `INCOMPLETE`, never `PASS`.
`PASS` means no verified blocking finding; it does not mean tests or CI passed.

Findings cite an applicable rule, a changed line (including a deleted line),
the concrete failure, and supporting source. Drop pre-existing defects unless
this change makes them reachable or worse. Do not invent rules from personal
preferences, automatic line limits, or another product's baselines.
