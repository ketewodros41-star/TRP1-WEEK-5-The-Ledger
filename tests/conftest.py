from __future__ import annotations

import os

import asyncpg
import pytest
import pytest_asyncio

from src.eventstore import EventStore
from src.projections.agent_performance import AgentPerformanceProjection
from src.projections.application_summary import ApplicationSummaryProjection
from src.projections.compliance_audit import ComplianceAuditProjection
from src.projections.daemon import ProjectionDaemon
from src.upcasting.registry import build_default_registry


def _dotenv_database_url() -> str | None:
    path = ".env"
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    return None


@pytest_asyncio.fixture
async def db_pool():
    dsn = os.getenv("DATABASE_URL") or _dotenv_database_url() or "postgres://localhost/apex_ledger"
    try:
        pool = await asyncpg.create_pool(dsn)
    except Exception as exc:
        pytest.skip(f"Database unavailable for integration tests: {exc}")

    async with pool.acquire() as conn:
        schema_sql = open("src/schema.sql", "r", encoding="utf-8").read()
        await conn.execute(schema_sql)

    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def reset_tables(db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE TABLE outbox,
                           projection_checkpoints,
                           read_application_summary,
                           read_agent_performance,
                           read_compliance_audit_logs,
                           read_compliance_verdicts,
                           event_streams,
                           events
            RESTART IDENTITY CASCADE
            """
        )
    yield


@pytest_asyncio.fixture
async def event_store(db_pool, reset_tables):
    return EventStore(db_pool, upcaster_registry=build_default_registry())


@pytest_asyncio.fixture
async def projection_daemon(event_store):
    return ProjectionDaemon(
        event_store=event_store,
        projections=[
            ApplicationSummaryProjection(),
            AgentPerformanceProjection(),
            ComplianceAuditProjection(),
        ],
        poll_interval_ms=20,
        batch_size=200,
        max_retries=3,
    )
