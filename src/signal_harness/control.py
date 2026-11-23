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

"""Compatibility import; implementation lives in signal_harness.sdd.control."""

import sys

from signal_harness.sdd import control as _implementation
from signal_harness.sdd.control import TERMINAL_STATUSES as TERMINAL_STATUSES
from signal_harness.sdd.control import _actor as _actor
from signal_harness.sdd.control import _next_actions as _next_actions
from signal_harness.sdd.control import cancel_run as cancel_run
from signal_harness.sdd.control import pause_run as pause_run
from signal_harness.sdd.control import recover_run as recover_run
from signal_harness.sdd.control import resume_run as resume_run
from signal_harness.sdd.control import run_summary as run_summary

sys.modules[__name__] = _implementation
