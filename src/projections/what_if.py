# src/projections/what_if.py
from typing import List, Dict, Any
from .base import BaseProjection
from ..models.events import StoredEvent

class WhatIfProjector:
    """
    Phase 6 Bonus: Implements counterfactual projections.
    Allows for re-running projections with different rule sets or models.
    """
    def __init__(self, event_store):
        self.event_store = event_store

    async def simulate_with_rule_change(self, application_id: str, new_ruleset: Dict[str, Any]) -> Dict[str, Any]:
        """
        Replays events for a loan but applies a different compliance or risk logic
        to see what 'would have happened'.
        """
        events = await self.event_store.load_stream(f"loan-{application_id}")
        
        # Simplified simulation state
        state = {"original_decision": None, "simulated_decision": None}
        
        for event in events:
            if event.event_type == "DecisionGenerated":
                state["original_decision"] = event.payload.get("recommendation")
                
            # Here we would inject custom logic
            if event.event_type == "CreditAnalysisCompleted":
                # Simulated rule: If limit > 100k, simulate as DECLINE if risk > 0.5
                risk = event.payload.get("confidence_score")
                limit = event.payload.get("recommended_limit_usd")
                if limit > 100000 and risk < 0.9:
                    state["simulated_decision"] = "DECLINE (Simulator)"
        
        return state
