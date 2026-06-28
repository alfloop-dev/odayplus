# Pricing Solver

Pure numeric engine for PriceOps (ODP-MOD-06). Knows nothing about plan
lifecycle or persistence — that orchestration lives in `modules/priceops`.

- `constraints.py` — `PriceConstraints` hard bounds (margin floor, max delta,
  price ladder, min/max) and `ConstraintViolation` / feasibility checks.
- `demand.py` — constant-elasticity demand model and `simulate_price`,
  producing P10/P50/P90 demand, revenue and gross-margin bands.
- `optimizer.py` — `build_safe_action_set` (feasible on-ladder prices only) and
  `optimize_price`, an exhaustive search that maximizes expected incremental
  gross margin. ODP-MOD-06 specifies OR-Tools; OR-Tools is not a repo dependency,
  so the discrete ladder is searched directly. Candidates are filtered through
  the hard constraints *before* scoring, so a recommended price can never violate
  a hard constraint (AC-06-01). `diagnose_infeasible` explains an empty region.
