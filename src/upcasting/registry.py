from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from ..models.events import StoredEvent


UpcasterFn = Callable[[Dict[str, Any], Dict[str, Any], datetime], Tuple[Dict[str, Any], int]]


class UpcasterRegistry:
    def __init__(self):
        self._upcasters: Dict[Tuple[str, int], UpcasterFn] = {}

    def upcaster(self, event_type: str, from_version: int) -> Callable[[UpcasterFn], UpcasterFn]:
        def decorator(fn: UpcasterFn) -> UpcasterFn:
            self._upcasters[(event_type, from_version)] = fn
            return fn

        return decorator

    def upcast(self, event: StoredEvent) -> StoredEvent:
        current = event
        while (current.event_type, current.event_version) in self._upcasters:
            fn = self._upcasters[(current.event_type, current.event_version)]
            payload, next_version = fn(current.payload, current.metadata, current.recorded_at)
            current = StoredEvent(
                event_id=current.event_id,
                stream_id=current.stream_id,
                stream_position=current.stream_position,
                global_position=current.global_position,
                event_type=current.event_type,
                event_version=next_version,
                payload=payload,
                metadata=current.metadata,
                recorded_at=current.recorded_at,
            )
        return current


def build_default_registry() -> UpcasterRegistry:
    registry = UpcasterRegistry()

    @registry.upcaster("CreditAnalysisCompleted", 1)
    def credit_analysis_v1_to_v2(payload: Dict[str, Any], metadata: Dict[str, Any], recorded_at: datetime):
        inferred_model = "credit-model-2024.1" if recorded_at.year <= 2024 else "credit-model-2025.2"
        rule_basis = ["REG-KYC-1", "REG-AML-2"] if recorded_at.year <= 2024 else ["REG-KYC-2", "REG-AML-3"]
        return (
            {
                **payload,
                "model_version": payload.get("model_version", inferred_model),
                "confidence_score": payload.get("confidence_score", None),
                "regulatory_basis": payload.get("regulatory_basis", rule_basis),
            },
            2,
        )

    @registry.upcaster("DecisionGenerated", 1)
    def decision_generated_v1_to_v2(payload: Dict[str, Any], metadata: Dict[str, Any], recorded_at: datetime):
        sessions = payload.get("contributing_agent_sessions", [])
        reconstructed = {}
        for session in sessions:
            reconstructed[session] = payload.get("model_version", "unknown")
        return ({**payload, "model_versions": payload.get("model_versions", reconstructed)}, 2)

    return registry
