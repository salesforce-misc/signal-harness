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

import pytest

from signal_harness.bootstrap import register_catalog_repository
from signal_harness.errors import ConfigurationError


@pytest.mark.parametrize("permission", ["", "    author: false\n", "    author: true\n"])
def test_existing_registration_keeps_owner_metadata_and_permissions(tmp_path, permission):
    policy = tmp_path / "policy"
    policy.mkdir()
    catalog = policy / "catalog.yaml"
    source = (
        "repositories:\n  service-a:\n"
        "    # Owner-reviewed transport and permissions.\n"
        "    remote: git@github.com:example/service-a.git\n"
        + permission
        + "    onboardingStatus: candidate\n"
    )
    catalog.write_text(source)
    register_catalog_repository(tmp_path, "service-a", "https://github.com/example/service-a")
    assert catalog.read_text() == source


def test_registration_rejects_conflicting_identity_without_writes(tmp_path):
    policy = tmp_path / "policy"
    policy.mkdir()
    catalog = policy / "catalog.yaml"
    source = "repositories:\n  service-a:\n    remote: example/other-service\n"
    catalog.write_text(source)
    with pytest.raises(ConfigurationError, match="different"):
        register_catalog_repository(tmp_path, "service-a", "example/service-a")
    assert catalog.read_text() == source
