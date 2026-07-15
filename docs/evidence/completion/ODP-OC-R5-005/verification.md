# ODP-OC-R5-005 Verification Evidence

## Commands Run

- `uv run pytest tests/security/test_assisted_listing_intake_security.py -q` - passed.
- `uv run pytest tests/contract/test_operator_assisted_listing_api.py -q` - passed.
- `uv run pytest tests/contract/test_operator_network_listings_api.py -q` - passed.
- `uv run pytest tests/integration/test_assisted_listing_intake_persistence.py -q` - passed.
- `uv run pytest tests/security tests/contract -q` - passed.
- `python3 scripts/e2e/check_product_release_gate.py` - `Product release gate static checks passed.`
- `uv run ruff check modules/external_data/security/assisted_listing_retrieval.py modules/external_data/application/assisted_intake.py modules/opsboard/application/network_listings.py apps/api/app/routes/operator_modules/network_listings.py tests/security/test_assisted_listing_intake_security.py tests/contract/test_operator_assisted_listing_api.py tests/contract/test_operator_network_listings_api.py tests/integration/test_assisted_listing_intake_persistence.py` - passed.

## Residual Risk

- The current product path remains deterministic fixture replay. The new live-retrieval gate is ready for a future approved adapter, but no live provider endpoint or credential was configured or exercised in this task.
