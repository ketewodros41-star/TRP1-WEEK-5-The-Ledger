import pytest

from src.aggregates.audit_ledger import AuditLedgerAggregate
from src.aggregates.agent_session import AgentSessionAggregate
from src.aggregates.loan_application import ApplicationState, LoanApplicationAggregate
from src.models.events import DomainError, StoredEvent


def test_loan_state_machine_and_business_rules():
    app = LoanApplicationAggregate("a1")
    app.state = ApplicationState.SUBMITTED
    with pytest.raises(DomainError):
        app.assert_transition(ApplicationState.FINAL_APPROVED)

    assert app.determine_recommendation("APPROVE", 0.55) == "REFER"

    with pytest.raises(DomainError):
        app.assert_compliance_dependency_for_approval("APPROVE")

    with pytest.raises(DomainError):
        app.assert_causal_chain(["agent-a", "agent-b"], {"agent-a"})

    app.state = ApplicationState.PENDING_DECISION
    app.compliance_completed = True
    recommendation, next_state = app.determine_and_validate_decision(
        requested_recommendation="APPROVE",
        confidence_score=0.91,
        contributing_sessions=["agent-a"],
        known_sessions={"agent-a"},
        model_versions={"agent-a": "m1"},
    )
    assert recommendation == "APPROVE"
    assert next_state == ApplicationState.APPROVED_PENDING_HUMAN

    app.state = ApplicationState.PENDING_DECISION
    with pytest.raises(DomainError):
        app.determine_and_validate_decision(
            requested_recommendation="APPROVE",
            confidence_score=0.91,
            contributing_sessions=["agent-a"],
            known_sessions={"agent-a"},
            model_versions={},
        )


def test_agent_session_rules():
    session = AgentSessionAggregate("agent-1", "s1")
    with pytest.raises(DomainError):
        session.assert_started()

    session.started = True
    with pytest.raises(DomainError):
        session.assert_context_loaded_before_decision()

    session.context_loaded = True
    session.model_version = "m1"
    with pytest.raises(DomainError):
        session.assert_model_version_locked("m2")


def test_loan_replay_applies_state_machine_transitions():
    app = LoanApplicationAggregate("a1")
    app.state = ApplicationState.SUBMITTED

    bad_event = StoredEvent.model_validate(
        {
            "event_id": "11111111-1111-1111-1111-111111111111",
            "stream_id": "loan-a1",
            "stream_position": 2,
            "global_position": 2,
            "event_type": "ComplianceCheckRequested",
            "event_version": 1,
            "payload": {"application_id": "a1"},
            "metadata": {},
            "recorded_at": "2026-03-25T00:00:00Z",
        }
    )

    with pytest.raises(DomainError):
        app.apply_events([bad_event])


def test_audit_chain_cannot_be_marked_valid_after_break():
    audit = AuditLedgerAggregate("loan", "a1")
    audit.chain_broken = True

    with pytest.raises(DomainError):
        audit.assert_chain_not_broken(next_chain_valid=True)
