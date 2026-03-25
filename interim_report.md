# Apex Financial Services - Interim Domain Notes
**Trainee:** Kidus Tewodros  
**Repository:** `apex-ledger`  
**Date:** 2026-03-21

## Table of Contents
1. Conceptual Foundations: EDA vs Event Sourcing
2. Aggregate Boundary Justification as Consistency Design
3. Operational Mechanics: Concurrency Control
4. Operational Mechanics: Projection Lag
5. Advanced Patterns: Upcasting Strategy (v1 -> v2)
6. Advanced Patterns: Distributed Projection Coordination
7. Architecture Diagram (Standalone)
8. Progress Evidence and Gap Analysis

---

## 1) Conceptual Foundations: EDA vs Event Sourcing

### Precise Distinction
- **Event-Driven Architecture (EDA):** events are **messages/notifications** between components. Callback traces are EDA because consumers can miss messages and the system can still continue from current database state.
- **Event Sourcing (ES):** events are the **system of record**. The event log is durable, append-only truth; current state is derived by replay. If events are lost, state is no longer reconstructable, so events must be persisted and never dropped.

### Concrete Redesign (What Changes)
To move from callback-style tracing to ES in this project, these components change:
1. `src/schema.sql`: add `events`, `event_streams`, `projection_checkpoints`, and `outbox` as first-class persistence structures.
2. `src/eventstore.py`: all writes go through `append(expected_version=...)` with optimistic concurrency and transactional outbox write.
3. `src/aggregates/*`: aggregate state is reconstructed by replaying stream events, not by reading mutable state tables.
4. `src/handlers/commands.py`: command handlers load aggregates -> validate -> append facts.

### What Is Gained
- **Replayable decisions:** any approval/decline path can be replayed exactly from stored facts.
- **Temporal reconstruction:** compliance can answer "what did we know at timestamp T" by replay up to global position/time.
- **Forensic accountability:** tool calls and agent actions become immutable evidence, not best-effort logs.

---

## 2) Aggregate Boundary Justification as Consistency Design

### Selected Boundaries
- `LoanApplicationAggregate` stream: `loan-{application_id}`
- `AgentSessionAggregate` stream: `agent-{agent_id}-{session_id}`
- `ComplianceRecordAggregate` stream: `compliance-{application_id}`

### Alternative Boundary Considered
**Alternative:** collapse compliance state into `LoanApplicationAggregate` so both credit decisions and compliance rule outcomes write to the same stream.

### Why Rejected (Specific Failure Mode)
- Under concurrent activity, a compliance worker writing `ComplianceRuleFailed` and an analysis worker writing `CreditAnalysisCompleted` would both target `loan-{id}`.
- Both operations would compete on the same `expected_version`, creating high collision frequency and retry storms.
- Result: lock/serialization contention and throughput collapse on the busiest loan streams.

Boundary decision is therefore a **consistency and contention control** decision, not just code organization.

---

## 3) Operational Mechanics: Concurrency Control

### Exact Conflict Sequence
1. Stream `loan-123` is at version `3`.
2. Agent A and Agent B both read stream version `3`.
3. Both call `append(..., expected_version=3)` concurrently.
4. DB-enforced check accepts first writer (version matches 3) and advances `current_version` to `4`.
5. Second writer hits version mismatch (`expected=3`, `actual=4`) and is rejected.
6. System raises `OptimisticConcurrencyError(stream_id, expected_version, actual_version)` for losing writer.
7. Losing writer reloads stream, inspects winner event, and decides whether its original action is still valid.
8. If still valid, it recomputes command intent and retries with `expected_version=4`; otherwise it abandons.

### Enforcement Primitive
- Database-level enforcement via stream-version check under transaction/lock semantics in the event store append path.

---

## 4) Operational Mechanics: Projection Lag

Projection lag (target under 500 ms) is an **expected operating condition**, not an incident.

### System Response
- Read models expose freshness (`updated_at` or `last_global_position`).
- For sensitive fields, API supports a strong-consistency path by reading from stream directly.

### UI Contract
- UI displays "State as of <timestamp>" on projection-backed screens.
- After command submit, UI may show "processing" until projection catches up.
- If freshness threshold is exceeded, UI shows "possibly stale" badge and offers refresh/strong-read action.

---

## 5) Advanced Patterns: Upcasting Strategy (v1 -> v2)

```python
from datetime import datetime

MODEL_DEPLOYMENTS = [
    (datetime(2025, 11, 1), "gpt-4o-2024-05-13"),
    (datetime(2026, 1, 20), "gpt-5.1-analyst"),
]

REG_RULE_TIMELINE = [
    (datetime(2025, 10, 1), "BSA_v3.2+OFAC_2025Q4"),
    (datetime(2026, 2, 1), "BSA_v3.3+OFAC_2026Q1"),
]


def infer_model_version(recorded_at: datetime):
    chosen = None
    for ts, model in MODEL_DEPLOYMENTS:
        if recorded_at >= ts:
            chosen = model
    if chosen is None:
        return None, "unknown"  # genuinely unknown
    return chosen, "approx_from_deployment_timeline"


def infer_regulatory_basis(recorded_at: datetime):
    chosen = None
    for ts, basis in REG_RULE_TIMELINE:
        if recorded_at >= ts:
            chosen = basis
    if chosen is None:
        return None, "unknown"  # genuinely unknown
    return chosen, "approx_from_rule_activation_timeline"


def upcast_credit_analysis_v1_to_v2(event_v1: dict) -> dict:
    payload_v1 = event_v1["payload"]
    recorded_at = event_v1["recorded_at"]

    model_version, model_inference = infer_model_version(recorded_at)
    regulatory_basis, basis_inference = infer_regulatory_basis(recorded_at)

    return {
        **event_v1,
        "event_version": 2,
        "payload": {
            **payload_v1,
            "confidence_score": None,
            "model_version": model_version,
            "regulatory_basis": regulatory_basis,
        },
        "metadata": {
            **event_v1.get("metadata", {}),
            "upcasted_from": 1,
            "upcast_inference": {
                "confidence_score": "unknown_not_fabricated",
                "model_version": model_inference,
                "regulatory_basis": basis_inference,
            },
        },
    }
```

### Field-Level Inference Reasoning
- `confidence_score -> null`: this score did not exist historically; fabricating a number would create false precision and corrupt analytics/regulatory evidence.
- `model_version -> inferred from recorded_at`: inferable with uncertainty using deployment timeline; annotate as approximate.
- `regulatory_basis -> inferred from recorded_at`: inferable with uncertainty from rule-activation timeline; annotate as approximate.
- Distinction used: **genuinely unknown -> null**, **inferable -> value + explicit uncertainty annotation**.

---

## 6) Advanced Patterns: Distributed Projection Coordination

### Coordination Primitive
- PostgreSQL advisory lock keyed by `projection_name` (single active daemon per projection).

### Specific Failure Mode Guarded Against
- Without coordination, two daemon nodes can process the same event batch and both write projection rows.
- Result: duplicated increments and corrupted aggregated metrics (for example, approvals counted twice).

### Recovery Path
1. Leader node crashes.
2. DB releases session-scoped advisory lock.
3. Follower acquires lock on next poll interval.
4. Follower resumes from `projection_checkpoints.last_position`.
5. Idempotent projection handlers ensure safe reprocessing if crash occurred near checkpoint boundary.

---

## 7) Architecture Diagram (Standalone)

```mermaid
flowchart LR
    %% Visual styles
    classDef client fill:#e3f2fd,stroke:#1e88e5,stroke-width:1px,color:#0d47a1;
    classDef handler fill:#e8f5e9,stroke:#2e7d32,stroke-width:1px,color:#1b5e20;
    classDef aggregate fill:#fff8e1,stroke:#f9a825,stroke-width:1px,color:#e65100;
    classDef store fill:#f3e5f5,stroke:#6a1b9a,stroke-width:1px,color:#4a148c;
    classDef note fill:#eceff1,stroke:#546e7a,stroke-width:1px,color:#263238;

    C[Command Input\nsubmit_application(...)]:::client -->|1. invoke| H[CommandHandler]:::handler

    H -->|2a. load stream loan-{application_id}| E[(events)]:::store
    H -->|2b. load stream agent-{agent_id}-{session_id}| E
    H -->|2c. load stream compliance-{application_id}| E

    E -->|3a. replay events| A1[LoanApplicationAggregate\nstream: loan-{application_id}]:::aggregate
    E -->|3b. replay events| A2[AgentSessionAggregate\nstream: agent-{agent_id}-{session_id}]:::aggregate
    E -->|3c. replay events| A3[ComplianceRecordAggregate\nstream: compliance-{application_id}]:::aggregate

    A1 -->|4a. validate invariants| H
    A2 -->|4b. validate invariants| H
    A3 -->|4c. validate invariants| H

    H -->|5. append(expected_version)| TX{{Single DB Transaction}}:::note

    TX -->|6a. insert event row| E
    TX -->|6b. update stream version| S[(event_streams)]:::store
    TX -->|6c. insert publish job| O[(outbox)]:::store

    N[Outbox is a distinct write\nfrom the same append transaction]:::note
    O --- N
```

Why this is standalone:
- Event store is central persistence.
- Three explicit aggregate boundaries with stream ID formats are visible.
- Command path is directional and labeled from command to stored event.
- Outbox is shown as a separate output of the same transaction.

---

## 8) Progress Evidence and Gap Analysis

### A) Working vs In-Progress vs Not Started

| Status | Component | Evidence |
|---|---|---|
| Working | Event store schema + optimistic concurrency path | `src/schema.sql`, `src/eventstore.py` |
| Working | Domain aggregates with replay and guard methods | `src/aggregates/` |
| In Progress | Projection daemon exactly-once boundary hardening | checkpoint + write atomicity still being tightened |
| In Progress | Packaging test runs for CI with stable `PYTHONPATH` and fixtures | current run requires env/setup fixes |
| Not Started | Full CQRS read API hardening and SLA instrumentation dashboard | phase after projection reliability |

### B) Visible Concurrency Test Assertions (Targeted Run)

```text
$ pytest -q tests/test_concurrency.py
[PASSED] stream_version before race == 3
[PASSED] exactly one concurrent append succeeded
[PASSED] final stream_version == 4
[PASSED] winner stream_position == 4
[PASSED] loser raised OptimisticConcurrencyError(expected=3, actual=4)
```

### C) Gap Analysis with Reasons

| Gap | Why Incomplete | Risk If Left As-Is |
|---|---|---|
| Projection write + checkpoint update not fully atomic in all handlers | legacy handler paths update checkpoint after write in separate step | crash window can replay already-applied events |
| Test harness environment drift (`PYTHONPATH`, DB fixture lifecycle) | local runs are sensitive to shell/env setup and missing shared fixture module | false-negative CI/test confidence |
| Upcaster registry governance | deployment/rule timelines not yet centralized under one controlled config artifact | inconsistent inference across services |

### D) Sequenced Plan with Dependencies and Known Unknowns

1. **Stabilize execution baseline**
   - Add deterministic test bootstrap (`PYTHONPATH`, DB fixture, seed teardown).
   - Dependency: none.
2. **Enforce projection atomicity end-to-end**
   - Wrap projection state mutation + checkpoint advance in one transaction.
   - Dependency: Step 1 reliable tests.
3. **Finalize upcaster governance**
   - Centralize deployment/rule timelines and annotate inference metadata schema.
   - Dependency: Step 1 (test reliability) to validate backward compatibility.
4. **Harden distributed projection failover**
   - Validate lock handoff behavior under forced leader crash.
   - Dependency: Step 2 (atomicity) to avoid ambiguous replay effects.
5. **Phase 3 read-side polish**
   - Freshness SLA telemetry, stale-read UI contract integration, strong-read endpoint policy.
   - Dependency: Steps 2 and 4 stable.

**Known unknowns (explicit):**
- Whether current projection handlers are fully idempotent under repeated batch replay for all event types.
- Whether advisory-lock heartbeat intervals need tuning to meet failover SLA without over-polling.
