# src/eventstore.py
import json
import logging
from typing import List, Optional, Any, Dict, AsyncGenerator
from datetime import datetime
import asyncpg
from uuid import UUID

from .models.events import (
    BaseEvent, StoredEvent, StreamMetadata,
    OptimisticConcurrencyError, EventStoreError
)

logger = logging.getLogger(__name__)

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
        aggregate_type: Optional[str] = None
    ) -> int:
        """
        Atomically appends events to stream_id.
        Raises OptimisticConcurrencyError if current_version != expected_version.
        Writes to outbox in the same transaction.
        """
        if not events:
            return await self.stream_version(stream_id)

        if aggregate_type is None:
            # Infer aggregate type from stream_id if not provided (e.g., "loan-123" -> "loan")
            aggregate_type = stream_id.split('-')[0]

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 1. Lock stream and check version
                # Insert if not exists, then lock for update
                await conn.execute(
                    """
                    INSERT INTO event_streams (stream_id, aggregate_type, current_version)
                    VALUES ($1, $2, 0)
                    ON CONFLICT (stream_id) DO NOTHING
                    """,
                    stream_id, aggregate_type
                )
                
                row = await conn.fetchrow(
                    "SELECT current_version FROM event_streams WHERE stream_id = $1 FOR UPDATE",
                    stream_id
                )
                current_version = row['current_version']

                if expected_version != -1 and current_version != expected_version:
                    raise OptimisticConcurrencyError(stream_id, expected_version, current_version)

                # 2. Prepare events for batch insert
                new_version = current_version
                event_ids = []
                
                for event in events:
                    new_version += 1
                    event_id = UUID(int=new_version) # Or generate real UUID, but usually random is better
                    # Wait, prompt says event_id is UUID PRIMARY KEY DEFAULT gen_random_uuid()
                    # Let's let DB generate IDs unless we need them for outbox
                    
                    # Metadata for each event
                    metadata = {
                        "correlation_id": correlation_id,
                        "causation_id": causation_id,
                        "recorded_at": datetime.utcnow().isoformat()
                    }

                    # Insert Event
                    row = await conn.fetchrow(
                        """
                        INSERT INTO events (stream_id, stream_position, event_type, event_version, payload, metadata)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        RETURNING event_id
                        """,
                        stream_id, new_version, event.event_type, event.event_version,
                        json.dumps(event.model_dump()), json.dumps(metadata)
                    )
                    event_ids.append(row['event_id'])

                    # 3. Add to Outbox in same transaction
                    await conn.execute(
                        """
                        INSERT INTO outbox (event_id, destination, payload)
                        VALUES ($1, $2, $3)
                        """,
                        row['event_id'], f"bus:{aggregate_type}", json.dumps(event.model_dump())
                    )

                # 4. Update Stream Version
                await conn.execute(
                    "UPDATE event_streams SET current_version = $1 WHERE stream_id = $2",
                    new_version, stream_id
                )

                return new_version

    async def load_stream(
        self,
        stream_id: str,
        from_position: int = 0,
        to_position: Optional[int] = None
    ) -> List[StoredEvent]:
        """Loads events for a stream, applying upcasters if registered."""
        query = "SELECT * FROM events WHERE stream_id = $1 AND stream_position > $2"
        params = [stream_id, from_position]
        
        if to_position:
            query += " AND stream_position <= $3"
            params.append(to_position)
            
        query += " ORDER BY stream_position ASC"

        rows = await self.pool.fetch(query, *params)
        events = []
        for row in rows:
            event = StoredEvent(
                event_id=row['event_id'],
                stream_id=row['stream_id'],
                stream_position=row['stream_position'],
                global_position=row['global_position'],
                event_type=row['event_type'],
                event_version=row['event_version'],
                payload=json.loads(row['payload']),
                metadata=json.loads(row['metadata']),
                recorded_at=row['recorded_at']
            )
            
            # Apply upcasting if registry exists
            if self.upcaster_registry:
                event = self.upcaster_registry.upcast(event)
                
            events.append(event)
            
        return events

    async def load_all(
        self,
        from_global_position: int = 0,
        event_types: Optional[List[str]] = None,
        batch_size: int = 500
    ) -> AsyncGenerator[StoredEvent, None]:
        """Async generator for replaying all events across streams."""
        current_pos = from_global_position
        
        while True:
            query = "SELECT * FROM events WHERE global_position > $1"
            params = [current_pos]
            
            if event_types:
                query += " AND event_type = ANY($2)"
                params.append(event_types)
                
            query += " ORDER BY global_position ASC LIMIT $" + str(len(params) + 1)
            params.append(batch_size)
            
            rows = await self.pool.fetch(query, *params)
            if not rows:
                break
                
            for row in rows:
                current_pos = row['global_position']
                event = StoredEvent(
                    event_id=row['event_id'],
                    stream_id=row['stream_id'],
                    stream_position=row['stream_position'],
                    global_position=row['global_position'],
                    event_type=row['event_type'],
                    event_version=row['event_version'],
                    payload=json.loads(row['payload']),
                    metadata=json.loads(row['metadata']),
                    recorded_at=row['recorded_at']
                )
                
                if self.upcaster_registry:
                    event = self.upcaster_registry.upcast(event)
                
                yield event

    async def stream_version(self, stream_id: str) -> int:
        version = await self.pool.fetchval(
            "SELECT current_version FROM event_streams WHERE stream_id = $1",
            stream_id
        )
        return version if version is not None else -1

    async def archive_stream(self, stream_id: str) -> None:
        await self.pool.execute(
            "UPDATE event_streams SET archived_at = NOW() WHERE stream_id = $1",
            stream_id
        )

    async def get_stream_metadata(self, stream_id: str) -> StreamMetadata:
        row = await self.pool.fetchrow(
            "SELECT * FROM event_streams WHERE stream_id = $1",
            stream_id
        )
        if not row:
            raise EventStoreError(f"Stream {stream_id} not found")
            
        return StreamMetadata(
            stream_id=row['stream_id'],
            aggregate_type=row['aggregate_type'],
            current_version=row['current_version'],
            created_at=row['created_at'],
            archived_at=row['archived_at'],
            metadata=json.loads(row['metadata'])
        )
