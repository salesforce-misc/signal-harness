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

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from signal_harness.runtime.errors import ConfigurationError

from ..config import require_list, require_mapping, require_string


class SignalStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    INFO = "info"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EngineeringSignal:
    """Provider-neutral evidence that can guide authoring or assurance."""

    id: str
    kind: str
    source: str
    repository: str
    status: SignalStatus
    summary: str
    evidence: tuple[str, ...] = ()
    observed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineeringSignal:
        raw_status = require_string(data.get("status"), "signal.status")
        try:
            status = SignalStatus(raw_status)
        except ValueError as exc:
            choices = ", ".join(item.value for item in SignalStatus)
            raise ConfigurationError(f"signal.status must be one of: {choices}") from exc
        raw_evidence = require_list(data.get("evidence", []), "signal.evidence")
        observed_at = data.get("observedAt")
        if observed_at is not None:
            observed_at = require_string(observed_at, "signal.observedAt")
        return cls(
            id=require_string(data.get("id"), "signal.id"),
            kind=require_string(data.get("kind"), "signal.kind"),
            source=require_string(data.get("source"), "signal.source"),
            repository=require_string(data.get("repository"), "signal.repository"),
            status=status,
            summary=require_string(data.get("summary"), "signal.summary"),
            evidence=tuple(require_string(item, "signal.evidence[]") for item in raw_evidence),
            observed_at=observed_at,
            metadata=require_mapping(data.get("metadata", {}), "signal.metadata"),
        )

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        observed_at = value.pop("observed_at")
        if observed_at is not None:
            value["observedAt"] = observed_at
        return value
