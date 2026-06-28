# AdLift Module

Ad incrementality evaluation and evidence grading (R4 — Intervention/Price/Ad).
Implements ODP-MOD-07 (AdLift) on the causal model in ODP-ML-05.

Given an ad campaign — treatment stores, candidate control stores, a pre-period
and a campaign-period of daily store metrics, and ad spend (channel/budget/period/
treatment scope per AC-07-01) — the module:

1. **Matched controls** — greedily pairs each treatment store with the nearest
   candidate control by pre-period average revenue (1:1, no replacement; ODP-ML-05 §8).
2. **Pre-trend test** — checks the parallel-trends assumption by comparing
   normalised pre-period daily growth slopes of treatment vs control groups.
   `PASS | FAIL | INCONCLUSIVE | NOT_TESTED`.
3. **Incrementality (matched-pair difference-in-differences)** —
   `(treatment_post − treatment_pre) − (control_post − control_pre)` per store-day,
   scaled by treatment campaign days, for revenue and gross margin. **Surface**
   (raw observed) revenue is reported separately from the **incremental** estimate
   (AC-07-03). IROMI = incremental gross margin ÷ ad spend (AC-07-04).
4. **Evidence Level** — the L0–L5 causal ladder (ODP-ML-05 §5):
   - `L0` anecdotal / no usable treatment data
   - `L1` before/after, **no control group**
   - `L2` matched control but pre-trend not clean **or** intervention overlap
     (contamination) — capped here, no causal claim (AC-07-02)
   - `L3` DiD validated: control + pre-trend + balance pass — causal claim allowed
   - `L4`/`L5` reserved for experimental / replicated designs (out of v1 scope)

   A causal claim is allowed only at `L3+` (`causal_claim_allowed`).
5. **Recommendation** — `CONTINUE / SCALE / STOP / CHANGE_CHANNEL / INCONCLUSIVE`
   (ODP-ML-05 §15). Below L3 the read is `INCONCLUSIVE`; at L3+ it is driven by IROMI.
6. **Writeback** — builds an InterventionOps writeback packet and a Label Registry
   outcome entry (AC-07-05); composes with ODP-R4-001 InterventionOps.

`IncrementalityReport.to_report_card()` projects onto the `AdLiftReportCard`
contract (component contracts §5.9).

## Layers

- `domain/incrementality.py` — pure model: matching, pre-trend, DiD, evidence, writeback.
- `infrastructure/repositories.py` — in-memory versioned report store.
- `application/incrementality.py` — `AdLiftService` orchestration.
- `workers/incrementality_worker.py` — batch job entry point (idempotent via API).
- API: `apps/api/app/routes/adlift.py` (`/adlift/incrementality-jobs`, `/adlift/reports`).

Tests: `tests/integration/test_adlift_incrementality.py`.
