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

"""Compatibility import; implementation lives in signal_harness.sdd.planner."""

import sys

from signal_harness.sdd import planner as _implementation
from signal_harness.sdd.planner import PlanningSnapshot as PlanningSnapshot
from signal_harness.sdd.planner import ResolvedRepository as ResolvedRepository
from signal_harness.sdd.planner import WorkspacePlan as WorkspacePlan
from signal_harness.sdd.planner import _inferred_default_branch as _inferred_default_branch
from signal_harness.sdd.planner import _local_ci_context as _local_ci_context
from signal_harness.sdd.planner import _resolve_root as _resolve_root
from signal_harness.sdd.planner import build_plan as build_plan
from signal_harness.sdd.planner import infer_repository_profile as infer_repository_profile
from signal_harness.sdd.planner import interaction_edges as interaction_edges
from signal_harness.sdd.planner import load_profile as load_profile
from signal_harness.sdd.planner import load_workspace as load_workspace
from signal_harness.sdd.planner import resolve_profile_path as resolve_profile_path
from signal_harness.sdd.planner import resolve_workspace as resolve_workspace
from signal_harness.sdd.planner import resolved_repository_dict as resolved_repository_dict
from signal_harness.sdd.planner import selected_repo_ids as selected_repo_ids
from signal_harness.sdd.planner import validate_references as validate_references

sys.modules[__name__] = _implementation
