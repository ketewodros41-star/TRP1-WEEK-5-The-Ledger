from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.models.events import BaseEvent


class LegacyCreditAnalysisV1(BaseEvent):
    event_type: str = "CreditAnalysisCompleted"
    event_version: int = 1
    application_id: str
    agent_id: str
    session_id: str
    risk_tier: str
    recommended_limit_usd: float


@pytest.mark.asyncio
async def test_load_stream_and_load_all_with_upcasting(event_store):
    stream_id = f"loan-{uuid4()}"

    await event_store.append(
        stream_id=stream_id,
        events=[
            LegacyCreditAnalysisV1(
                application_id="a1",
                agent_id="agent-1",
                session_id="s1",
                risk_tier="LOW",
                recommended_limit_usd=1000,
            )
        ],
        expected_version=-1,
    )

    events = await event_store.load_stream(stream_id, from_position=1, to_position=1)
    assert len(events) == 1
    assert events[0].event_version == 2
    assert "model_version" in events[0].payload
    assert "confidence_score" in events[0].payload

    replay = []
    async for event in event_store.load_all(
        from_global_position=0,
        event_types=["CreditAnalysisCompleted"],
        batch_size=10,
    ):
        replay.append(event)

    assert len(replay) == 1
    assert replay[0].stream_id == stream_id


@pytest.mark.asyncio
async def test_stream_metadata_and_archive(event_store):
    stream_id = f"loan-{uuid4()}"
    await event_store.append(
        stream_id=stream_id,
        events=[LegacyCreditAnalysisV1(application_id="x", agent_id="a", session_id="s", risk_tier="LOW", recommended_limit_usd=1)],
        expected_version=-1,
    )
    meta_before = await event_store.get_stream_metadata(stream_id)
    assert meta_before.current_version == 1
    assert meta_before.archived_at is None

    await event_store.archive_stream(stream_id)
    meta_after = await event_store.get_stream_metadata(stream_id)
    assert meta_after.archived_at is not None
