# Assisted Listing Intake — ordered production migration

Task: **ODP-INTAKE-SCHEMA-001** — converts the approved ODP-SD-INTAKE-001
four-artifact PostgreSQL 16 DDL stack into an ordered production migration for the
assisted listing intake bounded context (Cloud SQL target per the migration
rollout runbook).

## Apply order

| # | File | Reproduces (byte-for-byte) |
|---|------|-----------------------------|
| 1 | `001_baseline.sql`            | `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql` |
| 2 | `002_consistency.sql`         | `..._SCHEMA_0002_CONSISTENCY_PATCH.sql` |
| 3 | `003_promotion_state.sql`     | `..._SCHEMA_0003_PROMOTION_STATE_PATCH.sql` |
| 4 | `004_tenant_rls_lineage.sql`  | `..._SCHEMA_0004_TENANT_RLS_LINEAGE_PATCH.sql` |

Each file is a byte-for-byte copy of the reviewed contract artifact so the
production migration cannot silently drop or alter an approved type, enum,
nullability rule, foreign key, tenant-qualified unique constraint, check, version
column, index, retention field, legal-hold field, or authoritative timestamp. The
copies are checksum-guarded against drift by
`shared.infrastructure.persistence.assisted_listing_intake.contract_drift()` and by
`tests/contract/test_assisted_listing_intake_schema.py`.

`downgrade.sql` is a **structural boundary**: it drops the intake bounded-context
relations so a clean install can be replayed, but it never drops the shared
`expansion` / `workflow` / `audit` schema namespaces and — per the rollout runbook
§5.2 — is not the production rollback path (production rollback disables
tenant/source flags and keeps target data read-only).

## Why a dedicated directory (not the `versions/` alembic chain)

The canonical platform baseline (`infra/db/migrations/000001*`, `000002*`) already
owns `expansion.listings`, `expansion.candidate_sites`, and `audit.audit_events`
with a different shape. The intake contract redefines those names for its own
bounded context. Chaining this migration onto the linear canonical alembic chain
would make `CREATE TABLE IF NOT EXISTS` silently skip and break every downstream
`ALTER`. This migration therefore targets the intake service's own database and is
applied through the programmatic loader rather than the shared alembic chain.

## Applying it

```python
from shared.infrastructure.persistence import assisted_listing_intake as intake

# `execute` should run against an autocommit connection to an empty PostgreSQL 16
# database (the baseline step has no explicit transaction; the patch steps wrap
# themselves in BEGIN/COMMIT).
intake.apply_upgrade(cursor.execute)
```

After apply, `scripts/validate_assisted_listing_intake_schema.sql` must run clean
(it RAISEs on any RLS gap, missing tenant-qualified FK, or missing lineage
constraint). Application connections must `SET LOCAL app.tenant_id` inside every
request transaction; a missing or empty value fails closed under the RLS policy.
