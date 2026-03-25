from __future__ import annotations

from typing import List, Optional

from .base import BaseAggregate
from ..models.events import DomainError, StoredEvent


class AgentSessionAggregate(BaseAggregate):
    def __init__(self, agent_id: str, session_id: str):
        super().__init__(f"agent-{agent_id}-{session_id}")
        self.agent_id = agent_id
        self.session_id = session_id
        self.model_version: Optional[str] = None
        self.started = False
        self.context_loaded = False
        self.completed = False
        self.failed = False
        self.pending_decisions: List[str] = []

    @classmethod
    async def load(cls, store, agent_id: str, session_id: str) -> "AgentSessionAggregate":
        agg = cls(agent_id, session_id)
        events = await store.load_stream(agg.stream_id)
        agg.apply_events(events)
        return agg

    def assert_started(self) -> None:
        if not self.started:
            raise DomainError("Agent session must be started before use")

    def assert_context_loaded_before_decision(self) -> None:
        if not self.context_loaded:
            raise DomainError("AgentSession must load context before decision events")

    def assert_model_version_locked(self, requested_model_version: str) -> None:
        if self.model_version and self.model_version != requested_model_version:
            raise DomainError(
                f"Model version lock mismatch: expected {self.model_version}, got {requested_model_version}"
            )

    def on_agentsessionstarted(self, event: StoredEvent) -> None:
        self.started = True
        self.model_version = event.payload["model_version"]

    def on_agentcontextloaded(self, event: StoredEvent) -> None:
        self.context_loaded = True

    def on_agentdecisionpending(self, event: StoredEvent) -> None:
        self.pending_decisions.append(event.payload["decision_id"])

    def on_agentdecisioncompleted(self, event: StoredEvent) -> None:
        decision_id = event.payload["decision_id"]
        if decision_id in self.pending_decisions:
            self.pending_decisions.remove(decision_id)

    def on_agentsessioncompleted(self, event: StoredEvent) -> None:
        self.completed = True

    def on_agentsessionfailed(self, event: StoredEvent) -> None:
        self.failed = True
