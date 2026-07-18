# Completion Evidence: ODP-INTAKE-PRIVACY-001

## 1. Task Summary
Successfully implemented intake purge, legal hold, evidence export, and WORM integrity per ODP-SD-INTAKE-001 design response:
- **Blocker 1**: Operator console privacy sub-router edge permission check updated to require `operator_write_guard` instead of the less restrictive `operator_edge_write_guard` on all privacy mutations (intake purge, legal hold placement/release, and evidence export). This prevents unauthorized update access from the edge.
- **Blocker 2**: WORM saves (`_save_hold`, `_save_manifest`) implemented with fail-closed behavior on absent sink, write failure, or invalid receipt verification, returning a real `AuditWormReceipt` with correct `object_uri` and checksum, and checking it during verify.
- **Retention & Purge**: Implemented purge execution, legal-hold placement/release, residency enforcement on export, subject/export scope, and deletion-conflict fail-closed behavior.
- **Evidence Export**: Generated watermarked exports, manifests, checksums, signer/key versions, verification results, and download evidence.

## 2. Verification Results
### Pytest Security & Integration Tests
All 8 security and integration tests passed successfully.
Command run:
```bash
uv run pytest tests/security/test_assisted_listing_intake_privacy.py tests/integration/test_assisted_listing_evidence_export.py -q
```
Output:
```text
........                                                                 [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  /tmp/pantheon-worker-worktrees/oday-plus/odp-intake-privacy-001/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

tests/security/test_assisted_listing_intake_privacy.py::test_legal_hold_placement_and_segregation
  /tmp/pantheon-worker-worktrees/oday-plus/odp-intake-privacy-001/apps/api/app/routes/operator_modules/privacy.py:124: StarletteDeprecationWarning: 'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated. Use 'HTTP_422_UNPROCESSABLE_CONTENT' instead.
    handle_domain_error(exc)

tests/security/test_assisted_listing_intake_privacy.py::test_legal_hold_release_and_segregation
  /tmp/pantheon-worker-worktrees/oday-plus/odp-intake-privacy-001/apps/api/app/routes/operator_modules/privacy.py:146: StarletteDeprecationWarning: 'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated. Use 'HTTP_422_UNPROCESSABLE_CONTENT' instead.
    handle_domain_error(exc)

tests/security/test_assisted_listing_intake_privacy.py::test_purge_execution_and_conflict_fail_closed
  /tmp/pantheon-worker-worktrees/oday-plus/odp-intake-privacy-001/apps/api/app/routes/operator_modules/privacy.py:169: StarletteDeprecationWarning: 'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated. Use 'HTTP_422_UNPROCESSABLE_CONTENT' instead.
    handle_domain_error(exc)

tests/security/test_assisted_listing_intake_privacy.py::test_residency_enforcement_on_export
  /tmp/pantheon-worker-worktrees/oday-plus/odp-intake-privacy-001/apps/api/app/routes/operator_modules/privacy.py:196: StarletteDeprecationWarning: 'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated. Use 'HTTP_422_UNPROCESSABLE_CONTENT' instead.
    handle_domain_error(exc)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
```

### Ruff Check
Ruff checks are clean.
Command run:
```bash
uv run ruff check shared/audit modules/listing/application/intake_privacy.py apps/api/app/routes/operator_modules/privacy.py tests
```
Output:
```text
All checks passed!
```

### Git Diff Check
Git diff checklist is clean.
Command run:
```bash
git diff --check origin/dev...HEAD
```
Output:
```text
No issues found.
```
