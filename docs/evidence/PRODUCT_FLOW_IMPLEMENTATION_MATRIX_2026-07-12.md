# Product Flow Implementation Matrix

Date: 2026-07-12

This task-scoped matrix records the ODP-FLOW-005 PriceOps implementation row.
Other flow rows remain outside this task's ownership.

| Task | Flow | Backend | API | Frontend | Verification | Status |
|---|---|---|---|---|---|---|
| ODP-FLOW-005 | PriceOps constrained simulation, scheme comparison, approval, apply, monitor, outcome, rollback | `PricingPlanComparison`; approval gate blocks infeasible hard-constraint plans; rollback plan required before activation | `GET /priceops/plans/{plan_id}/comparison`; existing lifecycle endpoints retained | `/pricing` shows current/candidate comparison, approval guard, apply/monitor/outcome, rollback trigger/status | `pytest tests/integration/test_priceops_constraints.py tests/integration/test_priceops_api.py`; UI checks recorded in completion evidence | Implemented, pending review |

## Notes

- The comparison snapshot is built from persisted plan, optimization,
  approval, execution, observation, rollback, and evaluation records.
- `APPROVE`/`approved` decisions are normalized and gated; blocked approvals
  return API 422 and do not advance the plan.
- Rejection/request-revision decisions retain the existing `stop` path.
