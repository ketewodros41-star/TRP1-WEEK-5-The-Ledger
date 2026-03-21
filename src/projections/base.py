# src/projections/base.py
from abc import ABC, abstractmethod
from typing import List, Any
from ..models.events import StoredEvent

class BaseProjection(ABC):
    @property
    @abstractmethod
    def projection_name(self) -> str:
        pass

    @abstractmethod
    async def handle_events(self, conn, events: List[StoredEvent]):
        pass

    async def _handle_event(self, conn, event: StoredEvent):
        method_name = f"on_{event.event_type.lower()}"
        if hasattr(self, method_name):
            await getattr(self, method_name)(conn, event)
