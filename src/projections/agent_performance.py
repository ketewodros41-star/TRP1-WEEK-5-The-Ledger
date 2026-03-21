# src/projections/agent_performance.py
from .base import BaseProjection
from ..models.events import StoredEvent

class AgentPerformanceProjection(BaseProjection):
    @property
    def projection_name(self) -> str:
        return "AgentPerformanceLedger"

    async def handle_events(self, conn, events: List[StoredEvent]):
        for event in events:
            # We care about credit analysis and decision events
            if event.event_type in ["CreditAnalysisCompleted", "DecisionGenerated"]:
                await self._handle_event(conn, event)

    async def on_creditanalysiscompleted(self, conn, event: StoredEvent):
        model_version = event.payload.get("model_version")
        confidence = event.payload.get("confidence_score", 0)
        duration = event.payload.get("analysis_duration_ms", 0)
        
        await conn.execute(
            """
            INSERT INTO read_agent_performance (model_version, total_analyses, avg_confidence, avg_duration_ms)
            VALUES ($1, 1, $2, $3)
            ON CONFLICT (model_version) DO UPDATE SET
                total_analyses = read_agent_performance.total_analyses + 1,
                avg_confidence = (read_agent_performance.avg_confidence * read_agent_performance.total_analyses + $2) / (read_agent_performance.total_analyses + 1),
                avg_duration_ms = (read_agent_performance.avg_duration_ms * read_agent_performance.total_analyses + $3) / (read_agent_performance.total_analyses + 1)
            """,
            model_version, confidence, duration
        )

    async def on_decisiongenerated(self, conn, event: StoredEvent):
        # We could track recommendation distribution here
        pass
Line 1-38 of src/projections/agent_performance.py
