from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from ..signals import EngineeringSignal


@dataclass(frozen=True)
class LearningCandidate:
    id: str
    summary: str
    signal_ids: tuple[str, ...]
    proposed_change: str
    confidence: float | None = None


class LearningCapability(Protocol):
    """Boundary for feedback memory and governed policy promotion."""

    id: str

    def propose(
        self,
        signals: Iterable[EngineeringSignal],
    ) -> Iterable[LearningCandidate]: ...
