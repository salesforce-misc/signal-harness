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

"""Compatibility import; implementation lives in signal_harness.sdd.tdd."""

import sys

from signal_harness.sdd import tdd as _implementation
from signal_harness.sdd.tdd import INVALID_EVIDENCE_VALUES as INVALID_EVIDENCE_VALUES
from signal_harness.sdd.tdd import PHASES as PHASES
from signal_harness.sdd.tdd import _meaningful as _meaningful
from signal_harness.sdd.tdd import _task_tdd as _task_tdd
from signal_harness.sdd.tdd import assert_recorded_tdd_evidence as assert_recorded_tdd_evidence
from signal_harness.sdd.tdd import initialize_task_tdd as initialize_task_tdd
from signal_harness.sdd.tdd import record_tdd_phase as record_tdd_phase
from signal_harness.sdd.tdd import recorded_tdd_evidence as recorded_tdd_evidence
from signal_harness.sdd.tdd import waive_task_tdd as waive_task_tdd

sys.modules[__name__] = _implementation
