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

"""Compatibility import; implementation lives in signal_harness.runtime.security."""

import sys

from signal_harness.runtime import security as _implementation
from signal_harness.runtime.security import _SECRET_PATTERNS as _SECRET_PATTERNS
from signal_harness.runtime.security import _SHELL_WRAPPERS as _SHELL_WRAPPERS
from signal_harness.runtime.security import DEFAULT_SECURITY as DEFAULT_SECURITY
from signal_harness.runtime.security import _repository as _repository
from signal_harness.runtime.security import _trusted_signal_commands as _trusted_signal_commands
from signal_harness.runtime.security import _utc_now as _utc_now
from signal_harness.runtime.security import approve_command as approve_command
from signal_harness.runtime.security import authorize_command as authorize_command
from signal_harness.runtime.security import initialize_security_state as initialize_security_state
from signal_harness.runtime.security import record_command_run as record_command_run
from signal_harness.runtime.security import record_cost as record_cost
from signal_harness.runtime.security import redact_text as redact_text
from signal_harness.runtime.security import redact_value as redact_value

sys.modules[__name__] = _implementation
