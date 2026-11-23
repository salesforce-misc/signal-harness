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

"""Compatibility import; implementation lives in signal_harness.sdd.workflow."""

import sys

from signal_harness.sdd import workflow as _implementation
from signal_harness.sdd.workflow import EvidenceRequirements as EvidenceRequirements
from signal_harness.sdd.workflow import WorkflowDefinition as WorkflowDefinition
from signal_harness.sdd.workflow import WorkflowGate as WorkflowGate
from signal_harness.sdd.workflow import WorkflowStage as WorkflowStage
from signal_harness.sdd.workflow import load_workflow as load_workflow
from signal_harness.sdd.workflow import load_workflow_reference as load_workflow_reference

sys.modules[__name__] = _implementation
