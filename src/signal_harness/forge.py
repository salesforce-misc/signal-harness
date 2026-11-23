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

"""Compatibility import; implementation lives in signal_harness.runtime.forge."""

import sys

from signal_harness.runtime import forge as _implementation
from signal_harness.runtime.forge import CommandRunner as CommandRunner
from signal_harness.runtime.forge import RemoteLocation as RemoteLocation
from signal_harness.runtime.forge import ReviewProvider as ReviewProvider
from signal_harness.runtime.forge import provider_for as provider_for
from signal_harness.runtime.forge import remote_location as remote_location

sys.modules[__name__] = _implementation
