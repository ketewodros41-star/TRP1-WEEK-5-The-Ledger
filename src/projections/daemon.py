from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Sequence

from ..eventstore import EventStore
from ..models.events import StoredEvent
from .base import BaseProjection

logger = logging.getLogger(__name__)


class ProjectionDaemon:
    def __init__(
        self,
        event_store: EventStore,
        projections: Sequence[BaseProjection],
        poll_interval_ms: int = 100,
        batch_size: int = 250,
        max_retries: int = 3,
    ):
        self.event_store = event_store
        self.projections = list(projections)
        self.poll_interval = poll_interval_ms / 1000.0
        self.batch_size = batch_size
        self.max_retries = max_retries
        self._running = False

    async def start(self) -> None:
        self._running = True
        while self._running:
            await self.run_once()
            await asyncio.sleep(self.poll_interval)

    async def stop(self) -> None:
        self._running = False

    async def _projection_checkpoint(self, conn, name: str) -> int:
        row = await conn.fetchrow(
            """
            INSERT INTO projection_checkpoints (projection_name, last_position)
            VALUES ($1, 0)
            ON CONFLICT (projection_name) DO UPDATE SET projection_name = EXCLUDED.projection_name
            RETURNING last_position
            """,
            name,
        )
        return int(row["last_position"])

    async def run_once(self) -> None:
        async with self.event_store.pool.acquire() as conn:
            for projection in self.projections:
                await projection.ensure_schema(conn)

            checkpoints: Dict[str, int] = {}
            for projection in self.projections:
                checkpoints[projection.projection_name] = await self._projection_checkpoint(conn, projection.projection_name)

            start_position = min(checkpoints.values()) if checkpoints else 0
            events: List[StoredEvent] = []
            async for event in self.event_store.load_all(
                from_global_position=start_position,
                batch_size=self.batch_size,
            ):
                events.append(event)
                if len(events) >= self.batch_size:
                    break

            if not events:
                return

            for projection in self.projections:
                to_apply = [e for e in events if e.global_position > checkpoints[projection.projection_name]]
                if not to_apply:
                    continue
                attempts = 0
                while True:
                    try:
                        async with conn.transaction():
                            await projection.handle_events(conn, to_apply)
                            await conn.execute(
                                """
                                UPDATE projection_checkpoints
                                SET last_position = $1, updated_at = NOW()
                                WHERE projection_name = $2
                                """,
                                to_apply[-1].global_position,
                                projection.projection_name,
                            )
                        break
                    except Exception as exc:
                        attempts += 1
                        logger.exception("Projection %s failed on batch", projection.projection_name)
                        if attempts >= self.max_retries:
                            logger.error(
                                "Skipping event %s for projection %s after %s retries",
                                to_apply[0].global_position,
                                projection.projection_name,
                                attempts,
                            )
                            await conn.execute(
                                """
                                UPDATE projection_checkpoints
                                SET last_position = $1, updated_at = NOW()
                                WHERE projection_name = $2
                                """,
                                to_apply[0].global_position,
                                projection.projection_name,
                            )
                            to_apply = to_apply[1:]
                            attempts = 0
                            if not to_apply:
                                break

    async def get_lag(self) -> Dict[str, int]:
        async with self.event_store.pool.acquire() as conn:
            result: Dict[str, int] = {}
            for projection in self.projections:
                result[projection.projection_name] = await projection.get_lag(conn)
            return result
