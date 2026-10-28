from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping


@dataclass(frozen=True)
class GitHubPlan:
    repository_name: str
    branch: str
    files: Dict[str, str]


def build_plan(*, project_id: int, project_name: str, artifacts: Mapping[str, str]) -> GitHubPlan:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in project_name).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    if not safe:
        safe = f"project-{project_id}"
    repo = f"neiro-project-{project_id}-{safe[:40]}"
    return GitHubPlan(repository_name=repo, branch="main", files=dict(artifacts))
