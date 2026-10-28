from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class GateResult:
    status: str
    message: str


def evaluate_github(*, artifacts: Mapping[str, str], connected: bool) -> GateResult:
    if not connected:
        return GateResult("BLOCKED", "GitHub connection is not configured")
    if not artifacts:
        return GateResult("BLOCKED", "Project has no artifacts to publish")
    return GateResult("READY", "GitHub publication prerequisites are satisfied")


def evaluate_railway(*, github_status: Optional[str], connected: bool) -> GateResult:
    if not connected:
        return GateResult("BLOCKED", "Railway connection is not configured")
    if github_status not in {"READY", "SUCCESS"}:
        return GateResult("BLOCKED", "GitHub publication must be ready or successful first")
    return GateResult("READY", "Railway deployment prerequisites are satisfied")
