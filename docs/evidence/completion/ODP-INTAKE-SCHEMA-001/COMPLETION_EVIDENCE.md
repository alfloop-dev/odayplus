# ODP-INTAKE-SCHEMA-001 — Completion Evidence

**Title:** Implement assisted intake relational schema and tenant isolation
**Owner:** Claude · **Reviewer:** Codex2 · **Phase:** Assisted Listing Intake v1 Implementation
**Source design:** ODP-SD-INTAKE-001 (reviewed commit `e644bd0e`)

## Deliverables

| Artifact | Purpose |
|---|---|
| `infra/db/migrations/assisted_listing_intake/001_baseline.sql` … `004_tenant_rls_lineage.sql` | Ordered production migration — byte-for-byte copies of the approved four-artifact DDL stack |
| `infra/db/migrations/assisted_listing_intake/downgrade.sql` | Structural downgrade boundary (intake-context tables only; shared schemas preserved) |
| `infra/db/migrations/assisted_listing_intake/README.md` | Apply order, provenance, bounded-context rationale |
| `shared/infrastructure/persistence/assisted_listing_intake.py` | Driver-agnostic migration loader + checksum/manifest + contract-drift guard + tenant-table catalog |
| `tests/conftest.py` | Real PostgreSQL 16 provisioning fixtures (pgserver bundle or `INTAKE_TEST_DATABASE_URL`) |
| `tests/contract/test_assisted_listing_intake_schema.py` | Clean install, catalog constraints, downgrade boundary, contract-drift |
| `tests/security/test_assisted_listing_intake_rls.py` | Fail-closed tenant isolation proven as a non-superuser role |

## Acceptance mapping

- **Convert the approved four-artifact DDL stack into an ordered production migration without dropping approved constraints.**
  The four migration files are byte-for-byte copies of the reviewed contract SQL
  (sha256 equality asserted by `contract_drift()` and
  `test_migration_reproduces_reviewed_contract_artifacts_byte_for_byte`). Nothing is
  re-authored, so no constraint can be dropped in transit.
- **Implement every approved type, enum, nullability rule, FK, tenant-qualified unique constraint, check, version column, index, retention field, legal-hold field, and authoritative timestamp.**
  Proven against the live PostgreSQL 16 catalog: **30 tables**, **19 versioned tables**,
  **18 tenant-qualified unique constraints**, **22 lineage FKs**, **5 deferrable
  current-pointer FKs**, retention_class on 3 tables, legal_hold on 4 tables,
  authoritative timestamps and the load-bearing CHECK enums (incl. patch-0003
  `PENDING_REVIEW`) all asserted.
- **Enable and FORCE RLS on every tenant-bearing table with fail-closed tenant isolation policies.**
  All **28 tenant tables** have `relrowsecurity` + `relforcerowsecurity` + a
  `tenant_isolation` policy referencing `app.tenant_id` in both USING and WITH CHECK;
  the 2 global reference tables are correctly excluded. RLS security suite proves
  missing/empty context returns 0 rows, reads are tenant-scoped, cross-tenant
  INSERT is rejected, and UPDATE/DELETE cannot reach other tenants.
- **Prove clean install, upgrade, downgrade boundary, catalog constraints, tenant isolation, and rollback on PostgreSQL 16.**
  All exercised on a real PostgreSQL 16.2 server (no mocks). `test_downgrade_boundary_then_reinstall`
  proves downgrade removes only the intake context and that a clean install replays afterward.

## Verification (see `verification_run.log` for full captured output)

| Check | Result |
|---|---|
| `scripts/validate_assisted_listing_intake_design.py --strict-review-target` | PASS (16/16) |
| Contract suite (`tests/contract/...`) on PostgreSQL 16 | 15 passed |
| RLS security suite (`tests/security/...`) on PostgreSQL 16 | 6 passed |
| `scripts/validate_assisted_listing_intake_schema.sql` (RLS + tenant-FK + lineage) | PASS (no exception) |
| Canonical alembic chain regression (`tests/ops/...`) | 9 passed (chain still `["0001","0002"]`) |
| Minimal env (no pg driver) | 2 pure tests pass, 19 live tests skip cleanly |
| `ruff check` new files | clean |
| `git diff --check origin/dev...HEAD` | clean |

## Design decision: dedicated migration directory

The canonical platform baseline (`000001*`, `000002*`) already owns
`expansion.listings`, `expansion.candidate_sites`, and `audit.audit_events` with a
different shape. The intake contract redefines those names for its own bounded
context (Cloud SQL target per the rollout runbook). Adding this migration to the
linear canonical alembic chain would make `CREATE TABLE IF NOT EXISTS` silently
skip and break every downstream `ALTER`, and would also break the
`["0001","0002"]` chain assertion in `tests/ops/test_migration_backfill.py`. The
migration therefore lives in its own directory and is applied through the
programmatic loader against the intake service's own database.

## Reproducing the runtime proof

The live tests are marked `requires_live_env` (excluded from the default CI marker
expression) and skip cleanly without a database. To run them:

```bash
# Bundled PostgreSQL 16, no root required:
uv run --with pgserver --with 'psycopg[binary]' pytest \
  tests/contract/test_assisted_listing_intake_schema.py \
  tests/security/test_assisted_listing_intake_rls.py -q

# …or against an external server:
INTAKE_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db \
  uv run --with 'psycopg[binary]' pytest tests/contract/... tests/security/... -q
```
