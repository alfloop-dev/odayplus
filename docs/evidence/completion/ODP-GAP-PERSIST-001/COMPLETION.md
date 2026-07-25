# ODP-GAP-PERSIST-001 — Durable PostgreSQL (Cloud SQL) persistence backend

**Owner:** Claude · **Reviewer:** Codex2 · **Status:** implementation complete,
verified against real PostgreSQL 16.

## Problem

The product API's durable storage path (`ODP_PERSISTENCE=durable`) was wired
only to file-backed SQLite (`SqliteEngine`, ODP-PV-009). On Cloud Run that file
is ephemeral, so every revision/scale reset wiped intake/decision/audit data —
i.e. no production durability. A Cloud SQL PostgreSQL instance already runs for
the project (`alfaloop-data-project:asia-east1:oday-plus-dev-postgres`, DB
`oday_plus`, user `oday_app`, password in Secret Manager `oday-plus-dev-db-password`)
but **no code targeted it**.

## What was delivered

A PostgreSQL backend that is a drop-in for `SqliteEngine`, selected by
environment, with SQLite kept as the default for tests and E2E.

| Area | Change |
| --- | --- |
| Engine | `shared/infrastructure/persistence/postgres_engine.py` — `PostgresEngine` with the same `execute`/`query`/`query_one`/`next_ordinal`/`table_columns`/`close`/`lock` surface as `SqliteEngine`. |
| Dialect | The engine is the single dialect chokepoint: `?`→`%s` placeholder translation (`translate_placeholders`), DDL portability (`sqlite_ddl_to_postgres`: `INTEGER PRIMARY KEY AUTOINCREMENT`→`BIGSERIAL`, `BLOB`→`BYTEA`, `ADD COLUMN`→`ADD COLUMN IF NOT EXISTS`). Every durable repository runs unchanged. |
| PRAGMA | `PRAGMA table_info(...)` (SQLite-only) replaced by an engine-neutral `table_columns()` implemented by both engines; the two call sites (`audit_log.py`, `opsboard/audit/evidence_store.py`) now use it. |
| Migrations | Postgres bootstrap applies the same ordered durable migrations (`000002`–`000007`) with **`schema_migrations` once-only tracking** (revision + sha256 checksum); re-applying is a no-op, and a changed checksum raises `MigrationChecksumMismatch`. |
| Cloud SQL | `build_postgres_conninfo()` supports a full DSN, a Cloud SQL unix socket (`/cloudsql/<INSTANCE_CONNECTION_NAME>`), or explicit TCP parts. Password comes only from `ODP_DB_PASSWORD_FILE` (Secret Manager mount, preferred) or `ODP_DB_PASSWORD` — never hardcoded. `redact_conninfo()` keeps the password out of logs (`engine.dsn`). |
| Selection | `factory.build_persistence`: `durable` + a Postgres DSN/Cloud SQL instance → Postgres; `durable` with no DSN → SQLite (unchanged default); explicit `postgres`/`pg`/`cloudsql` → Postgres. |
| Dependency | `psycopg[binary]>=3.2` declared; imported lazily so the driverless CI env still collects. |

## Acceptance mapping

1. **Same interface, env-selected, SQLite default** — `PostgresEngine` mirrors
   `SqliteEngine`; `factory` routing; `test_postgres_dsn_configured`,
   `test_durable_migration_list_matches_sqlite_engine`.
2. **Migrations apply on Postgres + schema_migrations once-only** —
   `test_schema_migrations_tracked_once_only` (real PG),
   `test_every_durable_migration_translates_without_residual_sqlite_ddl`.
3. **OperatorConsole + decision/audit writes work on Postgres, correct
   parameterisation** — `test_durable_round_trip_survives_fresh_session` writes
   through `store_ops_repository` (OperatorConsole), `sitescore_decision_store`,
   and `audit_log`; placeholder/DDL unit tests cover the `?`/json_valid/strftime
   concern (no `json_valid`/SQL-`strftime` exist in durable SQL; all `strftime`
   are Python calls).
4. **Durability across a fresh session, durable-only** —
   `test_durable_round_trip_survives_fresh_session` (write → `close()` → fresh
   bundle → read back, real PG); `test_memory_mode_does_not_persist` proves the
   in-memory backend loses the same records.
5. **Cloud SQL wiring, password from env/secret** — `build_postgres_conninfo`
   unix-socket + secret-file tests.
6. **No secrets in code/fixtures/logs** — `test_redact_conninfo_hides_password`;
   no password literal anywhere in the diff.

## Verification (commands run)

```
# Real PostgreSQL 16 (provisioned by the pgserver package, no root) + unit layer
.venv/bin/python -m pytest tests/unit/test_postgres_engine.py \
  tests/integration/test_postgres_persistence.py -v -o addopts=""
# -> 21 passed (incl. the live PG intake+decision+audit fresh-session round-trip)

# Existing SQLite-backed durable suites stay green
.venv/bin/python -m pytest \
  tests/integration/test_operator_shell_persistence.py \
  tests/integration/test_assisted_listing_intake_persistence.py \
  tests/integration/test_audit_evidence_persistence.py \
  tests/integration/test_flow_002_expansion_persistence.py \
  tests/integration/test_durable_repository_wiring.py \
  tests/integration/test_external_ingestion_persistence.py \
  tests/integration/test_audit_evidence_export.py
# -> 51 passed

.venv/bin/ruff check shared/infrastructure/persistence/ ...   # All checks passed!
git diff --check origin/dev...HEAD                            # clean
```

## Notes for the reviewer

- The live PG tests are marked `requires_live_env` (excluded from the default CI
  marker), matching the assisted-intake schema suite. They run against
  `ODP_TEST_PG_DSN` if set, otherwise an ephemeral PostgreSQL 16 from the
  `pgserver` package. In this worktree they were executed against a real
  PostgreSQL 16 server and passed.
- This task is code-only. Cloud Run deployment and the memory→Postgres cutover
  are ODP-OC-R5-003, which depends on this task.
