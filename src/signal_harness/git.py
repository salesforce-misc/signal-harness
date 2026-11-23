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

"""Compatibility import; implementation lives in signal_harness.runtime.git."""

import sys

from signal_harness.runtime import git as _implementation
from signal_harness.runtime.git import GIT_TIMEOUT_SECONDS as GIT_TIMEOUT_SECONDS
from signal_harness.runtime.git import _cache_lock as _cache_lock
from signal_harness.runtime.git import _git as _git
from signal_harness.runtime.git import _git_process as _git_process
from signal_harness.runtime.git import _GitTimeout as _GitTimeout
from signal_harness.runtime.git import _missing_snapshot_retry as _missing_snapshot_retry
from signal_harness.runtime.git import _run_git as _run_git
from signal_harness.runtime.git import changed_paths as changed_paths
from signal_harness.runtime.git import current_branch as current_branch
from signal_harness.runtime.git import default_repository_cache as default_repository_cache
from signal_harness.runtime.git import head_sha as head_sha
from signal_harness.runtime.git import is_ancestor as is_ancestor
from signal_harness.runtime.git import is_dirty as is_dirty
from signal_harness.runtime.git import managed_repository as managed_repository
from signal_harness.runtime.git import normalize_remote as normalize_remote
from signal_harness.runtime.git import origin_url as origin_url
from signal_harness.runtime.git import remote_clone_url as remote_clone_url
from signal_harness.runtime.git import repository_root as repository_root

sys.modules[__name__] = _implementation
