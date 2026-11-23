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

"""Compatibility import; implementation lives in signal_harness.runtime.github."""

import sys

from signal_harness.runtime import github as _implementation
from signal_harness.runtime.github import _FAILING_CHECK_CONCLUSIONS as _FAILING_CHECK_CONCLUSIONS
from signal_harness.runtime.github import _PASSING_CHECK_CONCLUSIONS as _PASSING_CHECK_CONCLUSIONS
from signal_harness.runtime.github import CommandRunner as CommandRunner
from signal_harness.runtime.github import GitHubProvider as GitHubProvider
from signal_harness.runtime.github import _checks as _checks
from signal_harness.runtime.github import _github_repo as _github_repo
from signal_harness.runtime.github import _json_output as _json_output
from signal_harness.runtime.github import _open as _open
from signal_harness.runtime.github import _refresh as _refresh
from signal_harness.runtime.github import _review_entries as _review_entries
from signal_harness.runtime.github import _run_gh as _run_gh
from signal_harness.sdd.hosting import open_pull_request as open_pull_request
from signal_harness.sdd.hosting import publish_branch as publish_branch
from signal_harness.sdd.hosting import refresh_pull_request as refresh_pull_request
from signal_harness.sdd.hosting import resume_from_pull_request as resume_from_pull_request

for _name, _value in {
    "open_pull_request": open_pull_request,
    "publish_branch": publish_branch,
    "refresh_pull_request": refresh_pull_request,
    "resume_from_pull_request": resume_from_pull_request,
}.items():
    setattr(_implementation, _name, _value)

sys.modules[__name__] = _implementation
