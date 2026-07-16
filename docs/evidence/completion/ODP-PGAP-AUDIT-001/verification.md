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

## Closeout Remediation Recheck

Date: 2026-07-16

```bash
python3 -m ruff check shared/infrastructure/persistence/audit_log.py modules/opsboard/audit/evidence_store.py tests/integration/test_audit_evidence_persistence.py
```

Result: passed.

```bash
uv run pytest tests/integration/test_audit_evidence_persistence.py -q
```

Result: passed, 15 tests.

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

```bash
docker compose -p oday-plus-e2e-audit-001 -f infra/docker/docker-compose.e2e.yml down -v --remove-orphans
make product-release-gate
```

Result: not passed. The closeout remediation removed the audit-event sequence
collision: PV-006 and PV-007 product loops passed on a clean E2E volume. The
remaining local gate failures were outside the audit persistence fix: the
HeatZone direct-picking test timed out waiting for
`window.__odpHeatZoneMapProject`, and `product-e2e-env` saw one API health
request `socket hang up` while compose logs continued to show successful
`/platform/health` checks and no API traceback. PR closeout remains blocked
until product-e2e is green and the PR merges.
