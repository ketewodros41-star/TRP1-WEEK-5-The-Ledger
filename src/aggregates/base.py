from __future__ import annotations

from abc import ABC
from typing import Iterable

from ..models.events import StoredEvent


class BaseAggregate(ABC):
    def __init__(self, stream_id: str):
        self.stream_id = stream_id
        self.version = -1

    def apply_events(self, events: Iterable[StoredEvent]) -> None:
        for event in events:
            self._apply(event)
            self.version = event.stream_position

    def _apply(self, event: StoredEvent) -> None:
        handler = getattr(self, f"on_{event.event_type.lower()}", None)
        if handler:
            handler(event)
