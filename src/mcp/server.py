from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg
from fastmcp import FastMCP

from ..agents.openrouter_client import OpenRouterClient, OpenRouterError
from ..eventstore import EventStore
from ..handlers.commands import (
    ApplicationSubmittedCommand,
    GenerateDecisionCommand,
    HumanReviewCommand,
    RecordComplianceCheckCommand,
    RecordCreditAnalysisCommand,
    RecordFraudScreeningCommand,
    StartAgentSessionCommand,
    handle_generate_decision,
    handle_human_review,
    handle_record_compliance_check,
    handle_record_credit_analysis,
    handle_record_fraud_screening,
    handle_start_agent_session,
    handle_submit_application,
    mark_agent_decision_completed,
    mark_agent_decision_pending,
)
from ..integrity.audit_chain import run_integrity_check as run_integrity_check_impl
from ..models.events import DomainError, MCPError, OptimisticConcurrencyError
from ..projections.agent_performance import AgentPerformanceProjection
from ..projections.application_summary import ApplicationSummaryProjection
from ..projections.compliance_audit import ComplianceAuditProjection
from ..projections.daemon import ProjectionDaemon
from ..upcasting.registry import build_default_registry

mcp = FastMCP("Apex Ledger")

_db_pool: Optional[asyncpg.Pool] = None
_event_store: Optional[EventStore] = None
_projection_daemon: Optional[ProjectionDaemon] = None
_openrouter_client: OpenRouterClient = OpenRouterClient()


def configure_runtime_for_tests(pool: asyncpg.Pool, store: EventStore, daemon: ProjectionDaemon) -> None:
    global _db_pool, _event_store, _projection_daemon
    _db_pool = pool
    _event_store = store
    _projection_daemon = daemon


def _ok(**payload: Any) -> Dict[str, Any]:
    return {"ok": True, **payload}


def _err(error_type: str, message: str, suggested_action: str, **context: Any) -> Dict[str, Any]:
    model = MCPError(
        error_type=error_type,
        message=message,
        context=context,
        suggested_action=suggested_action,
    )
    return {"ok": False, "error": model.model_dump()}

def _resource(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, default=str)


async def _ensure_ready() -> EventStore:
    if _event_store is None:
        raise RuntimeError("MCP server not started")
    return _event_store


async def startup() -> None:
    global _db_pool, _event_store, _projection_daemon

    dsn = os.getenv("DATABASE_URL", "postgres://localhost/apex_ledger")
    _db_pool = await asyncpg.create_pool(dsn)
    registry = build_default_registry()
    _event_store = EventStore(_db_pool, upcaster_registry=registry)

    _projection_daemon = ProjectionDaemon(
        event_store=_event_store,
        projections=[
            ApplicationSummaryProjection(),
            AgentPerformanceProjection(),
            ComplianceAuditProjection(),
        ],
        poll_interval_ms=50,
        batch_size=250,
        max_retries=3,
    )


async def shutdown() -> None:
    global _db_pool, _projection_daemon
    if _projection_daemon:
        await _projection_daemon.stop()
    if _db_pool:
        await _db_pool.close()


if hasattr(mcp, "on_startup"):
    mcp.on_startup()(startup)
if hasattr(mcp, "on_shutdown"):
    mcp.on_shutdown()(shutdown)


@mcp.tool()
async def submit_application(
    application_id: str,
    applicant_id: str,
    requested_amount_usd: float,
    correlation_id: str,
) -> Dict[str, Any]:
    """
    Submit a new application.
    Preconditions: `application_id` must not already exist; `requested_amount_usd` must be > 0.
    """
    if requested_amount_usd <= 0:
        return _err("ValidationError", "requested_amount_usd must be > 0", "Provide a positive amount")

    try:
        store = await _ensure_ready()
        new_version = await handle_submit_application(
            ApplicationSubmittedCommand(
                application_id=application_id,
                applicant_id=applicant_id,
                requested_amount_usd=requested_amount_usd,
                correlation_id=correlation_id,
            ),
            store,
        )
        return _ok(application_id=application_id, new_version=new_version)
    except OptimisticConcurrencyError as exc:
        return _err(
            "OptimisticConcurrencyError",
            str(exc),
            "Reload the stream and retry the command",
            **exc.details,
        )
    except DomainError as exc:
        return _err("DomainError", str(exc), "Choose a new application_id", application_id=application_id)
    except RuntimeError as exc:
        return _err("Unavailable", str(exc), "Start MCP server")


@mcp.tool()
async def start_agent_session(agent_id: str, session_id: str, model_version: str) -> Dict[str, Any]:
    """
    Start an agent session for downstream decision events.
    Preconditions: session must be new; model_version must be the model to lock the session to.
    """
    try:
        store = await _ensure_ready()
        new_version = await handle_start_agent_session(
            StartAgentSessionCommand(agent_id=agent_id, session_id=session_id, model_version=model_version),
            store,
        )
        return _ok(agent_id=agent_id, session_id=session_id, new_version=new_version)
    except OptimisticConcurrencyError as exc:
        return _err(
            "OptimisticConcurrencyError",
            str(exc),
            "Reload the stream and retry the command",
            **exc.details,
        )
    except DomainError as exc:
        return _err("DomainError", str(exc), "Use a new session_id", agent_id=agent_id, session_id=session_id)
    except RuntimeError as exc:
        return _err("Unavailable", str(exc), "Start MCP server")


@mcp.tool()
async def record_credit_analysis(
    application_id: str,
    agent_id: str,
    session_id: str,
    model_version: str,
    confidence_score: Optional[float] = None,
    risk_tier: Optional[str] = None,
    recommended_limit_usd: Optional[float] = None,
    regulatory_basis: Optional[List[str]] = None,
    use_openrouter: bool = False,
) -> Dict[str, Any]:
    """
    Record credit analysis output.
    Preconditions: application must be in analysis phase; agent session must exist, be context-loaded, and model-locked.
    """
    try:
        store = await _ensure_ready()
        decision_id = f"credit-{application_id}-{session_id}"
        await mark_agent_decision_pending(store, agent_id=agent_id, session_id=session_id, decision_id=decision_id)

        if use_openrouter:
            stream = await store.load_stream(f"loan-{application_id}")
            submit = next((e for e in stream if e.event_type == "ApplicationSubmitted"), None)
            if submit is None:
                return _err(
                    "DomainError",
                    "ApplicationSubmitted event missing",
                    "Submit the application first",
                    application_id=application_id,
                )
            try:
                model_result = _openrouter_client.analyze_credit_application(
                    application_id=application_id,
                    requested_amount_usd=float(submit.payload.get("requested_amount_usd", 0)),
                    applicant_id=str(submit.payload.get("applicant_id", "unknown")),
                )
            except OpenRouterError as exc:
                return _err(
                    "OpenRouterError",
                    str(exc),
                    "Set OPENROUTER_API_KEY (or openrouterOPENROUTER_API_KEY) and retry",
                    application_id=application_id,
                )
            model_version = model_result.model_version
            confidence_score = model_result.confidence_score
            risk_tier = model_result.risk_tier
            recommended_limit_usd = model_result.recommended_limit_usd
            regulatory_basis = model_result.regulatory_basis

        if confidence_score is None or risk_tier is None or recommended_limit_usd is None:
            return _err(
                "ValidationError",
                "confidence_score, risk_tier, and recommended_limit_usd are required when use_openrouter=false",
                "Provide explicit analysis values or set use_openrouter=true",
                application_id=application_id,
            )

        new_version = await handle_record_credit_analysis(
            RecordCreditAnalysisCommand(
                application_id=application_id,
                agent_id=agent_id,
                session_id=session_id,
                model_version=model_version,
                confidence_score=float(confidence_score),
                risk_tier=str(risk_tier),
                recommended_limit_usd=float(recommended_limit_usd),
                regulatory_basis=regulatory_basis,
            ),
            store,
        )
        await mark_agent_decision_completed(store, agent_id=agent_id, session_id=session_id, decision_id=decision_id)
        return _ok(application_id=application_id, new_version=new_version)
    except OptimisticConcurrencyError as exc:
        return _err(
            "OptimisticConcurrencyError",
            str(exc),
            "Reload the stream and retry the command",
            **exc.details,
        )
    except DomainError as exc:
        return _err(
            "DomainError",
            str(exc),
            "Call start_agent_session first or move application to required state",
            application_id=application_id,
            agent_id=agent_id,
            session_id=session_id,
        )
    except RuntimeError as exc:
        return _err("Unavailable", str(exc), "Start MCP server")


@mcp.tool()
async def record_fraud_screening(
    application_id: str,
    risk_score: float,
    flags: List[str],
) -> Dict[str, Any]:
    """
    Record fraud screening results.
    Preconditions: application must exist.
    """
    try:
        store = await _ensure_ready()
        new_version = await handle_record_fraud_screening(
            RecordFraudScreeningCommand(application_id=application_id, risk_score=risk_score, flags=flags),
            store,
        )
        return _ok(application_id=application_id, new_version=new_version)
    except OptimisticConcurrencyError as exc:
        return _err(
            "OptimisticConcurrencyError",
            str(exc),
            "Reload the stream and retry the command",
            **exc.details,
        )
    except DomainError as exc:
        return _err("DomainError", str(exc), "Submit application before fraud screening", application_id=application_id)
    except RuntimeError as exc:
        return _err("Unavailable", str(exc), "Start MCP server")


@mcp.tool()
async def record_compliance_check(
    application_id: str,
    passed_rules: List[str],
    failed_rules: List[Dict[str, str]],
    verdict: str,
) -> Dict[str, Any]:
    """
    Record compliance evaluation outcomes.
    Preconditions: application must be ready for compliance completion; verdict must be one of CLEAR/BLOCKED/CONDITIONAL.
    """
    if verdict not in {"CLEAR", "BLOCKED", "CONDITIONAL"}:
        return _err("ValidationError", "Invalid verdict", "Use CLEAR, BLOCKED, or CONDITIONAL", verdict=verdict)

    try:
        store = await _ensure_ready()
        new_version = await handle_record_compliance_check(
            RecordComplianceCheckCommand(
                application_id=application_id,
                passed_rules=passed_rules,
                failed_rules=failed_rules,
                verdict=verdict,
            ),
            store,
        )
        return _ok(application_id=application_id, new_version=new_version)
    except OptimisticConcurrencyError as exc:
        return _err(
            "OptimisticConcurrencyError",
            str(exc),
            "Reload the stream and retry the command",
            **exc.details,
        )
    except DomainError as exc:
        return _err("DomainError", str(exc), "Record analysis events first", application_id=application_id)
    except RuntimeError as exc:
        return _err("Unavailable", str(exc), "Start MCP server")


@mcp.tool()
async def generate_decision(
    application_id: str,
    recommendation: str,
    confidence_score: float,
    reason: str,
    contributing_agent_sessions: List[str],
    model_versions: Dict[str, str],
) -> Dict[str, Any]:
    """
    Generate a machine decision recommendation.
    Preconditions: compliance must be completed before APPROVE; contributing sessions must be valid causal contributors.
    """
    try:
        store = await _ensure_ready()
        new_version = await handle_generate_decision(
            GenerateDecisionCommand(
                application_id=application_id,
                recommendation=recommendation,
                confidence_score=confidence_score,
                reason=reason,
                contributing_agent_sessions=contributing_agent_sessions,
                model_versions=model_versions,
            ),
            store,
        )
        return _ok(application_id=application_id, new_version=new_version)
    except OptimisticConcurrencyError as exc:
        return _err(
            "OptimisticConcurrencyError",
            str(exc),
            "Reload the stream and retry the command",
            **exc.details,
        )
    except DomainError as exc:
        return _err(
            "DomainError",
            str(exc),
            "Ensure compliance and causal prerequisites are met",
            application_id=application_id,
        )
    except RuntimeError as exc:
        return _err("Unavailable", str(exc), "Start MCP server")


@mcp.tool()
async def record_human_review(
    application_id: str,
    reviewer_id: str,
    final_decision: str,
    final_amount_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Record human override/final decision.
    Preconditions: a machine decision must exist; final_decision must be APPROVE or DECLINE.
    """
    if final_decision not in {"APPROVE", "DECLINE"}:
        return _err("ValidationError", "final_decision must be APPROVE or DECLINE", "Use valid final_decision")

    try:
        store = await _ensure_ready()
        new_version = await handle_human_review(
            HumanReviewCommand(
                application_id=application_id,
                reviewer_id=reviewer_id,
                final_decision=final_decision,
                final_amount_usd=final_amount_usd,
            ),
            store,
        )
        return _ok(application_id=application_id, new_version=new_version)
    except OptimisticConcurrencyError as exc:
        return _err(
            "OptimisticConcurrencyError",
            str(exc),
            "Reload the stream and retry the command",
            **exc.details,
        )
    except DomainError as exc:
        return _err("DomainError", str(exc), "Generate decision before human review", application_id=application_id)
    except RuntimeError as exc:
        return _err("Unavailable", str(exc), "Start MCP server")


@mcp.tool()
async def run_integrity_check(entity_type: str, entity_id: str) -> Dict[str, Any]:
    """
    Run audit integrity check.
    Preconditions: target stream must exist and be append-only event stream.
    """
    try:
        store = await _ensure_ready()
        result = await run_integrity_check_impl(store, entity_type=entity_type, entity_id=entity_id)
        return _ok(**result.model_dump())
    except DomainError as exc:
        return _err("DomainError", str(exc), "Resolve audit state and retry", entity_type=entity_type, entity_id=entity_id)
    except OptimisticConcurrencyError as exc:
        return _err(
            "OptimisticConcurrencyError",
            str(exc),
            "Reload the stream and retry the command",
            **exc.details,
        )
    except RuntimeError as exc:
        return _err("Unavailable", str(exc), "Start MCP server")


@mcp.tool()
async def flush_projections(max_passes: int = 10) -> Dict[str, Any]:
    """
    Flush projection updates for tests/debugging.
    Preconditions: projection daemon must be configured; max_passes must be between 1 and 100.
    """
    if _projection_daemon is None:
        return _err("Unavailable", "Projection daemon not configured", "Start server and configure daemon")
    if max_passes < 1 or max_passes > 100:
        return _err("ValidationError", "max_passes must be between 1 and 100", "Use a bounded max_passes value")

    final_lag: Dict[str, int] = {}
    for i in range(max_passes):
        await _projection_daemon.run_once()
        final_lag = await _projection_daemon.get_lag()
        if final_lag and all(ms == 0 for ms in final_lag.values()):
            return _ok(passes_run=i + 1, projection_lag_ms=final_lag)

    return _ok(passes_run=max_passes, projection_lag_ms=final_lag)


@mcp.resource("ledger://applications/{application_id}")
async def application_summary(application_id: str) -> str:
    """Projection-backed application summary resource."""
    if _db_pool is None:
        return _resource(_err("Unavailable", "Server not started", "Start MCP server"))
    async with _db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM read_application_summary WHERE application_id = $1",
            application_id,
        )
        if not row:
            return _resource(
                _err(
                    "NotFound",
                    "No application summary",
                    "Wait for projection daemon or verify id",
                    application_id=application_id,
                )
            )
        return _resource(_ok(data=dict(row)))


@mcp.resource("ledger://applications/{application_id}/compliance")
async def compliance_summary(application_id: str) -> str:
    """Projection-backed compliance record resource."""
    if _db_pool is None:
        return _resource(_err("Unavailable", "Server not started", "Start MCP server"))
    async with _db_pool.acquire() as conn:
        verdict = await conn.fetchrow(
            "SELECT * FROM read_compliance_verdicts WHERE application_id = $1",
            application_id,
        )
        logs = await conn.fetch(
            "SELECT event_type, rule_id, status, details, recorded_at FROM read_compliance_audit_logs WHERE application_id = $1 ORDER BY recorded_at ASC",
            application_id,
        )
        if verdict is None and not logs:
            return _resource(
                _err(
                    "NotFound",
                    "No compliance record",
                    "Record compliance check first",
                    application_id=application_id,
                )
            )
        return _resource(_ok(verdict=dict(verdict) if verdict else None, events=[dict(row) for row in logs]))


@mcp.resource("ledger://agents/{agent_id}/{session_id}")
async def agent_performance(agent_id: str, session_id: str) -> str:
    """Justified exception: agent-session detail reads event stream for debugging continuity."""
    try:
        store = await _ensure_ready()
        events = await store.load_stream(f"agent-{agent_id}-{session_id}")
        return _resource(_ok(events=[event.model_dump() for event in events]))
    except RuntimeError as exc:
        return _resource(_err("Unavailable", str(exc), "Start MCP server"))


@mcp.resource("ledger://audit-trail/{entity_type}/{entity_id}")
async def audit_trail(entity_type: str, entity_id: str) -> str:
    """Justified exception: cryptographic audit trail reads canonical stream data directly."""
    try:
        store = await _ensure_ready()
        events = await store.load_stream(f"{entity_type}-{entity_id}")
        return _resource(_ok(events=[event.model_dump() for event in events]))
    except RuntimeError as exc:
        return _resource(_err("Unavailable", str(exc), "Start MCP server"))


@mcp.resource("ledger://ledger/health")
async def ledger_health() -> str:
    """Projection lag health with per-projection milliseconds."""
    if _projection_daemon is None:
        return _resource(_err("Unavailable", "Projection daemon not configured", "Start server and configure daemon"))
    lag = await _projection_daemon.get_lag()
    return _resource(_ok(projection_lag_ms=lag, checked_at=datetime.now(timezone.utc).isoformat()))


@mcp.resource("ledger://agents/performance/{agent_id}")
async def agent_performance_summary(agent_id: str) -> str:
    """Projection-backed agent performance resource."""
    if _db_pool is None:
        return _resource(_err("Unavailable", "Server not started", "Start MCP server"))
    async with _db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM read_agent_performance WHERE agent_id = $1 ORDER BY model_version ASC",
            agent_id,
        )
        return _resource(_ok(rows=[dict(row) for row in rows]))


if __name__ == "__main__":
    mcp.run()
