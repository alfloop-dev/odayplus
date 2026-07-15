# ODP-PGAP-AUDIT-001 Verification

Task: ODP-PGAP-AUDIT-001
Owner: Codex
Reviewer: Claude

## Targeted Checks

```bash
python3 -m ruff check shared/audit shared/observability/audit.py modules/opsboard/audit apps/api/app/routes/audit.py tests/security/test_audit_policy.py tests/integration/test_audit_evidence_export.py tests/integration/test_audit_evidence_persistence.py
```

Result: passed before remediation anchor.

```bash
uv run pytest tests/security/test_audit_policy.py tests/integration/test_audit_evidence_export.py tests/integration/test_audit_evidence_persistence.py -q
```

Result: superseded by the focused remediation run below.

```bash
python3 -m ruff check shared/audit shared/infrastructure/persistence/audit_log.py shared/infrastructure/persistence/factory.py modules/opsboard/audit apps/api/app/routes/audit.py tests/integration/test_audit_evidence_persistence.py
```

Result: passed.

```bash
uv run pytest tests/integration/test_audit_evidence_persistence.py -q
```

Result: passed, 14 tests.

## Final Checks

```bash
python3 -m ruff check shared/audit shared/observability/audit.py modules/opsboard/audit apps/api/app/routes/audit.py tests
```

Result: passed.

```bash
uv run pytest tests/security tests/integration -q
```

Result: passed; collect-only count is 401 tests.

```bash
git diff --check origin/dev...HEAD
```

Result: passed.

## Additional Infra Check

```bash
terraform -chdir=infra/terraform fmt -check -recursive
```

Result: not run; `terraform` is not installed in this worker image.
