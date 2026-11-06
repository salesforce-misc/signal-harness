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

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import ConfigurationError, ResolutionError
from .security import authorize_command, record_command_run, redact_text, redact_value
from .state import RunStore, utc_now
from .worktrees import verify_prepared_task, verify_run_context

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _bounded(value: str, limit: int = 20_000) -> str:
    return value if len(value) <= limit else value[-limit:]


def _output_text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _safe_report_path(root: Path, relative: str) -> Path:
    if not relative.strip() or Path(relative).is_absolute():
        raise ConfigurationError(f"baseline report must be repository-relative: {relative}")
    root = root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError(f"baseline report escapes repository root: {relative}") from exc
    return path


def _pmd_findings(root: Path, reports: tuple[str, ...]) -> tuple[set[tuple[str, ...]], set[str]]:
    findings: set[tuple[str, ...]] = set()
    present: set[str] = set()
    for relative in reports:
        report = _safe_report_path(root, relative)
        if not report.is_file():
            continue
        present.add(relative)
        try:
            document = ET.parse(report)
        except ET.ParseError as exc:
            raise ResolutionError(f"PMD baseline-delta report is invalid: {relative}") from exc
        for file_element in document.findall(".//{*}file"):
            raw_name = file_element.get("name", "")
            source = Path(raw_name)
            if source.is_absolute():
                try:
                    source_name = source.resolve().relative_to(root.resolve()).as_posix()
                except ValueError as exc:
                    raise ResolutionError(
                        f"PMD finding path escapes repository root: {raw_name}"
                    ) from exc
            else:
                source_name = source.as_posix()
            for violation in file_element.findall("{*}violation"):
                findings.add(
                    (
                        source_name,
                        violation.get("rule", ""),
                        violation.get("beginline", ""),
                        violation.get("endline", ""),
                        violation.get("begincolumn", ""),
                        violation.get("endcolumn", ""),
                        violation.get("class", ""),
                        violation.get("method", ""),
                        violation.get("variable", ""),
                        " ".join((violation.text or "").split()),
                    )
                )
    return findings, present


def _finding_value(finding: tuple[str, ...]) -> dict[str, str]:
    keys = (
        "path",
        "rule",
        "beginLine",
        "endLine",
        "beginColumn",
        "endColumn",
        "class",
        "method",
        "variable",
        "message",
    )
    return dict(zip(keys, finding, strict=True))


def _git_worktree(repo_root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ResolutionError(f"git worktree {' '.join(arguments)} failed: {detail}")


def _run_baseline_delta_verification(
    store: RunStore,
    run_id: str,
    task_id: str,
    command_id: str,
    command: list[str],
    task: dict[str, Any],
    repo: dict[str, Any],
    state: dict[str, Any],
    *,
    timeout: int,
    reports: tuple[str, ...],
    report_format: str,
    runner: CommandRunner,
) -> dict[str, Any]:
    if report_format != "pmd":
        raise ConfigurationError(f"unsupported baseline report format: {report_format}")
    worktree = Path(task["worktree"]).resolve()
    repo_root = Path(repo["root"]).resolve()
    output_limit = state["security"]["limits"]["maxOutputBytes"]
    persisted = state["tasks"][task_id].setdefault("signals", [])
    signal_sequence = len(persisted) + 1

    with tempfile.TemporaryDirectory(prefix="signal-harness-baseline-") as temporary:
        baseline = Path(temporary) / "repository"
        _git_worktree(repo_root, "add", "--detach", str(baseline), task["baseRevision"])
        try:
            for root in (baseline, worktree):
                for relative in reports:
                    report = _safe_report_path(root, relative)
                    if report.exists():
                        report.unlink()
            try:
                baseline_result = runner(
                    command,
                    cwd=baseline,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                record_command_run(state)
                current_result = runner(
                    command,
                    cwd=worktree,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                record_command_run(state)
                baseline_findings, baseline_reports = _pmd_findings(baseline, reports)
                current_findings, current_reports = _pmd_findings(worktree, reports)
                asymmetric_reports = sorted(baseline_reports ^ current_reports)
                absent_reports = sorted(set(reports) - (baseline_reports | current_reports))
                new_findings = sorted(current_findings - baseline_findings)
                resolved_findings = sorted(baseline_findings - current_findings)
                unexplained_failure = (current_result.returncode != 0 and not current_findings) or (
                    baseline_result.returncode != 0 and not baseline_findings
                )
                status = (
                    "pass"
                    if not new_findings and not unexplained_failure and not asymmetric_reports
                    else "fail"
                )
                summary = (
                    f"{command_id} baseline delta: {len(new_findings)} new, "
                    f"{len(resolved_findings)} resolved findings"
                )
                delta = {
                    "format": report_format,
                    "reports": list(reports),
                    "baselineRevision": task["baseRevision"],
                    "baselineExitCode": baseline_result.returncode,
                    "currentExitCode": current_result.returncode,
                    "baselineFindingCount": len(baseline_findings),
                    "currentFindingCount": len(current_findings),
                    "newFindingCount": len(new_findings),
                    "resolvedFindingCount": len(resolved_findings),
                    "symmetricallyAbsentReports": absent_reports,
                    "asymmetricReports": asymmetric_reports,
                    "newFindings": [_finding_value(item) for item in new_findings[:100]],
                    "resolvedFindings": [_finding_value(item) for item in resolved_findings[:100]],
                }
                metadata = {
                    "task": task_id,
                    "commandId": command_id,
                    "command": redact_value(command),
                    "exitCode": current_result.returncode,
                    "stdout": redact_text(_bounded(current_result.stdout, output_limit)),
                    "stderr": redact_text(_bounded(current_result.stderr, output_limit)),
                    "baselineStdout": redact_text(_bounded(baseline_result.stdout, output_limit)),
                    "baselineStderr": redact_text(_bounded(baseline_result.stderr, output_limit)),
                    "baselineDelta": delta,
                }
            except subprocess.TimeoutExpired as exc:
                record_command_run(state)
                status = "unknown"
                summary = f"baseline-delta verification timed out after {timeout} seconds"
                metadata = {
                    "task": task_id,
                    "commandId": command_id,
                    "command": redact_value(command),
                    "exitCode": None,
                    "stdout": redact_text(_bounded(_output_text(exc.stdout), output_limit)),
                    "stderr": redact_text(_bounded(_output_text(exc.stderr), output_limit)),
                    "timeoutSeconds": timeout,
                    "baselineDelta": {
                        "format": report_format,
                        "reports": list(reports),
                        "baselineRevision": task["baseRevision"],
                    },
                }
        finally:
            _git_worktree(repo_root, "remove", "--force", str(baseline))

    return {
        "id": f"{run_id}:{task_id}:{command_id}:{signal_sequence}",
        "kind": "command",
        "source": "repository-profile-baseline-delta",
        "repository": repo["id"],
        "status": status,
        "summary": summary,
        "evidence": [f"run://{run_id}/tasks/{task_id}/commands/{command_id}"],
        "observedAt": utc_now(),
        "metadata": metadata,
    }


def run_verification(
    store: RunStore,
    run_id: str,
    task_id: str,
    command_ids: tuple[str, ...],
    *,
    timeout: int = 900,
    runner: CommandRunner = subprocess.run,
    baseline_reports: tuple[str, ...] = (),
    report_format: str | None = None,
) -> list[dict[str, Any]]:
    if not command_ids:
        raise ConfigurationError("verification requires at least one command id")
    verify_run_context(store, run_id)
    task, repo = verify_prepared_task(store, run_id, task_id)
    commands = repo["profile"]["commands"]
    unknown = [command_id for command_id in command_ids if command_id not in commands]
    if unknown:
        raise ResolutionError(f"repository {repo['id']} has no commands: {unknown}")
    if baseline_reports and (len(command_ids) != 1 or report_format is None):
        raise ConfigurationError(
            "baseline-delta verification requires exactly one command and --report-format"
        )
    if report_format is not None and not baseline_reports:
        raise ConfigurationError("--report-format requires at least one baseline report")

    state = store.load(run_id)
    persisted_signals = state["tasks"][task_id].setdefault("signals", [])
    signals: list[dict[str, Any]] = []
    for command_id in command_ids:
        command = authorize_command(state, repo["id"], command_id, commands[command_id], timeout)
        if baseline_reports:
            signal = _run_baseline_delta_verification(
                store,
                run_id,
                task_id,
                command_id,
                command,
                task,
                repo,
                state,
                timeout=timeout,
                reports=baseline_reports,
                report_format=report_format or "",
                runner=runner,
            )
            signals.append(signal)
            continue
        output_limit = state["security"]["limits"]["maxOutputBytes"]
        signal_sequence = len(persisted_signals) + len(signals) + 1
        try:
            completed = runner(
                command,
                cwd=Path(task["worktree"]),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            signal = {
                "id": f"{run_id}:{task_id}:{command_id}:{signal_sequence}",
                "kind": "command",
                "source": "repository-profile",
                "repository": repo["id"],
                "status": "pass" if completed.returncode == 0 else "fail",
                "summary": f"{command_id} exited {completed.returncode}",
                "evidence": [f"run://{run_id}/tasks/{task_id}/commands/{command_id}"],
                "observedAt": utc_now(),
                "metadata": {
                    "task": task_id,
                    "commandId": command_id,
                    "command": redact_value(command),
                    "exitCode": completed.returncode,
                    "stdout": redact_text(_bounded(completed.stdout, output_limit)),
                    "stderr": redact_text(_bounded(completed.stderr, output_limit)),
                },
            }
        except subprocess.TimeoutExpired as exc:
            signal = {
                "id": f"{run_id}:{task_id}:{command_id}:{signal_sequence}",
                "kind": "command",
                "source": "repository-profile",
                "repository": repo["id"],
                "status": "unknown",
                "summary": f"timed out after {timeout} seconds",
                "evidence": [f"run://{run_id}/tasks/{task_id}/commands/{command_id}"],
                "observedAt": utc_now(),
                "metadata": {
                    "task": task_id,
                    "commandId": command_id,
                    "command": redact_value(command),
                    "exitCode": None,
                    "stdout": redact_text(_bounded(_output_text(exc.stdout), output_limit)),
                    "stderr": redact_text(_bounded(_output_text(exc.stderr), output_limit)),
                    "timeoutSeconds": timeout,
                },
            }
        signals.append(signal)
        record_command_run(state)

    persisted_signals.extend(signals)
    for signal in signals:
        store.append_event(
            state,
            "verification.completed",
            f"{task_id}:{signal['metadata']['commandId']} -> {signal['status']}",
        )
    store.save(state)
    return signals
