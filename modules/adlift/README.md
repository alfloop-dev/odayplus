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
4. **Evidence assessment** (`assess_evidence`) — assessability and strength are
   two separate questions (ADR-0004 D3), answered in that order.

   **Is it assessable at all?** No treatment-period data means there is nothing
   to read in either direction — the campaign can be called neither effective
   nor ineffective. That is reported as `evidence_assessable=false`,
   `evidence_level=None`, `insufficiency_reason_code=NO_TREATMENT_DATA`, and it
   is **not** `L0`: `L0` asserts an observation was made and rated at the bottom
   of the scale. `NO_TREATMENT_DATA` is the only insufficiency reason code.
   `NO_CONTROL` and `OVERLAPPING_TREATMENT` are ladder downgrades (`L1` and
   `L2` respectively), not unassessability — the reading stands, only the causal
   claim fails. `SAMPLE_TOO_SMALL` and `DATA_QUALITY_FAIL` have no decision path
   in this module yet (no minimum-sample threshold, no data-quality signal), so
   they are deliberately not declared.

   **If assessable, how strong?** The L0–L5 causal ladder (ODP-ML-05 §5):
   - `L0` anecdotal — treatment observed, nothing to compare against
   - `L1` before/after, **no control group**
   - `L2` matched control but pre-trend not clean **or** intervention overlap
     (contamination) — capped here, no causal claim (AC-07-02)
   - `L3` DiD validated: control + pre-trend + balance pass — causal claim allowed
   - `L4`/`L5` reserved for experimental / replicated designs (out of v1 scope)

   v1 emits `L1`–`L3`. A causal claim is allowed only when the ladder applies
   **and** the level is `L3+` (`causal_claim_allowed`).
5. **Recommendation** — `CONTINUE / SCALE / STOP / CHANGE_CHANNEL / INCONCLUSIVE`
   (ODP-ML-05 §15). Unassessable, or below L3, the read is `INCONCLUSIVE`; at
   L3+ it is driven by IROMI.
6. **Writeback** — builds an InterventionOps writeback packet and a Label Registry
   outcome entry (AC-07-05); composes with ODP-R4-001 InterventionOps.

`IncrementalityReport.to_report_card()` projects onto the `AdLiftReportCard`
contract (component contracts §5.9). The card has a single string slot for
evidence, so an unassessable report renders as `INSUFFICIENT_EVIDENCE`
(ODP-BR-AD-004 wording) rather than `null` — a `null` display field reads as
"not loaded yet". The machine-readable three-field split
(`evidence_assessable` / `evidence_level` / `insufficiency_reason_code`) stays
in `to_dict()`. `INSUFFICIENT_EVIDENCE` is not an `EvidenceLevel` member and is
never comparable against the ladder, so `GET /adlift/reports?evidence_level=`
matches no unassessable report.

## Layers

- `domain/incrementality.py` — pure model: matching, pre-trend, DiD, evidence, writeback.
- `infrastructure/repositories.py` — in-memory versioned report store.
- `application/incrementality.py` — `AdLiftService` orchestration.
- `workers/incrementality_worker.py` — batch job entry point (idempotent via API).
- API: `apps/api/app/routes/adlift.py` (`/adlift/incrementality-jobs`, `/adlift/reports`).

Tests: `tests/integration/test_adlift_incrementality.py`,
`modules/adlift/tests/test_evidence_assessability.py`.
