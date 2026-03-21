import os
import asyncio
import logging
from typing import List, Optional, Dict, Any
from fastmcp import FastMCP
import asyncpg
from ..eventstore import EventStore
from ..handlers.commands import (
    handle_submit_application, ApplicationSubmittedCommand,
    handle_credit_analysis_completed, CreditAnalysisCompletedCommand
)
from ..models.events import DomainError, OptimisticConcurrencyError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastMCP
mcp = FastMCP("The Ledger - Apex Financial Services")

# Global state for DB pool
db_pool: Optional[asyncpg.Pool] = None
event_store: Optional[EventStore] = None

@mcp.on_startup()
async def startup():
    global db_pool, event_store
    dsn = os.getenv("DATABASE_URL", "postgres://localhost/apex_ledger")
    db_pool = await asyncpg.create_pool(dsn)
    event_store = EventStore(db_pool)
    logger.info("The Ledger MCP Server started and connected to DB.")

@mcp.on_shutdown()
async def shutdown():
    if db_pool:
        await db_pool.close()
    logger.info("The Ledger MCP Server shut down.")

# --- MCP Tools (Commands) ---

@mcp.tool()
async def submit_application(
    application_id: str,
    applicant_id: str,
    requested_amount_usd: float,
    correlation_id: str
) -> str:
    """
    Submits a new commercial loan application to the ledger.
    Precondition: Application ID must be unique.
    """
    try:
        cmd = ApplicationSubmittedCommand(
            application_id=application_id,
            applicant_id=applicant_id,
            requested_amount=requested_amount_usd,
            metadata={"correlation_id": correlation_id}
        )
        await handle_submit_application(cmd, event_store)
        return f"Successfully submitted application {application_id}"
    except DomainError as e:
        return f"ERROR: Domain validation failed: {str(e)}"
    except OptimisticConcurrencyError:
        return "ERROR: Concurrency collision. Please retry your submission."

@mcp.tool()
async def record_credit_analysis(
    application_id: str,
    agent_id: str,
    session_id: str,
    model_version: str,
    confidence_score: float,
    risk_tier: str,
    recommended_limit_usd: float,
    duration_ms: int,
    correlation_id: str,
    causation_id: str
) -> str:
    """
    Records the output of a Credit Analysis agent for a specific loan application.
    Enforces Gas Town patterns (session must be started).
    """
    try:
        cmd = CreditAnalysisCompletedCommand(
            application_id=application_id,
            agent_id=agent_id,
            session_id=session_id,
            model_version=model_version,
            confidence_score=confidence_score,
            risk_tier=risk_tier,
            recommended_limit_usd=recommended_limit_usd,
            duration_ms=duration_ms,
            input_data={}, # Simplified for demo
            correlation_id=correlation_id,
            causation_id=causation_id
        )
        await handle_credit_analysis_completed(cmd, event_store)
        return f"Successfully recorded credit analysis for {application_id}"
    except DomainError as e:
        return f"ERROR: Domain validation failed: {str(e)}"
    except OptimisticConcurrencyError:
        return "ERROR: Concurrency collision. Reload stream and retry."

# --- MCP Resources (Queries) ---

@mcp.resource("ledger://applications/{application_id}")
async def get_application_summary(application_id: str) -> str:
    """Returns the current state of a loan application from the summary projection."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM read_application_summary WHERE application_id = $1",
            application_id
        )
        if not row:
            return f"No summary found for application {application_id}"
        return str(dict(row))

@mcp.tool()
async def start_agent_session(
    agent_id: str,
    session_id: str,
    model_version: str
) -> str:
    """Starts a new agent session, enforcing Gas Town pattern metadata."""
    from src.models.events import BaseEvent, EventType
    event = BaseEvent(
        event_type=EventType.AGENT_SESSION_STARTED,
        payload={"model_version": model_version}
    )
    await event_store.append(
        stream_id=f"agent-{agent_id}-{session_id}",
        events=[event],
        expected_version=-1,
        aggregate_type="agent"
    )
    return f"Session {session_id} started for {agent_id}"

@mcp.tool()
async def record_fraud_screening(
    application_id: str,
    agent_id: str,
    session_id: str,
    risk_score: float,
    flags: List[str]
) -> str:
    """Records fraud screening results."""
    from src.models.events import BaseEvent, EventType
    event = BaseEvent(
        event_type=EventType.FRAUD_SCREENING_COMPLETED,
        payload={
            "application_id": application_id,
            "risk_score": risk_score,
            "flags": flags
        }
    )
    await event_store.append(
        stream_id=f"loan-{application_id}",
        events=[event],
        expected_version=-1
    )
    return f"Fraud screening recorded for {application_id}"

@mcp.resource("ledger://compliance-logs/{application_id}")
async def get_compliance_logs(application_id: str) -> str:
    """Returns all compliance audit logs for a specific application."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM read_compliance_audit_logs WHERE application_id = $1 ORDER BY recorded_at ASC",
            application_id
        )
        return str([dict(r) for r in rows])

@mcp.resource("ledger://audit-integrity/{entity_type}/{entity_id}")
async def check_integrity(entity_type: str, entity_id: str) -> str:
    """Verifies the cryptographic hash chain for a specific entity's audit stream."""
    from src.integrity.audit_chain import AuditChain
    events = await event_store.load_stream(f"audit-{entity_type}-{entity_id}")
    is_valid = AuditChain.verify_chain(events)
    return f"Integrity check for {entity_type}:{entity_id}: {'PASSED' if is_valid else 'FAILED'}"

if __name__ == "__main__":
    mcp.run()
Line 1-118 of src/mcp/server.py
