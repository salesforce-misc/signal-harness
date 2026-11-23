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

"""Compatibility import; implementation lives in signal_harness.sdd.bootstrap."""

import sys

from signal_harness.sdd import bootstrap as _implementation
from signal_harness.sdd.bootstrap import CORE_RANGE as CORE_RANGE
from signal_harness.sdd.bootstrap import SAFE_NAME as SAFE_NAME
from signal_harness.sdd.bootstrap import SAFE_PACKAGE as SAFE_PACKAGE
from signal_harness.sdd.bootstrap import STANDARD_WORKFLOW as STANDARD_WORKFLOW
from signal_harness.sdd.bootstrap import _add_catalog_repository as _add_catalog_repository
from signal_harness.sdd.bootstrap import _catalog_entry as _catalog_entry
from signal_harness.sdd.bootstrap import _catalog_path as _catalog_path
from signal_harness.sdd.bootstrap import _detected_commands as _detected_commands
from signal_harness.sdd.bootstrap import _repository_profile as _repository_profile
from signal_harness.sdd.bootstrap import _safe_name as _safe_name
from signal_harness.sdd.bootstrap import _safe_package as _safe_package
from signal_harness.sdd.bootstrap import _write_new_or_same as _write_new_or_same
from signal_harness.sdd.bootstrap import _yaml as _yaml
from signal_harness.sdd.bootstrap import register_catalog_repository as register_catalog_repository
from signal_harness.sdd.bootstrap import resolve_catalog_path as resolve_catalog_path
from signal_harness.sdd.bootstrap import scaffold_distribution as scaffold_distribution
from signal_harness.sdd.bootstrap import scaffold_repository as scaffold_repository
from signal_harness.sdd.bootstrap import scaffold_standalone as scaffold_standalone

sys.modules[__name__] = _implementation
