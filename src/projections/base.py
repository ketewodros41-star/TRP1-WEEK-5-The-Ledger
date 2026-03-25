from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Iterable, List, Sequence, Set

from ..models.events import StoredEvent


class BaseProjection(ABC):
    @property
    @abstractmethod
    def projection_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def subscribed_event_types(self) -> Set[str]:
        raise NotImplementedError

    @abstractmethod
    async def ensure_schema(self, conn) -> None:
        raise NotImplementedError

    async def handle_events(self, conn, events: Sequence[StoredEvent]) -> None:
        for event in events:
            if event.event_type in self.subscribed_event_types:
                handler = getattr(self, f"on_{event.event_type.lower()}", None)
                if handler:
                    await handler(conn, event)

    async def get_lag(self, conn) -> int:
        checkpoint = await conn.fetchval(
            "SELECT last_position FROM projection_checkpoints WHERE projection_name = $1",
            self.projection_name,
        )
        if checkpoint is None:
            return 0
        recorded_at = await conn.fetchval(
            "SELECT recorded_at FROM events WHERE global_position = $1",
            checkpoint,
        )
        if recorded_at is None:
            return 0
        now = datetime.now(timezone.utc)
        return max(0, int((now - recorded_at).total_seconds() * 1000))
