from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple


@dataclass(frozen=True)
class RailwayPlan:
    service_name: str
    repository: str
    branch: str
    required_variables: Tuple[str, ...]
    healthcheck_path: str


def infer_required_variables(files: Iterable[str], contents: Iterable[str]) -> Tuple[str, ...]:
    joined = "\n".join(contents)
    needed = []
    for name in ("BOT_TOKEN", "OPENAI_API_KEY", "DATABASE_URL"):
        if name in joined:
            needed.append(name)
    return tuple(needed)


def build_plan(*, project_id: int, repository: str, branch: str, artifacts: dict[str, str]) -> RailwayPlan:
    required = infer_required_variables(artifacts.keys(), artifacts.values())
    return RailwayPlan(
        service_name=f"project-{project_id}",
        repository=repository,
        branch=branch,
        required_variables=required,
        healthcheck_path="/api/health" if "server.py" in artifacts else "/",
    )
