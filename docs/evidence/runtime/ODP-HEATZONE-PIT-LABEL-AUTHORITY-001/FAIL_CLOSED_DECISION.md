# HeatZone authoritative PIT label decision

Task: `ODP-HEATZONE-PIT-LABEL-AUTHORITY-001`  
Executor: `Codex6`  
Decision date: `2026-07-27`  
Reviewer: `Codex`

## Decision

**FAIL CLOSED. No HeatZone training-label dataset or model alias was
activated.**

The approved opening-date authority inventory reports `0 / 2442`
`core.stores` rows with an authoritative `opened_on`. The HeatZone contract
requires an opened store with canonical tenant/store lineage before it can
form an origin, then requires 90 complete prior order partitions and 28
complete forward label partitions with geography valid at each transaction's
event time. With zero authoritative opening dates, the first mandatory gate
has zero eligible rows. Later gates cannot increase that count.

The live geography dependency is present but cannot substitute for opening
authority: it reports 1909 canonical store geography rows in 1443 H3
resolution-9 cells, all with source snapshots and canonical lineage.

## Reconciliation

| Gate | Observed | Required | Result |
|---|---:|---:|---|
| PG16 stores | 2442 | inventory basis | observed |
| Stores with approved immutable `opened_on` | 0 | at least one per participating store | blocked |
| Canonical PIT geography rows | 1909 | geography valid at transaction event time | available, but not sufficient |
| HeatZone origin rows | 0 | 90 prior days plus opened-store identity | blocked |
| Mature real HeatZone labels | 0 | 28 forward days per origin | blocked |
| Eligible real labels | 0 | at least 200 | blocked |
| Persisted label dataset | none | only when at least 200 labels pass every gate | intentionally not created |
| HeatZone model alias activation | none | only from an accepted reproducible dataset | intentionally not activated |

`scripts/models/sql/model_ready_views.sql` enforces the same boundary:
`store_source` filters on `store.opened_on IS NOT NULL`, historical geography
is selected with `place.valid_from <= txn.event_time`, and eligibility
requires 90 prior and 28 label partition days. No fixture, mock, synthetic
date, `created_at`, or ingestion timestamp is used as a substitute.

The machine-readable companion
[`reconciliation.json`](./reconciliation.json) pins the source evidence and
contract file by SHA-256. It records the empty result as a fail-closed
decision, not as a zero-row training dataset.

## Exact source data request

Data owner: **Expansion Operations / POS Data Platform**

Provide an approved immutable snapshot containing, for every participating
PG16 store:

- `tenant_id`: UUID equal to `core.stores.tenant_id`;
- `store_id`: UUID equal to `core.stores.store_id`;
- `opened_on`: ISO `YYYY-MM-DD`, the first day of real commercial operation;
- `authority_type`: for example `AUDITED_MERCHANT_RECORD`,
  `REGULATORY_FILING`, or `POS_FIRST_TRADING_DAY`;
- `provenance`: system-of-record name plus immutable record locator;
- `snapshot_id` and `content_sha256`.

Coverage must be all 2442 stores or an explicit eligible-subset store UUID
list. A missing record inside the declared subset fails closed. Corrections
must arrive as new immutable snapshots; conflicting dates must be quarantined
and must not overwrite prior evidence.

After that source is ingested through
`apps/data_platform/store_opening.py`, rerun the model-ready installer and
inventory. Activation is permitted only if at least 200 rows independently
pass canonical tenant/store lineage, 90 complete prior order days, 28
complete label days, transaction authority, historical geography, label
maturity, and reproducible snapshot persistence.

## Source evidence

- `docs/evidence/runtime/ODP-STORE-OPENING-AUTHORITY-ACTIVATION-001/fail_closed_decision.json`
  — authoritative inventory, zero mutations, exact opening-authority request.
- `docs/evidence/runtime/ODP-PRODUCTION-GEOGRAPHY-BACKFILL-001/live-geography-backfill.md`
  — live PG16 PIT geography reconciliation.
- `scripts/models/sql/model_ready_views.sql` — executable HeatZone PIT label
  contract.

