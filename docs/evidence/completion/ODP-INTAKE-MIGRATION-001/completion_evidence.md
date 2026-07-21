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
     - Property identity preservation and coordinate preservation without fabrication (resolves B1).
     - Partition filtering across intakes, listings, and candidates by tenant, source, and month (resolves B2).
     - Count proof, SHA-256 checksum proof, and shadow comparison verification (resolves B3).
     - Preserving raw snapshot provenance, policy states, and parser release metadata without hardcoded values (resolves B4).
     - Full lineage record insertion into `intake_stage_transitions`, `identity.match_candidates`, `identity.match_decisions`, `intake.human_corrections`, `identity.property_redirects`, `workflow.outbox_events`, and `audit.audit_events` (resolves B5).
     - Driver-agnostic dry-run isolation and rollback (resolves Major a).
     - Scoped data rollback via `rollback_migration(migration_ref)` (resolves Major b).
     - Accurate resume skip counts across intakes, listings, and candidates (resolves Major c).
     - Preserving observation timestamps, observation kinds, and evidence (resolves Major d).

2. **Operations & Rollback Runbook**:
   - `docs/runbooks/assisted-listing-intake-migration.md`: Detailed operational procedures for dry-run verification, live partitioned backfill, handling blocking vs warning findings, executing migration-ref scoped rollback, and forward recovery.

3. **TestSuite Verification**:
   - `tests/ops/test_assisted_listing_intake_migration.py`: 9 comprehensive tests:
     - `test_migration_schema_upgrade_and_rollback`: Schema application & rollback verification.
     - `test_backfill_happy_path`: End-to-end backfill & shadow comparison validation.
     - `test_b1_property_identity_preservation_and_geocodes`: Property identity preservation & coordinate accuracy.
     - `test_b2_month_and_source_partition_filtering`: Partition filtering by month and source.
     - `test_b3_shadow_comparison_count_and_checksum_proof`: Count/checksum drift detection & shadow comparison failure.
     - `test_b4_snapshot_provenance_and_policy_preservation`: Provenance & policy preservation.
     - `test_b5_full_lineage_migration`: Full lineage migration across stage transitions, decisions, corrections, outbox, and audit events.
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
collected 9 items

tests/ops/test_assisted_listing_intake_migration.py .........            [100%]

============================== 9 passed in 7.09s ===============================
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
