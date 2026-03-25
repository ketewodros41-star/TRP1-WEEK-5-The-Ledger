from __future__ import annotations

from enum import Enum
from typing import Dict, Optional

from .base import BaseAggregate
from ..models.events import DomainError, StoredEvent


class ComplianceVerdict(str, Enum):
    CLEAR = "CLEAR"
    BLOCKED = "BLOCKED"
    CONDITIONAL = "CONDITIONAL"


class ComplianceRecordAggregate(BaseAggregate):
    def __init__(self, application_id: str):
        super().__init__(f"compliance-{application_id}")
        self.application_id = application_id
        self.rules_evaluated: Dict[str, bool] = {}
        self.verdict: Optional[ComplianceVerdict] = None
        self.completed: bool = False

    @classmethod
    async def load(cls, store, application_id: str) -> "ComplianceRecordAggregate":
        agg = cls(application_id)
        events = await store.load_stream(agg.stream_id)
        agg.apply_events(events)
        return agg

    def assert_not_completed(self) -> None:
        if self.completed:
            raise DomainError("Compliance record already completed")

    def can_clear(self) -> bool:
        required = {"KYC", "AML", "SANCTIONS"}
        return required.issubset({k for k, v in self.rules_evaluated.items() if v})

    def on_compliancerulepassed(self, event: StoredEvent) -> None:
        self.rules_evaluated[event.payload["rule_id"]] = True

    def on_compliancerulefailed(self, event: StoredEvent) -> None:
        self.rules_evaluated[event.payload["rule_id"]] = False

    def on_compliancecheckcompleted(self, event: StoredEvent) -> None:
        self.completed = True
        self.verdict = ComplianceVerdict(event.payload["verdict"])
