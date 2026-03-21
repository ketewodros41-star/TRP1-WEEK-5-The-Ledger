# src/aggregates/base.py
from abc import ABC, abstractmethod
from typing import List, Any
from ..models.events import StoredEvent

class BaseAggregate(ABC):
    def __init__(self, stream_id: str):
        self.stream_id = stream_id
        self.version = -1

    def apply_events(self, events: List[StoredEvent]):
        for event in events:
            self._apply(event)
            self.version = event.stream_position

    def _apply(self, event: StoredEvent):
        # Dispatch to on_event_type method
        method_name = f"on_{event.event_type.lower()}"
        if hasattr(self, method_name):
            getattr(self, method_name)(event)
        else:
            # Optional: handle unknown events
            pass
