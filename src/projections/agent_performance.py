from __future__ import annotations

from typing import Set

from .base import BaseProjection


class AgentPerformanceProjection(BaseProjection):
    @property
    def projection_name(self) -> str:
        return "AgentPerformanceLedger"

    @property
    def subscribed_event_types(self) -> Set[str]:
        return {"CreditAnalysisCompleted", "HumanReviewCompleted", "DecisionGenerated"}

    async def ensure_schema(self, conn) -> None:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS read_agent_performance (
                agent_id TEXT NOT NULL,
                model_version TEXT NOT NULL,
                total_analyses BIGINT NOT NULL DEFAULT 0,
                override_count BIGINT NOT NULL DEFAULT 0,
                override_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                avg_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (agent_id, model_version)
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE read_agent_performance
            ADD COLUMN IF NOT EXISTS override_rate DOUBLE PRECISION NOT NULL DEFAULT 0
            """
        )

    async def on_creditanalysiscompleted(self, conn, event) -> None:
        agent_id = event.payload["agent_id"]
        model_version = event.payload["model_version"]
        confidence = float(event.payload.get("confidence_score") or 0.0)
        await conn.execute(
            """
            INSERT INTO read_agent_performance (
                agent_id,
                model_version,
                total_analyses,
                override_count,
                override_rate,
                avg_confidence,
                updated_at
            )
            VALUES ($1, $2, 1, 0, 0, $3, $4)
            ON CONFLICT (agent_id, model_version) DO UPDATE
            SET total_analyses = read_agent_performance.total_analyses + 1,
                avg_confidence = (
                    (read_agent_performance.avg_confidence * read_agent_performance.total_analyses) + EXCLUDED.avg_confidence
                ) / (read_agent_performance.total_analyses + 1),
                override_rate = CASE
                    WHEN (read_agent_performance.total_analyses + 1) = 0 THEN 0
                    ELSE read_agent_performance.override_count::double precision / (read_agent_performance.total_analyses + 1)
                END,
                updated_at = EXCLUDED.updated_at
            """,
            agent_id,
            model_version,
            confidence,
            event.recorded_at,
        )

    async def on_decisiongenerated(self, conn, event) -> None:
        # No-op by design: subscription ensures this projection is decision-aware.
        return

    async def on_humanreviewcompleted(self, conn, event) -> None:
        final_decision = event.payload["final_decision"]
        await conn.execute(
            """
            WITH latest_decision AS (
                SELECT payload->>'recommendation' AS recommendation
                FROM events
                WHERE stream_id = $2
                  AND event_type = 'DecisionGenerated'
                ORDER BY stream_position DESC
                LIMIT 1
            ),
            credits AS (
                SELECT DISTINCT payload->>'agent_id' AS agent_id, payload->>'model_version' AS model_version
                FROM events
                WHERE stream_id = $2
                  AND event_type = 'CreditAnalysisCompleted'
            ),
            is_override AS (
                SELECT CASE
                    WHEN (SELECT recommendation FROM latest_decision) IS NULL THEN 0
                    WHEN (SELECT recommendation FROM latest_decision) <> $3 THEN 1
                    ELSE 0
                END AS bump
            )
            UPDATE read_agent_performance p
            SET override_count = p.override_count + (SELECT bump FROM is_override),
                override_rate = CASE
                    WHEN p.total_analyses = 0 THEN 0
                    ELSE (p.override_count + (SELECT bump FROM is_override))::double precision / p.total_analyses
                END,
                updated_at = $1
            FROM credits c
            WHERE p.agent_id = c.agent_id
              AND p.model_version = c.model_version
            """,
            event.recorded_at,
            f"loan-{event.payload['application_id']}",
            final_decision,
        )
