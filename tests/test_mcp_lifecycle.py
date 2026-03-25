import json
from uuid import uuid4

import pytest

from src.mcp import server as mcp_server


def _decode_tool_result(result):
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise AssertionError("Unable to decode MCP tool result")


def _decode_resource_result(result):
    for content in getattr(result, "contents", []):
        text = getattr(content, "text", None) or getattr(content, "content", None)
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise AssertionError("Unable to decode MCP resource result")


@pytest.mark.asyncio
async def test_full_lifecycle_via_mcp_only(event_store, projection_daemon, db_pool):
    mcp_server.configure_runtime_for_tests(db_pool, event_store, projection_daemon)

    app_id = f"app-{uuid4()}"
    agent_id = "credit-agent"
    session_id = str(uuid4())

    submit = _decode_tool_result(
        await mcp_server.mcp.call_tool(
            "submit_application",
            {
                "application_id": app_id,
                "applicant_id": "customer-1",
                "requested_amount_usd": 120000,
                "correlation_id": f"corr-{uuid4()}",
            },
        )
    )
    assert submit["ok"] is True

    start = _decode_tool_result(
        await mcp_server.mcp.call_tool(
            "start_agent_session",
            {
                "agent_id": agent_id,
                "session_id": session_id,
                "model_version": "credit-v2",
            },
        )
    )
    assert start["ok"] is True

    analysis = _decode_tool_result(
        await mcp_server.mcp.call_tool(
            "record_credit_analysis",
            {
                "application_id": app_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "model_version": "credit-v2",
                "confidence_score": 0.82,
                "risk_tier": "LOW",
                "recommended_limit_usd": 90000,
                "regulatory_basis": ["REG-KYC-2"],
            },
        )
    )
    assert analysis["ok"] is True

    fraud = _decode_tool_result(
        await mcp_server.mcp.call_tool(
            "record_fraud_screening",
            {
                "application_id": app_id,
                "risk_score": 0.12,
                "flags": [],
            },
        )
    )
    assert fraud["ok"] is True

    compliance = _decode_tool_result(
        await mcp_server.mcp.call_tool(
            "record_compliance_check",
            {
                "application_id": app_id,
                "passed_rules": ["KYC", "AML", "SANCTIONS"],
                "failed_rules": [],
                "verdict": "CLEAR",
            },
        )
    )
    assert compliance["ok"] is True

    decision = _decode_tool_result(
        await mcp_server.mcp.call_tool(
            "generate_decision",
            {
                "application_id": app_id,
                "recommendation": "APPROVE",
                "confidence_score": 0.92,
                "reason": "Low risk profile",
                "contributing_agent_sessions": [f"agent-{agent_id}-{session_id}"],
                "model_versions": {f"agent-{agent_id}-{session_id}": "credit-v2"},
            },
        )
    )
    assert decision["ok"] is True

    human = _decode_tool_result(
        await mcp_server.mcp.call_tool(
            "record_human_review",
            {
                "application_id": app_id,
                "reviewer_id": "officer-77",
                "final_decision": "APPROVE",
                "final_amount_usd": 85000,
            },
        )
    )
    assert human["ok"] is True

    flushed = _decode_tool_result(await mcp_server.mcp.call_tool("flush_projections", {"max_passes": 8}))
    assert flushed["ok"] is True

    compliance_resource = _decode_resource_result(
        await mcp_server.mcp.read_resource(f"ledger://applications/{app_id}/compliance")
    )
    assert compliance_resource["ok"] is True
    event_types = {row["event_type"] for row in compliance_resource["events"]}
    assert {"ComplianceRulePassed", "ComplianceCheckCompleted"}.issubset(event_types)

    trail = _decode_resource_result(await mcp_server.mcp.read_resource(f"ledger://audit-trail/loan/{app_id}"))
    assert trail["ok"] is True
    stream_event_types = {row["event_type"] for row in trail["events"]}
    assert "FraudScreeningCompleted" in stream_event_types

    integrity = _decode_tool_result(
        await mcp_server.mcp.call_tool("run_integrity_check", {"entity_type": "loan", "entity_id": app_id})
    )
    assert integrity["ok"] is True
    assert integrity["tamper_detected"] is False
