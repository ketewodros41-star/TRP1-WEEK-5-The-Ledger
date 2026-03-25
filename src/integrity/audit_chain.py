from __future__ import annotations

import hashlib
import json
from typing import Iterable, List

from ..aggregates.audit_ledger import AuditLedgerAggregate
from ..eventstore import EventStore
from ..models.events import AuditIntegrityCheckRun, IntegrityCheckResult, StoredEvent


class AuditChain:
    @staticmethod
    def event_hash(event: StoredEvent) -> str:
        material = {
            "event_type": event.event_type,
            "stream_position": event.stream_position,
            "payload": event.payload,
            "metadata": event.metadata,
        }
        blob = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    @staticmethod
    def chain_hash(events: Iterable[StoredEvent]) -> str:
        current = ""
        for event in events:
            digest = AuditChain.event_hash(event)
            current = hashlib.sha256(f"{current}{digest}".encode("utf-8")).hexdigest()
        return current


async def run_integrity_check(store: EventStore, entity_type: str, entity_id: str) -> IntegrityCheckResult:
    target_stream_id = f"{entity_type}-{entity_id}"
    audit_stream_id = f"audit-{entity_type}-{entity_id}"

    target_events = await store.load_stream(target_stream_id)
    audit = await AuditLedgerAggregate.load(store, entity_type, entity_id)
    audit_events = await store.load_stream(audit_stream_id)

    previous_hash = None
    previous_tamper_detected = False
    for event in reversed(audit_events):
        if event.event_type == "AuditIntegrityCheckRun":
            previous_hash = event.payload.get("final_hash")
            previous_tamper_detected = bool(event.payload.get("tamper_detected"))
            break

    new_hash = AuditChain.chain_hash(target_events)
    tamper_detected = previous_tamper_detected or (previous_hash is not None and previous_hash != new_hash)
    chain_valid = not tamper_detected
    audit.assert_chain_not_broken(chain_valid)

    result = IntegrityCheckResult(
        chain_valid=chain_valid,
        tamper_detected=tamper_detected,
        checked_event_count=len(target_events),
        final_hash=new_hash,
    )

    check_event = AuditIntegrityCheckRun(
        chain_valid=result.chain_valid,
        tamper_detected=result.tamper_detected,
        checked_event_count=result.checked_event_count,
        final_hash=result.final_hash,
    )
    expected = await store.stream_version(audit_stream_id)
    await store.append(
        stream_id=audit_stream_id,
        events=[check_event],
        expected_version=expected,
        aggregate_type="audit",
    )

    return result
