# src/aggregates/audit_ledger.py
from typing import List, Optional
from .base import BaseAggregate
from ..models.events import StoredEvent, DomainError

class AuditLedgerAggregate(BaseAggregate):
    def __init__(self, entity_type: str, entity_id: str):
        super().__init__(f"audit-{entity_type}-{entity_id}")
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.event_ids: List[str] = []
        self.last_hash: Optional[str] = None

    @classmethod
    async def load(cls, store, entity_type: str, entity_id: str) -> "AuditLedgerAggregate":
        events = await store.load_stream(f"audit-{entity_type}-{entity_id}")
        agg = cls(entity_type, entity_id)
        agg.apply_events(events)
        return agg

    # --- Business Rules ---
    def assert_append_only(self):
        # Already enforced by store, but we can have domain checks here
        pass

    # --- Event Handlers ---
    def on_auditintegritycheckrun(self, event: StoredEvent):
        # We store the latest result of the integrity check
        self.last_hash = event.payload.get("integrity_hash")
        # We can also track which events were verified if needed
        pass
