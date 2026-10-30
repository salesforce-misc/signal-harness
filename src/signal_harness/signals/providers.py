from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from ..models import RepositoryProfile, SignalSource
from .models import EngineeringSignal


class SignalProvider(Protocol):
    """Adapter boundary for CI, tests, observability, incidents, and AIAL."""

    id: str

    def collect(
        self,
        repository_root: Path,
        profile: RepositoryProfile,
        source: SignalSource,
    ) -> Iterable[EngineeringSignal]: ...
