# src/integrity/audit_chain.py
import hashlib
import json
from typing import List, Optional
from ..models.events import StoredEvent

class AuditChain:
    """
    Implements a SHA-256 hash chain for the AuditLedger.
    Each event's integrity depends on the previous event's hash.
    """
    @staticmethod
    def calculate_hash(event: StoredEvent, previous_hash: Optional[str]) -> str:
        data = {
            "event_id": str(event.event_id),
            "stream_id": event.stream_id,
            "stream_position": event.stream_position,
            "payload": event.payload,
            "previous_hash": previous_hash
        }
        event_bytes = json.dumps(data, sort_keys=True).encode('utf-8')
        return hashlib.sha256(event_bytes).hexdigest()

    @staticmethod
    def verify_chain(events: List[StoredEvent]) -> bool:
        last_hash = None
        for event in events:
            # In a real system, the hash would be stored in the event's metadata or payload
            stored_hash = event.metadata.get("integrity_hash")
            calculated_hash = AuditChain.calculate_hash(event, last_hash)
            
            if stored_hash and stored_hash != calculated_hash:
                return False
            last_hash = calculated_hash
        return True
