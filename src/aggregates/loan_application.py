from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from .base import BaseAggregate
from ..models.events import DomainError, StoredEvent


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
        self.requested_amount: Decimal = Decimal("0")
        self.recommended_limit: Decimal = Decimal("0")
        self.latest_confidence: float = 0.0
        self.compliance_passed_rules: Set[str] = set()
        self.compliance_failed_rules: Set[str] = set()
        self.compliance_completed: bool = False
        self.decision_recommendation: Optional[str] = None
        self.contributing_sessions: Set[str] = set()

    @classmethod
    async def load(cls, store, application_id: str) -> "LoanApplicationAggregate":
        agg = cls(application_id)
        events = await store.load_stream(agg.stream_id)
        agg.apply_events(events)
        return agg

    def assert_can_submit(self) -> None:
        if self.state is not None:
            raise DomainError(f"Application {self.application_id} already exists")

    def assert_transition(self, next_state: ApplicationState) -> None:
        transitions = {
            None: {ApplicationState.SUBMITTED},
            ApplicationState.SUBMITTED: {ApplicationState.AWAITING_ANALYSIS},
            ApplicationState.AWAITING_ANALYSIS: {ApplicationState.ANALYSIS_COMPLETE},
            ApplicationState.ANALYSIS_COMPLETE: {ApplicationState.COMPLIANCE_REVIEW},
            ApplicationState.COMPLIANCE_REVIEW: {ApplicationState.PENDING_DECISION},
            ApplicationState.PENDING_DECISION: {
                ApplicationState.APPROVED_PENDING_HUMAN,
                ApplicationState.DECLINED_PENDING_HUMAN,
            },
            ApplicationState.APPROVED_PENDING_HUMAN: {
                ApplicationState.FINAL_APPROVED,
                ApplicationState.FINAL_DECLINED,
            },
            ApplicationState.DECLINED_PENDING_HUMAN: {
                ApplicationState.FINAL_APPROVED,
                ApplicationState.FINAL_DECLINED,
            },
        }
        if next_state not in transitions.get(self.state, set()):
            raise DomainError(f"Invalid transition from {self.state} to {next_state}")

    def determine_recommendation(self, requested: str, confidence: float) -> str:
        if confidence < 0.6:
            return "REFER"
        return requested

    def target_state_for_recommendation(self, recommendation: str) -> ApplicationState:
        if recommendation == "APPROVE":
            return ApplicationState.APPROVED_PENDING_HUMAN
        return ApplicationState.DECLINED_PENDING_HUMAN

    def assert_compliance_dependency_for_approval(self, recommendation: str) -> None:
        if recommendation == "APPROVE" and not self.compliance_completed:
            raise DomainError("Compliance must complete before approval recommendation")

    def assert_causal_chain(self, contributing_sessions: List[str], known_sessions: Set[str]) -> None:
        unknown = [sid for sid in contributing_sessions if sid not in known_sessions]
        if unknown:
            raise DomainError(f"Missing contributing agent sessions: {', '.join(unknown)}")

    def determine_and_validate_decision(
        self,
        requested_recommendation: str,
        confidence_score: float,
        contributing_sessions: List[str],
        known_sessions: Set[str],
        model_versions: Dict[str, str],
    ) -> Tuple[str, ApplicationState]:
        recommendation = self.determine_recommendation(requested_recommendation, confidence_score)
        self.assert_compliance_dependency_for_approval(recommendation)
        self.assert_causal_chain(contributing_sessions, known_sessions)
        missing_model_versions = [sid for sid in contributing_sessions if sid not in model_versions]
        if missing_model_versions:
            raise DomainError(f"Missing model_versions entries for: {', '.join(missing_model_versions)}")
        next_state = self.target_state_for_recommendation(recommendation)
        self.assert_transition(next_state)
        return recommendation, next_state

    def _transition_to(self, next_state: ApplicationState) -> None:
        self.assert_transition(next_state)
        self.state = next_state

    def on_applicationsubmitted(self, event: StoredEvent) -> None:
        self._transition_to(ApplicationState.SUBMITTED)
        self.applicant_id = event.payload["applicant_id"]
        self.requested_amount = Decimal(str(event.payload["requested_amount_usd"]))

    def on_creditanalysisrequested(self, event: StoredEvent) -> None:
        self._transition_to(ApplicationState.AWAITING_ANALYSIS)

    def on_creditanalysiscompleted(self, event: StoredEvent) -> None:
        self._transition_to(ApplicationState.ANALYSIS_COMPLETE)
        self.recommended_limit = Decimal(str(event.payload.get("recommended_limit_usd", 0)))
        self.latest_confidence = float(event.payload.get("confidence_score") or 0.0)

    def on_compliancecheckrequested(self, event: StoredEvent) -> None:
        self._transition_to(ApplicationState.COMPLIANCE_REVIEW)

    def on_compliancerulepassed(self, event: StoredEvent) -> None:
        rule_id = event.payload.get("rule_id")
        if rule_id:
            self.compliance_passed_rules.add(rule_id)

    def on_compliancerulefailed(self, event: StoredEvent) -> None:
        rule_id = event.payload.get("rule_id")
        if rule_id:
            self.compliance_failed_rules.add(rule_id)

    def on_compliancecheckcompleted(self, event: StoredEvent) -> None:
        self.compliance_completed = True
        self._transition_to(ApplicationState.PENDING_DECISION)

    def on_decisiongenerated(self, event: StoredEvent) -> None:
        self.decision_recommendation = event.payload["recommendation"]
        self.contributing_sessions = set(event.payload.get("contributing_agent_sessions", []))
        if self.decision_recommendation == "APPROVE":
            self._transition_to(ApplicationState.APPROVED_PENDING_HUMAN)
        else:
            self._transition_to(ApplicationState.DECLINED_PENDING_HUMAN)

    def on_humanreviewcompleted(self, event: StoredEvent) -> None:
        self._transition_to(
            ApplicationState.FINAL_APPROVED
            if event.payload["final_decision"] == "APPROVE"
            else ApplicationState.FINAL_DECLINED
        )

    def on_applicationapproved(self, event: StoredEvent) -> None:
        self._transition_to(ApplicationState.FINAL_APPROVED)

    def on_applicationdeclined(self, event: StoredEvent) -> None:
        self._transition_to(ApplicationState.FINAL_DECLINED)
