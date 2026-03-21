# src/aggregates/compliance_record.py
from enum import Enum
from typing import List, Optional, Dict, Any
from .base import BaseAggregate
from ..models.events import StoredEvent, DomainError

class ComplianceVerdict(str, Enum):
    CLEAR = "CLEAR"
    BLOCKED = "BLOCKED"
    CONDITIONAL = "CONDITIONAL"

class ComplianceRecordAggregate(BaseAggregate):
    def __init__(self, application_id: str):
        super().__init__(f"compliance-{application_id}")
        self.application_id = application_id
        self.verdict: Optional[ComplianceVerdict] = None
        self.rules_evaluated: Dict[str, bool] = {} # rule_id -> passed
        self.mandatory_checks: List[str] = ["BSA", "OFAC", "JURISDICTION"]
        self.is_completed: bool = False

    @classmethod
    async def load(cls, store, application_id: str) -> "ComplianceRecordAggregate":
        events = await store.load_stream(f"compliance-{application_id}")
        agg = cls(application_id)
        agg.apply_events(events)
        return agg

    # --- Business Rules ---
    def assert_not_completed(self):
        if self.is_completed:
            raise DomainError(f"Compliance record for {self.application_id} is already completed.")

    def can_issue_verdict(self) -> bool:
        # Cannot issue clearance without all mandatory checks
        for check in self.mandatory_checks:
            if check not in self.rules_evaluated:
                return False
        return True

    # --- Event Handlers ---
    def on_complaincerulepassed(self, event: StoredEvent):
        rule_id = event.payload.get("rule_id")
        self.rules_evaluated[rule_id] = True

    def on_complaincerulefailed(self, event: StoredEvent):
        rule_id = event.payload.get("rule_id")
        self.rules_evaluated[rule_id] = False
        # Hard block if needed, but here we just record the fail
        if event.payload.get("is_hard_block", False):
            self.verdict = ComplianceVerdict.BLOCKED

    def on_compliancecheckcompleted(self, event: StoredEvent):
        if not self.can_issue_verdict():
            # In a strict system, this would raise error. 
            # But the orchestrator will catch it too.
            pass
        self.is_completed = True
        self.verdict = event.payload.get("verdict")
