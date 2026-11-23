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

"""Compatibility import; implementation lives in signal_harness.sdd.models."""

import sys

from signal_harness.sdd import models as _implementation
from signal_harness.sdd.models import API_VERSION as API_VERSION
from signal_harness.sdd.models import SUPPORTED_API_VERSIONS as SUPPORTED_API_VERSIONS
from signal_harness.sdd.models import ContextConfig as ContextConfig
from signal_harness.sdd.models import RepositoryProfile as RepositoryProfile
from signal_harness.sdd.models import SignalSource as SignalSource
from signal_harness.sdd.models import Workspace as Workspace
from signal_harness.sdd.models import WorkspaceRepository as WorkspaceRepository
from signal_harness.sdd.models import WorkspaceTask as WorkspaceTask

sys.modules[__name__] = _implementation
