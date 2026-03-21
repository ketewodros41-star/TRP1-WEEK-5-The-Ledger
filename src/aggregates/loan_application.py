# src/aggregates/loan_application.py
from enum import Enum
from typing import List, Optional, Set
from decimal import Decimal
from .base import BaseAggregate
from ..models.events import StoredEvent, DomainError

class ApplicationState(str, Enum):
    SUBMITTED = "Submitted"
    AWAITING_ANALYSIS = "AwaitingAnalysis"
    ANALYSIS_COMPLETE = "AnalysisComplete"
    COMPLIANCE_REVIEW = "ComplianceReview"
    PENDING_DECISION = "PendingDecision"
    APPROVED_PENDING_HUMAN = "ApprovedPendingHuman"
    DECLINED_PENDING_HUMAN = "DeclinedPendingHuman"
    FINAL_APPROVED = "FinalApproved"
    FINAL_DECLINED = "FinalDeclined"

class LoanApplicationAggregate(BaseAggregate):
    def __init__(self, application_id: str):
        super().__init__(f"loan-{application_id}")
        self.application_id = application_id
        self.state: Optional[ApplicationState] = None
        self.applicant_id: Optional[str] = None
        self.requested_amount: Decimal = Decimal(0)
        self.approved_amount: Decimal = Decimal(0)
        self.compliance_checks_passed: Set[str] = set()
        self.compliance_checks_required: List[str] = []
        self.max_recommended_limit: Decimal = Decimal(0)

    # --- Factory ---
    @classmethod
    async def load(cls, store, application_id: str) -> "LoanApplicationAggregate":
        events = await store.load_stream(f"loan-{application_id}")
        agg = cls(application_id)
        agg.apply_events(events)
        return agg

    # --- Business Rules ---
    def assert_can_submit(self):
        if self.state is not None:
            raise DomainError(f"Application {self.application_id} already exists in state {self.state}")

    def assert_transition(self, next_state: ApplicationState):
        valid_transitions = {
            None: [ApplicationState.SUBMITTED],
            ApplicationState.SUBMITTED: [ApplicationState.AWAITING_ANALYSIS],
            ApplicationState.AWAITING_ANALYSIS: [ApplicationState.ANALYSIS_COMPLETE, ApplicationState.COMPLIANCE_REVIEW],
            ApplicationState.ANALYSIS_COMPLETE: [ApplicationState.COMPLIANCE_REVIEW, ApplicationState.PENDING_DECISION],
            ApplicationState.COMPLIANCE_REVIEW: [ApplicationState.PENDING_DECISION],
            ApplicationState.PENDING_DECISION: [ApplicationState.APPROVED_PENDING_HUMAN, ApplicationState.DECLINED_PENDING_HUMAN],
            ApplicationState.APPROVED_PENDING_HUMAN: [ApplicationState.FINAL_APPROVED, ApplicationState.FINAL_DECLINED],
            ApplicationState.DECLINED_PENDING_HUMAN: [ApplicationState.FINAL_APPROVED, ApplicationState.FINAL_DECLINED]
        }
        
        allowed = valid_transitions.get(self.state, [])
        if next_state not in allowed:
            raise DomainError(f"Invalid transition from {self.state} to {next_state}")

    def assert_compliance_passed(self):
        # Check if all required compliance checks are in passed set
        # This assumes we've synced required checks from ComplianceRecord
        missing = [c for c in self.compliance_checks_required if c not in self.compliance_checks_passed]
        if missing:
             raise DomainError(f"Compliance checks pending: {', '.join(missing)}")

    def assert_limit_within_bounds(self, amount: Decimal):
        if amount > self.max_recommended_limit:
            raise DomainError(f"Approved amount {amount} exceeds max recommended limit {self.max_recommended_limit}")

    # --- Event Handlers ---
    def on_applicationsubmitted(self, event: StoredEvent):
        self.state = ApplicationState.SUBMITTED
        self.applicant_id = event.payload.get("applicant_id")
        self.requested_amount = Decimal(str(event.payload.get("requested_amount_usd", 0)))
        # Default required checks if not specified
        self.compliance_checks_required = ["KYC", "AML", "SANCTIONS"]

    def on_creditanalysisrequested(self, event: StoredEvent):
        self.state = ApplicationState.AWAITING_ANALYSIS

    def on_creditanalysiscompleted(self, event: StoredEvent):
        # We store the latest recommendation limit
        self.max_recommended_limit = Decimal(str(event.payload.get("recommended_limit_usd", 0)))
        # If this is the last analysis needed, we can move to ComplianceReview
        if self.state == ApplicationState.AWAITING_ANALYSIS:
            self.state = ApplicationState.ANALYSIS_COMPLETE

    def on_compliancerulepassed(self, event: StoredEvent):
        rule_id = event.payload.get("rule_id")
        if rule_id:
            self.compliance_checks_passed.add(rule_id)

    def on_decisiongenerated(self, event: StoredEvent):
        recommendation = event.payload.get("recommendation")
        confidence = event.payload.get("confidence_score", 0)
        
        # Rule 4: Confidence floor
        if confidence < 0.6:
            # Force REFER if confidence is low, but we'll reflect the reco in our local state
            pass 
        
        if recommendation == "APPROVE":
            self.state = ApplicationState.APPROVED_PENDING_HUMAN
        else:
            self.state = ApplicationState.DECLINED_PENDING_HUMAN

    def on_humanreviewcompleted(self, event: StoredEvent):
        final_decision = event.payload.get("final_decision")
        if final_decision == "APPROVE":
            self.state = ApplicationState.FINAL_APPROVED
        else:
            self.state = ApplicationState.FINAL_DECLINED

    def on_applicationapproved(self, event: StoredEvent):
        self.state = ApplicationState.FINAL_APPROVED
        self.approved_amount = Decimal(str(event.payload.get("approved_amount_usd", 0)))

    def on_applicationdeclined(self, event: StoredEvent):
        self.state = ApplicationState.FINAL_DECLINED
