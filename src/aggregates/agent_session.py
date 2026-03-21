# src/aggregates/agent_session.py
from typing import Optional, List, Dict, Any
from .base import BaseAggregate
from ..models.events import StoredEvent, DomainError

class AgentSessionAggregate(BaseAggregate):
    def __init__(self, agent_id: str, session_id: str):
        super().__init__(f"agent-{agent_id}-{session_id}")
        self.agent_id = agent_id
        self.session_id = session_id
        self.model_version: Optional[str] = None
        self.is_started: bool = False
        self.is_completed: bool = False
        self.last_node: Optional[str] = None
        self.node_history: List[str] = []
        self.output_events: List[Dict[str, Any]] = []

    @classmethod
    async def load(cls, store, agent_id: str, session_id: str) -> "AgentSessionAggregate":
        events = await store.load_stream(f"agent-{agent_id}-{session_id}")
        agg = cls(agent_id, session_id)
        agg.apply_events(events)
        return agg

    # --- Business Rules ---
    def assert_started(self):
        """Gas Town: Enforces that a session must be started before any other work."""
        if not self.is_started:
            raise DomainError(f"Agent session {self.session_id} not started. Gas Town pattern violation.")

    def assert_not_completed(self):
        if self.is_completed:
            raise DomainError(f"Agent session {self.session_id} already completed.")

    def assert_model_version(self, version: str):
        if self.model_version and self.model_version != version:
            raise DomainError(f"Model version mismatch. Session locked to {self.model_version}, but got {version}.")

    # --- Event Handlers ---
    def on_agentsessionstarted(self, event: StoredEvent):
        if self.is_started:
            # This might happen on retry/replay, which is fine
            pass
        self.is_started = True
        self.model_version = event.payload.get("model_version")

    def on_agentnodeexecuted(self, event: StoredEvent):
        # No guard here (replay safety)
        node_name = event.payload.get("node_name")
        self.last_node = node_name
        self.node_history.append(node_name)

    def on_agentoutputwritten(self, event: StoredEvent):
        # No guard here (replay safety)
        self.output_events.extend(event.payload.get("events_written", []))

    def on_agentsessioncompleted(self, event: StoredEvent):
        self.is_completed = True

    def on_agentsessionfailed(self, event: StoredEvent):
        # We don't mark as completed, but we might record failure
        pass

    def on_agentsessionrecovered(self, event: StoredEvent):
        # Recovery fact
        pass
