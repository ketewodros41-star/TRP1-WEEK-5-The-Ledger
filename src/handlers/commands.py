from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..aggregates.agent_session import AgentSessionAggregate
from ..aggregates.compliance_record import ComplianceRecordAggregate
from ..aggregates.loan_application import ApplicationState, LoanApplicationAggregate
from ..eventstore import EventStore
from ..models.events import (
    AgentContextLoaded,
    AgentDecisionCompleted,
    AgentDecisionPending,
    AgentSessionStarted,
    ApplicationApproved,
    ApplicationDeclined,
    ApplicationSubmitted,
    ComplianceCheckCompleted,
    ComplianceCheckRequested,
    ComplianceRuleFailed,
    ComplianceRulePassed,
    CreditAnalysisCompleted,
    CreditAnalysisRequested,
    DecisionGenerated,
    DomainError,
    FraudScreeningCompleted,
    HumanReviewCompleted,
)


@dataclass
class ApplicationSubmittedCommand:
    application_id: str
    applicant_id: str
    requested_amount_usd: float
    correlation_id: str


@dataclass
class StartAgentSessionCommand:
    agent_id: str
    session_id: str
    model_version: str


@dataclass
class RecordCreditAnalysisCommand:
    application_id: str
    agent_id: str
    session_id: str
    model_version: str
    confidence_score: float
    risk_tier: str
    recommended_limit_usd: float
    regulatory_basis: Optional[List[str]] = None


@dataclass
class RecordFraudScreeningCommand:
    application_id: str
    risk_score: float
    flags: List[str]


@dataclass
class RecordComplianceCheckCommand:
    application_id: str
    passed_rules: List[str]
    failed_rules: List[Dict[str, str]]
    verdict: str


@dataclass
class GenerateDecisionCommand:
    application_id: str
    recommendation: str
    confidence_score: float
    reason: str
    contributing_agent_sessions: List[str]
    model_versions: Dict[str, str]


@dataclass
class HumanReviewCommand:
    application_id: str
    reviewer_id: str
    final_decision: str
    final_amount_usd: Optional[float] = None


async def handle_submit_application(cmd: ApplicationSubmittedCommand, store: EventStore) -> int:
    app = await LoanApplicationAggregate.load(store, cmd.application_id)
    app.assert_can_submit()

    event = ApplicationSubmitted(
        application_id=cmd.application_id,
        applicant_id=cmd.applicant_id,
        requested_amount_usd=cmd.requested_amount_usd,
    )
    return await store.append(
        stream_id=app.stream_id,
        events=[event],
        expected_version=app.version,
        correlation_id=cmd.correlation_id,
    )


async def handle_start_agent_session(cmd: StartAgentSessionCommand, store: EventStore) -> int:
    session = await AgentSessionAggregate.load(store, cmd.agent_id, cmd.session_id)
    if session.version != -1:
        raise DomainError("Agent session already exists")

    events = [
        AgentSessionStarted(
            agent_id=cmd.agent_id,
            session_id=cmd.session_id,
            model_version=cmd.model_version,
        ),
        AgentContextLoaded(
            agent_id=cmd.agent_id,
            session_id=cmd.session_id,
            context_token_count=0,
        ),
    ]
    return await store.append(
        stream_id=session.stream_id,
        events=events,
        expected_version=-1,
        aggregate_type="agent",
    )


async def handle_record_credit_analysis(cmd: RecordCreditAnalysisCommand, store: EventStore) -> int:
    app = await LoanApplicationAggregate.load(store, cmd.application_id)
    agent = await AgentSessionAggregate.load(store, cmd.agent_id, cmd.session_id)

    app.assert_transition(ApplicationState.AWAITING_ANALYSIS)
    agent.assert_started()
    agent.assert_context_loaded_before_decision()
    agent.assert_model_version_locked(cmd.model_version)

    events = [
        CreditAnalysisRequested(application_id=cmd.application_id),
        CreditAnalysisCompleted(
            application_id=cmd.application_id,
            agent_id=cmd.agent_id,
            session_id=cmd.session_id,
            model_version=cmd.model_version,
            confidence_score=cmd.confidence_score,
            risk_tier=cmd.risk_tier,
            recommended_limit_usd=cmd.recommended_limit_usd,
            regulatory_basis=cmd.regulatory_basis,
        ),
    ]
    return await store.append(
        stream_id=app.stream_id,
        events=events,
        expected_version=app.version,
    )


async def handle_record_fraud_screening(cmd: RecordFraudScreeningCommand, store: EventStore) -> int:
    app = await LoanApplicationAggregate.load(store, cmd.application_id)

    event = FraudScreeningCompleted(
        application_id=cmd.application_id,
        risk_score=cmd.risk_score,
        flags=cmd.flags,
    )
    return await store.append(stream_id=app.stream_id, events=[event], expected_version=app.version)


async def handle_record_compliance_check(cmd: RecordComplianceCheckCommand, store: EventStore) -> int:
    app = await LoanApplicationAggregate.load(store, cmd.application_id)
    compliance = await ComplianceRecordAggregate.load(store, cmd.application_id)

    app.assert_transition(ApplicationState.COMPLIANCE_REVIEW)
    compliance.assert_not_completed()

    events = []
    events.append(ComplianceCheckRequested(application_id=cmd.application_id))
    for rule_id in cmd.passed_rules:
        events.append(ComplianceRulePassed(application_id=cmd.application_id, rule_id=rule_id))
    for failed in cmd.failed_rules:
        events.append(
            ComplianceRuleFailed(
                application_id=cmd.application_id,
                rule_id=failed["rule_id"],
                failure_reason=failed["reason"],
                is_hard_block=bool(failed.get("is_hard_block", False)),
            )
        )
    events.append(ComplianceCheckCompleted(application_id=cmd.application_id, verdict=cmd.verdict))
    return await store.append(
        stream_id=app.stream_id,
        events=events,
        expected_version=app.version,
    )


async def handle_generate_decision(cmd: GenerateDecisionCommand, store: EventStore) -> int:
    app = await LoanApplicationAggregate.load(store, cmd.application_id)

    known_sessions = set()
    for session_id in cmd.contributing_agent_sessions:
        stream_id = session_id if session_id.startswith("agent-") else f"agent-{session_id}"
        rows = await store.load_stream(stream_id)
        if rows:
            known_sessions.add(session_id)

    recommendation, _ = app.determine_and_validate_decision(
        requested_recommendation=cmd.recommendation,
        confidence_score=cmd.confidence_score,
        contributing_sessions=cmd.contributing_agent_sessions,
        known_sessions=known_sessions,
        model_versions=cmd.model_versions,
    )

    decision_event = DecisionGenerated(
        application_id=cmd.application_id,
        recommendation=recommendation,
        confidence_score=cmd.confidence_score,
        reason=cmd.reason,
        contributing_agent_sessions=cmd.contributing_agent_sessions,
        model_versions=cmd.model_versions,
    )
    return await store.append(stream_id=app.stream_id, events=[decision_event], expected_version=app.version)


async def handle_human_review(cmd: HumanReviewCommand, store: EventStore) -> int:
    app = await LoanApplicationAggregate.load(store, cmd.application_id)
    target_state = (
        ApplicationState.FINAL_APPROVED
        if cmd.final_decision == "APPROVE"
        else ApplicationState.FINAL_DECLINED
    )
    app.assert_transition(target_state)

    events = [
        HumanReviewCompleted(
            application_id=cmd.application_id,
            reviewer_id=cmd.reviewer_id,
            final_decision=cmd.final_decision,
            final_amount_usd=cmd.final_amount_usd,
        )
    ]
    if cmd.final_decision == "APPROVE":
        amount = cmd.final_amount_usd or float(app.recommended_limit)
        events.append(ApplicationApproved(application_id=cmd.application_id, approved_amount_usd=amount))
    else:
        events.append(ApplicationDeclined(application_id=cmd.application_id, reason="Human reviewer decline"))

    return await store.append(stream_id=app.stream_id, events=events, expected_version=app.version)


async def mark_agent_decision_pending(store: EventStore, agent_id: str, session_id: str, decision_id: str) -> int:
    session = await AgentSessionAggregate.load(store, agent_id, session_id)
    session.assert_started()
    session.assert_context_loaded_before_decision()

    return await store.append(
        stream_id=session.stream_id,
        events=[AgentDecisionPending(agent_id=agent_id, session_id=session_id, decision_id=decision_id)],
        expected_version=session.version,
        aggregate_type="agent",
    )


async def mark_agent_decision_completed(store: EventStore, agent_id: str, session_id: str, decision_id: str) -> int:
    session = await AgentSessionAggregate.load(store, agent_id, session_id)
    session.assert_started()
    return await store.append(
        stream_id=session.stream_id,
        events=[AgentDecisionCompleted(agent_id=agent_id, session_id=session_id, decision_id=decision_id)],
        expected_version=session.version,
        aggregate_type="agent",
    )
