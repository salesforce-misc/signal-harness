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

"""Compatibility import; implementation lives in signal_harness.runtime.integrations."""

import sys

from signal_harness.runtime import integrations as _implementation
from signal_harness.runtime.integrations import SAFE_EXTERNAL_KEY as SAFE_EXTERNAL_KEY
from signal_harness.runtime.integrations import NotificationProvider as NotificationProvider
from signal_harness.runtime.integrations import _safe_key as _safe_key
from signal_harness.runtime.integrations import dispatch_outbox as dispatch_outbox
from signal_harness.runtime.integrations import (
    enqueue_integration_event as enqueue_integration_event,
)
from signal_harness.runtime.integrations import record_external_id as record_external_id
from signal_harness.runtime.integrations import request_human_input as request_human_input
from signal_harness.runtime.integrations import resolve_human_input as resolve_human_input

sys.modules[__name__] = _implementation
