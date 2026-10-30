from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import (
    load_yaml,
    require_list,
    require_mapping,
    require_string,
    string_list,
)
from .errors import ConfigurationError
from .models import API_VERSION, SUPPORTED_API_VERSIONS


@dataclass(frozen=True)
class EvidenceRequirements:
    caller_matrix: bool = False
    literal_audit: bool = False
    caller_reconciliation: bool = False
    placement_audit: bool = False
    check_derivation: bool = False
    test_traceability: bool = False
    test_case_categories: tuple[str, ...] = ()
    review_provenance: bool = False
    manifest_closure: bool = False
    surface_audit_kinds: tuple[str, ...] = ()
    preflight_checks: tuple[str, ...] = ()
    review_dimensions: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any], field_name: str) -> EvidenceRequirements:
        caller_matrix = data.get("callerMatrix", False)
        if not isinstance(caller_matrix, bool):
            raise ConfigurationError(f"{field_name}.callerMatrix must be a boolean")
        boolean_fields = {
            "literalAudit": "literal_audit",
            "callerReconciliation": "caller_reconciliation",
            "placementAudit": "placement_audit",
            "checkDerivation": "check_derivation",
            "testTraceability": "test_traceability",
            "reviewProvenance": "review_provenance",
            "manifestClosure": "manifest_closure",
        }
        booleans: dict[str, bool] = {}
        for external_name, attribute_name in boolean_fields.items():
            value = data.get(external_name, False)
            if not isinstance(value, bool):
                raise ConfigurationError(f"{field_name}.{external_name} must be a boolean")
            booleans[attribute_name] = value
        result = cls(
            caller_matrix=caller_matrix,
            **booleans,
            test_case_categories=string_list(
                data.get("testCaseCategories"), f"{field_name}.testCaseCategories"
            ),
            surface_audit_kinds=string_list(
                data.get("surfaceAuditKinds"), f"{field_name}.surfaceAuditKinds"
            ),
            preflight_checks=string_list(
                data.get("preflightChecks"), f"{field_name}.preflightChecks"
            ),
            review_dimensions=string_list(
                data.get("reviewDimensions"), f"{field_name}.reviewDimensions"
            ),
        )
        for name, values in (
            ("surfaceAuditKinds", result.surface_audit_kinds),
            ("preflightChecks", result.preflight_checks),
            ("reviewDimensions", result.review_dimensions),
            ("testCaseCategories", result.test_case_categories),
        ):
            if len(values) != len(set(values)):
                raise ConfigurationError(f"{field_name}.{name} must not contain duplicates")
        return result

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {}
        if self.caller_matrix:
            value["callerMatrix"] = True
        for name, enabled in (
            ("literalAudit", self.literal_audit),
            ("callerReconciliation", self.caller_reconciliation),
            ("placementAudit", self.placement_audit),
            ("checkDerivation", self.check_derivation),
            ("testTraceability", self.test_traceability),
            ("reviewProvenance", self.review_provenance),
            ("manifestClosure", self.manifest_closure),
        ):
            if enabled:
                value[name] = True
        if self.test_case_categories:
            value["testCaseCategories"] = list(self.test_case_categories)
        if self.surface_audit_kinds:
            value["surfaceAuditKinds"] = list(self.surface_audit_kinds)
        if self.preflight_checks:
            value["preflightChecks"] = list(self.preflight_checks)
        if self.review_dimensions:
            value["reviewDimensions"] = list(self.review_dimensions)
        return value


@dataclass(frozen=True)
class WorkflowGate:
    kind: str
    clears_when: str
    evidence_type: str | None = None
    waivable_in_evaluation: bool = False
    requirements: EvidenceRequirements = EvidenceRequirements()

    @classmethod
    def from_dict(cls, data: dict[str, Any], field_name: str) -> WorkflowGate:
        kind = require_string(data.get("kind"), f"{field_name}.kind")
        if kind not in {"automatic", "pr", "human"}:
            raise ConfigurationError(f"{field_name}.kind must be automatic, pr, or human")
        evidence_type = (
            require_string(data.get("evidenceType"), f"{field_name}.evidenceType")
            if data.get("evidenceType") is not None
            else None
        )
        supported = {
            "automatic": {None, "tdd", "knowledge"},
            "pr": {None, "specification", "task-plan", "review"},
            "human": {None, "integration", "smoke"},
        }
        if evidence_type not in supported[kind]:
            raise ConfigurationError(
                f"{field_name}.evidenceType {evidence_type!r} is not supported for {kind} gates"
            )
        waivable = data.get("waivableInEvaluation", False)
        if not isinstance(waivable, bool):
            raise ConfigurationError(f"{field_name}.waivableInEvaluation must be a boolean")
        if kind == "pr" and waivable:
            raise ConfigurationError(f"{field_name} PR gates cannot be waivable")
        requirements = EvidenceRequirements.from_dict(
            require_mapping(data.get("requirements", {}), f"{field_name}.requirements"),
            f"{field_name}.requirements",
        )
        specification_requirements = (
            requirements.caller_matrix
            or requirements.literal_audit
            or requirements.caller_reconciliation
            or requirements.placement_audit
            or requirements.surface_audit_kinds
            or requirements.preflight_checks
        )
        if specification_requirements and evidence_type != "specification":
            raise ConfigurationError(
                f"{field_name} caller/surface/preflight requirements require specification evidence"
            )
        if requirements.review_dimensions and evidence_type != "review":
            raise ConfigurationError(
                f"{field_name}.requirements.reviewDimensions requires review evidence"
            )
        if requirements.caller_reconciliation and not requirements.caller_matrix:
            raise ConfigurationError(
                f"{field_name}.requirements.callerReconciliation requires callerMatrix=true"
            )
        if (
            requirements.check_derivation
            or requirements.test_traceability
            or requirements.test_case_categories
        ) and evidence_type != "task-plan":
            raise ConfigurationError(
                f"{field_name}.requirements check/test requirements require task-plan evidence"
            )
        if requirements.review_provenance and evidence_type != "review":
            raise ConfigurationError(
                f"{field_name}.requirements.reviewProvenance requires review evidence"
            )
        if requirements.manifest_closure and evidence_type != "knowledge":
            raise ConfigurationError(
                f"{field_name}.requirements.manifestClosure requires knowledge evidence"
            )
        return cls(
            kind=kind,
            clears_when=require_string(data.get("clearsWhen"), f"{field_name}.clearsWhen"),
            evidence_type=evidence_type,
            waivable_in_evaluation=waivable,
            requirements=requirements,
        )

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "kind": self.kind,
            "clearsWhen": self.clears_when,
        }
        if self.evidence_type:
            value["evidenceType"] = self.evidence_type
        if self.waivable_in_evaluation:
            value["waivableInEvaluation"] = True
        requirements = self.requirements.as_dict()
        if requirements:
            value["requirements"] = requirements
        return value


@dataclass(frozen=True)
class WorkflowStage:
    id: str
    name: str
    objective: str
    activities: tuple[str, ...]
    gate: WorkflowGate
    per_task: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any], field_name: str) -> WorkflowStage:
        per_task = data.get("perTask", False)
        if not isinstance(per_task, bool):
            raise ConfigurationError(f"{field_name}.perTask must be a boolean")
        return cls(
            id=require_string(data.get("id"), f"{field_name}.id"),
            name=require_string(data.get("name"), f"{field_name}.name"),
            objective=require_string(data.get("objective"), f"{field_name}.objective"),
            activities=string_list(data.get("activities"), f"{field_name}.activities"),
            gate=WorkflowGate.from_dict(
                require_mapping(data.get("gate"), f"{field_name}.gate"),
                f"{field_name}.gate",
            ),
            per_task=per_task,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "objective": self.objective,
            "activities": list(self.activities),
            "perTask": self.per_task,
            "gate": self.gate.as_dict(),
        }


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    version: str
    stages: tuple[WorkflowStage, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowDefinition:
        if data.get("apiVersion") not in SUPPORTED_API_VERSIONS:
            raise ConfigurationError(
                f"workflow apiVersion must be one of {sorted(SUPPORTED_API_VERSIONS)}"
            )
        if data.get("kind") != "Workflow":
            raise ConfigurationError("workflow kind must be Workflow")
        stages_data = require_list(data.get("stages"), "stages")
        if not stages_data:
            raise ConfigurationError("workflow must declare at least one stage")
        stages = tuple(
            WorkflowStage.from_dict(require_mapping(item, "stages[]"), f"stages[{index}]")
            for index, item in enumerate(stages_data)
        )
        stage_ids = [stage.id for stage in stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ConfigurationError("workflow stage ids must be unique")
        return cls(
            id=require_string(data.get("id"), "id"),
            version=require_string(data.get("version"), "version"),
            stages=stages,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "apiVersion": API_VERSION,
            "kind": "Workflow",
            "id": self.id,
            "version": self.version,
            "stages": [stage.as_dict() for stage in self.stages],
        }


def load_workflow(path: Path) -> WorkflowDefinition:
    return WorkflowDefinition.from_dict(load_yaml(path))


def load_workflow_reference(reference: str, workspace_directory: Path) -> WorkflowDefinition:
    path = Path(reference)
    if path.suffix not in {".yaml", ".yml"}:
        raise ConfigurationError("workflow reference must identify a YAML file")
    return load_workflow((workspace_directory / path).resolve())
