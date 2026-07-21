# ODP-FLOW-009 · Verification

- Owner: Claude · Reviewer: Claude2
- Branch: `task/ODP-FLOW-009` (from `dev` tip `a5b7b29`)

## Commands run

### Backend — release monitor + regression
```
python3 -m pytest tests/integration/test_learninghub_release.py -q
# 5 passed (2 pre-existing release/rollback + 3 new monitor tests)

ruff check modules/learninghub apps/api/app/routes/learninghub.py \
  tests/integration/test_learninghub_release.py
# All checks passed!

python3 -m pytest tests/integration tests/contract tests/security tests/smoke
# 396 passed, 1 failed, 18 warnings
```
The single failure is `tests/smoke/test_foundation_smoke.py::test_production_dependency_stack_imports`
— `duckdb`, `sklearn`, and `statsmodels` are not installed in this worker
environment. It is pre-existing and unrelated to this task (no dependency files
were touched); CI installs the full production dependency stack and passes it.

### API endpoint smoke (FastAPI TestClient)
Registered dataset → two model versions → FULL release → monitor → list:
```
dataset 201 · mv230 201 · mv240 201 · release 201 FULL
monitor 201 BREACHED ROLLBACK · list 200 count=1
```

### Frontend
```
npm run typecheck --workspace=@oday-plus/openapi-client   # tsc --noEmit, clean
npm run typecheck --workspace=@oday-plus/web              # tsc --noEmit, clean
npm run lint --workspace=@oday-plus/web                   # next lint, No ESLint warnings or errors
```

### End-to-end (real uvicorn API + Next web + Playwright chromium)
```
CI=1 npx playwright test tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts --project=chromium
# 1 passed (15.8s) — drives dataset → versions → CANARY → FULL → monitor(BREACHED/
#   ROLLBACK) → ROLLBACK → GET /learninghub/releases; asserts learninghub.release_monitor.v1
#   audit; navigates /w/ai/releases and asserts learning-live-releases + learning-data-source.

CI=1 npx playwright test tests/e2e/e2e-learning-audit.spec.ts --project=chromium
# 5 passed — fixture-fallback path (no releases created) still renders the region + badge.
```

## Acceptance verification

1. **dataset model and artifact registrations persist** — durable repository +
   artifact store wired in `main.py`; product E2E registers dataset + two model
   versions with verified artifact digests (`artifact_verified === true`) and the
   registry evidence endpoint returns the persisted versions.
2. **validation gates and model cards are executable** — `validate_candidate` +
   `_assert_release_gate` enforce passed validation, complete + approved model
   card, and rollback target; product E2E and integration tests exercise both the
   pass and the block paths.
3. **release monitor and rollback state machine is audited** — the FULL release
   audits `learninghub.model_release.v1`; the new monitor audits
   `learninghub.release_monitor.v1` with the breach + recommended rollback, and
   the integration test proves the alias is left unchanged (never optimistic);
   the ROLLBACK release repoints PRODUCTION to the target.
4. **API backed Learning UI E2E passes** — `/w/ai/releases` binds to
   `GET /learninghub/releases`; the product E2E asserts the live region and the
   `DataSourceBadge` render and that the release-log endpoint returns the FULL +
   ROLLBACK decisions.
