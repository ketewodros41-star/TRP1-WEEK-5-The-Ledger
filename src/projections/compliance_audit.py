from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional, Set

from .base import BaseProjection


class ComplianceAuditProjection(BaseProjection):
    @property
    def projection_name(self) -> str:
        return "ComplianceAuditView"

    @property
    def subscribed_event_types(self) -> Set[str]:
        return {"ComplianceRulePassed", "ComplianceRuleFailed", "ComplianceCheckCompleted"}

    async def ensure_schema(self, conn) -> None:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS read_compliance_audit_logs (
                id BIGSERIAL PRIMARY KEY,
                application_id TEXT NOT NULL,
                event_id UUID NOT NULL,
                event_type TEXT NOT NULL,
                rule_id TEXT,
                status TEXT,
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                recorded_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_read_compliance_audit_event_id
            ON read_compliance_audit_logs(event_id)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS read_compliance_verdicts (
                application_id TEXT PRIMARY KEY,
                final_verdict TEXT NOT NULL,
                verified_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS read_compliance_audit_snapshots (
                snapshot_id BIGSERIAL PRIMARY KEY,
                built_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                source_event_count BIGINT NOT NULL
            )
            """
        )

    async def on_compliancerulepassed(self, conn, event) -> None:
        await conn.execute(
            """
            INSERT INTO read_compliance_audit_logs (application_id, event_id, event_type, rule_id, status, details, recorded_at)
            VALUES ($1, $2, $3, $4, 'PASSED', $5::jsonb, $6)
            ON CONFLICT (event_id) DO NOTHING
            """,
            event.payload["application_id"],
            event.event_id,
            event.event_type,
            event.payload.get("rule_id"),
            json.dumps(event.payload.get("details", {})),
            event.recorded_at,
        )

    async def on_compliancerulefailed(self, conn, event) -> None:
        await conn.execute(
            """
            INSERT INTO read_compliance_audit_logs (application_id, event_id, event_type, rule_id, status, details, recorded_at)
            VALUES ($1, $2, $3, $4, 'FAILED', $5::jsonb, $6)
            ON CONFLICT (event_id) DO NOTHING
            """,
            event.payload["application_id"],
            event.event_id,
            event.event_type,
            event.payload.get("rule_id"),
            json.dumps({"reason": event.payload.get("failure_reason")}),
            event.recorded_at,
        )

    async def on_compliancecheckcompleted(self, conn, event) -> None:
        await conn.execute(
            """
            INSERT INTO read_compliance_audit_logs (application_id, event_id, event_type, rule_id, status, details, recorded_at)
            VALUES ($1, $2, $3, NULL, 'VERDICT', $4::jsonb, $5)
            ON CONFLICT (event_id) DO NOTHING
            """,
            event.payload["application_id"],
            event.event_id,
            event.event_type,
            json.dumps({"verdict": event.payload["verdict"]}),
            event.recorded_at,
        )
        await conn.execute(
            """
            INSERT INTO read_compliance_verdicts (application_id, final_verdict, verified_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (application_id) DO UPDATE
            SET final_verdict = EXCLUDED.final_verdict,
                verified_at = EXCLUDED.verified_at
            """,
            event.payload["application_id"],
            event.payload["verdict"],
            event.recorded_at,
        )

    async def get_compliance_at(self, conn, application_id: str, timestamp: datetime) -> List[Dict[str, object]]:
        rows = await conn.fetch(
            """
            SELECT application_id, event_type, rule_id, status, details, recorded_at
            FROM read_compliance_audit_logs
            WHERE application_id = $1 AND recorded_at <= $2
            ORDER BY recorded_at ASC, id ASC
            """,
            application_id,
            timestamp,
        )
        return [dict(row) for row in rows]

    async def rebuild_from_scratch(self, conn, events: List[object]) -> None:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS read_compliance_audit_logs_rebuild (
                id BIGSERIAL PRIMARY KEY,
                application_id TEXT NOT NULL,
                event_id UUID NOT NULL,
                event_type TEXT NOT NULL,
                rule_id TEXT,
                status TEXT,
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                recorded_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        await conn.execute("TRUNCATE read_compliance_audit_logs_rebuild RESTART IDENTITY")
        for event in events:
            if event.event_type not in self.subscribed_event_types:
                continue
            status = "PASSED" if event.event_type == "ComplianceRulePassed" else "FAILED"
            details = event.payload.get("details", {})
            if event.event_type == "ComplianceRuleFailed":
                details = {"reason": event.payload.get("failure_reason")}
            if event.event_type == "ComplianceCheckCompleted":
                status = "VERDICT"
                details = {"verdict": event.payload["verdict"]}
            await conn.execute(
                """
                INSERT INTO read_compliance_audit_logs_rebuild (application_id, event_id, event_type, rule_id, status, details, recorded_at)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                """,
                event.payload["application_id"],
                event.event_id,
                event.event_type,
                event.payload.get("rule_id"),
                status,
                json.dumps(details),
                event.recorded_at,
            )
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO read_compliance_audit_logs (application_id, event_id, event_type, rule_id, status, details, recorded_at)
                SELECT application_id, event_id, event_type, rule_id, status, details, recorded_at
                FROM read_compliance_audit_logs_rebuild
                ON CONFLICT (event_id) DO UPDATE SET
                    application_id = EXCLUDED.application_id,
                    event_type = EXCLUDED.event_type,
                    rule_id = EXCLUDED.rule_id,
                    status = EXCLUDED.status,
                    details = EXCLUDED.details,
                    recorded_at = EXCLUDED.recorded_at
                """
            )
            await conn.execute(
                """
                DELETE FROM read_compliance_audit_logs live
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM read_compliance_audit_logs_rebuild snap
                    WHERE snap.event_id = live.event_id
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO read_compliance_verdicts (application_id, final_verdict, verified_at)
                SELECT application_id,
                       (details->>'verdict') AS final_verdict,
                       recorded_at
                FROM read_compliance_audit_logs_rebuild
                WHERE status = 'VERDICT'
                ON CONFLICT (application_id) DO UPDATE SET
                    final_verdict = EXCLUDED.final_verdict,
                    verified_at = EXCLUDED.verified_at
                """
            )
            await conn.execute(
                """
                INSERT INTO read_compliance_audit_snapshots (source_event_count)
                SELECT COUNT(*) FROM read_compliance_audit_logs_rebuild
                """
            )
