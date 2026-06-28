# Durable Audit Evidence Store and Export Retention (ODP-PV-011)

Phase: PV Product-Grade E2E Validation · Owner: Claude2 · Reviewer: Codex

## Goal

ODP-PV-009 made audit **events** restart-survivable. This task makes the
**evidence bundles** an export produces durable too: persisted with a content
**hash**, the **actor/reason** that produced them, the **privacy scope** they
cover, and a resolved **retention** window — so a subsidy/audit export can be
retrieved and proven byte-for-byte after a process restart, and aged-out
exports can be purged under a legal-hold-aware policy
(ODP-SD-09 §11 audit retention; subsidy evidence matrix).

## Approach

The export path already builds an immutable `AuditEvidenceBundle` and records an
`audit.evidence_export.v1` audit event. This task adds a parallel durable store
for the bundle itself, reusing the ODP-PV-009 `SqliteEngine`:

- **Contract** (`shared/audit/persistence.py`) — generic, module-free:
  `RetainedEvidence` (the persisted projection), the `EvidenceBundleStore`
  protocol, the `InMemoryEvidenceBundleStore` default, and
  `resolve_retention_policy()` / `EvidenceRetentionPolicy`.
- **OpsBoard adapter** (`modules/opsboard/audit/evidence_store.py`) —
  `retained_evidence_from_bundle()` projects an `AuditEvidenceBundle` into a
  `RetainedEvidence`, and `DurableEvidenceBundleStore` persists it.
- **Schema** (`infra/db/migrations/000003_durable_audit_evidence.sql`) — a
  `durable_evidence_bundles` table, columnar on its queryable dimensions
  (program, checksum, correlation, privacy scope, retention) plus a JSON blob
  preserving the full bundle. The engine now applies an explicit ordered list of
  engine-neutral E2E migrations at bootstrap, so this artifact and the runtime
  schema cannot drift (000001 Postgres/PostGIS is intentionally excluded).
- **Wiring** — `AuditEvidenceExportService` persists the bundle when an
  `evidence_store` is supplied; the persistence factory adds an `evidence_store`
  to its bundle (in-memory in `memory` mode, durable in `durable`/`sqlite`
  mode); `create_app` injects it into the audit router.

Default behaviour is unchanged: with no `evidence_store` the service behaves
exactly as before.

## What landed

| Layer | File |
| --- | --- |
| Retention contract + record + in-memory store | `shared/audit/persistence.py` |
| OpsBoard projection + durable SQLite store | `modules/opsboard/audit/evidence_store.py` |
| Evidence bundle DDL (executed verbatim on bootstrap) | `infra/db/migrations/000003_durable_audit_evidence.sql` |
| Engine: apply ordered E2E migrations | `shared/infrastructure/persistence/engine.py` |
| Service persists + retention metadata | `modules/opsboard/audit/application/evidence_export.py` |
| Bundle carries retention block | `modules/opsboard/audit/domain/evidence.py` |
| Factory bundle gains `evidence_store` | `shared/infrastructure/persistence/factory.py` |
| API wiring + retrieval/listing endpoints | `apps/api/oday_api/main.py`, `apps/api/app/routes/audit.py` |
| Integration tests | `tests/integration/test_audit_evidence_persistence.py` |

## Retention policy

Resolved from the export's privacy scope (`resolve_retention_policy`):

| Privacy scope | Retention class | Window |
| --- | --- | --- |
| `restricted`/highly classified, or any `sensitive` export | `regulatory-7y` | ~2557 days |
| `confidential` | `audit-5y` | ~1826 days |
| `internal`/`public` | `standard-1y` | 365 days |

`retain_until = generated_at + window`. A `legal_hold` flag freezes a record
against purge regardless of its window. `purge_expired(as_of)` deletes only
past-retention, non-held records.

## Acceptance evidence

Covered by `tests/integration/test_audit_evidence_persistence.py`:

1. **Bundles persist with hash / actor / reason / privacy scope / retention** —
   `test_export_persists_retained_record_with_metadata`.
2. **Retention resolves from privacy scope** —
   `test_retention_policy_resolves_from_privacy_scope`.
3. **Store + API survive process restart** —
   `test_durable_evidence_store_survives_restart`,
   `test_api_persists_and_serves_retained_evidence` (re-opened on-disk DB).
4. **Retention purge respects legal hold** —
   `test_purge_expired_respects_legal_hold`.
5. **Audit/correlation linkage preserved** — the persisted record carries the
   export's `audit_event_id`, `bundle_checksum`, and `correlation_id`; the
   `audit.evidence_export.v1` event now also records `retention_class` and
   `retain_until`.

## Verification

```bash
python3 -m pytest tests/integration/test_audit_evidence_persistence.py -q          # 6 passed
python3 -m pytest tests/integration tests/contract -p no:warnings -q               # 193 passed
python3 -m pytest tests -p no:warnings -q                                          # 268 passed
python3 -m ruff check shared/audit modules/opsboard/audit apps/api \
  shared/infrastructure/persistence tests/integration/test_audit_evidence_persistence.py   # clean
```

To run the API with a durable evidence store for E2E:

```bash
ODP_PERSISTENCE=durable ODP_DB_PATH=/data/odp-e2e.sqlite3 \
  uv run uvicorn apps.api.oday_api.main:app
```

## Notes / follow-ups

- The durable backend is the E2E/local durability path; production durability
  against the canonical Postgres schema is a separate wiring step (the
  `EvidenceBundleStore` seam makes that swap mechanical).
- The JSON bundle blob is written and read only by our own process; queryable
  fields (program, checksum, correlation, retention, privacy scope) are stored
  columnar so retention sweeps and audits stay real SQL queries.
- A scheduled retention sweep (calling `purge_expired`) is left as an ops wiring
  follow-up; the policy, query, and legal-hold guard are in place.
