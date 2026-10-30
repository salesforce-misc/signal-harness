#!/usr/bin/env python3
"""Repository convenience entrypoint for the shared ad hoc review skill."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(
            Path(__file__).resolve().parents[1] / "plugins/signal-harness/skills/"
            "harness-code-review/scripts/review.py"
        ),
        run_name="__main__",
    )
