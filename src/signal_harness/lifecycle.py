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

"""Compatibility import; implementation lives in signal_harness.sdd.lifecycle."""

import sys

from signal_harness.sdd import lifecycle as _implementation
from signal_harness.sdd.lifecycle import _meaningful as _meaningful
from signal_harness.sdd.lifecycle import _non_empty as _non_empty
from signal_harness.sdd.lifecycle import _non_empty_strings as _non_empty_strings
from signal_harness.sdd.lifecycle import _validate_caller_matrix as _validate_caller_matrix
from signal_harness.sdd.lifecycle import (
    _validate_caller_reconciliation as _validate_caller_reconciliation,
)
from signal_harness.sdd.lifecycle import (
    _validate_integration_evidence as _validate_integration_evidence,
)
from signal_harness.sdd.lifecycle import (
    _validate_knowledge_evidence as _validate_knowledge_evidence,
)
from signal_harness.sdd.lifecycle import _validate_literal_audit as _validate_literal_audit
from signal_harness.sdd.lifecycle import _validate_placement_audit as _validate_placement_audit
from signal_harness.sdd.lifecycle import _validate_precedent_audit as _validate_precedent_audit
from signal_harness.sdd.lifecycle import (
    _validate_repository_preflight as _validate_repository_preflight,
)
from signal_harness.sdd.lifecycle import _validate_review_evidence as _validate_review_evidence
from signal_harness.sdd.lifecycle import _validate_smoke_evidence as _validate_smoke_evidence
from signal_harness.sdd.lifecycle import _validate_source_references as _validate_source_references
from signal_harness.sdd.lifecycle import (
    _validate_specification_evidence as _validate_specification_evidence,
)
from signal_harness.sdd.lifecycle import _validate_surface_audit as _validate_surface_audit
from signal_harness.sdd.lifecycle import (
    _validate_task_plan_evidence as _validate_task_plan_evidence,
)
from signal_harness.sdd.lifecycle import _validate_test_coverage as _validate_test_coverage
from signal_harness.sdd.lifecycle import gate_evidence_outcome as gate_evidence_outcome
from signal_harness.sdd.lifecycle import initial_task_states as initial_task_states
from signal_harness.sdd.lifecycle import initial_workflow_state as initial_workflow_state
from signal_harness.sdd.lifecycle import next_stage_id as next_stage_id
from signal_harness.sdd.lifecycle import per_task_stages as per_task_stages
from signal_harness.sdd.lifecycle import stage_after_task_phase as stage_after_task_phase
from signal_harness.sdd.lifecycle import validate_check_derivation as validate_check_derivation
from signal_harness.sdd.lifecycle import validate_gate_evidence as validate_gate_evidence
from signal_harness.sdd.lifecycle import validate_tdd_evidence as validate_tdd_evidence

sys.modules[__name__] = _implementation
