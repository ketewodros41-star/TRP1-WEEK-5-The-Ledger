from uuid import uuid4

import pytest

from src.integrity.gas_town import reconstruct_agent_context
from src.models.events import (
    AgentContextLoaded,
    AgentDecisionPending,
    AgentNodeExecuted,
    AgentSessionStarted,
)


@pytest.mark.asyncio
async def test_gastown_crash_recovery_context(event_store):
    agent_id = "agent-x"
    session_id = str(uuid4())
    stream_id = f"agent-{agent_id}-{session_id}"

    await event_store.append(
        stream_id=stream_id,
        events=[
            AgentSessionStarted(agent_id=agent_id, session_id=session_id, model_version="m1"),
            AgentContextLoaded(agent_id=agent_id, session_id=session_id, context_token_count=120),
            AgentNodeExecuted(agent_id=agent_id, session_id=session_id, node_name="triage", status="OK"),
            AgentNodeExecuted(agent_id=agent_id, session_id=session_id, node_name="decide", status="PENDING"),
            AgentDecisionPending(agent_id=agent_id, session_id=session_id, decision_id="d-1"),
        ],
        expected_version=-1,
        aggregate_type="agent",
    )

    context = await reconstruct_agent_context(event_store, agent_id, session_id)
    assert context.pending_work
    assert context.last_event_position == 5
    assert context.session_health_status == "NEEDS_RECONCILIATION"
