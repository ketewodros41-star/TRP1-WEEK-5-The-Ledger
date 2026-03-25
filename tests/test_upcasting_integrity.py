from uuid import uuid4

import pytest

from src.integrity.audit_chain import run_integrity_check
from src.models.events import BaseEvent


class LegacyDecisionV1(BaseEvent):
    event_type: str = "DecisionGenerated"
    event_version: int = 1
    application_id: str
    recommendation: str
    confidence_score: float
    reason: str
    contributing_agent_sessions: list[str]


@pytest.mark.asyncio
async def test_upcasted_read_does_not_mutate_raw_payload(event_store, db_pool):
    stream_id = f"loan-{uuid4()}"
    legacy = LegacyDecisionV1(
        application_id="app-1",
        recommendation="APPROVE",
        confidence_score=0.9,
        reason="legacy",
        contributing_agent_sessions=["agent-a"],
    )
    await event_store.append(stream_id=stream_id, events=[legacy], expected_version=-1)

    loaded = await event_store.load_stream(stream_id)
    assert loaded[0].event_version == 2
    assert "model_versions" in loaded[0].payload

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT event_version, payload FROM events WHERE stream_id = $1", stream_id)
    assert row["event_version"] == 1
    assert "model_versions" not in row["payload"]


@pytest.mark.asyncio
async def test_tamper_detection_after_direct_db_update(event_store, db_pool):
    stream_id = f"loan-{uuid4()}"
    await event_store.append(stream_id=stream_id, events=[BaseEvent(event_type="ApplicationSubmitted", event_version=1)], expected_version=-1)

    first = await run_integrity_check(event_store, "loan", stream_id.split("loan-")[1])
    assert first.tamper_detected is False

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE events SET payload = payload || '{\"tampered\": true}'::jsonb WHERE stream_id = $1",
            stream_id,
        )

    second = await run_integrity_check(event_store, "loan", stream_id.split("loan-")[1])
    assert second.tamper_detected is True
