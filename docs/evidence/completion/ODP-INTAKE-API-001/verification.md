# ODP-INTAKE-API-001 verification

Verified on 2026-07-20 at task anchors `e54bad56` and `17f8ad46`:

- `uv run python scripts/build_validate_assisted_listing_intake_openapi.py` — PASS, effective version 1.1.3 with all five overlays applied in manifest order.
- `uv run python scripts/generate_assisted_listing_intake_client.py` — PASS; committed effective artifact and generated client remained unchanged.
- `uv run pytest tests/contract/test_operator_assisted_listing_api.py tests/contract/test_assisted_listing_openapi.py -q --disable-warnings` — PASS (25 tests).
- `uv run pytest tests/contract/test_assisted_listing_operations.py tests/contract/test_assisted_listing_v1_runtime.py -q --disable-warnings` — PASS (15 tests).
- `npm run typecheck --workspace=@oday-plus/openapi-client` — PASS (TypeScript compiles successfully).
- `uv run ruff check --select F,I apps/api/app/routes/listings.py shared/api/errors.py tests/contract/test_assisted_listing_openapi.py tests/contract/test_assisted_listing_operations.py tests/contract/test_assisted_listing_v1_runtime.py` — PASS.
- `git diff --check origin/dev` — PASS (no trailing whitespace warnings).

The generated artifact is `packages/schemas/assisted_listing_intake/openapi-effective.json`.
The generated client namespace is exported as `AssistedListingIntakeV1`.
