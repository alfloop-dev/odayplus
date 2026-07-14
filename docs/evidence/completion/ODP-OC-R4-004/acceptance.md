# ODP-OC-R4-004 — Acceptance

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | All three entry cards prefill and persist the correct draft type. | ✅ | `growth-entry-{offpeak,winback,priceops}` cards open the builder prefilled per kind; `POST /actions` persists `kind`, `GET /actions/{id}` round-trips it. Contract: `test_three_entry_cards_persist_correct_draft_type`. `api-proof.json §criterion_1`. |
| 2 | Blocked conflict states cannot submit and return actionable server reasons. | ✅ | Same-store/same-window active campaign → `check_conflicts.blocked=true`; `POST /actions/{id}/submit` returns **422** naming the conflicting live campaign; draft stays `DRAFT`. Contract: `test_blocked_conflict_cannot_submit_and_returns_actionable_reason`. `api-proof.json §criterion_2`. |
| 3 | Approval creates a Govern item and approval result advances the Growth state. | ✅ | `submit` creates approval `module="Growth"` and sets `PENDING_APPROVAL`; `POST /approvals/{id}/decision` `approved → APPROVED` (rejected → DRAFT), writing a Decision Log entry. Contract: `test_submit_creates_govern_item_and_approval_advances_state`, `test_rejected_approval_returns_action_to_draft`. `api-proof.json §criterion_3`. |
| 4 | Effective, ineffective and inconclusive outcomes persist and write Decision Log/Audit Trail. | ✅ | `POST /actions/{id}/outcome` persists each verdict (EFFECTIVE→CLOSED, INEFFECTIVE→INEFFECTIVE, INCONCLUSIVE stays OUTCOME_READY) and appends a Decision Log entry + Audit event. Contract: `test_outcomes_persist_and_write_decision_log`, `test_ineffective_action_cannot_close_directly`. `api-proof.json §criterion_4`. |

## Deliverables (task JSON)
- Off-peak promotion / member recall / PriceOps test entry cards — ✅ `GROWTH_ENTRY_CARDS`.
- Five-step builder (setup / audience-time / impact / risk-conflict / approval) — ✅ `BUILDER_STEPS` + `GrowthBuilderModal`.
- Server conflict checks (overlap / PriceOps / budget / fatigue / approval) — ✅ `check_conflicts`.
- Pending Approval → Scheduled → Running → Observing → Outcome Ready lifecycle — ✅ extended `_LIFECYCLE_TRANSITIONS`, contract `test_full_lifecycle_pending_to_outcome_ready`.

Cross-cutting R4 contract (Idempotency-Key de-dup, X-Correlation-Id round-trip,
fail-closed auth on writes) covered by
`test_create_action_idempotency_replays_same_draft`,
`test_create_action_round_trips_correlation_id`,
`test_create_action_without_role_is_denied`.
