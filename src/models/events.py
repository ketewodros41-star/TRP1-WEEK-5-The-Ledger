# src/models/events.py
from datetime import datetime
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum
from typing import Any, Dict, List, Optional

class EventType(str, Enum):
    # Loan Application Lifecycle
    APPLICATION_SUBMITTED = "ApplicationSubmitted"
    DOCUMENT_UPLOAD_REQUESTED = "DocumentUploadRequested"
    DOCUMENT_UPLOADED = "DocumentUploaded"
    CREDIT_ANALYSIS_REQUESTED = "CreditAnalysisRequested"
    FRAUD_SCREENING_REQUESTED = "FraudScreeningRequested"
    COMPLIANCE_CHECK_REQUESTED = "ComplianceCheckRequested"
    DECISION_GENERATED = "DecisionGenerated"
    HUMAN_REVIEW_COMPLETED = "HumanReviewCompleted"
    APPLICATION_APPROVED = "ApplicationApproved"
    APPLICATION_DECLINED = "ApplicationDeclined"

    # Agent Sessions (Gas Town)
    AGENT_SESSION_STARTED = "AgentSessionStarted"
    AGENT_NODE_EXECUTED = "AgentNodeExecuted"
    AGENT_TOOL_CALLED = "AgentToolCalled"
    AGENT_OUTPUT_WRITTEN = "AgentOutputWritten"
    AGENT_SESSION_COMPLETED = "AgentSessionCompleted"
    AGENT_SESSION_FAILED = "AgentSessionFailed"
    AGENT_SESSION_RECOVERED = "AgentSessionRecovered"

    # Integrity
    AUDIT_INTEGRITY_CHECK_RUN = "AuditIntegrityCheckRun"

    # Domain Specific
    CREDIT_ANALYSIS_COMPLETED = "CreditAnalysisCompleted"
    FRAUD_SCREENING_COMPLETED = "FraudScreeningCompleted"
    COMPLIANCE_RULE_PASSED = "ComplianceRulePassed"
    COMPLIANCE_RULE_FAILED = "ComplianceRuleFailed"

class BaseEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    event_type: str = Field(..., description="The type of the event (e.g., ApplicationSubmitted)")
    event_version: int = Field(default=1, description="Schema version of the event payload")

class StoredEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
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
    metadata: Dict[str, Any] = {}

# Exception Types
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
            f"Optimistic concurrency collision on stream {stream_id}. Expected version {expected_version}, but actual version is {actual_version}.",
            {
                "error_type": "OptimisticConcurrencyError",
                "stream_id": stream_id,
                "expected_version": expected_version,
                "actual_version": actual_version,
                "suggested_action": "reload_stream_and_retry"
            }
        )

class DomainError(EventStoreError):
    pass

# --- Typed Event Models (Mastered Requirement: 8+ Types) ---

class ApplicationSubmitted(BaseEvent):
    event_type: str = EventType.APPLICATION_SUBMITTED
    applicant_id: str
    requested_amount_usd: float
    product_type: str = "CommercialLoan"

class CreditAnalysisRequested(BaseEvent):
    event_type: str = EventType.CREDIT_ANALYSIS_REQUESTED
    application_id: str

class CreditAnalysisCompleted(BaseEvent):
    event_type: str = EventType.CREDIT_ANALYSIS_COMPLETED
    application_id: str
    agent_id: str
    session_id: str
    model_version: str
    confidence_score: float
    risk_tier: str
    recommended_limit_usd: float

class FraudScreeningRequested(BaseEvent):
    event_type: str = EventType.FRAUD_SCREENING_REQUESTED
    application_id: str

class FraudScreeningCompleted(BaseEvent):
    event_type: str = EventType.FRAUD_SCREENING_COMPLETED
    application_id: str
    risk_score: float
    flags: List[str]

class ComplianceCheckRequested(BaseEvent):
    event_type: str = EventType.COMPLIANCE_CHECK_REQUESTED
    application_id: str

class ComplianceRulePassed(BaseEvent):
    event_type: str = EventType.COMPLIANCE_RULE_PASSED
    application_id: str
    rule_id: str
    details: Dict[str, Any] = {}

class ComplianceRuleFailed(BaseEvent):
    event_type: str = EventType.COMPLIANCE_RULE_FAILED
    application_id: str
    rule_id: str
    is_hard_block: bool = False
    failure_reason: str

class DecisionGenerated(BaseEvent):
    event_type: str = EventType.DECISION_GENERATED
    application_id: str
    recommendation: str
    confidence_score: float
    reason: str
