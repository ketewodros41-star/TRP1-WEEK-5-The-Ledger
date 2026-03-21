# src/integrity/gas_town.py
import logging
from typing import Optional, Dict, Any
from ..eventstore import EventStore
from ..aggregates.agent_session import AgentSessionAggregate
from ..models.events import EventType

logger = logging.getLogger(__name__)

async def reconstruct_agent_context(store: EventStore, agent_id: str, session_id: str) -> Dict[str, Any]:
    """
    Gas Town Pattern: Reconstructs the agent's memory (context) by replaying its session stream.
    Used for crash recovery or picking up a task from a specific node.
    """
    # 1. Load the session aggregate
    agent_session = await AgentSessionAggregate.load(store, agent_id, session_id)
    
    if not agent_session.is_started:
        logger.warning(f"Attempted to reconstruct session {session_id} which was never started.")
        return {}

    # 2. Extract state from history
    # The 'context' is essentially the state derived from node history and outputs
    context = {
        "agent_id": agent_id,
        "session_id": session_id,
        "current_node": agent_session.last_node,
        "node_history": agent_session.node_history,
        "model_version": agent_session.model_version,
        "recovered_at": agent_session.version
    }

    # 3. Log recovery event (optional but good for audit)
    from ..models.events import BaseEvent
    recovery_event = BaseEvent(
        event_type=EventType.AGENT_SESSION_RECOVERED,
        event_version=1
    )
    
    await store.append(
        stream_id=agent_session.stream_id,
        events=[recovery_event],
        expected_version=agent_session.version
    )
    
    logger.info(f"Reconstructed agent context for {session_id} at position {agent_session.version}")
    return context
