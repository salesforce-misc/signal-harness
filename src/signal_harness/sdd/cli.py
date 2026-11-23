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

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from signal_harness import __version__
from signal_harness.runtime.context import collect_context
from signal_harness.runtime.errors import ConfigurationError, HarnessError
from signal_harness.runtime.git import origin_url, repository_root
from signal_harness.runtime.integrations import (
    enqueue_integration_event,
    record_external_id,
    request_human_input,
    resolve_human_input,
)
from signal_harness.runtime.security import approve_command, record_cost, redact_text
from signal_harness.sdd import build_sdd_plan, release_sdd_plan
from signal_harness.sdd.artifact_handoff import record_artifact_handoff
from signal_harness.sdd.bootstrap import (
    scaffold_distribution,
    scaffold_repository,
    scaffold_standalone,
)
from signal_harness.sdd.compatibility import compare_snapshots, snapshot_plan, snapshot_run
from signal_harness.sdd.control import cancel_run, pause_run, recover_run, resume_run, run_summary
from signal_harness.sdd.distributions import load_distribution
from signal_harness.sdd.findings import record_finding, resolve_finding
from signal_harness.sdd.hosting import open_pull_request, publish_branch, resume_from_pull_request
from signal_harness.sdd.leases import claim_task, release_task
from signal_harness.sdd.planner import build_plan, load_profile, resolve_profile_path
from signal_harness.sdd.pull_requests import pull_request_plan
from signal_harness.sdd.retention import delete_run
from signal_harness.sdd.session import (
    acknowledge_task_context,
    context_bundle,
    engage_repository,
    lock_discovery_context,
    next_action,
    record_artifact,
)
from signal_harness.sdd.state import RunStore
from signal_harness.sdd.tdd import record_tdd_phase, recorded_tdd_evidence
from signal_harness.sdd.verification import run_verification
from signal_harness.sdd.worktrees import cleanup_task, freeze_task_head, prepare_task


def _repo_overrides(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        repo_id, separator, raw_path = value.partition("=")
        if not separator or not repo_id or not raw_path:
            raise argparse.ArgumentTypeError(
                f"invalid --repo value {value!r}; expected REPO_ID=/absolute/path"
            )
        if repo_id in result:
            raise argparse.ArgumentTypeError(f"duplicate --repo mapping for {repo_id}")
        result[repo_id] = Path(raw_path).expanduser().resolve()
    return result


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _init_distribution(args: argparse.Namespace) -> int:
    paths = scaffold_distribution(Path(args.target), args.name, args.package)
    _print_json({"kind": "distribution", "files": [str(path) for path in paths]})
    return 0


def _init_repository(args: argparse.Namespace) -> int:
    paths = scaffold_repository(
        Path(args.target),
        args.repo_id,
        args.remote,
        distribution=Path(args.distribution) if args.distribution else None,
        forge=args.forge,
        detect_commands=args.detect_commands,
    )
    _print_json({"kind": "repository", "files": [str(path) for path in paths]})
    return 0


def _init_standalone(args: argparse.Namespace) -> int:
    paths = scaffold_standalone(
        Path(args.target),
        args.repo_id,
        args.remote,
        forge=args.forge,
        detect_commands=args.detect_commands,
    )
    _print_json({"kind": "standalone", "files": [str(path) for path in paths]})
    return 0


def _discover(args: argparse.Namespace) -> int:
    root = repository_root(Path(args.cwd))
    profile_path = resolve_profile_path(root, args.profile)
    profile = load_profile(profile_path)
    context = collect_context(root, profile)
    _print_json(
        {
            "repo": profile.id,
            "root": str(root),
            "remote": origin_url(root),
            "profile": str(profile_path),
            "context": asdict(context),
        }
    )
    return 0


def _plan(args: argparse.Namespace) -> int:
    plan = build_plan(
        Path(args.workspace).expanduser().resolve(),
        Path(args.cwd).expanduser().resolve(),
        _repo_overrides(args.repo),
    )
    _print_json(plan.as_dict())
    return 0


def _validate(args: argparse.Namespace) -> int:
    plan = build_plan(
        Path(args.workspace).expanduser().resolve(),
        Path(args.cwd).expanduser().resolve(),
        _repo_overrides(args.repo),
    )
    print(
        f"valid: {plan.workspace.feature} "
        f"({len(plan.repositories)} repos, "
        f"{len(plan.workspace.tasks)} tasks)"
    )
    return 0


def _route(args: argparse.Namespace) -> int:
    plan = build_plan(
        Path(args.workspace).expanduser().resolve(),
        Path(args.cwd).expanduser().resolve(),
        _repo_overrides(args.repo),
    )
    task = next((item for item in plan.workspace.tasks if item.id == args.task), None)
    if task is None:
        raise HarnessError(f"unknown task: {args.task}")
    resolved = next(item for item in plan.repositories if item.workspace.id == task.repo)
    _print_json(
        {
            "task": task.id,
            "repo": task.repo,
            "mode": resolved.workspace.mode,
            "root": str(resolved.root),
            "remote": resolved.workspace.remote,
            "defaultBranch": resolved.profile.default_branch,
            "featureManifest": resolved.workspace.feature_manifest,
        }
    )
    return 0


def _store(args: argparse.Namespace) -> RunStore:
    return RunStore(Path(args.state_root).expanduser() if args.state_root else None)


def _start(args: argparse.Namespace) -> int:
    workspace_path = Path(args.workspace).expanduser().resolve()
    plan = build_plan(
        workspace_path,
        Path(args.cwd).expanduser().resolve(),
        _repo_overrides(args.repo),
    )
    plan = replace(plan, policy_mode=args.mode)
    state = _store(args).create(plan, workspace_path, args.run_id)
    _print_json(
        {
            "runId": state["runId"],
            "feature": state["feature"],
            "status": state["status"],
            "stateFile": str(_store(args).state_path(state["runId"])),
        }
    )
    return 0


def _status(args: argparse.Namespace) -> int:
    state = _store(args).load(args.run_id)
    _print_json(state)
    return 0


def _summary(args: argparse.Namespace) -> int:
    _print_json(run_summary(_store(args), args.run_id))
    return 0


def _engage_repository(args: argparse.Namespace) -> int:
    _print_json(
        engage_repository(
            _store(args),
            args.run_id,
            args.repository,
            reason=args.reason,
            evidence=tuple(args.evidence),
            actor=args.actor,
            override=Path(args.override).expanduser().resolve() if args.override else None,
        )
    )
    return 0


def _migrate_author_permissions(args: argparse.Namespace) -> int:
    try:
        permissions = json.loads(Path(args.permissions).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"cannot read reviewed permission mapping: {exc}") from exc
    _print_json(
        _store(args).migrate_author_permissions(
            args.run_id, permissions, actor=args.actor, reason=args.reason
        )
    )
    return 0


def _lock_context(args: argparse.Namespace) -> int:
    _print_json(
        lock_discovery_context(
            _store(args),
            args.run_id,
            args.repository,
            paths=tuple(args.path),
            reason=args.reason,
            actor=args.actor,
        )
    )
    return 0


def _pause(args: argparse.Namespace) -> int:
    pause_run(_store(args), args.run_id, actor=args.actor, reason=args.reason)
    return _summary(args)


def _resume_run(args: argparse.Namespace) -> int:
    resume_run(_store(args), args.run_id, args.actor)
    return _summary(args)


def _cancel(args: argparse.Namespace) -> int:
    cancel_run(_store(args), args.run_id, actor=args.actor, reason=args.reason)
    return _summary(args)


def _recover(args: argparse.Namespace) -> int:
    _print_json(recover_run(_store(args), args.run_id))
    return 0


def _ask(args: argparse.Namespace) -> int:
    _print_json(
        request_human_input(
            _store(args),
            args.run_id,
            request_id=args.request_id,
            kind=args.kind,
            prompt=args.prompt,
            channels=tuple(args.channel),
        )
    )
    return 0


def _resolve_human(args: argparse.Namespace) -> int:
    _print_json(
        resolve_human_input(
            _store(args),
            args.run_id,
            args.request_id,
            actor=args.actor,
            decision=args.decision,
            response=args.response,
        )
    )
    return 0


def _external_id(args: argparse.Namespace) -> int:
    value = record_external_id(_store(args), args.run_id, args.system, args.kind, args.value)
    _print_json({"system": args.system, "kind": args.kind, "value": value})
    return 0


def _notify(args: argparse.Namespace) -> int:
    payload_path = Path(args.payload).expanduser().resolve()
    if not payload_path.is_file():
        raise HarnessError(f"integration payload does not exist: {payload_path}")
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessError(f"invalid integration payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise HarnessError("integration payload must be a JSON object")
    _print_json(
        enqueue_integration_event(
            _store(args),
            args.run_id,
            event_id=args.event_id,
            channel=args.channel,
            kind=args.kind,
            payload=payload,
        )
    )
    return 0


def _claim(args: argparse.Namespace) -> int:
    _print_json(
        claim_task(
            _store(args),
            args.run_id,
            args.task,
            owner=args.owner,
            ttl_seconds=args.ttl,
        )
    )
    return 0


def _release(args: argparse.Namespace) -> int:
    release_task(
        _store(args),
        args.run_id,
        args.task,
        owner=args.owner,
        token=args.token,
    )
    _print_json({"runId": args.run_id, "task": args.task, "status": "released"})
    return 0


def _approve_command(args: argparse.Namespace) -> int:
    _print_json(
        approve_command(
            _store(args),
            args.run_id,
            repository=args.repository,
            command_id=args.command_id,
            actor=args.actor,
        )
    )
    return 0


def _record_cost(args: argparse.Namespace) -> int:
    _print_json(
        record_cost(
            _store(args),
            args.run_id,
            amount_usd=args.amount,
            source=args.source,
        )
    )
    return 0


def _record_finding(args: argparse.Namespace) -> int:
    _print_json(
        record_finding(
            _store(args),
            args.run_id,
            args.task,
            finding_id=args.finding_id,
            source=args.source,
            severity=args.severity,
            summary=args.summary,
            evidence=args.evidence,
            affected_commands=tuple(args.command),
            behavioral_fix=args.behavioral_fix,
        )
    )
    return 0


def _resolve_finding(args: argparse.Namespace) -> int:
    regression_evidence = None
    if args.regression_evidence:
        path = Path(args.regression_evidence).expanduser().resolve()
        try:
            regression_evidence = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HarnessError(f"cannot read regression evidence: {exc}") from exc
        if not isinstance(regression_evidence, dict):
            raise HarnessError("regression evidence must be a JSON object")
    _print_json(
        resolve_finding(
            _store(args),
            args.run_id,
            args.task,
            args.finding_id,
            disposition=args.disposition,
            actor=args.actor,
            rationale=args.rationale,
            evidence=args.evidence,
            regression_evidence=regression_evidence,
        )
    )
    return 0


def _delete_run(args: argparse.Namespace) -> int:
    _print_json(delete_run(_store(args), args.run_id, confirmation=args.confirm))
    return 0


def _compatibility_plan(args: argparse.Namespace):
    return build_plan(
        Path(args.workspace).expanduser().resolve(),
        Path(args.cwd).expanduser().resolve(),
        _repo_overrides(args.repo),
    )


def _compat_snapshot(args: argparse.Namespace) -> int:
    _print_json(snapshot_plan(_compatibility_plan(args)))
    return 0


def _compat_check(args: argparse.Namespace) -> int:
    expected_path = Path(args.expected).expanduser().resolve()
    if not expected_path.is_file():
        raise HarnessError(f"compatibility snapshot does not exist: {expected_path}")
    try:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessError(f"invalid compatibility snapshot: {exc}") from exc
    differences = compare_snapshots(expected, snapshot_plan(_compatibility_plan(args)))
    _print_json({"compatible": not differences, "differences": differences})
    return 0 if not differences else 1


def _compat_run_snapshot(args: argparse.Namespace) -> int:
    _print_json(snapshot_run(_store(args).load(args.run_id)))
    return 0


def _compat_run_check(args: argparse.Namespace) -> int:
    expected_path = Path(args.expected).expanduser().resolve()
    if not expected_path.is_file():
        raise HarnessError(f"compatibility snapshot does not exist: {expected_path}")
    try:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessError(f"invalid compatibility snapshot: {exc}") from exc
    differences = compare_snapshots(expected, snapshot_run(_store(args).load(args.run_id)))
    _print_json({"compatible": not differences, "differences": differences})
    return 0 if not differences else 1


def _prepare(args: argparse.Namespace) -> int:
    task = prepare_task(_store(args), args.run_id, args.task)
    _print_json(task)
    return 0


def _cleanup(args: argparse.Namespace) -> int:
    _print_json(cleanup_task(_store(args), args.run_id, args.task))
    return 0


def _freeze(args: argparse.Namespace) -> int:
    _print_json(freeze_task_head(_store(args), args.run_id, args.task))
    return 0


def _tdd_record(args: argparse.Namespace) -> int:
    sensitivity = None
    if bool(args.sensitivity_mutation) != bool(args.sensitivity_signal):
        raise HarnessError("guard sensitivity requires both mutation and signal")
    if args.sensitivity_mutation:
        sensitivity = {
            "mutation": args.sensitivity_mutation,
            "status": "fail",
            "signal": args.sensitivity_signal,
        }
    _print_json(
        record_tdd_phase(
            _store(args),
            args.run_id,
            args.task,
            args.case,
            args.phase,
            status=args.status,
            signal=args.signal,
            failure_kind=args.failure_kind,
            sensitivity=sensitivity,
        )
    )
    return 0


def _tdd_evidence(args: argparse.Namespace) -> int:
    state = _store(args).load(args.run_id)
    _print_json(recorded_tdd_evidence(state, args.task))
    return 0


def _next(args: argparse.Namespace) -> int:
    _print_json(next_action(_store(args), args.run_id, args.task))
    return 0


def _context(args: argparse.Namespace) -> int:
    _print_json(context_bundle(_store(args), args.run_id, args.task))
    return 0


def _ack_context(args: argparse.Namespace) -> int:
    _print_json(acknowledge_task_context(_store(args), args.run_id, args.task, tuple(args.file)))
    return 0


def _record(args: argparse.Namespace) -> int:
    _print_json(
        record_artifact(
            _store(args),
            args.run_id,
            kind=args.kind,
            reference=args.reference,
            task_id=args.task,
        )
    )
    return 0


def _record_handoff(args: argparse.Namespace) -> int:
    _print_json(
        record_artifact_handoff(
            _store(args),
            args.run_id,
            task_id=args.task,
            artifact_id=args.artifact,
            uri=args.uri,
            digest=args.digest,
            source_revision=args.source_revision,
            provider=args.provider,
        )
    )
    return 0


def _verify(args: argparse.Namespace) -> int:
    _print_json(
        run_verification(
            _store(args),
            args.run_id,
            args.task,
            tuple(args.command),
            timeout=args.timeout,
            baseline_reports=tuple(args.baseline_report),
            report_format=args.report_format,
        )
    )
    return 0


def _publish(args: argparse.Namespace) -> int:
    _print_json(publish_branch(_store(args), args.run_id, args.task))
    return 0


def _open_pr(args: argparse.Namespace) -> int:
    _print_json(
        open_pull_request(
            _store(args),
            args.run_id,
            args.task,
            args.title,
            body_file=Path(args.body_file) if args.body_file else None,
            draft=args.draft,
        )
    )
    return 0


def _resume(args: argparse.Namespace) -> int:
    _print_json(resume_from_pull_request(_store(args), args.run_id, args.task))
    return 0


def _pr_plan(args: argparse.Namespace) -> int:
    plan = pull_request_plan(
        _store(args),
        args.run_id,
        args.task,
        args.title,
        Path(args.body_file) if args.body_file else None,
        args.draft,
    )
    _print_json(plan)
    return 0


def _sdd_plan(args: argparse.Namespace) -> int:
    distribution = load_distribution(args.distribution) if args.distribution else None
    plan = build_sdd_plan(
        feature=args.feature,
        current_path=Path(args.cwd).expanduser().resolve(),
        sponsor_id=args.sponsor,
        feature_manifest=args.manifest,
        policy=distribution.policy if distribution else args.policy,
        repo_overrides=_repo_overrides(args.repo),
        include=tuple(args.include),
        exclude=tuple(args.exclude),
    )
    persisted = False
    try:
        policy_mode = args.mode or (
            distribution.default_policy_mode if distribution else "evaluation"
        )
        if distribution and policy_mode not in distribution.policy_modes:
            raise HarnessError(
                f"distribution {distribution.name} does not allow policy mode {policy_mode}"
            )
        plan = replace(
            plan,
            distribution=distribution.as_dict() if distribution else None,
            policy_mode=policy_mode,
        )
        if args.start:
            sponsor = next(
                item for item in plan.repositories if item.workspace.id == plan.workspace.sponsor
            )
            if not sponsor.workspace.feature_manifest:
                raise HarnessError("central SDD plan has no sponsor feature manifest")
            source = sponsor.root / sponsor.workspace.feature_manifest
            state = _store(args).create(plan, source, args.run_id)
            persisted = True
            unavailable = state["plan"].get("unavailableRepositories", [])
            _print_json(
                {
                    "runId": state["runId"],
                    "feature": state["feature"],
                    "status": state["status"],
                    "currentStage": state["workflow"]["currentStage"],
                    "stateFile": str(_store(args).state_path(state["runId"])),
                    "repositoryAccess": {
                        "status": "incomplete" if unavailable else "complete",
                        "unavailable": unavailable,
                        "action": (
                            "obtain-repository-access"
                            if any(
                                item.get("requiredByManifest") or item.get("requiredByDiscovery")
                                for item in unavailable
                            )
                            else "assess-repository-access"
                        )
                        if unavailable
                        else None,
                    },
                }
            )
        else:
            _print_json(plan.as_dict())
    finally:
        if not persisted:
            release_sdd_plan(plan)
    return 0


def _clear_gate(args: argparse.Namespace) -> int:
    evidence_path = Path(args.evidence).expanduser().resolve()
    if not evidence_path.is_file():
        raise HarnessError(f"gate evidence file does not exist: {evidence_path}")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessError(f"invalid gate evidence JSON: {exc}") from exc
    if not isinstance(evidence, dict):
        raise HarnessError("gate evidence must be a JSON object")
    workflow = _store(args).clear_current_gate(
        args.run_id,
        evidence,
        args.task,
        actor=args.actor,
    )
    _print_json(workflow)
    return 0


def _rewind(args: argparse.Namespace) -> int:
    workflow = _store(args).rewind_workflow(
        args.run_id,
        args.to_stage,
        actor=args.actor,
        reason=args.reason,
        task_id=args.task,
    )
    _print_json(workflow)
    return 0


def _program_name() -> str:
    return "signal-harness"


def parser() -> argparse.ArgumentParser:
    program = _program_name()
    root = argparse.ArgumentParser(prog=program)
    root.add_argument("--version", action="version", version=f"{program} {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser(
        "init", help="scaffold a distribution, repository profile, or standalone setup"
    )
    initializers = initialize.add_subparsers(dest="init_kind", required=True)

    distribution_init = initializers.add_parser(
        "distribution", help="create a product or team distribution repository"
    )
    distribution_init.add_argument("target")
    distribution_init.add_argument("--name", required=True)
    distribution_init.add_argument("--package")
    distribution_init.set_defaults(handler=_init_distribution)

    def add_repository_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("target")
        command.add_argument("--id", dest="repo_id", required=True)
        command.add_argument("--remote", required=True)
        command.add_argument("--forge", choices=("github", "gitlab"))
        command.add_argument("--detect-commands", action="store_true")

    repository_init = initializers.add_parser(
        "repository", help="seed a repository profile and optionally update a catalog"
    )
    add_repository_arguments(repository_init)
    repository_init.add_argument("--distribution")
    repository_init.set_defaults(handler=_init_repository)

    standalone_init = initializers.add_parser(
        "standalone", help="seed one repository with a local distribution and policy"
    )
    add_repository_arguments(standalone_init)
    standalone_init.set_defaults(handler=_init_standalone)

    discover = commands.add_parser("discover", help="discover repo-owned engineering context")
    discover.add_argument("--cwd", default=".")
    discover.add_argument("--profile", default=".signal-harness/repo.yaml")
    discover.set_defaults(handler=_discover)

    sdd_plan = commands.add_parser(
        "sdd",
        aliases=["sdd-plan"],
        help="derive a central SDD workflow and multi-repo plan from repository profiles",
    )
    sdd_plan.add_argument("feature")
    sdd_plan.add_argument("--cwd", default=".")
    sdd_plan.add_argument(
        "--sponsor",
        help="repository id that owns the feature manifest when invoked elsewhere",
    )
    sdd_plan.add_argument("--manifest")
    policy_source = sdd_plan.add_mutually_exclusive_group(required=True)
    policy_source.add_argument(
        "--policy",
        metavar="PATH",
        help="external policy-pack directory or catalog YAML",
    )
    policy_source.add_argument(
        "--distribution",
        metavar="NAME_OR_PATH",
        help="installed distribution name or distribution manifest path",
    )
    sdd_plan.add_argument("--repo", action="append", default=[], metavar="ID=PATH")
    sdd_plan.add_argument("--include", action="append", default=[], metavar="REPO_ID")
    sdd_plan.add_argument("--exclude", action="append", default=[], metavar="REPO_ID")
    sdd_plan.add_argument("--start", action="store_true")
    sdd_plan.add_argument("--mode", choices=("evaluation", "production"))
    sdd_plan.add_argument("--run-id")
    sdd_plan.add_argument("--state-root")
    sdd_plan.set_defaults(handler=_sdd_plan)

    for name, help_text, handler in (
        ("validate", "validate and resolve a multi-repo workspace", _validate),
        ("plan", "emit a deterministic multi-repo execution plan", _plan),
        ("route", "resolve a task to its explicit repository", _route),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("workspace")
        command.add_argument("--cwd", default=".")
        command.add_argument(
            "--repo",
            action="append",
            default=[],
            metavar="ID=PATH",
            help="map a portable repository id to a local checkout; repeatable",
        )
        if name == "route":
            command.add_argument("task")
        command.set_defaults(handler=handler)

    start = commands.add_parser("start", help="persist an immutable validated run plan")
    start.add_argument("workspace")
    start.add_argument("--cwd", default=".")
    start.add_argument("--repo", action="append", default=[], metavar="ID=PATH")
    start.add_argument("--run-id")
    start.add_argument("--mode", choices=("evaluation", "production"), default="evaluation")
    start.add_argument("--state-root")
    start.set_defaults(handler=_start)

    status = commands.add_parser("status", help="show persisted run state")
    status.add_argument("run_id")
    status.add_argument("--state-root")
    status.set_defaults(handler=_status)

    summary = commands.add_parser("summary", help="show concise run state and next actions")
    summary.add_argument("run_id")
    summary.add_argument("--state-root")
    summary.set_defaults(handler=_summary)

    engage = commands.add_parser(
        "engage-repository",
        help="resolve and lock one evidence-backed Stage 0 repository",
    )
    engage.add_argument("run_id")
    engage.add_argument("repository")
    engage.add_argument("--reason", required=True)
    engage.add_argument("--evidence", action="append", required=True)
    engage.add_argument("--override")
    engage.add_argument("--actor", default="system")
    engage.add_argument("--state-root")
    engage.set_defaults(handler=_engage_repository)

    migration = commands.add_parser(
        "migrate-author-permissions", help="apply reviewed permissions to a legacy discovery run"
    )
    migration.add_argument("run_id")
    migration.add_argument("--permissions", required=True)
    migration.add_argument("--actor", required=True)
    migration.add_argument("--reason", required=True)
    migration.add_argument("--state-root")
    migration.set_defaults(handler=_migrate_author_permissions)

    lock_context = commands.add_parser(
        "lock-context",
        help="SHA-lock exact source evidence found during targeted Stage 0 search",
    )
    lock_context.add_argument("run_id")
    lock_context.add_argument("repository")
    lock_context.add_argument("path", nargs="+")
    lock_context.add_argument("--actor", default="system")
    lock_context.add_argument("--reason", required=True)
    lock_context.add_argument("--state-root")
    lock_context.set_defaults(handler=_lock_context)

    for name, help_text, handler in (
        ("pause", "pause a non-terminal run", _pause),
        ("cancel", "cancel a non-terminal run", _cancel),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("run_id")
        command.add_argument("--actor", required=True)
        command.add_argument("--reason", required=True)
        command.add_argument("--state-root")
        command.set_defaults(handler=handler)

    resume_run_command = commands.add_parser("resume-run", help="resume a paused run")
    resume_run_command.add_argument("run_id")
    resume_run_command.add_argument("--actor", required=True)
    resume_run_command.add_argument("--state-root")
    resume_run_command.set_defaults(handler=_resume_run)

    recover = commands.add_parser("recover", help="diagnose run blockers and recovery actions")
    recover.add_argument("run_id")
    recover.add_argument("--state-root")
    recover.set_defaults(handler=_recover)

    ask = commands.add_parser("ask", help="persist a headless human question or approval")
    ask.add_argument("run_id")
    ask.add_argument("request_id")
    ask.add_argument("--kind", choices=("question", "approval"), required=True)
    ask.add_argument("--prompt", required=True)
    ask.add_argument("--channel", action="append", default=[])
    ask.add_argument("--state-root")
    ask.set_defaults(handler=_ask)

    resolve_human = commands.add_parser(
        "resolve-human", help="record a human response and resume when all waits clear"
    )
    resolve_human.add_argument("run_id")
    resolve_human.add_argument("request_id")
    resolve_human.add_argument("--actor", required=True)
    resolve_human.add_argument(
        "--decision", choices=("answered", "approved", "rejected"), required=True
    )
    resolve_human.add_argument("--response", required=True)
    resolve_human.add_argument("--state-root")
    resolve_human.set_defaults(handler=_resolve_human)

    external_id = commands.add_parser(
        "external-id", help="record an idempotent external system identifier"
    )
    external_id.add_argument("run_id")
    external_id.add_argument("system")
    external_id.add_argument("kind")
    external_id.add_argument("value")
    external_id.add_argument("--state-root")
    external_id.set_defaults(handler=_external_id)

    notify = commands.add_parser(
        "notify", help="queue a replay-safe Slack, work-item, or custom provider event"
    )
    notify.add_argument("run_id")
    notify.add_argument("event_id")
    notify.add_argument("--channel", required=True)
    notify.add_argument("--kind", required=True)
    notify.add_argument("--payload", required=True)
    notify.add_argument("--state-root")
    notify.set_defaults(handler=_notify)

    claim = commands.add_parser("claim", help="claim an active task repository lease")
    claim.add_argument("run_id")
    claim.add_argument("task")
    claim.add_argument("--owner", required=True)
    claim.add_argument("--ttl", type=int, default=3600)
    claim.add_argument("--state-root")
    claim.set_defaults(handler=_claim)

    release = commands.add_parser("release", help="release a task repository lease")
    release.add_argument("run_id")
    release.add_argument("task")
    release.add_argument("--owner", required=True)
    release.add_argument("--token", required=True)
    release.add_argument("--state-root")
    release.set_defaults(handler=_release)

    approve = commands.add_parser(
        "approve-command", help="approve a non-signal repository command for one run"
    )
    approve.add_argument("run_id")
    approve.add_argument("repository")
    approve.add_argument("command_id")
    approve.add_argument("--actor", required=True)
    approve.add_argument("--state-root")
    approve.set_defaults(handler=_approve_command)

    cost = commands.add_parser("record-cost", help="record usage against the run cost budget")
    cost.add_argument("run_id")
    cost.add_argument("--amount", type=float, required=True)
    cost.add_argument("--source", required=True)
    cost.add_argument("--state-root")
    cost.set_defaults(handler=_record_cost)

    finding = commands.add_parser("finding", help="record a CI or review finding")
    finding.add_argument("run_id")
    finding.add_argument("task")
    finding.add_argument("finding_id")
    finding.add_argument("--source", required=True)
    finding.add_argument(
        "--severity",
        choices=("critical", "high", "medium", "low", "info"),
        required=True,
    )
    finding.add_argument("--summary", required=True)
    finding.add_argument("--evidence", required=True)
    finding.add_argument("--command", action="append", default=[])
    finding.add_argument("--behavioral-fix", action="store_true")
    finding.add_argument("--state-root")
    finding.set_defaults(handler=_record_finding)

    resolve_finding_command = commands.add_parser(
        "resolve-finding", help="address, disagree with, or defer a finding"
    )
    resolve_finding_command.add_argument("run_id")
    resolve_finding_command.add_argument("task")
    resolve_finding_command.add_argument("finding_id")
    resolve_finding_command.add_argument(
        "--disposition", choices=("address", "disagree", "defer"), required=True
    )
    resolve_finding_command.add_argument("--actor", required=True)
    resolve_finding_command.add_argument("--rationale", required=True)
    resolve_finding_command.add_argument("--evidence")
    resolve_finding_command.add_argument("--regression-evidence")
    resolve_finding_command.add_argument("--state-root")
    resolve_finding_command.set_defaults(handler=_resolve_finding)

    delete = commands.add_parser("delete-run", help="delete terminal run state after confirmation")
    delete.add_argument("run_id")
    delete.add_argument("--confirm", required=True)
    delete.add_argument("--state-root")
    delete.set_defaults(handler=_delete_run)

    for name, help_text, handler in (
        ("compat-snapshot", "emit a portable compatibility snapshot", _compat_snapshot),
        ("compat-check", "compare a plan with a compatibility snapshot", _compat_check),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("workspace")
        command.add_argument("--cwd", default=".")
        command.add_argument("--repo", action="append", default=[], metavar="ID=PATH")
        if name == "compat-check":
            command.add_argument("--expected", required=True)
        command.set_defaults(handler=handler)

    for name, help_text, handler in (
        ("compat-run-snapshot", "emit a portable completed-run snapshot", _compat_run_snapshot),
        ("compat-run-check", "compare a run with a native-run snapshot", _compat_run_check),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("run_id")
        command.add_argument("--state-root")
        if name == "compat-run-check":
            command.add_argument("--expected", required=True)
        command.set_defaults(handler=handler)

    clear_gate = commands.add_parser(
        "clear-gate",
        aliases=["advance"],
        help="validate evidence and advance the current central workflow stage",
    )
    clear_gate.add_argument("run_id")
    clear_gate.add_argument("--evidence", required=True)
    clear_gate.add_argument("--task")
    clear_gate.add_argument("--actor", required=True)
    clear_gate.add_argument("--state-root")
    clear_gate.set_defaults(handler=_clear_gate)

    rewind = commands.add_parser(
        "rewind", help="audit and activate an approved backward workflow transition"
    )
    rewind.add_argument("run_id")
    rewind.add_argument("--to-stage", required=True)
    rewind.add_argument("--task", required=True)
    rewind.add_argument("--actor", required=True)
    rewind.add_argument("--reason", required=True)
    rewind.add_argument("--state-root")
    rewind.set_defaults(handler=_rewind)

    prepare = commands.add_parser(
        "prepare", help="create and verify a task-specific branch and worktree"
    )
    prepare.add_argument("run_id")
    prepare.add_argument("task")
    prepare.add_argument("--state-root")
    prepare.set_defaults(handler=_prepare)

    freeze = commands.add_parser(
        "freeze", help="freeze a clean committed task head for local evaluation handoff"
    )
    freeze.add_argument("run_id")
    freeze.add_argument("task")
    freeze.add_argument("--state-root")
    freeze.set_defaults(handler=_freeze)

    cleanup = commands.add_parser(
        "cleanup", help="remove a clean, terminal task worktree owned by this run"
    )
    cleanup.add_argument("run_id")
    cleanup.add_argument("task")
    cleanup.add_argument("--state-root")
    cleanup.set_defaults(handler=_cleanup)

    tdd_record = commands.add_parser(
        "tdd-record", help="record the next legal RED, GREEN, or REFACTOR phase"
    )
    tdd_record.add_argument("run_id")
    tdd_record.add_argument("task")
    tdd_record.add_argument("case")
    tdd_record.add_argument("phase", choices=("red", "green", "refactor"))
    tdd_record.add_argument("--status", choices=("pass", "fail"), required=True)
    tdd_record.add_argument("--signal", required=True)
    tdd_record.add_argument("--failure-kind", choices=("assertion", "missing-behavior"))
    tdd_record.add_argument("--sensitivity-mutation")
    tdd_record.add_argument("--sensitivity-signal")
    tdd_record.add_argument("--state-root")
    tdd_record.set_defaults(handler=_tdd_record)

    tdd_evidence = commands.add_parser(
        "tdd-evidence", help="emit canonical evidence for a completed persisted lifecycle"
    )
    tdd_evidence.add_argument("run_id")
    tdd_evidence.add_argument("task")
    tdd_evidence.add_argument("--state-root")
    tdd_evidence.set_defaults(handler=_tdd_evidence)

    next_command = commands.add_parser(
        "next", help="return the next scoped action for the current agent session"
    )
    next_command.add_argument("run_id")
    next_command.add_argument("--task")
    next_command.add_argument("--state-root")
    next_command.set_defaults(handler=_next)

    context = commands.add_parser("context", help="return locked cross-repository context")
    context.add_argument("run_id")
    context.add_argument("--task")
    context.add_argument("--state-root")
    context.set_defaults(handler=_context)

    ack_context = commands.add_parser(
        "ack-context", help="acknowledge the locked files required by a routed task"
    )
    ack_context.add_argument("run_id")
    ack_context.add_argument("task")
    ack_context.add_argument("--file", action="append", required=True, metavar="REPO:PATH")
    ack_context.add_argument("--state-root")
    ack_context.set_defaults(handler=_ack_context)

    record = commands.add_parser("record", help="record an artifact reference in the run journal")
    record.add_argument("run_id")
    record.add_argument("--kind", required=True)
    record.add_argument("--reference", required=True)
    record.add_argument("--task")
    record.add_argument("--state-root")
    record.set_defaults(handler=_record)

    handoff = commands.add_parser(
        "handoff-record",
        help="record immutable provenance for a declared cross-task artifact",
    )
    handoff.add_argument("run_id")
    handoff.add_argument("--task", required=True)
    handoff.add_argument("--artifact", required=True)
    handoff.add_argument("--uri", required=True)
    handoff.add_argument("--digest", required=True)
    handoff.add_argument("--source-revision", required=True)
    handoff.add_argument("--provider", required=True)
    handoff.add_argument("--state-root")
    handoff.set_defaults(handler=_record_handoff)

    verify = commands.add_parser("verify", help="run repository-owned commands by stable id")
    verify.add_argument("run_id")
    verify.add_argument("task")
    verify.add_argument("--command", action="append", required=True)
    verify.add_argument("--timeout", type=int, default=900)
    verify.add_argument(
        "--baseline-report",
        action="append",
        default=[],
        help="repository-relative finding report to compare with the locked base",
    )
    verify.add_argument("--report-format", choices=("pmd",))
    verify.add_argument("--state-root")
    verify.set_defaults(handler=_verify)

    publish = commands.add_parser("publish", help="push a clean, committed routed task branch")
    publish.add_argument("run_id")
    publish.add_argument("task")
    publish.add_argument("--state-root")
    publish.set_defaults(handler=_publish)

    open_pr = commands.add_parser("open-pr", help="create or find the routed task pull request")
    open_pr.add_argument("run_id")
    open_pr.add_argument("task")
    open_pr.add_argument("--title", required=True)
    open_pr.add_argument("--body-file")
    open_pr.add_argument("--draft", action="store_true")
    open_pr.add_argument("--state-root")
    open_pr.set_defaults(handler=_open_pr)

    resume = commands.add_parser("resume", help="refresh a task PR and advance its gate once")
    resume.add_argument("run_id")
    resume.add_argument("task")
    resume.add_argument("--state-root")
    resume.set_defaults(handler=_resume)

    pr_plan = commands.add_parser(
        "pr-plan", help="emit, but do not execute, an explicit PR creation command"
    )
    pr_plan.add_argument("run_id")
    pr_plan.add_argument("task")
    pr_plan.add_argument("--title", required=True)
    pr_plan.add_argument("--body-file")
    pr_plan.add_argument("--draft", action="store_true")
    pr_plan.add_argument("--state-root")
    pr_plan.set_defaults(handler=_pr_plan)

    return root


def main(argv: list[str] | None = None) -> int:
    try:
        raw_args = list(argv) if argv is not None else sys.argv[1:]
        args = parser().parse_args(raw_args)
        return args.handler(args)
    except (HarnessError, argparse.ArgumentTypeError) as exc:
        print(f"{_program_name()}: error: {redact_text(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
