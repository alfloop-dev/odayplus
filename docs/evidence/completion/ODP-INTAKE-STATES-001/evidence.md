# ODP-INTAKE-STATES-001 State Engines Closeout Evidence

## Scope

Under task ODP-INTAKE-STATES-001, we implemented the formal binding state engines for intake processing, listing lifecycle, identity graph resolution decisions, task assignments, SLAs, and candidate promotion. Additionally, we addressed the non-blocking review nit by tightening the `IdentityGraphState.EXECUTING` and `IdentityGraphState.EXECUTED` role checks.

Key implementation components:
1. **Intake States Domain Module** (`modules/listing/domain/intake_states.py`):
   - Defines the enums for the five state families (`IntakeStage`, `ListingState`, `IdentityGraphState`, `AssignmentState`, `SlaState`, `PromotionState`, `PrincipalRole`, and `DenialCode`).
   - Implements aggregate classes representing the domain structures.
   - Enforces specific state machine rules (`IntakeStateMachine`, `ListingStateMachine`, `IdentityDecisionStateMachine`, `AssignmentStateMachine`, `SlaStateMachine`, and `PromotionStateMachine`) via a `.transition(...)` method.
   - Enforces actor role authorizations, tenant isolation (ABAC/RLS boundary), and proposer-reviewer segregation policies (e.g. self-review denied).
   - Enforces strict role restrictions on `IdentityGraphState` execution stages (`EXECUTING` and `EXECUTED`), allowing only system services or emergency admin.
2. **Intake Workflow Application Service** (`modules/listing/application/intake_workflow.py`):
   - Implements `IntakeWorkflowService` orchestrating intake state transitions.
   - Triggers state machine checks and records audit-trail events (e.g. `intake.submitted.v1`, `intake.quarantined.v1`, etc.).
   - Includes `InMemoryIntakeRepository` for persistence.
3. **Assignment & SLA Application Service** (`modules/listing/application/assignment_sla.py`):
   - Implements `AssignmentSlaService` managing assignments and SLA clock state derivation.
   - Handles SLA state updates (`ON_TRACK -> DUE_SOON -> OVERDUE -> BREACHED`) and pause/resume intervals.
   - Includes `InMemoryAssignmentRepository` and `InMemorySlaRepository`.

---

## Verification Evidence

All 25 unit and contract tests passed successfully.

### 1. Test Commands Run
- `uv run pytest tests/unit/listing tests/contract/test_assisted_listing_intake_states.py -v`

### 2. Test Execution Output
```
tests/unit/listing/test_intake_state_machines.py::test_intake_cannot_transition_from_terminal_ready PASSED
tests/unit/listing/test_intake_state_machines.py::test_intake_cannot_transition_from_terminal_cancelled PASSED
tests/unit/listing/test_intake_state_machines.py::test_intake_idempotency_requirement_on_submission PASSED
tests/unit/listing/test_intake_state_machines.py::test_intake_staff_ownership_on_cancellation PASSED
tests/unit/listing/test_intake_state_machines.py::test_listing_archived_is_terminal PASSED
tests/unit/listing/test_intake_state_machines.py::test_listing_steward_role_required_for_archive PASSED
tests/unit/listing/test_intake_state_machines.py::test_identity_decision_rejected_is_terminal PASSED
tests/unit/listing/test_intake_state_machines.py::test_identity_decision_failed_to_pending_review PASSED
tests/unit/listing/test_intake_state_machines.py::test_assignment_completed_is_terminal PASSED
tests/unit/listing/test_intake_state_machines.py::test_sla_paused_requires_manager_role PASSED
tests/unit/listing/test_intake_state_machines.py::test_promotion_completed_is_terminal PASSED
tests/contract/test_assisted_listing_intake_states.py::test_intake_submitted_creation PASSED
tests/contract/test_assisted_listing_intake_states.py::test_intake_legal_transitions_path PASSED
tests/contract/test_assisted_listing_intake_states.py::test_intake_quarantine_and_reopen PASSED
tests/contract/test_assisted_listing_intake_states.py::test_intake_tenant_isolation PASSED
tests/contract/test_assisted_listing_intake_states.py::test_intake_concurrency_version_conflict PASSED
tests/contract/test_assisted_listing_intake_states.py::test_intake_segregation_needs_review_to_ready PASSED
tests/contract/test_assisted_listing_intake_states.py::test_listing_transitions_success PASSED
tests/contract/test_assisted_listing_intake_states.py::test_listing_quarantine_segregation PASSED
tests/contract/test_assisted_listing_intake_states.py::test_listing_archiving_legal_hold PASSED
tests/contract/test_assisted_listing_intake_states.py::test_identity_decision_flow PASSED
tests/contract/test_assisted_listing_intake_states.py::test_assignment_lifecycle PASSED
tests/contract/test_assisted_listing_intake_states.py::test_sla_derived_and_pause_behavior PASSED
tests/contract/test_assisted_listing_intake_states.py::test_promotion_saga_flow PASSED
tests/contract/test_assisted_listing_intake_states.py::test_promotion_self_approval_denied PASSED

============================== 25 passed in 0.49s ==============================
```

### 3. Static Code Analysis (Ruff check)
- `python3 -m ruff check modules/listing tests/unit/listing tests/contract/test_assisted_listing_intake_states.py`
```
All checks passed!
```

---

## Artifact Mapping

- **State Engine Domain Rules**: `modules/listing/domain/intake_states.py` ([intake_states.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-intake-states-001/modules/listing/domain/intake_states.py))
- **Intake Workflow Orchestrator**: `modules/listing/application/intake_workflow.py` ([intake_workflow.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-intake-states-001/modules/listing/application/intake_workflow.py))
- **Assignment & SLA Service**: `modules/listing/application/assignment_sla.py` ([assignment_sla.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-intake-states-001/modules/listing/application/assignment_sla.py))
- **Contract Transition Tests**: `tests/contract/test_assisted_listing_intake_states.py` ([test_assisted_listing_intake_states.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-intake-states-001/tests/contract/test_assisted_listing_intake_states.py))
- **Unit Validation Tests**: `tests/unit/listing/test_intake_state_machines.py` ([test_intake_state_machines.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-intake-states-001/tests/unit/listing/test_intake_state_machines.py))
