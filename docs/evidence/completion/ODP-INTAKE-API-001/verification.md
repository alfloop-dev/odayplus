# ODP-INTAKE-API-001 verification

Verified on 2026-07-20:

- `uv run python3 scripts/build_validate_assisted_listing_intake_openapi.py` — PASS, effective version 1.1.3 with all five overlays applied.
- `uv run pytest tests/contract/test_operator_assisted_listing_api.py tests/contract/test_assisted_listing_openapi.py tests/contract/test_assisted_listing_v1_runtime.py tests/contract/test_assisted_listing_operations.py -q` — PASS (all 25 + 4 + 9 tests passed).
- `npm run typecheck --workspace=@oday-plus/openapi-client` — PASS (TypeScript compiles successfully).
- `git diff --check origin/dev` — PASS (no trailing whitespace warnings).

The generated artifact is `packages/schemas/assisted_listing_intake/openapi-effective.json`.
The generated client namespace is exported as `AssistedListingIntakeV1`.
