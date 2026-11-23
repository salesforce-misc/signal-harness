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

"""Compatibility import; implementation lives in signal_harness.sdd.worktrees."""

import sys

from signal_harness.sdd import worktrees as _implementation
from signal_harness.sdd.worktrees import _base_revision as _base_revision
from signal_harness.sdd.worktrees import _branch_exists as _branch_exists
from signal_harness.sdd.worktrees import _cleanup_task as _cleanup_task
from signal_harness.sdd.worktrees import _compensate_preparation as _compensate_preparation
from signal_harness.sdd.worktrees import _git_user as _git_user
from signal_harness.sdd.worktrees import _repo_record as _repo_record
from signal_harness.sdd.worktrees import _run_git as _run_git
from signal_harness.sdd.worktrees import _task_record as _task_record
from signal_harness.sdd.worktrees import _verify_context as _verify_context
from signal_harness.sdd.worktrees import cleanup_task as cleanup_task
from signal_harness.sdd.worktrees import freeze_task_head as freeze_task_head
from signal_harness.sdd.worktrees import prepare_task as prepare_task
from signal_harness.sdd.worktrees import verify_prepared_task as verify_prepared_task
from signal_harness.sdd.worktrees import verify_run_context as verify_run_context

sys.modules[__name__] = _implementation
