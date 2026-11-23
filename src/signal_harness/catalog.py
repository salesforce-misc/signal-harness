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

"""Compatibility import; implementation lives in signal_harness.sdd.catalog."""

import sys

from signal_harness.sdd import catalog as _implementation
from signal_harness.sdd.catalog import CatalogRepository as CatalogRepository
from signal_harness.sdd.catalog import RepositoryCatalog as RepositoryCatalog
from signal_harness.sdd.catalog import load_policy_catalog as load_policy_catalog

sys.modules[__name__] = _implementation
