from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List


FINAL = {"APPROVED", "REJECTED"}
EXEC_FINAL = {"SUCCESS", "FAILED", "BLOCKED"}


@dataclass
class ApprovalRecord:
    approval_id: int
    project_id: int
    action: str
    status: str = "PENDING"
    execution_status: str = "NOT_STARTED"
    audit: List[str] = field(default_factory=list)

    def decide(self, decision: str) -> bool:
        decision = decision.upper()
        if decision not in FINAL:
            raise ValueError("decision must be APPROVED or REJECTED")
        if self.status in FINAL:
            return False
        self.status = decision
        self.audit.append(self._entry(f"decision:{decision}"))
        return True

    def can_execute(self) -> bool:
        return self.status == "APPROVED" and self.execution_status not in {"RUNNING", "SUCCESS"}

    def start_execution(self) -> bool:
        if not self.can_execute():
            return False
        self.execution_status = "RUNNING"
        self.audit.append(self._entry("execution:RUNNING"))
        return True

    def finish_execution(self, status: str) -> None:
        status = status.upper()
        if status not in EXEC_FINAL:
            raise ValueError("invalid execution status")
        self.execution_status = status
        self.audit.append(self._entry(f"execution:{status}"))

    @staticmethod
    def _entry(event: str) -> str:
        return f"{datetime.now(timezone.utc).isoformat()} {event}"
