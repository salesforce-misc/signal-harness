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

"""Compatibility import; implementation lives in signal_harness.sdd.hosting."""

import sys

from signal_harness.sdd import hosting as _implementation
from signal_harness.sdd.hosting import _save_pull_request as _save_pull_request
from signal_harness.sdd.hosting import _sync_review_findings as _sync_review_findings
from signal_harness.sdd.hosting import open_pull_request as open_pull_request
from signal_harness.sdd.hosting import publish_branch as publish_branch
from signal_harness.sdd.hosting import refresh_pull_request as refresh_pull_request
from signal_harness.sdd.hosting import resume_from_pull_request as resume_from_pull_request

sys.modules[__name__] = _implementation
