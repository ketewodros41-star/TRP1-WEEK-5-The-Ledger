from __future__ import annotations

from typing import Optional

from .base import BaseAggregate
from ..models.events import DomainError, StoredEvent


class AuditLedgerAggregate(BaseAggregate):
    def __init__(self, entity_type: str, entity_id: str):
        super().__init__(f"audit-{entity_type}-{entity_id}")
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.last_hash: Optional[str] = None
        self.tamper_detected: bool = False
        self.chain_broken: bool = False

    @classmethod
    async def load(cls, store, entity_type: str, entity_id: str) -> "AuditLedgerAggregate":
        agg = cls(entity_type, entity_id)
        events = await store.load_stream(agg.stream_id)
        agg.apply_events(events)
        return agg

    def on_auditintegritycheckrun(self, event: StoredEvent) -> None:
        if self.chain_broken and bool(event.payload.get("chain_valid", True)):
            raise DomainError("Audit chain is already broken and cannot transition back to valid without remediation")

        self.last_hash = event.payload.get("final_hash")
        self.tamper_detected = bool(event.payload.get("tamper_detected"))
        if self.tamper_detected:
            self.chain_broken = True

    def assert_chain_not_broken(self, next_chain_valid: bool) -> None:
        if self.chain_broken and next_chain_valid:
            raise DomainError("Audit chain previously detected tampering; cannot mark chain as valid")
