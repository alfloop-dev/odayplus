# ODP-OC-R5-005 Verification Evidence

## Commands Run

### Review Rejection Fix (2026-07-15)

- `npm ci` - passed; restored workspace dependencies from `package-lock.json`.
- `npm run typecheck --workspace=@oday-plus/openapi-client` - passed.
- `npm run typecheck --workspace=@oday-plus/web` - passed.
- `npm run build --workspace=@oday-plus/web` - passed; emitted existing autoprefixer CSS compatibility warnings in `designAligned.module.css`, `governance.module.css`, and `networkFindAreas.module.css`.
- `npx playwright test tests/e2e/operator-network-assisted-intake.spec.ts --project=chromium` - passed, 15 tests. Added regression coverage for retry-stable `Idempotency-Key` headers on browser-driven `correct` and `decide` writes.
- `uv run pytest tests/security/test_assisted_listing_intake_security.py -q` - passed, 24 tests.
- `uv run pytest tests/contract/test_operator_assisted_listing_api.py -q` - passed, 20 tests.
- `uv run pytest tests/security tests/contract -q` - passed.
- `python3 scripts/e2e/check_product_release_gate.py` - `Product release gate static checks passed.`

### Original Security Gate Verification

- `uv run pytest tests/security/test_assisted_listing_intake_security.py -q` - passed.
- `uv run pytest tests/contract/test_operator_assisted_listing_api.py -q` - passed.
- `uv run pytest tests/contract/test_operator_network_listings_api.py -q` - passed.
- `uv run pytest tests/integration/test_assisted_listing_intake_persistence.py -q` - passed.
- `uv run pytest tests/security tests/contract -q` - passed.
- `python3 scripts/e2e/check_product_release_gate.py` - `Product release gate static checks passed.`
- `uv run ruff check modules/external_data/security/assisted_listing_retrieval.py modules/external_data/application/assisted_intake.py modules/opsboard/application/network_listings.py apps/api/app/routes/operator_modules/network_listings.py tests/security/test_assisted_listing_intake_security.py tests/contract/test_operator_assisted_listing_api.py tests/contract/test_operator_network_listings_api.py tests/integration/test_assisted_listing_intake_persistence.py` - passed.

## Residual Risk

- The current product path remains deterministic fixture replay. The new live-retrieval gate is ready for a future approved adapter, but no live provider endpoint or credential was configured or exercised in this task.
