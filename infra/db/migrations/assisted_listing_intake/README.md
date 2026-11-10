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
| 5 | `005_decision_service_actor.sql` | `..._SCHEMA_0005_DECISION_SERVICE_ACTOR_PATCH.sql` |
| 6 | `006_workflow_schedule.sql` | `..._SCHEMA_0006_WORKFLOW_SCHEDULE_PATCH.sql` |

The original four-artifact receipt remains immutable. The existing loader
records the five- and six-step prefixes separately and retains prior checksums
and application timestamps. Engineering extensions do not approve production
activation. Step 006 adds the original Assignment/SLA binding and recorded
calendar/warning/breach checkpoints without supplying values for historical
rows. Its replay rejects partial or changed columns/constraints; a recorded
upgrade cannot authorize silent recreation of removed fields.

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

The canonical platform baseline already owns shared Listing, CandidateSite and
audit tables. The existing `apply_upgrade_to_database` loader uses
`000_canonical_compatibility.sql` before the ordered stack when those original
tables are present. This composes the Intake tables with the same PostgreSQL
database used by the runtime factory. Applying only `CREATE TABLE IF NOT EXISTS`
against the older shapes would silently skip required expansion. Use the
existing loader and its original receipt/checksum checks rather than a second
migration runner or an assumed separate production database.

## Applying it

```python
from shared.infrastructure.persistence import assisted_listing_intake as intake

# `execute` should run against an autocommit connection to an empty PostgreSQL 16
# database (the baseline step has no explicit transaction; the patch steps wrap
# themselves in BEGIN/COMMIT).
intake.apply_upgrade(cursor.execute)
```

After apply, `delivery_toolchain/governance/validate_assisted_listing_intake_schema.sql` must run clean
(it RAISEs on any RLS gap, missing tenant-qualified FK, or missing lineage
constraint). Application connections must `SET LOCAL app.tenant_id` inside every
request transaction; a missing or empty value fails closed under the RLS policy.
