# ODP-PLAN-AVM-OUTCOME-001 Acceptance and Dependency Packet

## Packet identity

| Field | Value |
|---|---|
| Sidecar task | `ODP-PLAN-AVM-OUTCOME-001-SIDECAR-ACCEPTANCE` |
| Parent task | `ODP-PLAN-AVM-OUTCOME-001` |
| Gap | `GAP-P1-003` |
| Sidecar owner / reviewer | `Claude2` / `Antigravity2` (live task state; originally authored under `Codex6` / `Antigravity`) |
| Parent owner / reviewer | `Antigravity` / `Antigravity5` as recorded at authoring time; unverifiable at this HEAD (see below) |
| Packet purpose | Pre-review checklist, dependency map, and evidence handoff aid |
| Verdict | **No parent acceptance verdict. No activation claim.** |

This is a support-only packet. It does not change the AVM contract, runtime,
registry, governance policy, activation state, or canonical planning truth. The
parent owner remains responsible for implementation and for filling the evidence
references below; the parent reviewer remains responsible for the acceptance
decision at the exact reviewed commit.

## Scope guard and current contract boundary

The parent batch must cover all four surfaces together:

1. an authoritative inventory of at least 120 eligible, mature DealRoom
   transaction outcomes, including lineage, freshness, and confidential-data
   classification;
2. an exact model/version prediction-to-outcome join and aligned-population
   interval coverage, calibration, and value-band separation calculations;
3. authoritative RBAC/ABAC readback plus redacted access-audit evidence; and
4. a governed-disabled receipt and concrete Human/Ops backfill handoff whenever
   the data, model lineage, metrics, or access authority is insufficient.

The repository currently distinguishes two similarly named surfaces that must
not be conflated:

- the production DealRoom AVM contract is `avm` / `dealroom_avm`, trained from
  `model_ready.valuation_view`, with an activation threshold of 120;
- official real-estate sales feed `listing_property_avm` /
  `model_ready.listing_property_valuation_view`, a separate optional model.

Official property-sale rows are therefore not evidence that the DealRoom AVM
outcome contract is satisfied. Existing contract tests explicitly preserve this
separation. Likewise, an API valuation flow or Finance approval test does not by
itself prove the outcome inventory, prediction lineage, calibration, or
confidential-access gates in this packet.

## Dependency map

| Relationship | Dependency or consumer | Gate supplied | Required disposition in parent handoff |
|---|---|---|---|
| Formal prerequisite | `ODP-PLAN-OSS-LICENSE-GATE-001` | License-aware dependency/release evidence | Record task status and merged evidence reference. An unresolved prerequisite blocks an activation/release claim. |
| Parent implementation obligation | Authoritative DealRoom transaction source | Mature realized outcomes, stable business identity, source owner, cutoff/freshness, confidential classification | Bind source/readback identity, query, snapshot, and counts to immutable hashes. Repository fixtures or official property-sale data are not substitutes. |
| Parent implementation obligation | Exact `dealroom_avm` prediction source and model registry/version | Prediction values created before outcomes, immutable model/version lineage, prediction timestamps and join keys | Record model name, immutable version, artifact/config hash, prediction relation/query, and unmatched/duplicate counts. Missing lineage fails closed. |
| Conditional Human/Ops gate | `ODP-PLAN-AVM-OUTCOME-BACKFILL-001` | At least 120 authentic eligible mature transactions plus dataset hash, lineage, owner, freshness/cutoff, access policy, and readback location | This task is formally downstream of the parent but becomes the concrete handback when authentic inputs are absent. AI may validate returned evidence; it may not manufacture or approve it. |
| Governance authority | Human/Ops data/access authority | Authoritative RBAC/ABAC policy and auditable access decision | Record principal, resource/scope, policy/version, decision, authority readback, correlation ID, and time in redacted form. A role string or repository JSON alone is not authority. |
| Runtime consumer | Governed production binding for `avm` | Fail-closed availability until every gate passes | Preserve `DATA_CONTRACT_NOT_MATURE` at the governed binding when data contract maturity is absent. More specific installer evidence may retain `MATURE_REALIZED_TRANSACTION_OUTCOME_RELATION_MISSING`; do not collapse the two layers into a fabricated `ACTIVE` state. |
| Program consumers | Live staging proof and final gate audit | Exact-SHA model alias/readback, production proof, and release decision | Parent completion is evidence input only. It does not authorize deployment or GO; those remain separate downstream gates. |

Planning documents and live task state may show different historical assignees.
Use current task state for routing and the execution packet for acceptance content.

Routing caveat as of 2026-08-17: the live `ai-status.json` task set was rebuilt
from open GitHub PRs (`source: github-pr-reimport-2026-08-17`) and no longer
contains task records for `ODP-PLAN-AVM-OUTCOME-001`,
`ODP-PLAN-OSS-LICENSE-GATE-001`, or `ODP-PLAN-AVM-OUTCOME-BACKFILL-001`; they are
also absent from `ai-task-archive/`. The parent owner, parent reviewer, and the
prerequisite/backfill task statuses named above therefore cannot be confirmed
from live state at this commit. Whoever picks up the parent work must
re-establish those task records and their current assignees before treating any
row in the dependency map as routable. The acceptance content below is unaffected
by this, because it is bound to repository contracts rather than to assignees.

The surviving in-repository reference for those task dispositions is
`docs/evidence/gates/RELEASE_GATE_REGISTRY.json`, which at this commit records
`ODP-PLAN-AVM-OUTCOME-001` as still open under Gate 3 and
`ODP-PLAN-OSS-LICENSE-GATE-001` as archived done under Gate 4 with no
exact-candidate Gate 4 receipt or Human/Ops legal approval. Read against the
dependency map, that means the license prerequisite is closed as a task but still
short of the release-gate evidence an activation or release claim would need.

## Acceptance checklist

The parent owner should replace each `PENDING` with `PASS`, `FAIL`, or
`NOT_APPLICABLE`, and add an immutable evidence path/URI plus SHA-256. `PASS`
requires both the positive evidence and the stated negative/fail-closed proof.

### A. Authoritative inventory and maturity

| ID | Required proof | Fail-closed / mutation proof | Status | Evidence |
|---|---|---|---|---|
| A1 | Source-system identity, accountable owner, authoritative readback location, source/query version, cutoff/freshness, dataset snapshot SHA-256, and query SHA-256. | Reject fixture, mock, synthetic, auto-seeded, research-only, unverifiable, stale, or hash-mismatched input. | `PENDING` | Parent owner to fill |
| A2 | Exact eligibility and maturity definitions, including event-time boundary and exclusion reasons. Report total, eligible, mature, excluded, and distinct-business-key counts from the same snapshot. | Boundary mutations immediately below and at the inclusive `>=120` threshold; immature rows never count. | `PENDING` | Parent owner to fill |
| A3 | At least 120 unique eligible mature DealRoom transactions after deterministic deduplication. Stable join/business keys and duplicate policy are documented. | Reject duplicate outcome rows, conflicting corrections, missing keys, and count drift between receipt and source. | `PENDING` | Parent owner to fill |
| A4 | Confidential classification is attached to source, relevant fields, evidence outputs, and permitted use. | Reject missing classification or raw confidential values in committed/logged evidence. | `PENDING` | Parent owner to fill |

### B. Prediction lineage and aligned join

| ID | Required proof | Fail-closed / mutation proof | Status | Evidence |
|---|---|---|---|---|
| B1 | Every scored row binds to `dealroom_avm`, an immutable model version/artifact hash, feature/config version, prediction time, and prediction source snapshot/query. | Reject mutable aliases without version readback, missing model/dataset hashes, or predictions made after the outcome cutoff. | `PENDING` | Parent owner to fill |
| B2 | The documented join is one prediction to one mature outcome. Report outcome, prediction, matched, unmatched-on-each-side, duplicate, and final-evaluation counts. | Reject many-to-many joins, silent dropping, unmatched prediction filling, and count/population drift. | `PENDING` | Parent owner to fill |
| B3 | Prediction columns are independently produced and temporally prior to the outcome. | Substitute outcome for prediction, copy/shift outcome-derived values, or remove prediction lineage; every mutation must fail. | `PENDING` | Parent owner to fill |
| B4 | One immutable aligned population manifest/hash is used by coverage, calibration, and value-band calculations, with exclusions recorded once. | Reject metrics whose denominators, segment membership, cutoff, or population hashes diverge. | `PENDING` | Parent owner to fill |

### C. Coverage, calibration, and value-band separation

| ID | Required proof | Fail-closed / mutation proof | Status | Evidence |
|---|---|---|---|---|
| C1 | Finite, ordered intervals (`p10 <= p50 <= p90`) and explicit numerator/denominator for empirical p80 coverage on the aligned population. Current model spec requires `min_p80_coverage = 0.70`; bind the evaluated policy/version so threshold drift is visible. | Reject reversed/malformed intervals, non-finite values, zero denominators, fixed or forged metrics, and coverage below policy. | `PENDING` | Parent owner to fill |
| C2 | Calibration report includes the predeclared statistic(s), formula, threshold, overall result, segment/band populations, and uncertainty or small-sample handling. | No acceptance until the threshold is explicit and version-bound; reject empty/undersized groups and non-finite results. | `PENDING` | Parent owner to fill |
| C3 | Value-band report predeclares band boundaries and separation rule, then shows prediction and realized distributions/counts for every band on the same population. | Reject post-hoc boundaries, empty/undersized bands, outcome-derived band assignment, or failure of the predeclared separation rule. | `PENDING` | Parent owner to fill |
| C4 | If normalized MAE is used in the parent verdict, record formula, scale/denominator, and policy binding. Current model spec has `max_normalized_mae = 0.30`. | Reject zero/invalid normalization, non-finite values, or a metric computed on a different population. | `PENDING` | Parent owner to fill |

The planning packet says coverage, calibration, and value-band separation must
pass, but it does not itself define every calibration or band-separation
threshold. The implementation must bind those missing thresholds to an approved,
versioned policy before claiming `ACTIVE`; this support packet does not invent
them.

### D. Confidential access and audit

| ID | Required proof | Fail-closed / mutation proof | Status | Evidence |
|---|---|---|---|---|
| D1 | Authoritative RBAC/ABAC policy/version readback covers the dataset, fields, purpose, environment, tenant/scope, and requesting principal. | Reject unknown principal, wrong tenant/scope/purpose, expired policy, denied decision, or a role string presented as authentication. | `PENDING` | Parent owner to fill |
| D2 | Correlated audit receipt records request/decision/event IDs, authority/source system, policy/version, resource scope, decision, and timestamps without secret or raw confidential values. | Reject missing/tampered authority readback, missing correlation, raw values, credentials, tokens, or access without an audit event. | `PENDING` | Parent owner to fill |
| D3 | Evidence is minimized/redacted and includes a reproducible redaction/validation check. | Scan committed artifacts and logs for raw transaction values and secrets; any finding blocks handoff. | `PENDING` | Parent owner to fill |

### E. Verdict, receipt, and handback

| ID | Required proof | Fail-closed / mutation proof | Status | Evidence |
|---|---|---|---|---|
| E1 | A deterministic receipt binds task/release/source/model/dataset/query/population/policy/evidence hashes, observed and eligible counts, threshold, gate results, reason code, and generation time. | Any field/hash/count mutation invalidates the receipt. Reject unbound or non-reproducible evidence. | `PENDING` | Parent owner to fill |
| E2 | `ACTIVE` is possible only when A1-D3 all pass on one evidence batch and every required hash resolves. Otherwise the governed binding remains unavailable. | Remove one required gate, forge `ACTIVE`, alter a bound hash, or provide a non-finite metric; validator must reject and runtime must stay governed-disabled. | `PENDING` | Parent owner to fill |
| E3 | When any authentic input/control is missing, a concrete `ODP-PLAN-AVM-OUTCOME-BACKFILL-001` handoff states owner, source/readback, query/schema, required count, maturity/cutoff, join keys, confidentiality/access fields, validation command, and return destination. | Reject vague requests, AI-generated outcomes/authority, or a handback that omits the specific failed gates and observed counts. | `PENDING` | Parent owner to fill |
| E4 | Parent handoff identifies exact implementation HEAD, changed paths, one-batch verification results, receipt/evidence hashes, unresolved authentic Human/Ops gates, and an explicit `ACTIVE` or governed-disabled conclusion. | Any source/config/test change after review invalidates prior approval and requires full-batch re-audit/re-review. | `PENDING` | Parent owner to fill |

## Required verification ledger for the parent

The parent handoff should record exact commands, exit codes, test counts, and the
tested source/evidence HEAD. At minimum it must cover:

```text
pytest -q tests -k "avm and (outcome or coverage or calibration or confidential)"
ruff check <explicit changed AVM Python paths>
git diff --check
```

It must also identify the exact tests or validator invocations for these
mutations: synthetic/fixture/auto-seeded input, maturity boundary, duplicates,
prediction/outcome substitution, unmatched join, unauthorized access, raw-value
leakage, source/model/dataset/query/count/population hash drift, non-finite
metrics, malformed intervals, and forged activation.

Existing tests such as
`tests/integration/test_avm_official_outcome_contract.py` are useful boundary
evidence, but they are not a substitute for the parent task's new outcome and
calibration verification.

## Reviewer handoff decision record

Sidecar reviewer `Antigravity2` reviews this packet for completeness and routing
usefulness only. The parent reviewer of record owns the implementation verdict;
that assignment must be re-confirmed from live task state per the routing caveat
above before the parent verdict is stamped.

| Review question | Expected answer |
|---|---|
| Did this sidecar modify canonical or runtime truth? | No; only this support artifact is in scope. |
| Does the packet authorize `ACTIVE`, deployment, or GO? | No. |
| Is official property-sale evidence allowed to satisfy DealRoom AVM? | No; the contracts are separate. |
| Can technical mechanics close while authentic data/access is absent? | Yes, only with complete fail-closed mechanics, a governed-disabled receipt, and a concrete Human/Ops handback. |
| What must the parent reviewer stamp? | Exact implementation HEAD plus the complete A-E evidence batch and explicit verdict. |

## Source basis

- `ai-status.json` task records for the current owner/reviewer, formal
  `ODP-PLAN-OSS-LICENSE-GATE-001` dependency, and parent acceptance wording
  (task-scoped worker snapshot, 2026-08-01).
- `docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.md`
  and `.json`, AVM batch deliverables, rejection set, evidence set, verification,
  handoff gate, and program deployment boundary.
- `docs/evidence/DEVELOPMENT_PLAN_GAP_EXECUTION_TASKS_2026-07-30.md`, AVM
  acceptance, Human/Ops backfill gate, and dependency table.
- `models/shared_ml/production_contracts.py`, `models/model_ready/contracts.py`,
  `product_ops/modeling/install_views.py`, and
  `tests/integration/test_avm_official_outcome_contract.py`, current repository
  identifiers, thresholds, governed-disabled reasons, and model-surface
  separation.
