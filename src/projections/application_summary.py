from __future__ import annotations

from typing import Set

from .base import BaseProjection


class ApplicationSummaryProjection(BaseProjection):
    @property
    def projection_name(self) -> str:
        return "ApplicationSummary"

    @property
    def subscribed_event_types(self) -> Set[str]:
        return {
            "ApplicationSubmitted",
            "CreditAnalysisCompleted",
            "DecisionGenerated",
            "ComplianceCheckCompleted",
            "ApplicationApproved",
            "ApplicationDeclined",
        }

    async def ensure_schema(self, conn) -> None:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS read_application_summary (
                application_id TEXT PRIMARY KEY,
                applicant_id TEXT NOT NULL,
                requested_amount NUMERIC NOT NULL,
                current_state TEXT NOT NULL,
                latest_recommendation TEXT,
                confidence_score DOUBLE PRECISION,
                recommended_limit NUMERIC,
                approved_amount NUMERIC,
                compliance_verdict TEXT,
                last_updated TIMESTAMPTZ NOT NULL
            )
            """
        )

    async def on_applicationsubmitted(self, conn, event) -> None:
        await conn.execute(
            """
            INSERT INTO read_application_summary (
                application_id, applicant_id, requested_amount, current_state, last_updated
            ) VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (application_id) DO UPDATE
            SET applicant_id = EXCLUDED.applicant_id,
                requested_amount = EXCLUDED.requested_amount,
                current_state = EXCLUDED.current_state,
                last_updated = EXCLUDED.last_updated
            """,
            event.payload["application_id"],
            event.payload["applicant_id"],
            event.payload["requested_amount_usd"],
            "Submitted",
            event.recorded_at,
        )

    async def on_creditanalysiscompleted(self, conn, event) -> None:
        await conn.execute(
            """
            UPDATE read_application_summary
            SET current_state = 'AnalysisComplete',
                recommended_limit = $1,
                confidence_score = $2,
                last_updated = $3
            WHERE application_id = $4
            """,
            event.payload.get("recommended_limit_usd"),
            event.payload.get("confidence_score"),
            event.recorded_at,
            event.payload["application_id"],
        )

    async def on_compliancecheckcompleted(self, conn, event) -> None:
        await conn.execute(
            """
            UPDATE read_application_summary
            SET compliance_verdict = $1,
                current_state = 'PendingDecision',
                last_updated = $2
            WHERE application_id = $3
            """,
            event.payload["verdict"],
            event.recorded_at,
            event.payload["application_id"],
        )

    async def on_decisiongenerated(self, conn, event) -> None:
        await conn.execute(
            """
            UPDATE read_application_summary
            SET current_state = $1,
                latest_recommendation = $2,
                confidence_score = $3,
                last_updated = $4
            WHERE application_id = $5
            """,
            "ApprovedPendingHuman" if event.payload["recommendation"] == "APPROVE" else "DeclinedPendingHuman",
            event.payload["recommendation"],
            event.payload["confidence_score"],
            event.recorded_at,
            event.payload["application_id"],
        )

    async def on_applicationapproved(self, conn, event) -> None:
        await conn.execute(
            """
            UPDATE read_application_summary
            SET current_state = 'FinalApproved',
                approved_amount = $1,
                last_updated = $2
            WHERE application_id = $3
            """,
            event.payload["approved_amount_usd"],
            event.recorded_at,
            event.payload["application_id"],
        )

    async def on_applicationdeclined(self, conn, event) -> None:
        await conn.execute(
            """
            UPDATE read_application_summary
            SET current_state = 'FinalDeclined',
                last_updated = $1
            WHERE application_id = $2
            """,
            event.recorded_at,
            event.payload["application_id"],
        )
