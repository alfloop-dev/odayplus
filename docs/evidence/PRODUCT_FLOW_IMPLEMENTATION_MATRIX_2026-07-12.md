# Product Flow Implementation Matrix — 2026-07-12

Tracks the "Product Flow Implementation" wave (ODP-FLOW-\*): for each end-to-end
product flow, the state machine, persistence, audit, API-backed UI, and E2E
coverage that make the loop demonstrably closed.

Each FLOW owner appends/updates the row for their task. Columns:

- **State machine** — canonical states + invalid-transition rejection.
- **Jobs persist** — execution/observation/background jobs are durably recorded.
- **Audited** — approval / execution / outcome / rollback / close emit audit events.
- **API-backed UI** — UI renders live API data with a documented fixture fallback.
- **E2E** — a product-grade E2E drives the full loop.
- **Evidence** — completion implementation/verification refs.

| Task | Flow | State machine | Jobs persist | Audited | API-backed UI | E2E | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ODP-FLOW-004 | InterventionOps recommendation → conflict → plan → approval → execution → observation → matured outcome → **close/follow-up** | ✅ `CANDIDATE…COMPLETED→CLOSED`; close rejects non-`COMPLETED`/empty-reason/double-close | ✅ repo `save` on execute/observe + `run_observation_sweep` | ✅ `intervention.lifecycle.v1` for approve/execute/evaluate/**close**(+rollback) | ✅ `GET /interventions` binding + `DataSourceBadge`, fixtures as fallback | ✅ `e2e-ops-intervention-price-ad-product.spec.ts` drives through `close`+follow-up | `docs/evidence/completion/ODP-FLOW-004/{implementation,verification}.md` |

## Notes

- ODP-FLOW-004 introduced the `CLOSED` terminal state and `close_case`
  (disposition + optional linked follow-up CANDIDATE) so the InterventionOps
  loop closes rather than ending at `COMPLETED`. See the completion evidence for
  the acceptance mapping and exact verification commands.
