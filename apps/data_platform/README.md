# ODay Plus Mongo to PostgreSQL Data Plane

## Production Contract

This package is the production-only data plane from the authorized MongoDB
database `fongniao_prod` into PostgreSQL. It has no in-memory, generated,
synthetic, local-file, or development fallback.

The runtime path is:

```text
Mongo projected read
-> content-addressed SourceEnvelope
-> dlt PostgreSQL raw merge
-> typed per-record validation
-> canonical PostgreSQL upsert or durable quarantine
-> checkpoint
-> count/checksum reconciliation
```

Raw capture always precedes canonical projection. A malformed row is retained
in `fongniao_raw.raw_<collection>` and recorded in
`data_plane.quarantined_records`; valid rows in the same batch continue.
Infrastructure failures stop the batch and do not advance its checkpoint.

## Required Environment

| Variable | Contract |
|---|---|
| `ODP_DATA_ENV` | Must be exactly `production`. |
| `ODP_DATA_MONGO_URI` | Authorized non-local MongoDB URI. |
| `ODP_DATA_MONGO_DATABASE` | Must be `fongniao_prod`. |
| `ODP_DATA_POSTGRES_DSN` or `ODAY_POSTGRES_DSN` | PostgreSQL DSN. SQLite and other sinks are rejected. |
| `ODP_DATA_RAW_SCHEMA` | dlt dataset; default `fongniao_raw`. |
| `ODP_DATA_CONTROL_SCHEMA` | Run/lineage/quarantine schema; default `data_plane`. |
| `ODP_DATA_BATCH_SIZE` | 100..20,000; default 5,000. |
| `ODP_DATA_MAX_RECORDS_PER_RUN` | Batch size..5,000,000; source policy may impose a lower bound. |
| `ODP_DATA_STATUS_MAPPING_PATH` | Owner-approved JSON contract conforming to `status_mapping.schema.json`. Optional globally; governed numeric/connection records are quarantined if the required namespace is absent. |

Cloud SQL Auth Proxy or connector local transports require all of:

```text
ODP_DATA_CLOUD_SQL_PROXY=true
ODP_DATA_CLOUD_SQL_INSTANCE=<project>:<region>:<instance>
ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE=cloud-sql-auth-proxy-sidecar
```

`cloud-sql-python-connector` is the other accepted evidence value. A unix
socket DSN must contain the same instance name. Unapproved localhost remains
blocked.

The status mapping JSON is governed input, not a code default:

```json
{
  "version": "approved-version",
  "approved_by": "data-governance-subject",
  "approved_at": "2026-07-24T00:00:00Z",
  "mappings": {
    "transaction": {
      "<source numeric code>": "<succeeded|failed|refunded|voided|partial>"
    },
    "trade": {
      "<source numeric code>": "<succeeded|failed|refunded|voided|partial>"
    },
    "merchant_operation": {
      "<source numeric code>": "<active|inactive>"
    },
    "place_operation": {
      "<source numeric code>": "<planned|open|suspended|closed|transferred>"
    },
    "place_type": {
      "<source numeric code>": "<owner-approved type code>"
    },
    "device_connection": {
      "<source state/action>": "<online|offline|error|available|occupied|maintenance>"
    }
  },
  "trade_paid_amount_rule": null
}
```

No numeric meaning is shipped in this repository. `trade_paid_amount_rule`
may only become `gross_when_succeeded_zero_otherwise` after explicit owner
approval; otherwise trade rows without `amountPaid` remain quarantined.
The same rule applies to merchant/place numeric operations, place numeric type,
and `device_log` connection state/action. Missing namespaces fail closed as
`OPERATION_MAPPING_UNAPPROVED`, `TYPE_MAPPING_UNAPPROVED`, or
`CONNECTION_MAPPING_UNAPPROVED`.

## Source Inventory and Selection

Authorized counts observed on 2026-07-24:

| Collection | Approx rows | Scheduled | Projection |
|---|---:|---|---|
| `merchant` | 1,436 | daily | `core.tenants`, owned `core.brands` |
| `place` | 3,511 | daily after merchant | `core.address_locations`, `core.stores` |
| `device` | 17,180 | daily after place | `core.machines` |
| `device_daily_statistics` | 8,252,111 | daily after device | tenant/store/device daily facts |
| `orders` | 2,136,497 | daily | authoritative canonical transactions |
| `transactions` | 13,106,594 | manual until numeric mapping approval | canonical transactions |
| `trade` | 158,750,080 | manual only | lowest-priority transactions; one-day, <=100k runs |
| `ai_revenue_stats` | 2,609,685 | daily | forecast inputs |
| `campaign` | 659 | daily | commercial input lineage |
| `product` / `products` | 5,349 / 1,933 | daily | pricing input lineage |
| `promotions` | 1,024 | daily | promotion input lineage |
| `ai_consumer_kmeans_v1` | 35,307 | daily | learning import lineage |
| `member` | 12,383 | manual only | minimized raw reference plus quarantine; no PII canonical |
| `device_log` | 14,864,112 | manual only | minimized raw plus defensible `core.machine_status_events` |

Small mutable dimensions and commercial collections use a bounded full
snapshot scan, not a date predicate. This is intentional: rows missing
`createdAt` must still land raw and be quarantined. Facts, logs, and AI
time-series outputs remain date-partitioned.

Live field bindings are explicit:

- device lifecycle: boolean `enable`; `connection` and nested
  `modelStatus.operationStatus.machineStatus` remain operational evidence
  rather than being coerced into the lifecycle enum; `machineType` and `model`
  identify the equipment;
- device daily: `startDatetime` and `endDatetime`;
- campaign: `offerName`, `isActive`, `startDatetime`, `endDatetime`,
  discount amount/percentage, offer type/method;
- singular product: `title` and nested `details`;
- plural products: `name`, `category`, `country`, `publish`, nested `template`;
- promotions: `enabled`, `start`, `end`, and nested `rule`;
- KMeans: list-valued `segmentLabel`.

Nested `details`, `template`, and `rule` values are retained intact in raw
evidence and as opaque namespaced JSON in `data_plane.domain_inputs`; their
unknown child keys are never flattened or assigned guessed semantics.

`orders` is authority rank 1 because its state contract is explicit:
`TRADE_SUCCESS`, `TRADE_FAIL`, `TRADE_REFUND`. `transactions` is rank 2 and
requires approved numeric status mapping. `trade` is rank 3 and additionally
requires an approved paid-amount rule. A lower-authority duplicate is
quarantined as `SOURCE_SUPERSEDED`; it cannot overwrite higher-authority data.
For linked order/transaction rows, canonical identity uses `orderId`; raw
lineage retains each collection record's own identity and snapshot. Trade uses
`transactionId` first because no reliable order link was established in the
observed contract.

## Quality and Isolation

Known source gaps are expected and accounted for:

- 72 merchants lack `country` or `createdAt`;
- 72 places lack `createdAt`;
- 73 places have no resolvable merchant;
- 7,165 devices have no resolvable current place/merchant;
- transaction/trade dates include 1970 epoch outliers;
- trade contains future dates through 2217.

The accepted event-time window starts at `2000-01-01`. Operational event times
more than seven days beyond observation are quarantined; forecast dates may be
up to 366 days ahead. Raw `source_updated_at`, data-plane `observed_at`, and
canonical `ingested_at` remain separate.

Missing merchant/place/device references use
`MISSING_MERCHANT_MAPPING`, `MISSING_PLACE_MAPPING`, or
`MISSING_DEVICE_MAPPING`. No orphan canonical row is created. Tenant identity
is deterministic from merchant source ID, and every place/device/fact must
resolve through that tenant.

`ai_revenue_stats` and `ai_consumer_kmeans_v1` are stored only as
`legacy_external_model_output`. Their source has no approved `model_version`
or `run_id`, so those columns are constrained to remain null. Each row retains
its source snapshot, content checksum, source freshness, observation time, and
ingestion run. These rows may support comparison/backfill evidence; they do not
represent newly registered MLflow training runs.

`device_log` is manual-first and limited to one-day, at-most-100,000-row runs.
`logType=error` requires `errCode` and produces machine error evidence.
`logType=connection` requires an owner-approved `device_connection` mapping.
Other log types remain raw-only and are accounted for as
`NON_CANONICAL_LOG_TYPE`. Free-form `info`, `payload`, `orders`,
`refundOrders`, and `result` are removed before raw landing; the snapshot
records which fields were redacted. The content checksum and deterministic
snapshot ID are computed before redaction, preserving distinct source evidence
without retaining the sensitive free-form value.

Reconciliation is successful only when:

```text
source_total = raw_count
source_total = valid_loaded + quarantined_count
valid_loaded = canonical_count
source_checksum = raw_checksum
valid_checksum = canonical_checksum
sum(quarantine reason counts) = quarantined_count
```

## Scheduling and Backfill

Dagster definitions are in `apps.data_platform.definitions`. Daily order:

```text
01:00 merchant -> place -> device
02:00 device_daily_statistics
02:30 authoritative orders -> canonical transactions
03:00 ai_revenue_stats
04:00 campaign + product(s) + promotions
05:00 ai_consumer_kmeans_v1
```

Change sensors exist for dimensions and operational facts. `transactions`,
`trade`, `member`, and `device_log` are manual jobs. No scheduled job attempts
an unbounded 158M trade or 14.8M device-log load.

Example bounded backfill:

```bash
python -m scripts.data_platform.backfill \
  --kind merchant --kind place --kind device \
  --start 2026-07-20T00:00:00Z \
  --end 2026-07-24T00:00:00Z \
  --partition-days 1 \
  --max-partitions 4
```

Trade additionally requires `--allow-trade`, one-day partitions, and remains
limited to 100,000 source records per run. Checkpoints advance after both raw
landing and per-record canonical/quarantine accounting complete.

Device logs likewise require:

```bash
python -m scripts.data_platform.backfill \
  --kind device_log \
  --start 2026-07-23T00:00:00Z \
  --end 2026-07-24T00:00:00Z \
  --partition-days 1 \
  --max-partitions 1 \
  --max-records 100000 \
  --allow-device-log
```
