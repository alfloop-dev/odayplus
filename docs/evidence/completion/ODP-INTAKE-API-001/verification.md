# ODP-INTAKE-API-001 verification

Verified on 2026-07-18:

- `uv run --with jsonpath-ng --with openapi-spec-validator python scripts/build_validate_assisted_listing_intake_openapi.py --json` — PASS, effective version 1.1.3 and all five overlays applied.
- `uv run --with jsonpath-ng --with openapi-spec-validator pytest tests/contract/test_operator_assisted_listing_api.py tests/contract/test_assisted_listing_openapi.py tests/contract/test_assisted_listing_v1_runtime.py -q` — PASS.
- `npm exec --yes --package=typescript -- tsc --noEmit -p packages/openapi-client/tsconfig.json` — PASS. The requested workspace command cannot locate `tsc` because this package declares no TypeScript devDependency; the equivalent pinned project typecheck passes.
- `git diff --check origin/dev...HEAD` and `git diff --check` — PASS.

The generated artifact is `packages/schemas/assisted_listing_intake/openapi-effective.json`.
The generated client namespace is exported as `AssistedListingIntakeV1`.
