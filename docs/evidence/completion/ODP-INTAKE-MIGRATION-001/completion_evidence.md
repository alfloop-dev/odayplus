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
     - Property identity preservation without coordinate fabrication (passes NULL for missing coordinates, no invented `UNKNOWN_ADDRESS_...` or `REDIRECTED_PROPERTY_...` identities; resolves B1).
     - Month partition filtering based on real legacy timestamps, failing closed with findings on missing timestamps (resolves B2).
     - Durable proof persistence in `workflow.reconciliation_findings` (keyed by tenant/migration) and shadow comparison verification on fresh migrator instances without vacuous defaults (resolves B3).
     - Raw snapshot provenance and parser release metadata verification, failing closed without hardcoded fake placeholders (`odp-artifact://...` or sha256 of literal strings; resolves B4).
     - Full lineage record insertion into stage transitions, decisions, human corrections, outbox, and audit events without creating synthetic intake source rows (resolves B5).
     - Driver-agnostic dry-run transaction rollback (resolves Major a).
     - Full table-scoped data rollback via `rollback_migration(migration_ref, tenant_id)` leaving zero leftover records across all 17 touched tables (resolves Major b).
     - Executable CLI entrypoint with `argparse` supporting `--action backfill|verify|rollback|schema-upgrade|schema-rollback` (resolves Major b CLI requirement).
     - Accurate resume skip counts across intakes, listings, and candidates (resolves Major c).
     - Preserving observation timestamps, observation kinds, and evidence (resolves Major d).

2. **Operations & Rollback Runbook**:
   - `docs/runbooks/assisted-listing-intake-migration.md`: Operational procedures with executable CLI commands for dry-run verification, live partitioned backfill, handling findings, executing tenant/migration-scoped table rollback, and forward recovery.

3. **TestSuite Verification**:
   - `tests/ops/test_assisted_listing_intake_migration.py`: 12 comprehensive tests:
     - `test_migration_schema_upgrade_and_rollback`: Schema application & rollback verification.
     - `test_backfill_happy_path`: End-to-end backfill & shadow comparison validation.
     - `test_b1_property_identity_preservation_and_geocodes`: Property identity preservation & coordinate accuracy.
     - `test_b1_probe_null_coordinates_and_no_fabricated_identities`: Proves NULL coordinates for missing geocodes & zero invented identities.
     - `test_b2_month_and_source_partition_filtering`: Partition filtering by month and source using real timestamps.
     - `test_b3_shadow_comparison_count_and_checksum_proof`: Count/checksum drift detection & shadow comparison failure.
     - `test_b3_probe_shadow_proof_persistence_on_fresh_migrator`: Proves persistent shadow proof verification on fresh migrator instances.
     - `test_b4_snapshot_provenance_and_policy_preservation`: Provenance & policy preservation.
     - `test_b5_probe_no_synthetic_intake_creation`: Proves zero synthetic intakes created for orphan candidates.
     - `test_major_b_probe_complete_table_scoped_rollback`: Proves zero leftover rows across all 17 tables after scoped rollback.
     - `test_backfill_dry_run_does_not_commit`: Verification that dry run leaves DB untouched.
     - `test_backfill_resume_skips_existing`: Verification of idempotent resume functionality.

---

## Verification Proof

### Pytest Execution
```text
$ uv run pytest tests/ops/test_assisted_listing_intake_migration.py -v
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /tmp/pantheon-worker-worktrees/oday-plus/odp-intake-migration-001
configfile: pyproject.toml
plugins: anyio-4.14.1
collected 12 items

tests/ops/test_assisted_listing_intake_migration.py ............        [100%]

============================== 12 passed in 8.53s ==============================
```

### Ruff Check
```text
$ uv run ruff check scripts/migrations/assisted_listing_intake tests/ops/test_assisted_listing_intake_migration.py
All checks passed!
```

### Git Diff Check
```text
$ git diff --check origin/dev...HEAD
Passes without errors or trailing whitespace issues.
```
