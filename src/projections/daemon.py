# src/projections/daemon.py
import asyncio
import logging
from typing import List
import asyncpg
from .base import BaseProjection
from ..eventstore import EventStore

logger = logging.getLogger(__name__)

class ProjectionDaemon:
    def __init__(self, pool: asyncpg.Pool, event_store: EventStore, projections: List[BaseProjection], poll_interval_ms: int = 100):
        self.pool = pool
        self.event_store = event_store
        self.projections = projections
        self.poll_interval = poll_interval_ms / 1000.0
        self.is_running = False

    async def start(self):
        self.is_running = True
        logger.info(f"Starting ProjectionDaemon with {len(self.projections)} projections.")
        while self.is_running:
            try:
                await self.run_once()
            except Exception as e:
                logger.error(f"Error in ProjectionDaemon loop: {e}", exc_info=True)
            await asyncio.sleep(self.poll_interval)

    async def stop(self):
        self.is_running = False

    async def run_once(self):
        async with self.pool.acquire() as conn:
            for projection in self.projections:
                # 1. Get checkpoint
                row = await conn.fetchrow(
                    """
                    INSERT INTO projection_checkpoints (projection_name, last_position)
                    VALUES ($1, 0)
                    ON CONFLICT (projection_name) DO UPDATE SET projection_name = EXCLUDED.projection_name
                    RETURNING last_position
                    """,
                    projection.projection_name
                )
                last_pos = row['last_position']

                # 2. Fetch new events
                # We use the event store's generator but pinned to a connection if needed, 
                # or just fetch a batch.
                batch_size = 100
                rows = await conn.fetch(
                    "SELECT * FROM events WHERE global_position > $1 ORDER BY global_position ASC LIMIT $2",
                    last_pos, batch_size
                )
                
                if not rows:
                    continue

                from ..models.events import StoredEvent
                import json
                
                events = [
                    StoredEvent(
                        event_id=r['event_id'],
                        stream_id=r['stream_id'],
                        stream_position=r['stream_position'],
                        global_position=r['global_position'],
                        event_type=r['event_type'],
                        event_version=r['event_version'],
                        payload=json.loads(r['payload']),
                        metadata=json.loads(r['metadata']),
                        recorded_at=r['recorded_at']
                    ) for r in rows
                ]

                # 3. Apply to projection in a transaction
                async with conn.transaction():
                    await projection.handle_events(conn, events)
                    # Update checkpoint
                    new_last_pos = events[-1].global_position
                    await conn.execute(
                        "UPDATE projection_checkpoints SET last_position = $1, updated_at = NOW() WHERE projection_name = $2",
                        new_last_pos, projection.projection_name
                    )
                
                logger.debug(f"Projection {projection.projection_name} advanced to {new_last_pos}")
