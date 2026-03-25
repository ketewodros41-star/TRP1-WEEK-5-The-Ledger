from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Iterable, List, Optional

import asyncpg

from .models.events import (
    BaseEvent,
    EventStoreError,
    OptimisticConcurrencyError,
    StoredEvent,
    StreamMetadata,
)


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _to_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


class EventStore:
    def __init__(self, pool: asyncpg.Pool, upcaster_registry: Optional[Any] = None):
        self.pool = pool
        self.upcaster_registry = upcaster_registry

    async def append(
        self,
        stream_id: str,
        events: List[BaseEvent],
        expected_version: int,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        aggregate_type: Optional[str] = None,
    ) -> int:
        if not events:
            return await self.stream_version(stream_id)

        aggregate_name = aggregate_type or stream_id.split("-")[0]

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                if expected_version == -1:
                    created = await conn.fetchrow(
                        """
                        INSERT INTO event_streams (stream_id, aggregate_type, current_version)
                        VALUES ($1, $2, 0)
                        ON CONFLICT (stream_id) DO NOTHING
                        RETURNING current_version
                        """,
                        stream_id,
                        aggregate_name,
                    )
                    if created is None:
                        existing_version = await conn.fetchval(
                            "SELECT current_version FROM event_streams WHERE stream_id = $1 FOR UPDATE",
                            stream_id,
                        )
                        actual_version = int(existing_version) if existing_version is not None else -1
                        raise OptimisticConcurrencyError(stream_id, expected_version, actual_version)
                    current_version = 0
                else:
                    await conn.execute(
                        """
                        INSERT INTO event_streams (stream_id, aggregate_type, current_version)
                        VALUES ($1, $2, 0)
                        ON CONFLICT (stream_id) DO NOTHING
                        """,
                        stream_id,
                        aggregate_name,
                    )

                    row = await conn.fetchrow(
                        "SELECT current_version FROM event_streams WHERE stream_id = $1 FOR UPDATE",
                        stream_id,
                    )
                    current_version = int(row["current_version"]) if row else -1
                    if current_version != expected_version:
                        raise OptimisticConcurrencyError(stream_id, expected_version, current_version)

                new_version = current_version
                for event in events:
                    new_version += 1
                    metadata = {
                        "correlation_id": correlation_id,
                        "causation_id": causation_id,
                        "recorded_at": datetime.now(timezone.utc).isoformat(),
                    }
                    payload = _to_jsonable(event)
                    row = await conn.fetchrow(
                        """
                        INSERT INTO events (stream_id, stream_position, event_type, event_version, payload, metadata)
                        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
                        RETURNING event_id
                        """,
                        stream_id,
                        new_version,
                        event.event_type,
                        event.event_version,
                        json.dumps(payload),
                        json.dumps(metadata),
                    )

                    await conn.execute(
                        """
                        INSERT INTO outbox (event_id, destination, payload)
                        VALUES ($1, $2, $3::jsonb)
                        """,
                        row["event_id"],
                        f"bus:{aggregate_name}",
                        json.dumps(payload),
                    )

                await conn.execute(
                    "UPDATE event_streams SET current_version = $1 WHERE stream_id = $2",
                    new_version,
                    stream_id,
                )
                return new_version

    async def load_stream(
        self,
        stream_id: str,
        from_position: int = 0,
        to_position: Optional[int] = None,
    ) -> List[StoredEvent]:
        query = "SELECT * FROM events WHERE stream_id = $1 AND stream_position >= $2"
        params: List[Any] = [stream_id, from_position]
        if to_position is not None:
            query += " AND stream_position <= $3"
            params.append(to_position)
        query += " ORDER BY stream_position ASC"

        rows = await self.pool.fetch(query, *params)
        loaded: List[StoredEvent] = []
        for row in rows:
            event = StoredEvent(
                event_id=row["event_id"],
                stream_id=row["stream_id"],
                stream_position=row["stream_position"],
                global_position=row["global_position"],
                event_type=row["event_type"],
                event_version=row["event_version"],
                payload=_to_dict(row["payload"]),
                metadata=_to_dict(row["metadata"]),
                recorded_at=row["recorded_at"],
            )
            if self.upcaster_registry:
                event = self.upcaster_registry.upcast(event)
            loaded.append(event)
        return loaded

    async def load_all(
        self,
        from_global_position: int = 0,
        event_types: Optional[List[str]] = None,
        batch_size: int = 500,
    ) -> AsyncGenerator[StoredEvent, None]:
        position = from_global_position
        while True:
            query = "SELECT * FROM events WHERE global_position > $1"
            params: List[Any] = [position]
            if event_types:
                query += " AND event_type = ANY($2)"
                params.append(event_types)
            query += f" ORDER BY global_position ASC LIMIT ${len(params) + 1}"
            params.append(batch_size)
            rows = await self.pool.fetch(query, *params)
            if not rows:
                break
            for row in rows:
                position = int(row["global_position"])
                event = StoredEvent(
                    event_id=row["event_id"],
                    stream_id=row["stream_id"],
                    stream_position=row["stream_position"],
                    global_position=row["global_position"],
                    event_type=row["event_type"],
                    event_version=row["event_version"],
                    payload=_to_dict(row["payload"]),
                    metadata=_to_dict(row["metadata"]),
                    recorded_at=row["recorded_at"],
                )
                if self.upcaster_registry:
                    event = self.upcaster_registry.upcast(event)
                yield event

    async def stream_version(self, stream_id: str) -> int:
        value = await self.pool.fetchval(
            "SELECT current_version FROM event_streams WHERE stream_id = $1",
            stream_id,
        )
        return int(value) if value is not None else -1

    async def archive_stream(self, stream_id: str) -> None:
        await self.pool.execute(
            "UPDATE event_streams SET archived_at = NOW() WHERE stream_id = $1",
            stream_id,
        )

    async def get_stream_metadata(self, stream_id: str) -> StreamMetadata:
        row = await self.pool.fetchrow(
            "SELECT * FROM event_streams WHERE stream_id = $1",
            stream_id,
        )
        if not row:
            raise EventStoreError(f"Stream {stream_id} not found")
        return StreamMetadata(
            stream_id=row["stream_id"],
            aggregate_type=row["aggregate_type"],
            current_version=row["current_version"],
            created_at=row["created_at"],
            archived_at=row["archived_at"],
            metadata=_to_dict(row["metadata"]),
        )

    async def get_event(self, event_id: str) -> Optional[StoredEvent]:
        row = await self.pool.fetchrow("SELECT * FROM events WHERE event_id = $1", event_id)
        if not row:
            return None
        event = StoredEvent(
            event_id=row["event_id"],
            stream_id=row["stream_id"],
            stream_position=row["stream_position"],
            global_position=row["global_position"],
            event_type=row["event_type"],
            event_version=row["event_version"],
            payload=_to_dict(row["payload"]),
            metadata=_to_dict(row["metadata"]),
            recorded_at=row["recorded_at"],
        )
        if self.upcaster_registry:
            return self.upcaster_registry.upcast(event)
        return event
