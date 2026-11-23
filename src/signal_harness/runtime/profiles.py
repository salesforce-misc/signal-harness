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

from dataclasses import dataclass, field
from typing import Any

from .config import require_list, require_mapping, require_string, string_list
from .errors import ConfigurationError

API_VERSION = "signal-harness/v1alpha1"


SUPPORTED_API_VERSIONS = {API_VERSION}


@dataclass(frozen=True)
class ContextConfig:
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextConfig:
        return cls(
            required=string_list(data.get("required"), "context.required"),
            optional=string_list(data.get("optional"), "context.optional"),
            exclude=string_list(data.get("exclude"), "context.exclude"),
        )


@dataclass(frozen=True)
class SignalSource:
    id: str
    kind: str
    command_ref: str | None = None
    provider: str | None = None
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], field_name: str) -> SignalSource:
        command_ref = data.get("commandRef")
        provider = data.get("provider")
        if command_ref is not None:
            command_ref = require_string(command_ref, f"{field_name}.commandRef")
        if provider is not None:
            provider = require_string(provider, f"{field_name}.provider")
        if command_ref is None and provider is None:
            raise ConfigurationError(f"{field_name} must declare commandRef, provider, or both")
        return cls(
            id=require_string(data.get("id"), f"{field_name}.id"),
            kind=require_string(data.get("kind"), f"{field_name}.kind"),
            command_ref=command_ref,
            provider=provider,
            config=require_mapping(data.get("config", {}), f"{field_name}.config"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "commandRef": self.command_ref,
            "provider": self.provider,
            "config": self.config,
        }


@dataclass(frozen=True)
class RepositoryProfile:
    id: str
    remote: str
    default_branch: str
    context: ContextConfig
    commands: dict[str, str] = field(default_factory=dict)
    signal_sources: tuple[SignalSource, ...] = ()
    forge: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryProfile:
        if data.get("apiVersion") not in SUPPORTED_API_VERSIONS:
            raise ConfigurationError(
                f"repository profile apiVersion must be one of {sorted(SUPPORTED_API_VERSIONS)}"
            )
        if data.get("kind") != "RepositoryProfile":
            raise ConfigurationError("repository profile kind must be RepositoryProfile")
        if "contracts" in data:
            raise ConfigurationError(
                "repository profiles must not declare contracts; declare only repository "
                "interactions in the external catalog and discover interfaces at runtime"
            )

        repo = require_mapping(data.get("repo"), "repo")
        forge = repo.get("forge")
        if forge is not None and (not isinstance(forge, str) or forge not in {"github", "gitlab"}):
            raise ConfigurationError("repo.forge must be github or gitlab")
        context = ContextConfig.from_dict(require_mapping(data.get("context", {}), "context"))
        commands_data = require_mapping(data.get("commands", {}), "commands")
        commands = {
            require_string(name, "commands key"): require_string(command, f"commands.{name}")
            for name, command in commands_data.items()
        }

        signals = require_mapping(data.get("signals", {}), "signals")
        sources_data = require_list(signals.get("sources", []), "signals.sources")
        signal_sources = tuple(
            SignalSource.from_dict(require_mapping(item, "signals.sources[]"), "signals.sources[]")
            for item in sources_data
        )
        source_ids = [source.id for source in signal_sources]
        if len(source_ids) != len(set(source_ids)):
            raise ConfigurationError("signals.sources ids must be unique")
        for source in signal_sources:
            if source.command_ref and source.command_ref not in commands:
                raise ConfigurationError(
                    f"signal source {source.id} references unknown command {source.command_ref}"
                )

        return cls(
            id=require_string(repo.get("id"), "repo.id"),
            remote=require_string(repo.get("remote"), "repo.remote"),
            default_branch=require_string(repo.get("defaultBranch", "main"), "repo.defaultBranch"),
            context=context,
            commands=commands,
            signal_sources=signal_sources,
            forge=forge,
        )
