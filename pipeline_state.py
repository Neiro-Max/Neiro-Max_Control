from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from artifacts import ProjectSpec, build_artifacts
from execution_gate import evaluate_github, evaluate_railway
from github_adapter import GitHubPlan, build_plan as build_github_plan
from railway_adapter import RailwayPlan, build_plan as build_railway_plan


@dataclass(frozen=True)
class PublicationState:
    artifacts: Dict[str, str]
    github_gate: str
    github_message: str
    github_plan: Optional[GitHubPlan]
    railway_gate: str
    railway_message: str
    railway_plan: Optional[RailwayPlan]


def prepare_publication(*, project_id: int, name: str, description: str, project_type: str,
                        github_connected: bool, railway_connected: bool,
                        github_execution_status: Optional[str] = None) -> PublicationState:
    artifacts = build_artifacts(ProjectSpec(project_id, name, description, project_type))
    gh_gate = evaluate_github(artifacts=artifacts, connected=github_connected)
    gh_plan = None
    if gh_gate.status == "READY":
        gh_plan = build_github_plan(project_id=project_id, project_name=name, artifacts=artifacts)

    rail_input_status = github_execution_status or gh_gate.status
    rail_gate = evaluate_railway(github_status=rail_input_status, connected=railway_connected)
    rail_plan = None
    if rail_gate.status == "READY" and gh_plan is not None:
        rail_plan = build_railway_plan(
            project_id=project_id,
            repository=gh_plan.repository_name,
            branch=gh_plan.branch,
            artifacts=artifacts,
        )

    return PublicationState(
        artifacts=artifacts,
        github_gate=gh_gate.status,
        github_message=gh_gate.message,
        github_plan=gh_plan,
        railway_gate=rail_gate.status,
        railway_message=rail_gate.message,
        railway_plan=rail_plan,
    )
