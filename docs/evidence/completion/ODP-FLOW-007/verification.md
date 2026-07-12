# ODP-FLOW-007 - Verification

**Owner:** Codex2 · **Reviewer:** Claude2

## Commands run

```
python3 -m pytest tests/integration/test_avm_valuation.py -q
# 4 passed

python3 -m ruff check modules/avm/domain/valuation.py \
                       modules/avm/application/valuation.py \
                       modules/avm/infrastructure/repositories.py \
                       modules/avm/workers/valuation_worker.py \
                       apps/api/app/routes/avm.py \
                       shared/infrastructure/persistence/repositories.py \
                       tests/integration/test_avm_valuation.py
# All checks passed!

python3 -m pytest tests/integration/test_durable_repository_wiring.py -q
# 8 passed
```

## New regression coverage

- AVM report emits `income`, `asset`, `market`, and `blended` lenses and keeps
  reserve/asking separate from fair P50.
- Finance approval cannot run before `REVIEW_REQUIRED`, cannot self-approve,
  requires a reason, and records the correlation id.
- DataRoom build is rejected before approval; export is rejected before build.
- DataRoom responses expose checklist completeness and export audit.
- Durable SQLite restart test creates a case, writes two report versions,
  approves the latest version, builds and exports the DataRoom, reopens the app
  on the same DB, and verifies case status, report history, DataRoom export
  audit, and AVM audit events survive restart.

## Caveats

- Playwright product E2E was not run in this worker turn; the focused API and
  durable integration coverage above exercises the changed AVM loop directly.
