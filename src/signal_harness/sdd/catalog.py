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
from pathlib import Path
from typing import Any

from signal_harness.runtime.config import load_yaml, require_mapping, require_string
from signal_harness.runtime.errors import ConfigurationError
from signal_harness.sdd.models import SUPPORTED_API_VERSIONS


@dataclass(frozen=True)
class CatalogRepository:
    id: str
    remote: str
    author: bool = False

    @classmethod
    def from_dict(cls, repo_id: str, data: dict[str, Any]) -> CatalogRepository:
        if "contracts" in data:
            raise ConfigurationError(
                f"repositories.{repo_id} must not inventory contracts; "
                "discover interfaces during Stage 0"
            )
        if "interactsWith" in data:
            raise ConfigurationError(
                f"repositories.{repo_id}.interactsWith was removed; discover and engage "
                "feature-specific repository interactions during Stage 0"
            )
        author = data.get("author", False)
        if not isinstance(author, bool):
            raise ConfigurationError(f"repositories.{repo_id}.author must be a boolean")
        return cls(
            id=repo_id,
            remote=require_string(data.get("remote"), f"repositories.{repo_id}.remote"),
            author=author,
        )


@dataclass(frozen=True)
class RepositoryCatalog:
    id: str
    workflow: str
    repositories: dict[str, CatalogRepository]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryCatalog:
        if data.get("apiVersion") not in SUPPORTED_API_VERSIONS:
            raise ConfigurationError(
                f"catalog apiVersion must be one of {sorted(SUPPORTED_API_VERSIONS)}"
            )
        if data.get("kind") != "RepositoryCatalog":
            raise ConfigurationError("catalog kind must be RepositoryCatalog")
        if "contracts" in data:
            raise ConfigurationError(
                "repository catalogs must not inventory contracts; "
                "discover interfaces during Stage 0"
            )
        repositories = require_mapping(data.get("repositories"), "repositories")
        return cls(
            id=require_string(data.get("id"), "id"),
            workflow=require_string(data.get("workflow"), "workflow"),
            repositories={
                require_string(repo_id, "repositories key"): CatalogRepository.from_dict(
                    repo_id,
                    require_mapping(value, f"repositories.{repo_id}"),
                )
                for repo_id, value in repositories.items()
            },
        )


def load_policy_catalog(policy: str | Path | None) -> RepositoryCatalog:
    if policy is None:
        raise ConfigurationError(
            "an external policy pack is required; pass --policy PATH_TO_CATALOG_OR_DIRECTORY"
        )

    requested = Path(policy).expanduser()
    path = requested / "catalog.yaml" if requested.is_dir() else requested
    if not path.is_file():
        raise ConfigurationError(f"policy catalog does not exist: {path.resolve()}")

    catalog = RepositoryCatalog.from_dict(load_yaml(path))
    workflow = Path(catalog.workflow)
    if workflow.suffix in {".yaml", ".yml"} and not workflow.is_absolute():
        catalog = RepositoryCatalog(
            id=catalog.id,
            workflow=str((path.resolve().parent / workflow).resolve()),
            repositories=catalog.repositories,
        )
    return catalog
