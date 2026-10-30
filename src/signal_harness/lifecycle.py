from __future__ import annotations

from typing import Any

from .errors import ConfigurationError
from .workflow import WorkflowDefinition, WorkflowStage


def initial_workflow_state(
    definition: WorkflowDefinition, tasks: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "id": definition.id,
        "version": definition.version,
        "currentStage": definition.stages[0].id,
        "status": "active",
        "stages": {
            stage.id: {
                "name": stage.name,
                "status": "active" if index == 0 else "pending",
                "perTask": stage.per_task,
                "gate": stage.gate.as_dict(),
                "evidence": None,
                "completedAt": None,
            }
            for index, stage in enumerate(definition.stages)
        },
        "tasks": initial_task_states(definition, tasks),
    }


def initial_task_states(
    definition: WorkflowDefinition, tasks: list[dict[str, Any]]
) -> dict[str, Any]:
    per_task = [stage for stage in definition.stages if stage.per_task]
    return {
        task["id"]: {
            "repo": task["repo"],
            "dependsOn": list(task.get("dependsOn", [])),
            "status": "pending",
            "currentStage": per_task[0].id if per_task else None,
            "stages": {
                stage.id: {
                    "status": "pending",
                    "evidence": None,
                    "completedAt": None,
                }
                for stage in per_task
            },
        }
        for task in tasks
    }


def validate_gate_evidence(
    stage: WorkflowStage,
    evidence: dict[str, Any],
    *,
    policy_mode: str = "evaluation",
) -> None:
    if policy_mode not in {"evaluation", "production"}:
        raise ConfigurationError(f"unsupported policy mode: {policy_mode}")
    if gate_evidence_outcome(stage, evidence, policy_mode=policy_mode) == "waived":
        return
    gate_kind = stage.gate.kind
    if gate_kind == "automatic":
        if evidence.get("status") != "pass":
            raise ConfigurationError(f"stage {stage.id} automatic gate requires status=pass")
        signals = evidence.get("signals")
        if not isinstance(signals, list) or not signals:
            raise ConfigurationError(f"stage {stage.id} automatic gate requires non-empty signals")
        if stage.gate.evidence_type == "tdd":
            validate_tdd_evidence(stage.id, evidence)
        elif stage.gate.evidence_type == "knowledge":
            _validate_knowledge_evidence(stage, evidence)
        return
    if gate_kind == "pr":
        if evidence.get("state") != "MERGED" or evidence.get("reviewDecision") != "APPROVED":
            raise ConfigurationError(
                f"stage {stage.id} PR gate requires state=MERGED and reviewDecision=APPROVED"
            )
        if not isinstance(evidence.get("url"), str) or not evidence["url"].strip():
            raise ConfigurationError(f"stage {stage.id} PR gate requires a URL")
        if policy_mode == "production" and evidence["url"].startswith("local://"):
            raise ConfigurationError(
                f"stage {stage.id} production PR gate requires hosted provider evidence"
            )
        if stage.gate.evidence_type == "specification":
            _validate_specification_evidence(stage, evidence)
        elif stage.gate.evidence_type == "task-plan":
            _validate_task_plan_evidence(stage, evidence)
        elif stage.gate.evidence_type == "review":
            _validate_review_evidence(stage, evidence)
        return
    if gate_kind == "human":
        verified_by = evidence.get("verifiedBy")
        marker = evidence.get("marker")
        if not isinstance(verified_by, str) or not verified_by.strip():
            raise ConfigurationError(f"stage {stage.id} human gate requires verifiedBy")
        if not isinstance(marker, str) or "human verified" not in marker.lower():
            raise ConfigurationError(
                f"stage {stage.id} human gate requires a human verified marker"
            )
        if stage.gate.evidence_type == "integration":
            _validate_integration_evidence(stage.id, evidence)
        elif stage.gate.evidence_type == "smoke":
            _validate_smoke_evidence(stage.id, evidence)
        return
    raise ConfigurationError(f"unsupported gate kind for stage {stage.id}: {gate_kind}")


def gate_evidence_outcome(
    stage: WorkflowStage,
    evidence: dict[str, Any],
    *,
    policy_mode: str = "evaluation",
) -> str:
    if evidence.get("outcome") != "waived":
        return "complete"
    if policy_mode == "production":
        raise ConfigurationError(f"stage {stage.id} cannot be waived in production mode")
    if stage.gate.kind == "pr":
        raise ConfigurationError(f"stage {stage.id} PR gate cannot be waived")
    if not stage.gate.waivable_in_evaluation:
        raise ConfigurationError(
            f"stage {stage.id} is not waivable in evaluation mode by workflow policy"
        )
    waiver = evidence.get("waiver")
    if not isinstance(waiver, dict):
        raise ConfigurationError(f"stage {stage.id} waiver requires waiver evidence")
    for field in ("reason", "authorizedBy", "scope"):
        if not _non_empty(waiver.get(field)):
            raise ConfigurationError(f"stage {stage.id} waiver requires {field}")
    return "waived"


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _meaningful(value: Any) -> bool:
    return _non_empty(value) and value.strip().lower() not in {
        "undefined",
        "null",
        "none",
        "n/a",
        "na",
    }


def _non_empty_strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_non_empty(item) for item in value)


def _validate_source_references(value: Any, field_name: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise ConfigurationError(f"{field_name} requires {qualifier} of source references")
    for index, item in enumerate(value):
        if (
            not isinstance(item, dict)
            or not _non_empty(item.get("repo"))
            or not _non_empty(item.get("path"))
        ):
            raise ConfigurationError(f"{field_name}[{index}] requires repo and path")


def _validate_specification_evidence(stage: WorkflowStage, evidence: dict[str, Any]) -> None:
    stage_id = stage.id
    specification = evidence.get("specification")
    if not isinstance(specification, dict):
        raise ConfigurationError(f"stage {stage_id} requires specification evidence")
    for field in ("artifact", "scope", "behavior", "errorHandling", "rollout"):
        if not _non_empty(specification.get(field)):
            raise ConfigurationError(f"specification evidence requires {field}")
    for field in ("repositoryBoundaries", "decisions"):
        if not _non_empty_strings(specification.get(field)):
            raise ConfigurationError(f"specification evidence requires non-empty {field}")
    interface_impact = specification.get("interfaceImpact")
    if not isinstance(interface_impact, dict):
        raise ConfigurationError("specification evidence requires interfaceImpact")
    if not _non_empty_strings(interface_impact.get("evaluatedRepositories")):
        raise ConfigurationError(
            "specification interfaceImpact requires non-empty evaluatedRepositories"
        )
    affected_interfaces = interface_impact.get("affectedInterfaces")
    if not isinstance(affected_interfaces, list) or not all(
        _non_empty(item) for item in affected_interfaces
    ):
        raise ConfigurationError(
            "specification interfaceImpact requires affectedInterfaces as a string list"
        )
    if not _non_empty(interface_impact.get("summary")):
        raise ConfigurationError("specification interfaceImpact requires summary")
    unavailable_repositories = interface_impact.get("unavailableRepositories", [])
    if not isinstance(unavailable_repositories, list):
        raise ConfigurationError(
            "specification interfaceImpact unavailableRepositories must be a list"
        )
    unavailable_ids: set[str] = set()
    for index, unavailable in enumerate(unavailable_repositories):
        if not isinstance(unavailable, dict):
            raise ConfigurationError(
                f"interfaceImpact.unavailableRepositories[{index}] must be an object"
            )
        repo_id = unavailable.get("id")
        if not _non_empty(repo_id) or repo_id in unavailable_ids:
            raise ConfigurationError(
                f"interfaceImpact.unavailableRepositories[{index}] requires a unique id"
            )
        assert isinstance(repo_id, str)
        unavailable_ids.add(repo_id)
        if unavailable.get("relevance") not in {"required", "not-required"}:
            raise ConfigurationError(
                f"unavailable repository {repo_id} relevance must be required or not-required"
            )
        if not _non_empty(unavailable.get("reason")):
            raise ConfigurationError(
                f"unavailable repository {repo_id} requires an evidence-backed reason"
            )
    affected_surfaces = specification.get("affectedSurfaces")
    if not isinstance(affected_surfaces, list) or not affected_surfaces:
        raise ConfigurationError("specification requires non-empty affectedSurfaces")
    surface_ids: set[str] = set()
    for index, surface in enumerate(affected_surfaces):
        if not isinstance(surface, dict):
            raise ConfigurationError(f"affectedSurfaces[{index}] must be an object")
        surface_id = surface.get("id")
        if not _non_empty(surface_id) or surface_id in surface_ids:
            raise ConfigurationError(f"affectedSurfaces[{index}] requires a unique id")
        assert isinstance(surface_id, str)
        surface_ids.add(surface_id)
        for field in ("kind", "ownerRepo"):
            if not _non_empty(surface.get(field)):
                raise ConfigurationError(f"affected surface {surface_id} requires {field}")
        _validate_source_references(surface.get("evidence"), f"affected surface {surface_id}")
        if stage.gate.requirements.surface_audit_kinds:
            if surface.get("criticality") not in {"mandatory", "optional"}:
                raise ConfigurationError(
                    f"affected surface {surface_id} requires criticality=mandatory or optional"
                )
            verification = surface.get("verification")
            if not isinstance(verification, dict):
                raise ConfigurationError(f"affected surface {surface_id} requires verification")
            checks = verification.get("requiredChecks")
            if not isinstance(checks, list) or not all(_non_empty(item) for item in checks):
                raise ConfigurationError(
                    f"affected surface {surface_id} verification requires requiredChecks list"
                )
            if len(checks) != len(set(checks)):
                raise ConfigurationError(
                    f"affected surface {surface_id} verification checks must be unique"
                )
            if not _non_empty(verification.get("rationale")):
                raise ConfigurationError(
                    f"affected surface {surface_id} verification requires rationale"
                )

    requirements = stage.gate.requirements
    if requirements.surface_audit_kinds:
        _validate_surface_audit(specification, requirements.surface_audit_kinds, surface_ids)
        _validate_precedent_audit(specification, surface_ids)
    if requirements.caller_matrix:
        caller_ids = _validate_caller_matrix(specification, surface_ids)
        if requirements.caller_reconciliation:
            _validate_caller_reconciliation(specification, caller_ids)
    else:
        caller_ids = set()
    if requirements.literal_audit:
        _validate_literal_audit(specification)
    if requirements.placement_audit:
        _validate_placement_audit(specification, caller_ids)
    if requirements.preflight_checks:
        _validate_repository_preflight(
            specification,
            requirements.preflight_checks,
            set(interface_impact["evaluatedRepositories"]),
        )

    interface_decisions = specification.get("interfaceDecisions")
    if not isinstance(interface_decisions, list):
        raise ConfigurationError("specification requires interfaceDecisions as a list")
    decision_ids: set[str] = set()
    for index, decision in enumerate(interface_decisions):
        if not isinstance(decision, dict):
            raise ConfigurationError(f"interfaceDecisions[{index}] must be an object")
        decision_id = decision.get("id")
        if not _non_empty(decision_id) or decision_id in decision_ids:
            raise ConfigurationError(f"interfaceDecisions[{index}] requires a unique id")
        assert isinstance(decision_id, str)
        decision_ids.add(decision_id)
        for field in ("kind", "ownerRepo", "identity", "compatibility"):
            if not _non_empty(decision.get(field)):
                raise ConfigurationError(f"interface decision {decision_id} requires {field}")
        _validate_source_references(
            decision.get("compatibilityPeers"),
            f"interface decision {decision_id}.compatibilityPeers",
            allow_empty=True,
        )
        if not _non_empty_strings(decision.get("consumersEvaluated")):
            raise ConfigurationError(
                f"interface decision {decision_id} requires non-empty consumersEvaluated"
            )
    if set(affected_interfaces) != decision_ids:
        raise ConfigurationError(
            "specification interfaceDecisions must exactly cover affectedInterfaces"
        )
    if specification.get("openQuestions") != []:
        raise ConfigurationError("specification evidence must resolve all openQuestions")
    source_audit = specification.get("sourceAudit")
    if not isinstance(source_audit, dict):
        raise ConfigurationError("specification evidence requires sourceAudit")
    if not _non_empty_strings(source_audit.get("consulted")):
        raise ConfigurationError("specification sourceAudit requires non-empty consulted")
    for field in ("missing", "delegationFindings"):
        if not isinstance(source_audit.get(field), list) or not all(
            _non_empty(item) for item in source_audit[field]
        ):
            raise ConfigurationError(f"specification sourceAudit requires {field} as a string list")
    if source_audit.get("unresolvedDelegations") != []:
        raise ConfigurationError("specification sourceAudit must resolve all unresolvedDelegations")


def _validate_surface_audit(
    specification: dict[str, Any], required_kinds: tuple[str, ...], surface_ids: set[str]
) -> None:
    audit = specification.get("surfaceAudit")
    if not isinstance(audit, list):
        raise ConfigurationError("specification requires surfaceAudit")
    by_kind: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(audit):
        if not isinstance(item, dict) or not _non_empty(item.get("kind")):
            raise ConfigurationError(f"surfaceAudit[{index}] requires kind")
        kind = item["kind"]
        if kind in by_kind:
            raise ConfigurationError(f"surfaceAudit has duplicate kind {kind}")
        by_kind[kind] = item
        if item.get("disposition") not in {"affected", "no-impact"}:
            raise ConfigurationError(
                f"surfaceAudit {kind} disposition must be affected or no-impact"
            )
        surfaces = item.get("surfaces")
        if not isinstance(surfaces, list) or not all(_non_empty(value) for value in surfaces):
            raise ConfigurationError(f"surfaceAudit {kind} requires surfaces as a list")
        if len(surfaces) != len(set(surfaces)):
            raise ConfigurationError(f"surfaceAudit {kind} surfaces must be unique")
        unknown = sorted(set(surfaces) - surface_ids)
        if unknown:
            raise ConfigurationError(f"surfaceAudit {kind} names unknown surfaces: {unknown}")
        if item["disposition"] == "affected" and not surfaces:
            raise ConfigurationError(f"surfaceAudit {kind} affected requires surfaces")
        if item["disposition"] == "no-impact" and surfaces:
            raise ConfigurationError(f"surfaceAudit {kind} no-impact requires empty surfaces")
        if not _non_empty(item.get("reason")):
            raise ConfigurationError(f"surfaceAudit {kind} requires reason")
        _validate_source_references(
            item.get("evidence"), f"surfaceAudit {kind}.evidence", allow_empty=False
        )
    missing = sorted(set(required_kinds) - set(by_kind))
    extra = sorted(set(by_kind) - set(required_kinds))
    if missing or extra:
        raise ConfigurationError(f"surfaceAudit kinds mismatch; missing={missing}, unknown={extra}")
    audited_surface_list = [surface for item in audit for surface in item.get("surfaces", [])]
    audited_surfaces = set(audited_surface_list)
    if audited_surfaces != surface_ids or len(audited_surface_list) != len(audited_surfaces):
        raise ConfigurationError(
            "surfaceAudit affected entries must cover every affected surface exactly once"
        )


def _validate_caller_matrix(specification: dict[str, Any], surface_ids: set[str]) -> set[str]:
    audit = specification.get("callerAudit")
    if not isinstance(audit, dict):
        raise ConfigurationError("specification requires callerAudit")
    if not _non_empty_strings(audit.get("searches")) or not _non_empty(audit.get("conclusion")):
        raise ConfigurationError("callerAudit requires searches and conclusion")
    callers = specification.get("callerMatrix")
    if not isinstance(callers, list):
        raise ConfigurationError("specification requires callerMatrix as a list")
    seen: set[str] = set()
    for index, caller in enumerate(callers):
        if not isinstance(caller, dict):
            raise ConfigurationError(f"callerMatrix[{index}] must be an object")
        caller_id = caller.get("id")
        if not _non_empty(caller_id) or caller_id in seen:
            raise ConfigurationError(f"callerMatrix[{index}] requires a unique id")
        assert isinstance(caller_id, str)
        seen.add(caller_id)
        for field in (
            "repo",
            "path",
            "symbol",
            "surface",
            "behavior",
            "errorHandling",
            "observability",
        ):
            if not _non_empty(caller.get(field)):
                raise ConfigurationError(f"caller {caller_id} requires {field}")
        if caller["surface"] not in surface_ids:
            raise ConfigurationError(
                f"caller {caller_id} names unknown surface {caller['surface']}"
            )
        _validate_source_references(caller.get("evidence"), f"caller {caller_id}.evidence")
    return seen


def _validate_caller_reconciliation(specification: dict[str, Any], caller_ids: set[str]) -> None:
    audit = specification["callerAudit"]
    inventory = audit.get("inventory")
    if not isinstance(inventory, list):
        raise ConfigurationError("callerAudit requires inventory for caller reconciliation")
    inventoried: set[str] = set()
    for index, item in enumerate(inventory):
        if not isinstance(item, dict):
            raise ConfigurationError(f"callerAudit.inventory[{index}] must be an object")
        caller = item.get("caller")
        if not _non_empty(caller) or caller in inventoried:
            raise ConfigurationError(f"callerAudit.inventory[{index}] requires a unique caller")
        assert isinstance(caller, str)
        inventoried.add(caller)
        for field in ("repo", "path", "symbol"):
            if not _non_empty(item.get(field)):
                raise ConfigurationError(f"caller inventory {caller} requires {field}")
        _validate_source_references(item.get("evidence"), f"caller inventory {caller}.evidence")
    if inventoried != caller_ids:
        raise ConfigurationError(
            "caller inventory mismatch; "
            f"missing={sorted(caller_ids - inventoried)}, "
            f"unknown={sorted(inventoried - caller_ids)}"
        )


def _validate_literal_audit(specification: dict[str, Any]) -> None:
    audit = specification.get("literalAudit")
    if not isinstance(audit, dict):
        raise ConfigurationError("specification requires literalAudit")
    if not _non_empty_strings(audit.get("searches")):
        raise ConfigurationError("literalAudit requires searches")
    literals = audit.get("literals")
    if not isinstance(literals, list):
        raise ConfigurationError("literalAudit requires literals as a list")
    seen: set[str] = set()
    for index, item in enumerate(literals):
        if not isinstance(item, dict):
            raise ConfigurationError(f"literalAudit.literals[{index}] must be an object")
        literal_id = item.get("id")
        if not _non_empty(literal_id) or literal_id in seen:
            raise ConfigurationError(f"literalAudit.literals[{index}] requires a unique id")
        assert isinstance(literal_id, str)
        seen.add(literal_id)
        for field in ("kind", "value"):
            if not _non_empty(item.get(field)):
                raise ConfigurationError(f"literal {literal_id} requires {field}")
        disposition = item.get("disposition")
        if disposition not in {"preserved", "superseded"}:
            raise ConfigurationError(
                f"literal {literal_id} disposition must be preserved or superseded"
            )
        if disposition == "superseded":
            for field in ("replacement", "reason"):
                if not _non_empty(item.get(field)):
                    raise ConfigurationError(f"superseded literal {literal_id} requires {field}")
        _validate_source_references(item.get("evidence"), f"literal {literal_id}.evidence")
    if audit.get("unaccounted") != []:
        raise ConfigurationError("literalAudit must have no unaccounted literals")
    if not _non_empty(audit.get("conclusion")):
        raise ConfigurationError("literalAudit requires conclusion")


def _validate_placement_audit(specification: dict[str, Any], caller_ids: set[str]) -> None:
    audit = specification.get("placementAudit")
    if not isinstance(audit, list):
        raise ConfigurationError("specification requires placementAudit as a list")
    seen: set[str] = set()
    for index, item in enumerate(audit):
        if not isinstance(item, dict):
            raise ConfigurationError(f"placementAudit[{index}] must be an object")
        placement_id = item.get("id")
        if not _non_empty(placement_id) or placement_id in seen:
            raise ConfigurationError(f"placementAudit[{index}] requires a unique id")
        assert isinstance(placement_id, str)
        seen.add(placement_id)
        for field in ("repo", "path", "symbol", "effect", "rationale"):
            if not _non_empty(item.get(field)):
                raise ConfigurationError(f"placement {placement_id} requires {field}")
        if not isinstance(item.get("reusable"), bool):
            raise ConfigurationError(f"placement {placement_id} requires reusable boolean")
        candidates = item.get("candidateCallers")
        affected = item.get("affectedCallers")
        excluded = item.get("excludedCallers")
        if not isinstance(candidates, list) or not all(_non_empty(value) for value in candidates):
            raise ConfigurationError(f"placement {placement_id} requires candidateCallers")
        if not isinstance(affected, list) or not all(_non_empty(value) for value in affected):
            raise ConfigurationError(f"placement {placement_id} requires affectedCallers")
        if not isinstance(excluded, list):
            raise ConfigurationError(f"placement {placement_id} requires excludedCallers")
        excluded_ids: set[str] = set()
        for excluded_item in excluded:
            if (
                not isinstance(excluded_item, dict)
                or not _non_empty(excluded_item.get("caller"))
                or not _non_empty(excluded_item.get("reason"))
            ):
                raise ConfigurationError(
                    f"placement {placement_id} excluded callers require caller and reason"
                )
            excluded_ids.add(excluded_item["caller"])
            _validate_source_references(
                excluded_item.get("evidence"),
                f"placement {placement_id} excluded caller evidence",
            )
        candidate_set = set(candidates)
        classified = set(affected) | excluded_ids
        unknown = sorted(candidate_set - caller_ids)
        if unknown:
            raise ConfigurationError(f"placement {placement_id} names unknown caller(s): {unknown}")
        if candidate_set != classified or set(affected) & excluded_ids:
            raise ConfigurationError(
                f"placement {placement_id} must classify every candidate caller exactly once"
            )
        if item["reusable"] and not candidates:
            raise ConfigurationError(
                f"reusable placement {placement_id} requires at least one candidate caller"
            )
        _validate_source_references(item.get("evidence"), f"placement {placement_id}.evidence")


def _validate_precedent_audit(specification: dict[str, Any], surface_ids: set[str]) -> None:
    audit = specification.get("precedentAudit")
    if not isinstance(audit, list):
        raise ConfigurationError("specification requires precedentAudit")
    by_surface: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(audit):
        if not isinstance(item, dict) or not _non_empty(item.get("surface")):
            raise ConfigurationError(f"precedentAudit[{index}] requires surface")
        surface = item["surface"]
        if surface in by_surface:
            raise ConfigurationError(f"precedentAudit has duplicate surface {surface}")
        by_surface[surface] = item
        if not _non_empty_strings(item.get("searches")):
            raise ConfigurationError(f"precedentAudit {surface} requires searches")
        decision = item.get("decision")
        if decision not in {"reuse", "adapt", "no-precedent"}:
            raise ConfigurationError(
                f"precedentAudit {surface} decision must be reuse, adapt, or no-precedent"
            )
        selected = item.get("selected")
        _validate_source_references(
            selected, f"precedentAudit {surface}.selected", allow_empty=True
        )
        if decision in {"reuse", "adapt"} and not selected:
            raise ConfigurationError(
                f"precedentAudit {surface} {decision} requires a selected precedent"
            )
        if decision == "no-precedent" and selected:
            raise ConfigurationError(
                f"precedentAudit {surface} no-precedent requires empty selected"
            )
        if not _non_empty(item.get("rationale")):
            raise ConfigurationError(f"precedentAudit {surface} requires rationale")
    missing = sorted(surface_ids - set(by_surface))
    extra = sorted(set(by_surface) - surface_ids)
    if missing or extra:
        raise ConfigurationError(
            f"precedentAudit surfaces mismatch; missing={missing}, unknown={extra}"
        )


def _validate_repository_preflight(
    specification: dict[str, Any],
    required_checks: tuple[str, ...],
    evaluated_repositories: set[str],
) -> None:
    preflight = specification.get("repositoryPreflight")
    if not isinstance(preflight, list):
        raise ConfigurationError("specification requires repositoryPreflight")
    by_repo: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(preflight):
        if not isinstance(item, dict) or not _non_empty(item.get("repo")):
            raise ConfigurationError(f"repositoryPreflight[{index}] requires repo")
        repo = item["repo"]
        if repo in by_repo:
            raise ConfigurationError(f"repositoryPreflight has duplicate repo {repo}")
        by_repo[repo] = item
        checks = item.get("checks")
        if not isinstance(checks, list):
            raise ConfigurationError(f"repositoryPreflight {repo} requires checks")
        by_check: dict[str, dict[str, Any]] = {}
        for check in checks:
            if not isinstance(check, dict) or not _non_empty(check.get("id")):
                raise ConfigurationError(f"repositoryPreflight {repo} check requires id")
            check_id = check["id"]
            if check_id in by_check:
                raise ConfigurationError(
                    f"repositoryPreflight {repo} has duplicate check {check_id}"
                )
            by_check[check_id] = check
            if check.get("status") not in {"pass", "not-applicable"}:
                raise ConfigurationError(
                    f"repositoryPreflight {repo}:{check_id} must pass or be not-applicable"
                )
            if not _meaningful(check.get("evidence")):
                raise ConfigurationError(
                    f"repositoryPreflight {repo}:{check_id} requires meaningful evidence"
                )
            if check["status"] == "not-applicable" and not _non_empty(check.get("reason")):
                raise ConfigurationError(
                    f"repositoryPreflight {repo}:{check_id} not-applicable requires reason"
                )
        missing = sorted(set(required_checks) - set(by_check))
        extra = sorted(set(by_check) - set(required_checks))
        if missing or extra:
            raise ConfigurationError(
                f"repositoryPreflight {repo} checks mismatch; missing={missing}, unknown={extra}"
            )
    missing_repos = sorted(evaluated_repositories - set(by_repo))
    extra_repos = sorted(set(by_repo) - evaluated_repositories)
    if missing_repos or extra_repos:
        raise ConfigurationError(
            "repositoryPreflight repositories mismatch; "
            f"missing={missing_repos}, unknown={extra_repos}"
        )


def _validate_task_plan_evidence(stage: WorkflowStage, evidence: dict[str, Any]) -> None:
    stage_id = stage.id
    task_plan = evidence.get("taskPlan")
    if not isinstance(task_plan, dict) or not _non_empty(task_plan.get("artifact")):
        raise ConfigurationError(f"stage {stage_id} requires a taskPlan artifact")
    tasks = task_plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ConfigurationError("taskPlan evidence requires non-empty tasks")
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or not _non_empty(task.get("id")):
            raise ConfigurationError("taskPlan tasks require ids")
        if task["id"] in seen:
            raise ConfigurationError(f"taskPlan has duplicate task {task['id']}")
        seen.add(task["id"])
        if not _non_empty(task.get("repo")) or not _non_empty(task.get("requirement")):
            raise ConfigurationError(f"taskPlan task {task['id']} requires repo and requirement")
        if (
            not isinstance(task.get("dependsOn"), list)
            or not isinstance(task.get("testCases"), list)
            or not task["testCases"]
        ):
            raise ConfigurationError(
                f"taskPlan task {task['id']} requires dependsOn and ordered testCases"
            )
        if not _non_empty_strings(task.get("surfaces")):
            raise ConfigurationError(f"taskPlan task {task['id']} requires non-empty surfaces")
        callers = task.get("callers", [])
        if not isinstance(callers, list) or not all(_non_empty(item) for item in callers):
            raise ConfigurationError(f"taskPlan task {task['id']} callers must be a string list")
        if len(callers) != len(set(callers)):
            raise ConfigurationError(
                f"taskPlan task {task['id']} callers must not contain duplicates"
            )
        for field in ("produces", "consumes"):
            if not isinstance(task.get(field, []), list):
                raise ConfigurationError(f"taskPlan task {task['id']} {field} must be a list")
        produced: set[str] = set()
        for product in task.get("produces", []):
            if (
                not isinstance(product, dict)
                or not _non_empty(product.get("id"))
                or not _non_empty(product.get("kind"))
                or product["id"] in produced
            ):
                raise ConfigurationError(
                    f"taskPlan task {task['id']} produced artifacts require unique id and kind"
                )
            produced.add(product["id"])
        consumed: set[tuple[str, str]] = set()
        for product in task.get("consumes", []):
            if not isinstance(product, dict):
                raise ConfigurationError(
                    f"taskPlan task {task['id']} consumed artifacts must be objects"
                )
            from_task = product.get("fromTask")
            artifact = product.get("artifact")
            if not _non_empty(from_task) or not _non_empty(artifact):
                raise ConfigurationError(
                    f"taskPlan task {task['id']} consumed artifacts require unique "
                    "fromTask and artifact"
                )
            assert isinstance(from_task, str) and isinstance(artifact, str)
            key = (from_task, artifact)
            if key in consumed:
                raise ConfigurationError(
                    f"taskPlan task {task['id']} consumed artifacts require unique "
                    "fromTask and artifact"
                )
            consumed.add(key)
        seen_cases: set[str] = set()
        for index, case in enumerate(task["testCases"], start=1):
            if not isinstance(case, dict):
                raise ConfigurationError(
                    f"taskPlan task {task['id']} testCases[{index}] must be an object"
                )
            case_id = case.get("id")
            if not _non_empty(case_id) or case_id in seen_cases:
                raise ConfigurationError(
                    f"taskPlan task {task['id']} testCases[{index}] requires a unique id"
                )
            assert isinstance(case_id, str)
            seen_cases.add(case_id)
            if case.get("kind") not in {"delta", "guard"}:
                raise ConfigurationError(
                    f"taskPlan task {task['id']} test case {case_id} kind must be delta or guard"
                )
            if not _non_empty(case.get("requirement")):
                raise ConfigurationError(
                    f"taskPlan task {task['id']} test case {case_id} requires requirement"
                )
            if stage.gate.requirements.test_traceability and not _non_empty_strings(
                case.get("traces")
            ):
                raise ConfigurationError(
                    f"taskPlan task {task['id']} test case {case_id} requires traces"
                )
    if stage.gate.requirements.check_derivation:
        validate_check_derivation(task_plan, tasks)
    if stage.gate.requirements.test_case_categories:
        _validate_test_coverage(task_plan, tasks, stage.gate.requirements.test_case_categories)
    coverage = task_plan.get("surfaceCoverage")
    if not isinstance(coverage, list) or not coverage:
        raise ConfigurationError("taskPlan evidence requires non-empty surfaceCoverage")
    covered: set[str] = set()
    for index, item in enumerate(coverage):
        if not isinstance(item, dict) or not _non_empty(item.get("surface")):
            raise ConfigurationError(f"surfaceCoverage[{index}] requires surface")
        surface_id = item["surface"]
        if surface_id in covered:
            raise ConfigurationError(f"surfaceCoverage has duplicate surface {surface_id}")
        covered.add(surface_id)
        disposition = item.get("disposition")
        if disposition not in {"task", "no-change", "deferred"}:
            raise ConfigurationError(
                f"surfaceCoverage {surface_id} disposition must be task, no-change, or deferred"
            )
        if disposition == "task" and not _non_empty(item.get("task")):
            raise ConfigurationError(f"surfaceCoverage {surface_id} requires task")
        if disposition == "no-change":
            if not _non_empty(item.get("reason")):
                raise ConfigurationError(f"surfaceCoverage {surface_id} requires reason")
            _validate_source_references(item.get("evidence"), f"surfaceCoverage {surface_id}")
        if disposition == "deferred":
            for field in ("owner", "reason", "compatibilityStrategy", "blockingGate"):
                if not _non_empty(item.get(field)):
                    raise ConfigurationError(
                        f"surfaceCoverage {surface_id} deferred decision requires {field}"
                    )
    caller_coverage = task_plan.get("callerCoverage", [])
    if not isinstance(caller_coverage, list):
        raise ConfigurationError("taskPlan callerCoverage must be a list")
    covered_callers: set[str] = set()
    for index, item in enumerate(caller_coverage):
        if not isinstance(item, dict) or not _non_empty(item.get("caller")):
            raise ConfigurationError(f"callerCoverage[{index}] requires caller")
        caller_id = item["caller"]
        if caller_id in covered_callers:
            raise ConfigurationError(f"callerCoverage has duplicate caller {caller_id}")
        covered_callers.add(caller_id)
        disposition = item.get("disposition")
        if disposition not in {"task", "no-change", "deferred"}:
            raise ConfigurationError(
                f"callerCoverage {caller_id} disposition must be task, no-change, or deferred"
            )
        if disposition == "task" and not _non_empty(item.get("task")):
            raise ConfigurationError(f"callerCoverage {caller_id} requires task")
        if disposition == "no-change":
            if not _non_empty(item.get("reason")):
                raise ConfigurationError(f"callerCoverage {caller_id} requires reason")
            _validate_source_references(item.get("evidence"), f"callerCoverage {caller_id}")
        if disposition == "deferred":
            for field in ("owner", "reason", "compatibilityStrategy", "blockingGate"):
                if not _non_empty(item.get(field)):
                    raise ConfigurationError(
                        f"callerCoverage {caller_id} deferred decision requires {field}"
                    )


def validate_check_derivation(task_plan: dict[str, Any], tasks: list[dict[str, Any]]) -> None:
    derivations = task_plan.get("checkDerivation")
    if not isinstance(derivations, list):
        raise ConfigurationError("taskPlan requires checkDerivation")
    tasks_by_id = {task["id"]: task for task in tasks}
    by_task: dict[str, dict[str, Any]] = {}
    for entry in derivations:
        if not isinstance(entry, dict) or not _non_empty(entry.get("task")):
            raise ConfigurationError("checkDerivation entries require task")
        task_id = entry["task"]
        if task_id in by_task:
            raise ConfigurationError(f"checkDerivation has duplicate task {task_id}")
        by_task[task_id] = entry
        checks = entry.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ConfigurationError(f"checkDerivation {task_id} requires checks")
        seen: set[str] = set()
        required: set[str] = set()
        for check in checks:
            if not isinstance(check, dict) or not _non_empty(check.get("id")):
                raise ConfigurationError(f"checkDerivation {task_id} checks require id")
            check_id = check["id"]
            if check_id in seen:
                raise ConfigurationError(
                    f"checkDerivation {task_id} has duplicate check {check_id}"
                )
            seen.add(check_id)
            if not _non_empty(check.get("command")):
                raise ConfigurationError(f"checkDerivation {task_id}:{check_id} requires command")
            if not isinstance(check.get("executable"), bool):
                raise ConfigurationError(
                    f"checkDerivation {task_id}:{check_id} requires executable boolean"
                )
            applicable = check.get("applicable", True)
            if not isinstance(applicable, bool):
                raise ConfigurationError(
                    f"checkDerivation {task_id}:{check_id} requires applicable boolean"
                )
            disposition = check.get("disposition")
            if disposition not in {"required", "not-applicable"}:
                raise ConfigurationError(
                    f"checkDerivation {task_id}:{check_id} requires disposition"
                )
            if check["executable"] and applicable and disposition != "required":
                raise ConfigurationError(
                    f"locally executable check {task_id}:{check_id} must be required"
                )
            if disposition == "not-applicable" and not _non_empty(check.get("reason")):
                raise ConfigurationError(
                    f"checkDerivation {task_id}:{check_id} not-applicable requires reason"
                )
            if not _non_empty_strings(check.get("sources")):
                raise ConfigurationError(f"checkDerivation {task_id}:{check_id} requires sources")
            _validate_source_references(
                check.get("evidence"), f"checkDerivation {task_id}:{check_id}.evidence"
            )
            if disposition == "required":
                required.add(check_id)
        declared = set(tasks_by_id.get(task_id, {}).get("requiredChecks", []))
        if declared != required:
            raise ConfigurationError(
                f"checkDerivation {task_id} required checks mismatch; "
                f"missing={sorted(declared - required)}, unknown={sorted(required - declared)}"
            )
    missing = sorted(set(tasks_by_id) - set(by_task))
    unknown = sorted(set(by_task) - set(tasks_by_id))
    if missing or unknown:
        raise ConfigurationError(
            f"checkDerivation tasks mismatch; missing={missing}, unknown={unknown}"
        )


def _validate_test_coverage(
    task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    required_categories: tuple[str, ...],
) -> None:
    coverage = task_plan.get("testCoverage")
    if not isinstance(coverage, list):
        raise ConfigurationError("taskPlan requires testCoverage")
    tasks_by_id = {task["id"]: task for task in tasks}
    by_task: dict[str, dict[str, Any]] = {}
    for entry in coverage:
        if not isinstance(entry, dict) or not _non_empty(entry.get("task")):
            raise ConfigurationError("testCoverage entries require task")
        task_id = entry["task"]
        if task_id in by_task:
            raise ConfigurationError(f"testCoverage has duplicate task {task_id}")
        by_task[task_id] = entry
        categories = entry.get("categories")
        if not isinstance(categories, list):
            raise ConfigurationError(f"testCoverage {task_id} requires categories")
        by_category: dict[str, dict[str, Any]] = {}
        known_cases = {case["id"] for case in tasks_by_id.get(task_id, {}).get("testCases", [])}
        for category in categories:
            if not isinstance(category, dict) or not _non_empty(category.get("id")):
                raise ConfigurationError(f"testCoverage {task_id} categories require id")
            category_id = category["id"]
            if category_id in by_category:
                raise ConfigurationError(
                    f"testCoverage {task_id} has duplicate category {category_id}"
                )
            by_category[category_id] = category
            cases = category.get("testCases", [])
            if cases:
                if not _non_empty_strings(cases):
                    raise ConfigurationError(
                        f"testCoverage {task_id}:{category_id} testCases must be non-empty strings"
                    )
                unknown_cases = sorted(set(cases) - known_cases)
                if unknown_cases:
                    raise ConfigurationError(
                        f"testCoverage {task_id}:{category_id} names unknown test cases: "
                        f"{unknown_cases}"
                    )
            else:
                if category.get("disposition") != "not-applicable":
                    raise ConfigurationError(
                        f"testCoverage {task_id}:{category_id} requires testCases or "
                        "disposition=not-applicable"
                    )
                if not _non_empty(category.get("reason")):
                    raise ConfigurationError(
                        f"testCoverage {task_id}:{category_id} not-applicable requires reason"
                    )
                _validate_source_references(
                    category.get("evidence"),
                    f"testCoverage {task_id}:{category_id}.evidence",
                )
        missing_categories = sorted(set(required_categories) - set(by_category))
        unknown_categories = sorted(set(by_category) - set(required_categories))
        if missing_categories or unknown_categories:
            raise ConfigurationError(
                f"testCoverage {task_id} categories mismatch; "
                f"missing={missing_categories}, unknown={unknown_categories}"
            )
    missing_tasks = sorted(set(tasks_by_id) - set(by_task))
    unknown_tasks = sorted(set(by_task) - set(tasks_by_id))
    if missing_tasks or unknown_tasks:
        raise ConfigurationError(
            f"testCoverage tasks mismatch; missing={missing_tasks}, unknown={unknown_tasks}"
        )


def _validate_review_evidence(stage: WorkflowStage, evidence: dict[str, Any]) -> None:
    stage_id = stage.id
    review = evidence.get("review")
    if not isinstance(review, dict) or not _non_empty(review.get("reviewer")):
        raise ConfigurationError(f"stage {stage_id} review evidence requires reviewer")
    criteria = review.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ConfigurationError(f"stage {stage_id} review evidence requires criteria")
    for item in criteria:
        if (
            not isinstance(item, dict)
            or not _non_empty(item.get("id"))
            or item.get("status") != "pass"
            or not _non_empty_strings(item.get("evidence"))
        ):
            raise ConfigurationError(
                f"stage {stage_id} review criteria require id, status=pass, and evidence"
            )
    surfaces = review.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        raise ConfigurationError(f"stage {stage_id} review evidence requires surfaces")
    for item in surfaces:
        if (
            not isinstance(item, dict)
            or not _non_empty(item.get("id"))
            or item.get("disposition") not in {"changed", "no-change"}
            or not _non_empty_strings(item.get("evidence"))
        ):
            raise ConfigurationError(
                f"stage {stage_id} review surfaces require id, disposition, and evidence"
            )
    privacy = review.get("privacy")
    if (
        not isinstance(privacy, dict)
        or privacy.get("status") != "pass"
        or not _non_empty_strings(privacy.get("evidence"))
    ):
        raise ConfigurationError(
            f"stage {stage_id} review evidence requires a passing privacy check"
        )
    callers = review.get("callers", [])
    if not isinstance(callers, list):
        raise ConfigurationError(f"stage {stage_id} review callers must be a list")
    for item in callers:
        if (
            not isinstance(item, dict)
            or not _non_empty(item.get("id"))
            or item.get("disposition") not in {"changed", "no-change"}
            or not _non_empty_strings(item.get("evidence"))
        ):
            raise ConfigurationError(
                f"stage {stage_id} review callers require id, disposition, and evidence"
            )
    required_dimensions = stage.gate.requirements.review_dimensions
    if required_dimensions:
        readiness = review.get("productionReadiness")
        if not isinstance(readiness, list):
            raise ConfigurationError(f"stage {stage_id} review requires productionReadiness")
        by_id: dict[str, dict[str, Any]] = {}
        for item in readiness:
            if not isinstance(item, dict) or not _non_empty(item.get("id")):
                raise ConfigurationError(f"stage {stage_id} productionReadiness entries require id")
            item_id = item["id"]
            if item_id in by_id:
                raise ConfigurationError(
                    f"stage {stage_id} productionReadiness has duplicate {item_id}"
                )
            by_id[item_id] = item
            if item.get("status") not in {"pass", "not-applicable"}:
                raise ConfigurationError(
                    f"productionReadiness {item_id} must pass or be not-applicable"
                )
            if not _non_empty_strings(item.get("evidence")):
                raise ConfigurationError(f"productionReadiness {item_id} requires evidence")
            if item["status"] == "not-applicable" and not _non_empty(item.get("reason")):
                raise ConfigurationError(
                    f"productionReadiness {item_id} not-applicable requires reason"
                )
        missing = sorted(set(required_dimensions) - set(by_id))
        extra = sorted(set(by_id) - set(required_dimensions))
        if missing or extra:
            raise ConfigurationError(
                f"productionReadiness dimensions mismatch; missing={missing}, unknown={extra}"
            )
    if stage.gate.requirements.review_provenance:
        provenance = review.get("provenance")
        if not isinstance(provenance, dict):
            raise ConfigurationError(f"stage {stage_id} review requires provenance")
        if provenance.get("kind") not in {
            "self",
            "independent-agent",
            "hosted-human",
            "ci",
        }:
            raise ConfigurationError(f"stage {stage_id} review provenance requires valid kind")
        for field in ("reviewer", "implementationAuthor"):
            if not _non_empty(provenance.get(field)):
                raise ConfigurationError(f"stage {stage_id} review provenance requires {field}")
        if not isinstance(provenance.get("independent"), bool):
            raise ConfigurationError(
                f"stage {stage_id} review provenance requires independent boolean"
            )
        if not _non_empty_strings(provenance.get("evidence")):
            raise ConfigurationError(f"stage {stage_id} review provenance requires evidence")
        claim = review.get("assuranceClaim")
        if claim not in {"candidate", "verified"}:
            raise ConfigurationError(
                f"stage {stage_id} review requires assuranceClaim=candidate or verified"
            )
        if provenance["kind"] == "self" and claim == "verified":
            raise ConfigurationError("self review cannot claim verified assurance")
        if claim == "verified" and not provenance["independent"]:
            raise ConfigurationError("verified assurance requires independent review")
        if provenance["kind"] == "self" and (
            provenance["independent"]
            or provenance["reviewer"] != provenance["implementationAuthor"]
        ):
            raise ConfigurationError(
                "self review provenance must identify the implementation author"
            )
        if provenance["independent"] and (
            provenance["reviewer"] == provenance["implementationAuthor"]
        ):
            raise ConfigurationError(
                f"stage {stage_id} independent review must use a different reviewer"
            )


def _validate_integration_evidence(stage_id: str, evidence: dict[str, Any]) -> None:
    for field in ("signals", "repositories", "interfaces"):
        if not _non_empty_strings(evidence.get(field)):
            raise ConfigurationError(
                f"stage {stage_id} integration evidence requires non-empty {field}"
            )


def _validate_smoke_evidence(stage_id: str, evidence: dict[str, Any]) -> None:
    scenarios = evidence.get("scenarios")
    if not isinstance(scenarios, list):
        raise ConfigurationError(f"stage {stage_id} smoke evidence requires scenarios")
    by_kind = {item.get("kind"): item for item in scenarios if isinstance(item, dict)}
    for kind in ("happy", "failure", "compatibility", "rollout"):
        scenario = by_kind.get(kind)
        if (
            not isinstance(scenario, dict)
            or scenario.get("status") != "pass"
            or not _non_empty(scenario.get("evidence"))
        ):
            raise ConfigurationError(
                f"stage {stage_id} smoke evidence requires passing {kind} scenario"
            )


def _validate_knowledge_evidence(stage: WorkflowStage, evidence: dict[str, Any]) -> None:
    stage_id = stage.id
    if not _non_empty_strings(evidence.get("signals")):
        raise ConfigurationError(f"stage {stage_id} knowledge evidence requires non-empty signals")
    if not _non_empty_strings(evidence.get("artifacts")):
        raise ConfigurationError(
            f"stage {stage_id} knowledge evidence requires non-empty artifacts"
        )
    if not isinstance(evidence.get("learningCandidates"), list) or not all(
        _non_empty(item) for item in evidence["learningCandidates"]
    ):
        raise ConfigurationError(
            f"stage {stage_id} knowledge evidence requires learningCandidates as a string list"
        )
    if stage.gate.requirements.manifest_closure:
        closure = evidence.get("manifestClosure")
        if not isinstance(closure, dict):
            raise ConfigurationError(
                f"stage {stage_id} knowledge evidence requires manifestClosure"
            )
        if not _non_empty(closure.get("manifest")):
            raise ConfigurationError("manifestClosure requires manifest")
        if closure.get("status") != "complete":
            raise ConfigurationError("manifestClosure requires status=complete")
        if not _non_empty_strings(closure.get("evidence")):
            raise ConfigurationError("manifestClosure requires evidence")


def validate_tdd_evidence(stage_id: str, evidence: dict[str, Any]) -> None:
    test_cases = evidence.get("testCases")
    if not isinstance(test_cases, list) or not test_cases:
        raise ConfigurationError(f"stage {stage_id} TDD gate requires non-empty testCases")
    seen: set[str] = set()
    for index, case in enumerate(test_cases, start=1):
        if not isinstance(case, dict):
            raise ConfigurationError(f"testCases[{index}] must be an object")
        case_id = case.get("id")
        if not _non_empty(case_id) or case_id in seen:
            raise ConfigurationError(f"testCases[{index}] requires a unique non-empty id")
        assert isinstance(case_id, str)
        seen.add(case_id)
        if not _non_empty(case.get("requirement")):
            raise ConfigurationError(f"test case {case_id} requires requirement traceability")
        kind = case.get("kind")
        if kind not in {"delta", "guard"}:
            raise ConfigurationError(f"test case {case_id} kind must be delta or guard")
        lifecycle = case.get("lifecycle")
        if (
            not isinstance(lifecycle, list)
            or not all(isinstance(item, dict) for item in lifecycle)
            or [item.get("phase") for item in lifecycle]
            != [
                "red",
                "green",
                "refactor",
            ]
        ):
            raise ConfigurationError(
                f"test case {case_id} lifecycle must be ordered RED, GREEN, REFACTOR"
            )
        red, green, refactor = lifecycle
        if red.get("status") != "fail" or red.get("failureKind") not in {
            "assertion",
            "missing-behavior",
        }:
            raise ConfigurationError(
                f"test case {case_id} RED must be an observed behavioral assertion failure"
            )
        if not _meaningful(red.get("signal")):
            raise ConfigurationError(f"test case {case_id} RED requires a signal reference")
        for phase in (green, refactor):
            if phase.get("status") != "pass" or not _meaningful(phase.get("signal")):
                raise ConfigurationError(
                    f"test case {case_id} {phase.get('phase')} requires passing signal evidence"
                )
        if kind == "guard":
            sensitivity = case.get("sensitivity")
            if (
                not isinstance(sensitivity, dict)
                or not _meaningful(sensitivity.get("mutation"))
                or sensitivity.get("status") != "fail"
                or not _meaningful(sensitivity.get("signal"))
            ):
                raise ConfigurationError(
                    f"guard test case {case_id} requires observed sensitivity evidence"
                )


def next_stage_id(definition: WorkflowDefinition, completed_stage: str) -> str | None:
    ids = [stage.id for stage in definition.stages]
    try:
        index = ids.index(completed_stage)
    except ValueError as exc:
        raise ConfigurationError(f"unknown workflow stage: {completed_stage}") from exc
    return ids[index + 1] if index + 1 < len(ids) else None


def per_task_stages(definition: WorkflowDefinition) -> tuple[WorkflowStage, ...]:
    return tuple(stage for stage in definition.stages if stage.per_task)


def stage_after_task_phase(definition: WorkflowDefinition) -> WorkflowStage | None:
    task_stage_ids = {stage.id for stage in per_task_stages(definition)}
    if not task_stage_ids:
        return None
    last_index = max(
        index for index, stage in enumerate(definition.stages) if stage.id in task_stage_ids
    )
    return definition.stages[last_index + 1] if last_index + 1 < len(definition.stages) else None
