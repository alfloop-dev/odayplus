# Completion Evidence: ODP-INTAKE-MIGRATION-001

## Task Overview
- **Task ID**: `ODP-INTAKE-MIGRATION-001`
- **Title**: Implement staging backfill, reconciliation, and rollback
- **Owner**: `Antigravity3`
- **Reviewer**: `Claude`
- **Phase**: Assisted Listing Intake v1 Implementation

---

## Deliverables Summary

1. **Staging Backfill & Reconciliation Engine**:
   - `scripts/migrations/assisted_listing_intake/migrate.py`: Implements `IntakeMigrator` handling:
     - Scoped `migration_ref` rollback without destroying unrelated migrations, live outbox events (`CandidateSitePromoted`), or pre-existing orphan properties (resolves Blocker 2 and Blocker 3).
     - WORM append-only audit preservation during rollback per reliability contract (resolves Blocker 3c).
     - Accumulating full migrated set (both skipped and newly-inserted UUIDs) during resume pass so interrupt->resume->verify cutover path succeeds with 0 blocking findings (resolves Blocker 4).
     - Executable CLI entrypoint requiring explicit database connection targets (`--db-dsn` / `--sqlite-path` / `ODAY_DATABASE_URL`) failing closed if omitted (resolves Blocker 5).
     - Per-partition count and SHA256 checksum shadow proof verification for multi-partition backfills (resolves Major c).
     - Parser release validation status default without provenance invention (resolves Minor).

2. **Operations & Rollback Runbook**:
   - `docs/runbooks/assisted-listing-intake-migration.md`: Operational procedures with fully executable CLI commands specifying `--db-dsn "$ODAY_DATABASE_URL"` for dry-run verification, live partitioned backfill, shadow verification, tenant/migration-scoped table rollback, and forward recovery.

3. **TestSuite Verification**:
   - `tests/ops/test_assisted_listing_intake_migration.py`: 20 comprehensive tests:
     - `test_migration_schema_upgrade_and_rollback`: Schema application & rollback verification.
     - `test_backfill_happy_path`: End-to-end backfill & shadow comparison validation.
     - `test_b1_property_identity_preservation_and_geocodes`: Property identity preservation & coordinate accuracy.
     - `test_b1_probe_null_coordinates_and_no_fabricated_identities`: Proves NULL coordinates for missing geocodes & zero invented identities.
     - `test_b2_month_and_source_partition_filtering`: Partition filtering by month and source using real timestamps.
     - `test_b3_shadow_comparison_count_and_checksum_proof`: Count/checksum drift detection & shadow comparison failure.
     - `test_b3_probe_shadow_proof_persistence_on_fresh_migrator`: Proves persistent shadow proof verification on fresh migrator instances.
     - `test_b4_snapshot_provenance_and_policy_preservation`: Provenance & policy preservation.
     - `test_b5_probe_no_synthetic_intake_creation`: Proves zero synthetic intakes created for orphan candidates.
     - `test_major_b_probe_complete_table_scoped_rollback`: Proves zero leftover state records across all 16 state tables after scoped rollback.
     - `test_backfill_dry_run_does_not_commit`: Verification that dry run leaves DB untouched.
     - `test_backfill_resume_skips_existing`: Verification of idempotent resume functionality.
     - `test_blocker3_rollback_preserves_unrelated_tenant_data`: Verifies rollback preserves unrelated tenant intakes and audit events.
     - `test_blocker2_month_partitioning_no_fabricated_created_at_date`: Month filtering requirement for legacy timestamps.
     - `test_cli_subprocess_against_pg_fixture`: Real CLI-subprocess end-to-end test against PostgreSQL PG16 fixture.
     - `test_cli_fail_closed_without_database_target`: Proves CLI fails closed when no DB target is specified.
     - `test_blocker2_rollback_scoped_to_migration_ref`: Proves rollback of MIG-REF-B does not touch MIG-REF-A data.
     - `test_blocker3_rollback_preserves_live_outbox_and_orphan_properties`: Proves rollback preserves live outbox events and pre-existing properties.
     - `test_blocker4_resume_then_verify_cutover_path`: Proves interrupt -> resume -> verify cutover path succeeds with 0 blocking findings.
     - `test_major_c_multi_partition_checksum_and_preexisting_tenant_rows`: Proves multi-partition count and checksum verification.

---

## Verification Proof

### Final Re-Verification (2026-07-21T02:34Z · HEAD 1ad6d445)

All three required verification checks pass on HEAD `1ad6d445` (Antigravity3 re-dispatch):

### Pytest Execution
```text
$ uv run pytest tests/ops/test_assisted_listing_intake_migration.py -q
....................                                                     [100%]
20 passed
```

### Ruff Check
```text
$ uv run ruff check scripts/migrations/assisted_listing_intake tests/ops/test_assisted_listing_intake_migration.py
All checks passed!
```

### Git Diff Check
```text
$ git diff --check origin/dev...HEAD
(no output — all checks pass, exit 0)
```

---

## PR Reference

- **PR #343**: [ODP-INTAKE-MIGRATION-001: Implement staging backfill, reconciliation, and rollback](https://github.com/alfloop-dev/odayplus/pull/343)
- **Branch**: `task/ODP-INTAKE-MIGRATION-001` → `dev`
- **HEAD SHA**: `1ad6d445` (pushed 2026-07-21; re-verified by Antigravity3 re-dispatch 2026-07-21T02:34Z)
