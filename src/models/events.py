from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    APPLICATION_SUBMITTED = "ApplicationSubmitted"
    CREDIT_ANALYSIS_REQUESTED = "CreditAnalysisRequested"
    CREDIT_ANALYSIS_COMPLETED = "CreditAnalysisCompleted"
    FRAUD_SCREENING_REQUESTED = "FraudScreeningRequested"
    FRAUD_SCREENING_COMPLETED = "FraudScreeningCompleted"
    COMPLIANCE_CHECK_REQUESTED = "ComplianceCheckRequested"
    COMPLIANCE_RULE_PASSED = "ComplianceRulePassed"
    COMPLIANCE_RULE_FAILED = "ComplianceRuleFailed"
    COMPLIANCE_CHECK_COMPLETED = "ComplianceCheckCompleted"
    DECISION_GENERATED = "DecisionGenerated"
    HUMAN_REVIEW_COMPLETED = "HumanReviewCompleted"
    APPLICATION_APPROVED = "ApplicationApproved"
    APPLICATION_DECLINED = "ApplicationDeclined"

    AGENT_SESSION_STARTED = "AgentSessionStarted"
    AGENT_CONTEXT_LOADED = "AgentContextLoaded"
    AGENT_NODE_EXECUTED = "AgentNodeExecuted"
    AGENT_TOOL_CALLED = "AgentToolCalled"
    AGENT_DECISION_PENDING = "AgentDecisionPending"
    AGENT_OUTPUT_WRITTEN = "AgentOutputWritten"
    AGENT_DECISION_COMPLETED = "AgentDecisionCompleted"
    AGENT_SESSION_COMPLETED = "AgentSessionCompleted"
    AGENT_SESSION_FAILED = "AgentSessionFailed"
    AGENT_SESSION_RECOVERED = "AgentSessionRecovered"

    AUDIT_INTEGRITY_CHECK_RUN = "AuditIntegrityCheckRun"


class EventStoreError(Exception):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


class OptimisticConcurrencyError(EventStoreError):
    def __init__(self, stream_id: str, expected_version: int, actual_version: int):
        self.stream_id = stream_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Optimistic concurrency collision on {stream_id}: expected {expected_version}, actual {actual_version}",
            {
                "error_type": "OptimisticConcurrencyError",
                "stream_id": stream_id,
                "expected_version": expected_version,
                "actual_version": actual_version,
                "suggested_action": "reload_stream_and_retry",
            },
        )


class DomainError(EventStoreError):
    pass


class BaseEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    event_type: str
    event_version: int = 1


class StoredEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    event_id: UUID
    stream_id: str
    stream_position: int
    global_position: int
    event_type: str
    event_version: int
    payload: Dict[str, Any]
    metadata: Dict[str, Any]
    recorded_at: datetime


class StreamMetadata(BaseModel):
    stream_id: str
    aggregate_type: str
    current_version: int
    created_at: datetime
    archived_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IntegrityCheckResult(BaseModel):
    chain_valid: bool
    tamper_detected: bool
    checked_event_count: int
    final_hash: str


class MCPError(BaseModel):
    error_type: str
    message: str
    context: Dict[str, Any] = Field(default_factory=dict)
    suggested_action: str


class AgentContext(BaseModel):
    agent_id: str
    session_id: str
    context_text: str
    last_event_position: int
    pending_work: List[str]
    session_health_status: Literal["HEALTHY", "NEEDS_RECONCILIATION", "FAILED"]


class ApplicationSubmitted(BaseEvent):
    event_type: str = EventType.APPLICATION_SUBMITTED.value
    application_id: str
    applicant_id: str
    requested_amount_usd: float


class CreditAnalysisRequested(BaseEvent):
    event_type: str = EventType.CREDIT_ANALYSIS_REQUESTED.value
    application_id: str


class CreditAnalysisCompleted(BaseEvent):
    event_type: str = EventType.CREDIT_ANALYSIS_COMPLETED.value
    application_id: str
    agent_id: str
    session_id: str
    model_version: str
    confidence_score: Optional[float] = None
    risk_tier: str
    recommended_limit_usd: float
    regulatory_basis: Optional[List[str]] = None


class FraudScreeningRequested(BaseEvent):
    event_type: str = EventType.FRAUD_SCREENING_REQUESTED.value
    application_id: str


class FraudScreeningCompleted(BaseEvent):
    event_type: str = EventType.FRAUD_SCREENING_COMPLETED.value
    application_id: str
    risk_score: float
    flags: List[str] = Field(default_factory=list)


class ComplianceCheckRequested(BaseEvent):
    event_type: str = EventType.COMPLIANCE_CHECK_REQUESTED.value
    application_id: str


class ComplianceRulePassed(BaseEvent):
    event_type: str = EventType.COMPLIANCE_RULE_PASSED.value
    application_id: str
    rule_id: str
    details: Dict[str, Any] = Field(default_factory=dict)


class ComplianceRuleFailed(BaseEvent):
    event_type: str = EventType.COMPLIANCE_RULE_FAILED.value
    application_id: str
    rule_id: str
    is_hard_block: bool = False
    failure_reason: str


class ComplianceCheckCompleted(BaseEvent):
    event_type: str = EventType.COMPLIANCE_CHECK_COMPLETED.value
    application_id: str
    verdict: Literal["CLEAR", "BLOCKED", "CONDITIONAL"]


class DecisionGenerated(BaseEvent):
    event_type: str = EventType.DECISION_GENERATED.value
    application_id: str
    recommendation: Literal["APPROVE", "DECLINE", "REFER"]
    confidence_score: float
    reason: str
    contributing_agent_sessions: List[str] = Field(default_factory=list)
    model_versions: Optional[Dict[str, str]] = None


class HumanReviewCompleted(BaseEvent):
    event_type: str = EventType.HUMAN_REVIEW_COMPLETED.value
    application_id: str
    reviewer_id: str
    final_decision: Literal["APPROVE", "DECLINE"]
    final_amount_usd: Optional[float] = None


class ApplicationApproved(BaseEvent):
    event_type: str = EventType.APPLICATION_APPROVED.value
    application_id: str
    approved_amount_usd: float


class ApplicationDeclined(BaseEvent):
    event_type: str = EventType.APPLICATION_DECLINED.value
    application_id: str
    reason: str


class AgentSessionStarted(BaseEvent):
    event_type: str = EventType.AGENT_SESSION_STARTED.value
    agent_id: str
    session_id: str
    model_version: str


class AgentContextLoaded(BaseEvent):
    event_type: str = EventType.AGENT_CONTEXT_LOADED.value
    agent_id: str
    session_id: str
    context_token_count: int


class AgentNodeExecuted(BaseEvent):
    event_type: str = EventType.AGENT_NODE_EXECUTED.value
    agent_id: str
    session_id: str
    node_name: str
    status: Literal["PENDING", "OK", "ERROR"]


class AgentToolCalled(BaseEvent):
    event_type: str = EventType.AGENT_TOOL_CALLED.value
    agent_id: str
    session_id: str
    tool_name: str


class AgentDecisionPending(BaseEvent):
    event_type: str = EventType.AGENT_DECISION_PENDING.value
    agent_id: str
    session_id: str
    decision_id: str


class AgentOutputWritten(BaseEvent):
    event_type: str = EventType.AGENT_OUTPUT_WRITTEN.value
    agent_id: str
    session_id: str
    events_written: List[str] = Field(default_factory=list)


class AgentDecisionCompleted(BaseEvent):
    event_type: str = EventType.AGENT_DECISION_COMPLETED.value
    agent_id: str
    session_id: str
    decision_id: str


class AgentSessionCompleted(BaseEvent):
    event_type: str = EventType.AGENT_SESSION_COMPLETED.value
    agent_id: str
    session_id: str


class AgentSessionFailed(BaseEvent):
    event_type: str = EventType.AGENT_SESSION_FAILED.value
    agent_id: str
    session_id: str
    reason: str


class AgentSessionRecovered(BaseEvent):
    event_type: str = EventType.AGENT_SESSION_RECOVERED.value
    agent_id: str
    session_id: str
    recovered_from_position: int


class AuditIntegrityCheckRun(BaseEvent):
    event_type: str = EventType.AUDIT_INTEGRITY_CHECK_RUN.value
    chain_valid: bool
    tamper_detected: bool
    checked_event_count: int
    final_hash: str
