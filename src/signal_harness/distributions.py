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

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from importlib.resources import files
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from . import __version__
from .config import load_yaml, require_mapping, require_string, string_list
from .errors import ConfigurationError
from .models import SUPPORTED_API_VERSIONS

DISTRIBUTION_API_VERSION = "signal-harness.io/v1alpha1"
DISTRIBUTION_ENTRY_POINT_GROUP = "signal_harness.distributions"


@dataclass(frozen=True)
class Distribution:
    """A validated, installed consumer distribution of the generic harness."""

    name: str
    version: str
    requires_core: str
    profile_api: str
    policy: Path
    providers: dict[str, str]
    policy_modes: tuple[str, ...]
    default_policy_mode: str
    source: Path

    def as_dict(self) -> dict[str, Any]:
        """Return portable distribution identity for plans and persisted runs."""
        return {
            "apiVersion": DISTRIBUTION_API_VERSION,
            "name": self.name,
            "version": self.version,
            "requiresCore": self.requires_core,
            "profileApi": self.profile_api,
            "providers": dict(sorted(self.providers.items())),
            "policyModes": {
                "allowed": list(self.policy_modes),
                "default": self.default_policy_mode,
            },
        }


def _resolve_package_policy(reference: str) -> Path:
    value = reference.removeprefix("package://")
    package, separator, resource = value.partition("/")
    if not separator or not package or not resource:
        raise ConfigurationError(
            "distribution spec.policy package URI must be package://PACKAGE/RESOURCE"
        )
    target = files(package).joinpath(resource)
    path = Path(str(target)).resolve()
    if not path.exists():
        raise ConfigurationError(f"distribution package policy does not exist: {reference}")
    return path


def _check_core_compatibility(requirement: str, source: Path) -> None:
    try:
        compatible = Version(__version__) in SpecifierSet(requirement)
    except (InvalidSpecifier, InvalidVersion) as exc:
        raise ConfigurationError(
            f"invalid core compatibility requirement {requirement!r} in {source}"
        ) from exc
    if not compatible:
        raise ConfigurationError(
            f"distribution at {source} requires signal-engineering-harness "
            f"{requirement}, installed version is {__version__}"
        )


def load_distribution_manifest(path: str | Path) -> Distribution:
    requested = Path(path).expanduser()
    source = requested / "distribution.yaml" if requested.is_dir() else requested
    if not source.is_file():
        raise ConfigurationError(f"distribution manifest does not exist: {source.resolve()}")

    data = load_yaml(source)
    if data.get("apiVersion") != DISTRIBUTION_API_VERSION:
        raise ConfigurationError(f"distribution apiVersion must be {DISTRIBUTION_API_VERSION}")
    if data.get("kind") != "Distribution":
        raise ConfigurationError("distribution kind must be Distribution")

    metadata = require_mapping(data.get("metadata"), "metadata")
    spec = require_mapping(data.get("spec"), "spec")
    name = require_string(metadata.get("name"), "metadata.name")
    version = require_string(metadata.get("version"), "metadata.version")
    requires_core = require_string(spec.get("requiresCore"), "spec.requiresCore")
    profile_api = require_string(spec.get("profileApi"), "spec.profileApi")
    if profile_api not in SUPPORTED_API_VERSIONS:
        raise ConfigurationError(
            f"distribution profileApi must be one of {sorted(SUPPORTED_API_VERSIONS)}"
        )

    raw_policy = require_string(spec.get("policy"), "spec.policy")
    if raw_policy.startswith("package://"):
        policy = _resolve_package_policy(raw_policy)
    else:
        policy_path = Path(raw_policy)
        policy = (
            policy_path.resolve()
            if policy_path.is_absolute()
            else (source.resolve().parent / policy_path).resolve()
        )
    catalog = policy / "catalog.yaml" if policy.is_dir() else policy
    if not catalog.is_file():
        raise ConfigurationError(f"distribution policy catalog does not exist: {catalog}")

    provider_data = require_mapping(spec.get("providers", {}), "spec.providers")
    providers = {
        require_string(kind, "spec.providers key"): require_string(
            provider, f"spec.providers.{kind}"
        )
        for kind, provider in provider_data.items()
    }
    policy_modes_data = require_mapping(spec.get("policyModes", {}), "spec.policyModes")
    policy_modes = string_list(
        policy_modes_data.get("allowed", ["evaluation"]), "spec.policyModes.allowed"
    )
    if not policy_modes or any(item not in {"evaluation", "production"} for item in policy_modes):
        raise ConfigurationError(
            "spec.policyModes.allowed must contain evaluation, production, or both"
        )
    default_policy_mode = require_string(
        policy_modes_data.get("default", policy_modes[0]), "spec.policyModes.default"
    )
    if default_policy_mode not in policy_modes:
        raise ConfigurationError("spec.policyModes.default must be present in allowed")
    _check_core_compatibility(requires_core, source.resolve())
    try:
        Version(version)
    except InvalidVersion as exc:
        raise ConfigurationError(
            f"distribution metadata.version must be a valid version: {version!r}"
        ) from exc

    return Distribution(
        name=name,
        version=version,
        requires_core=requires_core,
        profile_api=profile_api,
        policy=policy,
        providers=providers,
        policy_modes=policy_modes,
        default_policy_mode=default_policy_mode,
        source=source.resolve(),
    )


def installed_distributions() -> dict[str, EntryPoint]:
    result: dict[str, EntryPoint] = {}
    duplicates: set[str] = set()
    for entry_point in entry_points(group=DISTRIBUTION_ENTRY_POINT_GROUP):
        if entry_point.name in result:
            duplicates.add(entry_point.name)
        result[entry_point.name] = entry_point
    if duplicates:
        raise ConfigurationError(f"duplicate installed harness distributions: {sorted(duplicates)}")
    return result


def _coerce_distribution(value: object, entry_point: EntryPoint) -> Distribution:
    loaded = value() if callable(value) else value
    if isinstance(loaded, Distribution):
        _check_core_compatibility(loaded.requires_core, loaded.source)
        return loaded
    if isinstance(loaded, (str, Path)):
        return load_distribution_manifest(loaded)
    raise ConfigurationError(
        f"distribution entry point {entry_point.name!r} must return a Distribution or path"
    )


def load_distribution(reference: str | Path) -> Distribution:
    """Load a distribution by manifest path, directory, or installed entry-point name."""
    raw = str(reference)
    requested = Path(raw).expanduser()
    if requested.exists():
        return load_distribution_manifest(requested)
    if requested.is_absolute() or "/" in raw or raw.endswith((".yaml", ".yml")):
        raise ConfigurationError(f"distribution manifest does not exist: {requested.resolve()}")

    available = installed_distributions()
    entry_point = available.get(raw)
    if entry_point is None:
        raise ConfigurationError(
            f"unknown harness distribution {raw!r}; installed: {sorted(available)}"
        )
    return _coerce_distribution(entry_point.load(), entry_point)
