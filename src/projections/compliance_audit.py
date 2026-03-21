# src/projections/compliance_audit.py
from .base import BaseProjection
from ..models.events import StoredEvent

class ComplianceAuditProjection(BaseProjection):
    @property
    def projection_name(self) -> str:
        return "ComplianceAuditView"

    async def handle_events(self, conn, events: List[StoredEvent]):
        for event in events:
            if event.event_type in ["ComplianceRulePassed", "ComplianceRuleFailed", "ComplianceCheckCompleted"]:
                await self._handle_event(conn, event)

    async def on_complaincerulepassed(self, conn, event: StoredEvent):
        await conn.execute(
            """
            INSERT INTO read_compliance_audit_logs (application_id, event_id, rule_id, status, details, recorded_at)
            VALUES ($1, $2, $3, 'PASSED', $4, $5)
            """,
            event.payload.get("application_id"),
            event.event_id,
            event.payload.get("rule_id"),
            json.dumps(event.payload.get("details", {})),
            event.recorded_at
        )

    async def on_complaincerulefailed(self, conn, event: StoredEvent):
        await conn.execute(
            """
            INSERT INTO read_compliance_audit_logs (application_id, event_id, rule_id, status, details, recorded_at)
            VALUES ($1, $2, $3, 'FAILED', $4, $5)
            """,
            event.payload.get("application_id"),
            event.event_id,
            event.payload.get("rule_id"),
            json.dumps(event.payload.get("details", {})),
            event.recorded_at
        )

    async def on_compliancecheckcompleted(self, conn, event: StoredEvent):
        # Update a summary record or just log the final verdict
        await conn.execute(
            """
            INSERT INTO read_compliance_verdicts (application_id, final_verdict, verified_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (application_id) DO UPDATE SET 
                final_verdict = EXCLUDED.final_verdict,
                verified_at = EXCLUDED.verified_at
            """,
            event.payload.get("application_id"),
            event.payload.get("verdict"),
            event.recorded_at
        )
