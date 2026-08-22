# ODP-ECONOMICS-001 — Completion Evidence

Implement target-format and monthly site economics simulator.

- **Task ID:** `ODP-ECONOMICS-001`
- **Owner:** `Antigravity2`
- **Reviewer:** `Claude`
- **Phase:** `ODayPlus Decision`
- **Provides Contract:** `odayplus.site-economics.v1` (version `1.0.0`, `decision_product`)
- **Requires Contract:** `emgi.site-market-context.v1` (version `1.0.0`, `decision_product`)
- **Source Ref:** `alfloop-dev/oday-data-platform@63e9c2fc5171c0e335f6465f5860704fe4dc4694` (`docs/design/emgi/v0.4.1/tasks/definitions/consumer-c.json`)

---

## Deliverables Summary

| Path | Description |
|---|---|
| `modules/site_economics/domain/models.py` | Versioned domain models for machine specifications, machine mix, fitout engineering, utilities rates, maintenance contracts, debt financing, corporate taxation, residual values, monthly cash flow line items, and financial return metrics. |
| `modules/site_economics/domain/formats.py` | Target format catalog (`ODAY_G2`, `ODAY_G3_COMPACT`, `ODAY_FLAGSHIP`) with machine mix, fitout specs, and versioning registry. |
| `modules/site_economics/domain/simulator.py` | Pure deterministic financial simulation engine computing month-by-month cash flows, debt amortization, depreciation schedules, tax schedules with loss carryforward, DCF, NPV, IRR, and censored payback analysis. |
| `modules/site_economics/domain/contracts.py` | `odayplus.site-economics.v1` contract schema, `SiteEconomicsDocument` model, validation routines, and SHA256 digest computation. |
| `modules/site_economics/application/service.py` | `SiteEconomicsService` application service consuming `emgi.site-market-context.v1` or parametric inputs, generating sensitivity scenario trees (Base, Optimistic, Pessimistic, Stress Test), and rendering decision documents. |
| `modules/site_economics/infrastructure/repositories.py` | In-memory and persistence-ready `SiteEconomicsRepository` for document version tracking. |
| `modules/site_economics/__init__.py` | Public module API exports. |
| `tests/domain/test_site_economics.py` | Comprehensive domain test suite covering machine mix versioning, DCF/NPV/IRR/payback, right-censoring, contract schema round-trips, and market context integration. |

---

## Acceptance Verification

### 1. Version machine mix, CAPEX, fitout, utilities, maintenance, financing, tax and residual value
- **Machine Models & Mix:** Full versioned catalog (`WASHER_LARGE_20KG_V1`, `WASHER_MEDIUM_14KG_V1`, `WASHER_JUMBO_27KG_V1`, `DRYER_STACK_15KG_V1`, `COMBO_ALL_IN_ONE_12KG_V1`, `VENDING_DETERGENT_SMART_V1`, `PET_WASHER_10KG_V1`) tracking unit CAPEX, cycle pricing, utility consumption, and useful life.
- **CAPEX & Fitout:** Structural decomposition across equipment purchase/delivery, plumbing, electrical, gas piping, facade signage, IoT telemetry, and per-ping flooring/partition civil works.
- **Operating Expenses:** Variable utilities (water, electricity, gas, detergents) proportional to cycle volumes; maintenance contracts plus revenue-indexed repair reserve; platform royalty fees and store operations.
- **Financing & Debt Service:** Configurable loan-to-cost ratio, APR, loan term, and equal monthly installment (PMT) amortization separating principal and tax-deductible interest.
- **Taxation & Depreciation:** Straight-line equipment depreciation and fitout leasehold amortization; 20% corporate income tax rate with multi-year loss carryforward accounting.
- **Residual & Salvage Value:** Explicit terminal equipment salvage ratios, lease security deposit return, and decommissioning/reinstatement cost deductions.

### 2. Compute monthly cash flow, NPV, IRR and censored payback outcomes
- **Monthly Projection Schedule:** Month 0 (initial equity and debt outlay) through Month N (e.g. Month 60/84) with ramp-up trajectory and 12-month seasonality adjustments.
- **NPV (Net Present Value):** Discounted cash flow (DCF) using compounded monthly hurdle rates (default 8.0% annual WACC) for both unlevered and levered cash flow streams.
- **IRR (Internal Rate of Return):** Annualized internal rate of return solved via robust Newton-Raphson and bracketed bisection search with fail-safe non-convergence handling.
- **Censored Payback Analysis:** Simple and discounted payback periods evaluated against the projection horizon. Detects and flags:
  - `NOT_CENSORED`: Exact interpolated month when cumulative cash flows turn positive within horizon.
  - `RIGHT_CENSORED`: Unprofitable or slow-recovery investment that does not achieve breakeven within horizon months.
  - `NEGATIVE_CASH_FLOW`: Permanently loss-making site where operational cash flows remain non-positive.

---

## Verification Evidence

Run command:
```bash
uv run pytest tests/domain/test_site_economics.py -q
```

Output:
```text
..............                                                           [100%]
14 passed in 0.05s
```
