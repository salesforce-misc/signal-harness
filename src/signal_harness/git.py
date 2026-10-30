from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .errors import ConfigurationError, ResolutionError
from .forge import remote_location

GIT_TIMEOUT_SECONDS = 120


class _GitTimeout(ResolutionError):
    """A bounded Git operation timed out rather than reporting a missing ref."""


def _git_process(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        # Do not echo remote URLs, helper arguments, or captured credentials.
        raise _GitTimeout(f"Git operation timed out after {GIT_TIMEOUT_SECONDS} seconds") from exc


def _git(root: Path, *args: str) -> str:
    completed = _git_process(["git", "-C", str(root), *args])
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ResolutionError(f"git {' '.join(args)} failed in {root}: {detail}")
    return completed.stdout.strip()


def repository_root(path: Path) -> Path:
    path = path.expanduser().resolve()
    candidate = path if path.is_dir() else path.parent
    return Path(_git(candidate, "rev-parse", "--show-toplevel")).resolve()


def head_sha(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


def origin_url(root: Path) -> str:
    return _git(root, "remote", "get-url", "origin")


def current_branch(root: Path) -> str:
    return _git(root, "branch", "--show-current") or "(detached)"


def is_dirty(root: Path) -> bool:
    return bool(_git(root, "status", "--porcelain"))


def is_ancestor(root: Path, ancestor: str, descendant: str = "HEAD") -> bool:
    completed = _git_process(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant]
    )
    if completed.returncode not in {0, 1}:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ResolutionError(f"git merge-base --is-ancestor failed in {root}: {detail}")
    return completed.returncode == 0


def changed_paths(root: Path, base: str, head: str = "HEAD") -> tuple[str, ...]:
    output = _git(
        root, "diff", "--no-renames", "--name-only", "--diff-filter=ACDMRTUXB", base, head
    )
    return tuple(line for line in output.splitlines() if line)


def normalize_remote(value: str) -> str:
    # Git acquisition also accepts local bare repositories. Keep their identity
    # separate from hosted projects, and retain the meaningful '.git' suffix.
    value = value.strip()
    if value.startswith("file:"):
        parsed = urlsplit(value)
        if parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
            raise ConfigurationError("local Git URI must not contain a host, query, or fragment")
        path = Path(unquote(parsed.path))
        if not path.is_absolute():
            raise ConfigurationError("local Git URI must identify an absolute path")
        return path.resolve().as_uri()
    if value.startswith(("/", "./", "../", "~/")):
        return Path(value).expanduser().resolve().as_uri()
    return remote_location(value).identity


def remote_clone_url(remote: str) -> str:
    if remote.startswith("-"):
        raise ResolutionError("repository remote must not start with a hyphen")
    if remote.startswith(("./", "../", "~/")):
        return str(Path(remote).expanduser().resolve())
    normalized = remote.removesuffix(".git")
    if "://" in normalized or normalized.startswith("git@") or Path(normalized).is_absolute():
        return remote
    if normalized.count("/") == 1:
        return f"git@github.com:{normalized}.git"
    return remote


def default_repository_cache() -> Path:
    configured = os.environ.get("SIGNAL_HARNESS_REPOSITORY_CACHE")
    if configured:
        return Path(configured).expanduser().resolve()
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
    return (base / "signal-harness" / "repositories").resolve()


@contextmanager
def _cache_lock(path: Path):
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _run_git(*args: str, cwd: Path | None = None) -> str:
    command = ["git", *(("-C", str(cwd)) if cwd else ()), *args]
    completed = _git_process(command)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ResolutionError(f"{' '.join(command)} failed: {detail}")
    return completed.stdout.strip()


def _missing_snapshot_retry(root: Path, snapshot: Path, revision: str) -> tuple[str, ...]:
    """Replace only this owned, missing, unlocked registration at its locked HEAD."""
    records = _run_git("worktree", "list", "--porcelain", "-z", cwd=root)
    for record in records.split("\0\0"):
        fields = record.split("\0")
        path = next((field[9:] for field in fields if field.startswith("worktree ")), None)
        if path is None or Path(path).resolve() != snapshot:
            continue
        if (
            f"HEAD {revision}" not in fields
            or "detached" not in fields
            or any(field == "locked" or field.startswith("locked ") for field in fields)
        ):
            raise ResolutionError(
                f"managed snapshot recovery required at {snapshot}; "
                "preserved mismatched or locked Git registration"
            )
        return ("--force",)
    return ()


def managed_repository(
    remote: str,
    repo_id: str,
    *,
    snapshot_root: Path | None = None,
) -> Path:
    """Return an engine-owned, clean checkout for a catalog repository."""
    clone_url = remote_clone_url(remote)
    cache_root = default_repository_cache()
    digest = hashlib.sha256(normalize_remote(remote).encode("utf-8")).hexdigest()[:12]
    safe_id = re.sub(r"[^a-zA-Z0-9._-]+", "-", repo_id).strip("-") or "repository"
    snapshot_digest = hashlib.sha256(
        json.dumps([repo_id, normalize_remote(remote)], separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    snapshot_key = f"{safe_id[:80]}-{snapshot_digest}"
    target = cache_root / f"{safe_id}-{digest}"
    lock = cache_root / ".locks" / f"{safe_id}-{digest}.lock"
    with _cache_lock(lock):
        if not target.exists():
            cache_root.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix=f".{safe_id}-", dir=cache_root))
            try:
                _run_git(
                    "clone",
                    "--quiet",
                    "--no-tags",
                    "--filter=blob:none",
                    "--",
                    clone_url,
                    str(temporary),
                )
                temporary.rename(target)
            except Exception:
                import shutil

                shutil.rmtree(temporary, ignore_errors=True)
                raise
        root = repository_root(target)
        actual = normalize_remote(origin_url(root))
        expected = normalize_remote(remote)
        if actual != expected:
            raise ResolutionError(
                f"managed checkout remote mismatch for {repo_id}: expected {remote}, "
                f"got {origin_url(root)}"
            )
        if is_dirty(root):
            raise ResolutionError(f"managed checkout is dirty for {repo_id}: {root}")
        # A partially completed engagement may already own an immutable snapshot.
        # Validate its original identity before consulting the moving remote tip.
        identity = None
        if snapshot_root is not None:
            snapshot_base = snapshot_root.expanduser().resolve()
            legacy_identity = snapshot_base / f".{safe_id}.snapshot.json"
            if legacy_identity.is_symlink():
                raise ResolutionError("legacy snapshot identity is a symlink")
            if legacy_identity.is_file():
                try:
                    legacy = json.loads(legacy_identity.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise ResolutionError("legacy snapshot identity is unreadable") from exc
                if (
                    isinstance(legacy, dict)
                    and legacy.get("repository") == repo_id
                    and legacy.get("remote") == expected
                ):
                    snapshot_key = safe_id
            snapshot = snapshot_base / snapshot_key
            identity_path = snapshot_base / f".{snapshot_key}.snapshot.json"
            if identity_path.is_symlink():
                raise ResolutionError("managed snapshot identity is a symlink")
            if snapshot.exists() or snapshot.is_symlink() or identity_path.exists():
                try:
                    identity = json.loads(identity_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise ResolutionError(
                        f"managed snapshot identity unavailable for {repo_id}"
                    ) from exc
                if (
                    not isinstance(identity, dict)
                    or identity.get("repository") != repo_id
                    or identity.get("remote") != expected
                    or not isinstance(identity.get("revision"), str)
                    or not re.fullmatch(r"[0-9a-f]{40}", identity["revision"])
                ):
                    raise ResolutionError(f"managed snapshot identity mismatch for {repo_id}")
                if snapshot.is_symlink():
                    raise ResolutionError(
                        f"managed snapshot recovery required at {snapshot}; preserved symlink"
                    )
                if snapshot.exists():
                    try:
                        snapshot_repo = repository_root(snapshot)
                    except ResolutionError as exc:
                        raise ResolutionError(
                            f"managed snapshot recovery required at {snapshot}; "
                            "preserved incomplete snapshot"
                        ) from exc
                    if (
                        snapshot_repo != snapshot.resolve()
                        or normalize_remote(origin_url(snapshot_repo)) != expected
                    ):
                        raise ResolutionError(f"managed snapshot repository mismatch for {repo_id}")
                    if (
                        current_branch(snapshot_repo) != "(detached)"
                        or head_sha(snapshot_repo) != identity["revision"]
                    ):
                        raise ResolutionError(
                            f"managed snapshot moved for {repo_id}: {snapshot_repo}"
                        )
                    if is_dirty(snapshot_repo):
                        raise ResolutionError(
                            f"managed snapshot is dirty for {repo_id}: {snapshot_repo}; "
                            "recovery required; preserved snapshot content"
                        )
                    return snapshot_repo
        if identity is not None:
            locked_revision = identity["revision"]
        else:
            _run_git("fetch", "--quiet", "--prune", "origin", cwd=root)
            try:
                _run_git("remote", "set-head", "origin", "--auto", cwd=root)
                refreshed_head = True
            except _GitTimeout:
                raise
            except ResolutionError:
                # A readable remote may advertise a missing default branch. Do not
                # let a stale cached origin/HEAD override the validated fallbacks.
                refreshed_head = False
            candidates = []
            symbolic = _git_process(
                ["git", "-C", str(root), "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"]
            )
            if refreshed_head and symbolic.returncode == 0:
                candidates.append(symbolic.stdout.strip())
            candidates.extend(("refs/remotes/origin/main", "refs/remotes/origin/master"))
            revision = next(
                (
                    candidate
                    for candidate in candidates
                    if _git_process(
                        ["git", "-C", str(root), "show-ref", "--verify", "--quiet", candidate]
                    ).returncode
                    == 0
                ),
                None,
            )
            if revision is None:
                raise ResolutionError(
                    f"managed checkout has no discoverable default branch: {repo_id}"
                )
            # Profileless inference reads this same reference from the snapshot.
            _run_git("symbolic-ref", "refs/remotes/origin/HEAD", revision, cwd=root)
            locked_revision = _run_git("rev-parse", revision, cwd=root)
        snapshot_base = (
            snapshot_root.expanduser().resolve()
            if snapshot_root is not None
            else cache_root / "snapshots" / f"{safe_id}-{digest}" / locked_revision
        )
        snapshot = snapshot_base / snapshot_key
        if snapshot.exists():
            snapshot_repo = repository_root(snapshot)
            if _run_git("rev-parse", "HEAD", cwd=snapshot_repo) != locked_revision:
                raise ResolutionError(f"managed snapshot moved for {repo_id}: {snapshot_repo}")
            if is_dirty(snapshot_repo):
                raise ResolutionError(f"managed snapshot is dirty for {repo_id}: {snapshot_repo}")
            return snapshot_repo
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if snapshot_root is not None and identity is None:
            # Publish ownership and the selected revision before creating a worktree.
            # A retry can finish provisioning without following a newer remote tip.
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{snapshot_key}.snapshot-", dir=snapshot_base
            )
            temporary_identity = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(
                        {"repository": repo_id, "remote": expected, "revision": locked_revision},
                        handle,
                    )
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_identity, identity_path)
            finally:
                temporary_identity.unlink(missing_ok=True)
        # An interrupted add may retain Git registration without a directory.
        # The validated descriptor pins identity/revision; never prune other worktrees.
        retry_flags = (
            _missing_snapshot_retry(root, snapshot, locked_revision)
            if identity is not None and snapshot_root is not None
            else ()
        )
        try:
            _run_git(
                "worktree",
                "add",
                "--quiet",
                *retry_flags,
                "--detach",
                str(snapshot),
                locked_revision,
                cwd=root,
            )
        except ResolutionError as exc:
            detail = (
                f"Git operation timed out after {GIT_TIMEOUT_SECONDS} seconds; "
                if isinstance(exc, _GitTimeout)
                else ""
            )
            raise ResolutionError(
                f"managed snapshot recovery required at {snapshot}; {detail}"
                "ownership and original revision retained; retry acquisition after inspection"
            ) from exc
        return repository_root(snapshot)
