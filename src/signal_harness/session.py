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

"""Compatibility import; implementation lives in signal_harness.sdd.session."""

import sys

from signal_harness.sdd import session as _implementation
from signal_harness.sdd.session import _active_task as _active_task
from signal_harness.sdd.session import _repository as _repository
from signal_harness.sdd.session import _stage as _stage
from signal_harness.sdd.session import acknowledge_task_context as acknowledge_task_context
from signal_harness.sdd.session import context_bundle as context_bundle
from signal_harness.sdd.session import engage_repository as engage_repository
from signal_harness.sdd.session import lock_discovery_context as lock_discovery_context
from signal_harness.sdd.session import next_action as next_action
from signal_harness.sdd.session import record_artifact as record_artifact

sys.modules[__name__] = _implementation
