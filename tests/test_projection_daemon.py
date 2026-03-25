import asyncio
import time
from uuid import uuid4

import pytest

from src.handlers.commands import ApplicationSubmittedCommand, handle_submit_application
from src.models.events import CreditAnalysisCompleted, DecisionGenerated, HumanReviewCompleted


@pytest.mark.asyncio
async def test_projection_slo_and_rebuild_non_blocking(event_store, projection_daemon, db_pool):
    async def fire(i: int):
        app_id = f"app-{i}-{uuid4()}"
        return await handle_submit_application(
            ApplicationSubmittedCommand(
                application_id=app_id,
                applicant_id=f"user-{i}",
                requested_amount_usd=1000 + i,
                correlation_id=f"corr-{i}",
            ),
            event_store,
        )

    await asyncio.gather(*[fire(i) for i in range(50)])

    for _ in range(5):
        await projection_daemon.run_once()

    lag = await projection_daemon.get_lag()
    assert lag["ApplicationSummary"] < 500

    compliance_projection = [p for p in projection_daemon.projections if p.projection_name == "ComplianceAuditView"][0]
    all_events = []
    async for ev in event_store.load_all(from_global_position=0, batch_size=1000):
        all_events.append(ev)

    async def rebuild():
        async with db_pool.acquire() as conn:
            await compliance_projection.rebuild_from_scratch(conn, all_events)

    async def live_read():
        start = time.perf_counter()
        async with db_pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM read_compliance_audit_logs")
        return count, time.perf_counter() - start

    rebuilt, read_result = await asyncio.wait_for(asyncio.gather(rebuild(), live_read()), timeout=5)
    summary_count, read_elapsed_s = read_result
    assert isinstance(summary_count, int)
    assert summary_count >= 0
    assert read_elapsed_s < 1.0


@pytest.mark.asyncio
async def test_agent_performance_tracks_override_rate(event_store, projection_daemon, db_pool):
    app_id = f"app-{uuid4()}"
    stream_id = f"loan-{app_id}"

    await event_store.append(
        stream_id=stream_id,
        events=[
            CreditAnalysisCompleted(
                application_id=app_id,
                agent_id="credit-agent",
                session_id="s1",
                model_version="credit-v2",
                confidence_score=0.85,
                risk_tier="LOW",
                recommended_limit_usd=10000,
                regulatory_basis=["REG-KYC-2"],
            )
        ],
        expected_version=-1,
    )
    await event_store.append(
        stream_id=stream_id,
        events=[
            DecisionGenerated(
                application_id=app_id,
                recommendation="APPROVE",
                confidence_score=0.91,
                reason="test",
                contributing_agent_sessions=["agent-credit-agent-s1"],
                model_versions={"agent-credit-agent-s1": "credit-v2"},
            )
        ],
        expected_version=1,
    )
    await event_store.append(
        stream_id=stream_id,
        events=[
            HumanReviewCompleted(
                application_id=app_id,
                reviewer_id="human-1",
                final_decision="DECLINE",
                final_amount_usd=None,
            )
        ],
        expected_version=2,
    )

    for _ in range(3):
        await projection_daemon.run_once()

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT total_analyses, override_count, override_rate FROM read_agent_performance WHERE agent_id = $1 AND model_version = $2",
            "credit-agent",
            "credit-v2",
        )
    assert row is not None
    assert row["total_analyses"] == 1
    assert row["override_count"] == 1
    assert float(row["override_rate"]) == pytest.approx(1.0)
