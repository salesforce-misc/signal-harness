# Copyright (c) 2026 Salesforce, Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Compatibility import; implementation lives in signal_harness.sdd.verification."""

import sys

from signal_harness.sdd import verification as _implementation
from signal_harness.sdd.verification import CommandRunner as CommandRunner
from signal_harness.sdd.verification import _bounded as _bounded
from signal_harness.sdd.verification import _finding_value as _finding_value
from signal_harness.sdd.verification import _git_worktree as _git_worktree
from signal_harness.sdd.verification import _output_text as _output_text
from signal_harness.sdd.verification import _pmd_findings as _pmd_findings
from signal_harness.sdd.verification import (
    _run_baseline_delta_verification as _run_baseline_delta_verification,
)
from signal_harness.sdd.verification import _safe_report_path as _safe_report_path
from signal_harness.sdd.verification import run_verification as run_verification

sys.modules[__name__] = _implementation
