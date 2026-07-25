# ODay Plus Live Production Data Gate

`scripts/e2e/check_live_production_data.py` is the release-time proof that the
deployed ODay Plus API is not serving fixture, mock, synthetic, seeded, SQLite,
or in-memory data.

The gate requires all three independent inputs:

1. a deployed API origin (`ODP_LIVE_API_URL`);
2. a PostgreSQL DSN held in `ODP_LIVE_POSTGRES_DSN`; and
3. a commit-bound JSON evidence file (`ODP_LIVE_DATA_EVIDENCE`).

It also requires `ODP_LIVE_EXPECTED_SHA`. The PostgreSQL DSN and optional
`ODP_LIVE_BEARER_TOKEN` are consumed from environment variables and are never
included in output. PostgreSQL verification runs in a read-only transaction.

## Evidence Contract

The JSON evidence root must contain:

```json
{
  "schema_version": 1,
  "generated_at": "2026-07-24T11:30:00Z",
  "release_sha": "<40-character deployed commit>",
  "api_url": "https://<deployed-api-origin>",
  "data_mode": "live",
  "persistence": "postgresql",
  "source_database": "fongniao_prod",
  "datasets": {
    "merchant": {
      "run_id": "<uuid>",
      "source_count": 1,
      "raw_count": 1,
      "valid_loaded": 1,
      "canonical_count": 1,
      "quarantined_count": 0,
      "source_checksum": "<digest>",
      "raw_checksum": "<same digest>",
      "valid_checksum": "<digest for non-quarantined source snapshots>",
      "canonical_checksum": "<digest>"
    }
  },
  "runtime_receipts": {
    "operator": {
      "receipt_id": "<durable receipt>",
      "run_id": "<durable run>",
      "status": "SUCCEEDED",
      "occurred_at": "2026-07-24T11:00:00Z",
      "data_origin": "live",
      "persistence": "postgresql",
      "source_snapshot_ids": ["<real source snapshot>"],
      "proof_path": "/api/v1/live-data/receipts/operator/<receipt>"
    }
  }
}
```

`datasets` must contain `merchant`, `place`, and `transaction`. Their values
must exactly match the gate's live PostgreSQL queries. Every dataset must
satisfy:

```text
source_count = raw_count
source_count = valid_loaded + quarantined_count
valid_loaded = canonical_count = distinct lineage source snapshots
recorded quarantine = active quarantine for the run
source_checksum = raw_checksum
valid_checksum = canonical_checksum
```

`runtime_receipts` must contain `operator`, `forecastops`, `sitescore`, `avm`,
`netplan`, `priceops`, `adlift`, and `learninghub`. All receipts require fresh
live source lineage and a relative API `proof_path`. Every service except
Operator also requires an immutable model binding and execution proof:

```json
{
  "model_binding": {
    "model_name": "<registered model>",
    "model_version": "<approved version>",
    "artifact_uri": "gs://<immutable artifact>",
    "artifact_sha256": "<64 hex characters>",
    "registry_run_id": "<MLflow run>",
    "feature_schema_version": "<schema>",
    "dataset_snapshot_id": "<training/evaluation snapshot>"
  },
  "execution": {
    "engine": "<production engine>",
    "library": "<actual OSS library>",
    "actual_model_invoked": true,
    "fallback_used": false
  }
}
```

The deployed proof endpoint must return the same `receipt_id`, `run_id`, and
status. Absolute proof URLs, query strings, traversal, local artifact URIs,
missing digests, stale timestamps, missing records, and known surrogate markers
are blockers.

## Invocation

```bash
export ODP_LIVE_API_URL="https://api.example.invalid"
export ODP_LIVE_POSTGRES_DSN="<secret PostgreSQL DSN>"
export ODP_LIVE_DATA_EVIDENCE="/secure/path/live-data-evidence.json"
export ODP_LIVE_EXPECTED_SHA="<deployed commit SHA>"
export ODP_LIVE_BEARER_TOKEN="<optional proof API token>"

python3 scripts/e2e/check_live_production_data.py \
  --output .odp_data/live-production-data-gate/report.json
```

The example hostname above is documentation only and cannot pass the
production gate. Absence of any required live input returns exit code `1`.
