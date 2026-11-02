"""Code-host identity and the adapter boundary; no authentication at discovery time."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from .errors import ConfigurationError, ResolutionError


@dataclass(frozen=True)
class RemoteLocation:
    host: str
    project: str
    identity: str


def remote_location(value: str) -> RemoteLocation:
    value = value.strip().rstrip("/")
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "ssh"} or not parsed.hostname:
            raise ConfigurationError("forge remote must use HTTPS or SSH")
        if (
            parsed.password
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "https" and parsed.username)
        ):
            raise ConfigurationError(
                "forge remote must not contain credentials, query, or fragment"
            )
        host = parsed.hostname.lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise ConfigurationError("forge remote contains an invalid port") from exc
        authority = host if parsed.scheme == "ssh" or port in {None, 443} else f"{host}:{port}"
        api_host = host if parsed.scheme == "ssh" else authority
        project = unquote(parsed.path.lstrip("/"))
    else:
        match = re.fullmatch(r"(?:[^@/:\s]+@)?([^/:\s]+):(.+)", value)
        if match:
            host = match.group(1).lower()
            authority = api_host = host
            project = match.group(2)
        else:
            authority = api_host = "github.com"
            project = value
    project = project.removesuffix(".git").rstrip("/")
    parts = project.split("/")
    if len(parts) < 2 or any(
        not part or part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
        for part in parts
    ):
        raise ConfigurationError("forge remote must identify a host and namespace/project")
    identity = project if authority == "github.com" else f"https://{authority}/{project}"
    return RemoteLocation(api_host, project, identity)


class ReviewProvider(Protocol):
    name: str
    location: RemoteLocation

    def open(
        self,
        repo: dict[str, Any],
        task: dict[str, Any],
        title: str,
        body_file: Path | None,
        draft: bool,
        runner: Any,
    ) -> dict[str, Any]: ...

    def refresh(
        self,
        repo: dict[str, Any],
        task: dict[str, Any],
        recorded: dict[str, Any],
        runner: Any,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]: ...


def provider_for(repo: dict[str, Any]) -> ReviewProvider:
    location = remote_location(repo["remote"])
    configured = repo.get("profile", {}).get("forge")
    if configured is None:
        configured = {"github.com": "github", "gitlab.com": "gitlab"}.get(location.host)
    if configured == "github":
        from .github import GitHubProvider

        return GitHubProvider(location)
    if configured == "gitlab":
        from .gitlab import GitLabProvider

        return GitLabProvider(location)
    raise ResolutionError(
        f"No forge provider selected for {location.host}. Set repo.forge to github or gitlab "
        "in the repository profile; discovery does not require forge authentication."
    )


CommandRunner = Any  # subprocess.run-compatible injection, including input for JSON requests.
