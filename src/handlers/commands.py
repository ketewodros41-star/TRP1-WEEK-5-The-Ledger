# src/handlers/commands.py
import logging
from typing import List, Dict, Any, Optional
from ..eventstore import EventStore
from ..models.events import (
    BaseEvent, DomainError, EventType,
    ApplicationSubmitted, CreditAnalysisCompleted
)
from ..aggregates.loan_application import LoanApplicationAggregate, ApplicationState
from ..aggregates.agent_session import AgentSessionAggregate
from ..aggregates.compliance_record import ComplianceRecordAggregate

logger = logging.getLogger(__name__)

# --- Command Models ---
class ApplicationSubmittedCommand:
    def __init__(self, application_id: str, applicant_id: str, requested_amount: float, metadata: Dict[str, Any]):
        self.application_id = application_id
        self.applicant_id = applicant_id
        self.requested_amount = requested_amount
        self.metadata = metadata

class CreditAnalysisCompletedCommand:
    def __init__(self, application_id: str, agent_id: str, session_id: str, model_version: str, confidence_score: float, risk_tier: str, recommended_limit_usd: float, duration_ms: int, input_data: Dict[str, Any], correlation_id: str, causation_id: str):
        self.application_id = application_id
        self.agent_id = agent_id
        self.session_id = session_id
        self.model_version = model_version
        self.confidence_score = confidence_score
        self.risk_tier = risk_tier
        self.recommended_limit_usd = recommended_limit_usd
        self.duration_ms = duration_ms
        self.input_data = input_data
        self.correlation_id = correlation_id
        self.causation_id = causation_id

# --- Handlers ---
async def handle_submit_application(cmd: ApplicationSubmittedCommand, store: EventStore) -> None:
    # 1. Reconstruct aggregate
    app = await LoanApplicationAggregate.load(store, cmd.application_id)
    
    # 2. Validate
    app.assert_can_submit()
    
    # 3. Determine new events (Typed Model)
    event = ApplicationSubmitted(
        applicant_id=cmd.applicant_id,
        requested_amount_usd=cmd.requested_amount,
        **{k: v for k, v in cmd.metadata.items() if k not in ['correlation_id', 'causation_id']}
    )
    
    # 4. Append
    await store.append(
        stream_id=app.stream_id,
        events=[event],
        expected_version=app.version,
        correlation_id=cmd.metadata.get("correlation_id"),
        causation_id=cmd.metadata.get("causation_id") # Fix: pass causation_id
    )

async def handle_credit_analysis_completed(cmd: CreditAnalysisCompletedCommand, store: EventStore) -> None:
    # 1. Reconstruct aggregate
    app = await LoanApplicationAggregate.load(store, cmd.application_id)
    agent = await AgentSessionAggregate.load(store, cmd.agent_id, cmd.session_id)
    
    # 2. Validate
    app.assert_transition(ApplicationState.ANALYSIS_COMPLETE)
    agent.assert_started()
    agent.assert_model_version(cmd.model_version)
    
    # 3. Determine new events (Typed Model)
    event = CreditAnalysisCompleted(
        application_id=cmd.application_id,
        agent_id=cmd.agent_id,
        session_id=cmd.session_id,
        model_version=cmd.model_version,
        confidence_score=cmd.confidence_score,
        risk_tier=cmd.risk_tier,
        recommended_limit_usd=cmd.recommended_limit_usd
    )
    
    # 4. Append
    await store.append(
        stream_id=app.stream_id,
        events=[event],
        expected_version=app.version,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id
    )
