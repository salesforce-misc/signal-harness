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

"""Compatibility import; implementation lives in signal_harness.sdd.distributions."""

import sys

from signal_harness.sdd import distributions as _implementation
from signal_harness.sdd.distributions import DISTRIBUTION_API_VERSION as DISTRIBUTION_API_VERSION
from signal_harness.sdd.distributions import (
    DISTRIBUTION_ENTRY_POINT_GROUP as DISTRIBUTION_ENTRY_POINT_GROUP,
)
from signal_harness.sdd.distributions import Distribution as Distribution
from signal_harness.sdd.distributions import _check_core_compatibility as _check_core_compatibility
from signal_harness.sdd.distributions import _coerce_distribution as _coerce_distribution
from signal_harness.sdd.distributions import _resolve_package_policy as _resolve_package_policy
from signal_harness.sdd.distributions import installed_distributions as installed_distributions
from signal_harness.sdd.distributions import load_distribution as load_distribution
from signal_harness.sdd.distributions import (
    load_distribution_manifest as load_distribution_manifest,
)

sys.modules[__name__] = _implementation
