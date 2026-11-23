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

"""Compatibility import; implementation lives in signal_harness.sdd.artifact_handoff."""

import sys

from signal_harness.sdd import artifact_handoff as _implementation
from signal_harness.sdd.artifact_handoff import _DIGEST as _DIGEST
from signal_harness.sdd.artifact_handoff import (
    ARTIFACT_PROVIDER_ENTRY_POINT_GROUP as ARTIFACT_PROVIDER_ENTRY_POINT_GROUP,
)
from signal_harness.sdd.artifact_handoff import ArtifactProvider as ArtifactProvider
from signal_harness.sdd.artifact_handoff import ArtifactRecord as ArtifactRecord
from signal_harness.sdd.artifact_handoff import (
    installed_artifact_providers as installed_artifact_providers,
)
from signal_harness.sdd.artifact_handoff import load_artifact_provider as load_artifact_provider
from signal_harness.sdd.artifact_handoff import record_artifact_handoff as record_artifact_handoff
from signal_harness.sdd.artifact_handoff import resolved_task_artifacts as resolved_task_artifacts

sys.modules[__name__] = _implementation
