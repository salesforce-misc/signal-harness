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

"""Compatibility import; implementation lives in signal_harness.sdd.feature_context."""

import sys

from signal_harness.sdd import feature_context as _implementation
from signal_harness.sdd.feature_context import (
    _OPTIONAL_DIRECTORY_FIELDS as _OPTIONAL_DIRECTORY_FIELDS,
)
from signal_harness.sdd.feature_context import _OPTIONAL_LIST_FIELDS as _OPTIONAL_LIST_FIELDS
from signal_harness.sdd.feature_context import _OPTIONAL_PATH_FIELDS as _OPTIONAL_PATH_FIELDS
from signal_harness.sdd.feature_context import (
    _OPTIONAL_REQUIRED_LIST_FIELDS as _OPTIONAL_REQUIRED_LIST_FIELDS,
)
from signal_harness.sdd.feature_context import _REQUIRED_LIST_FIELDS as _REQUIRED_LIST_FIELDS
from signal_harness.sdd.feature_context import ExternalPriorFeature as ExternalPriorFeature
from signal_harness.sdd.feature_context import _local_path as _local_path
from signal_harness.sdd.feature_context import _prior_feature_path as _prior_feature_path
from signal_harness.sdd.feature_context import _values as _values
from signal_harness.sdd.feature_context import external_prior_features as external_prior_features
from signal_harness.sdd.feature_context import feature_context_patterns as feature_context_patterns

sys.modules[__name__] = _implementation
