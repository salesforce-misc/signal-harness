from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from ..signals import EngineeringSignal, SignalStatus


@dataclass(frozen=True)
class QualityGateResult:
    id: str
    status: SignalStatus
    summary: str
    signal_ids: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssuranceReport:
    repository: str
    gates: tuple[QualityGateResult, ...]

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(gate.status == SignalStatus.PASS for gate in self.gates)


class AssuranceCapability(Protocol):
    """Boundary implemented by AIAL-style investigation and quality engines."""

    id: str

    def evaluate(
        self,
        repository: str,
        signals: Iterable[EngineeringSignal],
    ) -> AssuranceReport: ...
