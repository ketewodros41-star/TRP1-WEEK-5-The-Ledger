# src/integrity/regulatory_package.py
import json
from datetime import datetime
from typing import List, Dict, Any
from ..eventstore import EventStore

class RegulatoryPackageGenerator:
    """
    Phase 6 Bonus: Generates a human-auditable regulatory package.
    Aggregates data from multiple streams into a single JSON/PDF report.
    """
    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    async def generate_package(self, application_id: str) -> Dict[str, Any]:
        # 1. Gather all related streams
        loan_events = await self.event_store.load_stream(f"loan-{application_id}")
        compliance_events = await self.event_store.load_stream(f"compliance-{application_id}")
        
        # 2. Extract agent sessions mentioned in loan events
        agent_sessions = []
        for event in loan_events:
            if "session_id" in event.payload and "agent_id" in event.payload:
                sid = event.payload["session_id"]
                aid = event.payload["agent_id"]
                session_events = await self.event_store.load_stream(f"agent-{aid}-{sid}")
                agent_sessions.append({
                    "agent_id": aid,
                    "session_id": sid,
                    "events": [e.model_dump() for e in session_events]
                })

        # 3. Compile report
        report = {
            "report_id": f"REG-{application_id}-{int(datetime.now().timestamp())}",
            "generated_at": datetime.now().isoformat(),
            "application_id": application_id,
            "loan_lifecycle": [e.model_dump() for e in loan_events],
            "compliance_verdicts": [e.model_dump() for e in compliance_events],
            "agent_audit_trails": agent_sessions,
            "integrity_signature": "SHA256:TODO_HASH_OF_ALL"
        }
        
        return report
