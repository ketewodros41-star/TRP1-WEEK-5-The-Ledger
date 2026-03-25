import asyncio
from uuid import uuid4

import pytest

from src.models.events import ApplicationSubmitted, OptimisticConcurrencyError


@pytest.mark.asyncio
async def test_double_decision_concurrency(event_store):
    stream_id = f"loan-{uuid4()}"

    await event_store.append(
        stream_id=stream_id,
        events=[ApplicationSubmitted(application_id="a1", applicant_id="u1", requested_amount_usd=1000)],
        expected_version=-1,
    )
    for i in range(2, 4):
        await event_store.append(
            stream_id=stream_id,
            events=[ApplicationSubmitted(application_id="a1", applicant_id=f"u{i}", requested_amount_usd=1000 + i)],
            expected_version=i - 1,
        )

    assert await event_store.stream_version(stream_id) == 3

    async def contender(name: str):
        return await event_store.append(
            stream_id=stream_id,
            events=[ApplicationSubmitted(application_id="a1", applicant_id=name, requested_amount_usd=5000)],
            expected_version=3,
        )

    first, second = await asyncio.gather(
        contender("agent-a"),
        contender("agent-b"),
        return_exceptions=True,
    )

    winners = [x for x in (first, second) if isinstance(x, int)]
    losers = [x for x in (first, second) if isinstance(x, Exception)]

    assert len(winners) == 1
    assert winners[0] == 4
    assert len(losers) == 1
    assert isinstance(losers[0], OptimisticConcurrencyError)

    events = await event_store.load_stream(stream_id)
    assert len(events) == 4
    assert await event_store.stream_version(stream_id) == 4


@pytest.mark.asyncio
async def test_concurrent_new_stream_append_allows_single_winner(event_store):
    stream_id = f"loan-{uuid4()}"

    async def create_once(applicant_id: str):
        return await event_store.append(
            stream_id=stream_id,
            events=[ApplicationSubmitted(application_id="a2", applicant_id=applicant_id, requested_amount_usd=1200)],
            expected_version=-1,
        )

    first, second = await asyncio.gather(
        create_once("u1"),
        create_once("u2"),
        return_exceptions=True,
    )

    winners = [x for x in (first, second) if isinstance(x, int)]
    losers = [x for x in (first, second) if isinstance(x, Exception)]
    assert len(winners) == 1
    assert winners[0] == 1
    assert len(losers) == 1
    assert isinstance(losers[0], OptimisticConcurrencyError)

    events = await event_store.load_stream(stream_id)
    assert len(events) == 1
    assert await event_store.stream_version(stream_id) == 1
