from __future__ import annotations

from typing import List

from ..eventstore import EventStore
from ..models.events import AgentContext, AgentSessionRecovered


async def reconstruct_agent_context(store: EventStore, agent_id: str, session_id: str) -> AgentContext:
    stream_id = f"agent-{agent_id}-{session_id}"
    events = await store.load_stream(stream_id)
    if not events:
        return AgentContext(
            agent_id=agent_id,
            session_id=session_id,
            context_text="Session has no events.",
            last_event_position=-1,
            pending_work=[],
            session_health_status="FAILED",
        )

    pending_decisions: List[str] = []
    lines: List[str] = []
    last_three = events[-3:]
    for event in events:
        if event.event_type == "AgentDecisionPending":
            pending_decisions.append(event.payload.get("decision_id", "unknown"))
        if event.event_type == "AgentDecisionCompleted":
            done = event.payload.get("decision_id", "unknown")
            if done in pending_decisions:
                pending_decisions.remove(done)

    preserved = []
    cutoff = set(id(e) for e in last_three)
    for event in events:
        is_recent = id(event) in cutoff
        status = event.payload.get("status")
        is_stateful = status in {"PENDING", "ERROR"}
        if is_recent or is_stateful:
            preserved.append(f"[{event.stream_position}] {event.event_type}: {event.payload}")

    older = [e for e in events if id(e) not in cutoff]
    if older:
        lines.append(
            f"Summary: {len(older)} prior events collapsed for token efficiency; session progressed through {', '.join(sorted({e.event_type for e in older}))}."
        )
    lines.extend(preserved)

    last = events[-1]
    needs_reconciliation = last.event_type == "AgentDecisionPending" and bool(pending_decisions)
    health = "NEEDS_RECONCILIATION" if needs_reconciliation else "HEALTHY"
    if last.event_type == "AgentSessionFailed":
        health = "FAILED"

    context = AgentContext(
        agent_id=agent_id,
        session_id=session_id,
        context_text="\n".join(lines),
        last_event_position=last.stream_position,
        pending_work=[f"Complete decision {decision_id}" for decision_id in pending_decisions],
        session_health_status=health,
    )

    recovery_event = AgentSessionRecovered(
        agent_id=agent_id,
        session_id=session_id,
        recovered_from_position=last.stream_position,
    )
    await store.append(
        stream_id=stream_id,
        events=[recovery_event],
        expected_version=last.stream_position,
        aggregate_type="agent",
    )
    return context
