# src/upcasting/registry.py
from typing import Dict, Callable, List, Type
from ..models.events import StoredEvent

class UpcasterRegistry:
    def __init__(self):
        # (event_type, from_version) -> func(payload, metadata) -> (new_payload, new_version)
        self.upcasters: Dict[tuple, Callable] = {}

    def register(self, event_type: str, from_version: int, upcaster: Callable):
        self.upcasters[(event_type, from_version)] = upcaster

    def upcast(self, event: StoredEvent) -> StoredEvent:
        """Recursively upcasts an event until it reaches the latest version."""
        while (event.event_type, event.event_version) in self.upcasters:
            upcaster = self.upcasters[(event.event_type, event.event_version)]
            new_payload, new_version = upcaster(event.payload, event.metadata)
            
            # Create a new StoredEvent with the upcasted data
            event = StoredEvent(
                event_id=event.event_id,
                stream_id=event.stream_id,
                stream_position=event.stream_position,
                global_position=event.global_position,
                event_type=event.event_type,
                event_version=new_version,
                payload=new_payload,
                metadata=event.metadata,
                recorded_at=event.recorded_at
            )
        return event

# --- Example Upcasters (as per Section 5 of requirements) ---
def upcast_creditanalysis_v1_to_v2(payload: dict, metadata: dict) -> tuple:
    """Adds inferred model_version and confidence_score."""
    # Logic: If recorded_at < 2024-06, it was old-model
    # For this exercise, we just add defaults or infer from other fields
    return {
        **payload,
        "model_version": payload.get("model_version", "legacy-v1"),
        "confidence_score": 0.85 # Standard historical average
    }, 2

def upcast_decision_v1_to_v2(payload: dict, metadata: dict) -> tuple:
    """Adds regulatory_basis list."""
    return {
        **payload,
        "regulatory_basis": ["REG-2024-STD"]
    }, 2
