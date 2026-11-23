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

"""Compatibility import; implementation lives in signal_harness.sdd.task_cards."""

import sys

from signal_harness.sdd import task_cards as _implementation
from signal_harness.sdd.task_cards import TaskCard as TaskCard
from signal_harness.sdd.task_cards import _dependency_tokens as _dependency_tokens
from signal_harness.sdd.task_cards import _description as _description
from signal_harness.sdd.task_cards import _frontmatter as _frontmatter
from signal_harness.sdd.task_cards import _normalize_status as _normalize_status
from signal_harness.sdd.task_cards import _resolve_dependencies as _resolve_dependencies
from signal_harness.sdd.task_cards import _test_cases as _test_cases
from signal_harness.sdd.task_cards import discover_task_cards as discover_task_cards

sys.modules[__name__] = _implementation
