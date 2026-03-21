# tests/test_concurrency.py
import asyncio
import pytest
from uuid import uuid4
from src.eventstore import EventStore
from src.models.events import BaseEvent, OptimisticConcurrencyError
from typing import List

class TestEvent(BaseEvent):
    event_type: str = "TestEvent"
    data: str

@pytest.mark.asyncio
async def test_concurrent_append_collision(event_store: EventStore):
    """
    Two AI agents simultaneously attempt to append to the same loan application stream.
    Both read the stream at version 3 and pass expected_version=3.
    Exactly one must succeed. The other must receive OptimisticConcurrencyError.
    """
    stream_id = f"loan-{uuid4()}"
    
    # 1. Setup: Stream at version 3
    for i in range(1, 4):
        await event_store.append(
            stream_id=stream_id,
            events=[TestEvent(data=f"setup-{i}")],
            expected_version=i-1
        )
    
    assert await event_store.stream_version(stream_id) == 3

    # 2. Define concurrent tasks
    async def append_task(agent_name: str):
        try:
            # We must use expected_version=3 for both
            new_version = await event_store.append(
                stream_id=stream_id,
                events=[TestEvent(data=f"decision-from-{agent_name}")],
                expected_version=3
            )
            return ("SUCCESS", agent_name, new_version)
        except OptimisticConcurrencyError as e:
            # Ensure the error has the structured fields as per rubric
            assert e.stream_id == stream_id
            assert e.expected_version == 3
            assert e.actual_version >= 3
            return ("ERROR", agent_name, e)

    # 3. Run concurrently
    results = await asyncio.gather(
        append_task("Agent-A"),
        append_task("Agent-B")
    )

    # 4. Assertions
    successes = [r for r in results if r[0] == "SUCCESS"]
    errors = [r for r in results if r[0] == "ERROR"]

    # (a) Total events appended = 4 (one success from setup 3 + one from concurrent test)
    assert await event_store.stream_version(stream_id) == 4
    
    # Exactly one must succeed
    assert len(successes) == 1
    # Exactly one must fail with OptimisticConcurrencyError
    assert len(errors) == 1
    
    # (b) the winning task's event has stream_position = 4
    assert successes[0][2] == 4

    print(f"Concurrency result: {successes[0][1]} WON, {errors[0][1]} LOST as expected.")
