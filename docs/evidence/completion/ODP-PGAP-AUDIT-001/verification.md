# ODP-PGAP-AUDIT-001 Verification

Task: ODP-PGAP-AUDIT-001
Owner: Codex
Reviewer: Claude

## Targeted Checks

```bash
python3 -m ruff check shared/audit shared/observability/audit.py modules/opsboard/audit apps/api/app/routes/audit.py tests/security/test_audit_policy.py tests/integration/test_audit_evidence_export.py tests/integration/test_audit_evidence_persistence.py
```

Result: passed.

```bash
uv run pytest tests/security/test_audit_policy.py tests/integration/test_audit_evidence_export.py tests/integration/test_audit_evidence_persistence.py -q
```

Result: passed, 22 tests.

## Final Checks

```bash
python3 -m ruff check shared/audit shared/observability/audit.py modules/opsboard/audit apps/api/app/routes/audit.py tests
```

Result: passed.

```bash
uv run pytest tests/security tests/integration -q
```

Result: passed.

```bash
git diff --check origin/dev...HEAD
```

Result: passed.

## Additional Infra Check

```bash
terraform -chdir=infra/terraform fmt -check -recursive
```

Result: not run; `terraform` is not installed in this worker image.
