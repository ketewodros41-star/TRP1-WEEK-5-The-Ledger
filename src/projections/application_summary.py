# src/projections/application_summary.py
import json
from .base import BaseProjection
from ..models.events import StoredEvent

class ApplicationSummaryProjection(BaseProjection):
    @property
    def projection_name(self) -> str:
        return "ApplicationSummary"

    async def handle_events(self, conn, events: List[StoredEvent]):
        for event in events:
            # We only care about loan application events
            if not event.stream_id.startswith("loan-"):
                continue
            await self._handle_event(conn, event)

    async def on_applicationsubmitted(self, conn, event: StoredEvent):
        # Create or update summary table
        await conn.execute(
            """
            INSERT INTO read_application_summary (application_id, applicant_id, requested_amount, current_state, last_updated)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (application_id) DO UPDATE 
            SET current_state = EXCLUDED.current_state, last_updated = EXCLUDED.last_updated
            """,
            event.payload['application_id'],
            event.payload['applicant_id'],
            event.payload['requested_amount_usd'],
            "Submitted",
            event.recorded_at
        )

    async def on_creditanalysiscompleted(self, conn, event: StoredEvent):
        await conn.execute(
            """
            UPDATE read_application_summary 
            SET current_state = 'AnalysisComplete', 
                recommended_limit = $1,
                last_updated = $2
            WHERE application_id = $3
            """,
            event.payload['recommended_limit_usd'],
            event.recorded_at,
            event.payload['application_id']
        )

    async def on_decisiongenerated(self, conn, event: StoredEvent):
        await conn.execute(
            """
            UPDATE read_application_summary 
            SET current_state = $1, 
                last_updated = $2
            WHERE application_id = $3
            """,
            f"Decision:{event.payload['recommendation']}",
            event.recorded_at,
            event.payload['application_id']
        )

    async def on_applicationapproved(self, conn, event: StoredEvent):
        await conn.execute(
            """
            UPDATE read_application_summary 
            SET current_state = 'Approved', 
                approved_amount = $1,
                last_updated = $2
            WHERE application_id = $3
            """,
            event.payload['approved_amount_usd'],
            event.recorded_at,
            event.payload['application_id']
        )
