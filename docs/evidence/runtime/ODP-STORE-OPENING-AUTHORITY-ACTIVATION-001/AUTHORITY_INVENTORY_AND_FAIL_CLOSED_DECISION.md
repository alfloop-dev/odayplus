# ODP-STORE-OPENING-AUTHORITY-ACTIVATION-001 — Store Opening Authority Inventory and Fail-Closed Decision

- Task: Activate authoritative store opening dates in PG16
- Owner: Claude · Reviewer: Codex
- Decision recorded: 2026-07-27T20:11:25Z
- Decision: **FAIL CLOSED — no approved authoritative `opened_on` source exists in any reachable approved source. Zero rows mutated.**

## 1. Scope and rule set

The task requires backfilling `core.stores.opened_on` in PG16 only from a source with
explicit approved authority and immutable source identity. Prohibited substitutes:
`created_at` inference, fixtures, mocks, synthetic dates, manual fabrication. If no
approved source exists, the task must remain fail closed and publish the exact owner,
format, coverage, and history requirements for the missing data. This document is that
fail-closed publication.

## 2. Source inventory (acceptance: "inventory every available operational and raw source")

All inventory queries were read-only. Verification was performed against the live
Cloud SQL proxies and the approved legacy raw capture, and recorded in the task
activity log by reviewer Codex at 2026-07-27T20:05:02Z and 2026-07-27T20:07:35Z.

| # | Source | Access path | Result for `opened_on` authority |
|---|--------|-------------|----------------------------------|
| 1 | Target PG16 `core.stores` | Cloud SQL proxy 127.0.0.1:6433 (secret `oday-plus-dev-api-database-url-pg16`) | 2442 rows, `opened_on` non-null count = **0**. No other non-null SQL column matching opening/launch/start/establish/founding/activation exists. `service_start_time` is `00:00` (daily service hour, not opening authority). |
| 2 | Legacy PG15 `core.stores` | Cloud SQL proxy 127.0.0.1:6432 (secret `oday-plus-dev-api-database-url`) | 2442 rows, `opened_on` non-null count = **0**. Same column sweep result as PG16: no opening/launch/start/establish/founding/activation column carries data. |
| 3 | Approved legacy capture `fongniao_raw.raw_place` | Immutable raw snapshot tables | 3516 captured rows. Document keys are exactly `_id, id, title, address, geolocation, merchant, merchantId, operation, publish, type, createdAt, updatedAt`. Counts for every opening-date key variant — `opened_on, opening_date, openingDate, openedOn, openedAt, open_date, openDate, launchDate, launchedAt, operationStartDate, establishedAt` — are all **0**. |
| 4 | All other `fongniao_raw` snapshot tables | Immutable raw snapshot tables | Only date-bearing fields are `campaign.startDatetime` and `promotions.start`. These are campaign/promotion windows, unrelated to store opening, and are not opening authority. |
| 5 | Direct approved Mongo URI (fongniao production) | Direct connection | **Unreachable: `ReplicaSetNoPrimary` after 15s.** Recorded as an external source-access blocker. This is an access failure, not evidence that a date does or does not exist upstream. |

Environment receipt (owner session, 2026-07-27T20:10Z): both Cloud SQL proxy ports
127.0.0.1:6432 (PG15) and 127.0.0.1:6433 (PG16) accept TCP connections from the worker
environment; source facts above match the reviewer-recorded inventory.

## 3. Authority selection result

No inventoried source provides an explicit, approved, immutable store-opening authority:

- The only fields that superficially resemble dates in the raw capture (`createdAt`,
  `updatedAt`) are record-lifecycle timestamps. Using them would be `created_at`
  inference, which is explicitly prohibited.
- Campaign/promotion start datetimes describe marketing windows, not store opening.
- The direct Mongo replica is unreachable, so no additional upstream collection could
  be inventoried; that path stays open as an access blocker (§6), not as authority.

Therefore no source qualifies under "explicit approved authority and immutable source
identity", and the backfill remains fail closed.

## 4. Zero-mutation proof

- PG16 `core.stores.opened_on` non-null count before this task: **0 / 2442**.
- PG16 `core.stores.opened_on` non-null count after this task: **0 / 2442** (no
  backfill executed; no INSERT/UPDATE was issued against PG15 or PG16 by this task —
  all queries were read-only inventory).
- `product_ops/modeling/store_opening_backfill.py` was **not** run against any live database.
  The delivered engine (`apps/data_platform/store_opening.py`, ODP-STORE-OPENING-001,
  merged via PR #435) raises `MissingStoreOpeningAuthorityError` /
  `UnauthoritativeStoreOpeningError` on exactly this condition; fail-closed behavior,
  replay idempotency, conflict quarantine, and tenant isolation are covered by
  `tests/integration/test_store_opening_backfill.py`.

## 5. Downstream eligibility report

- Non-null `opened_on` count available to downstream consumers: **0**.
- SiteScore propensity (`sitescore_propensity`): **0 stores eligible** on the
  opened_on axis; must remain fail closed for any feature or label derived from
  store opening dates.
- HeatZone PIT labels (ODP-HEATZONE-PIT-LABEL-AUTHORITY-001): **0 eligible labels**
  from this authority; that task's dependency on approved immutable `opened_on`
  authority is NOT satisfied and it must remain fail closed on this axis.
- DealRoom AVM / ForecastOps: no change; they do not consume `opened_on` from this
  path today.

## 6. Exact data request (fail-closed publication)

- **Owner of the missing data:** Expansion Operations / POS Data Platform.
- **Required record format (per store):**
  - `tenant_id` — tenant UUID matching PG16 `core.stores.tenant_id`
  - `store_id` — store UUID matching PG16 `core.stores.store_id`
  - `opened_on` — ISO 8601 calendar date (`YYYY-MM-DD`) of first day of real
    commercial operation
  - `authority_type` — e.g. `AUDITED_MERCHANT_RECORD`, `REGULATORY_FILING`,
    `POS_FIRST_TRADING_DAY`
  - `provenance` — human-traceable provenance note (system of record + record locator)
  - immutable snapshot identity — content-addressable snapshot (snapshot UUID +
    `content_sha256`) so replay and audit resolve to the same bytes
- **Required coverage:** all **2442** PG16 `core.stores` rows, or an explicit eligible
  subset list (store UUIDs); any store in the eligible subset without a record fails
  closed per the delivered engine.
- **History requirements:** dates must reflect actual historical opening (not record
  creation or migration timestamps), must be stable under replay (immutable snapshot),
  and must be point-in-time safe for training-label use (no retroactive silent edits;
  corrections arrive as new snapshots and conflicting values are quarantined, not
  overwritten).
- **Access blocker to clear (alternative path):** restore reachability of the approved
  fongniao production Mongo URI (`ReplicaSetNoPrimary` after 15s from the worker
  environment) so a full upstream collection inventory can rule in/out any additional
  authority fields at origin.

## 7. Activation path once data arrives

No new code was required for this task. The approved activation path is already
merged and reviewed (ODP-STORE-OPENING-001, PR #435):

```
python -m product_ops.modeling.store_opening_backfill \
  --tenant-id <UUID> --snapshot-id <UUID> \
  --input-json <authority-records.json> \
  --eligible-stores-json <eligible-store-uuids.json> \
  [--dry-run]
```

It enforces tenant lineage, immutable snapshot identity (`content_sha256`), replay
idempotency, conflict quarantine, and fail-closed validation of eligible stores.

## 8. Machine-readable summary

See `fail_closed_decision.json` in this directory.
