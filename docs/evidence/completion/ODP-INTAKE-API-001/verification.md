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

## Changes-requested remediation

Reviewer follow-up was implemented at runtime anchor `6748b17e` and live
artifact anchor `ecc33a29`:

- same-tenant Expansion staff no longer inherit ownership of unassigned
  intakes submitted by another subject, including the Operator compatibility
  helper;
- replay cache values are deep-copied, so claiming an assignment cannot mutate
  the original assignment response or its ETag;
- cursor pagination is configured through
  `ODP_INTAKE_CURSOR_SIGNING_KEY`, uses the approved 24-hour lifetime and a
  deterministic sort tuple/resource-ID keyset, and excludes post-snapshot
  inserts instead of advancing by offset;
- `packages/openapi-client/openapi.json` and generated shared types were rebuilt
  from the live FastAPI app, removing the PR #335 artifact drift.

Regression coverage verifies both ownership helpers, assignment replay after
claim, configured HMAC signing, absence of an offset in the cursor, stable
pagination after an intervening insert, and artifact/client byte parity.

## Re-review verification

Verified on 2026-07-20 after `ecc33a29`:

- `uv run python scripts/build_validate_assisted_listing_intake_openapi.py` — PASS, effective OpenAPI 1.1.3.
- `uv run pytest tests/contract/test_operator_assisted_listing_api.py tests/contract/test_assisted_listing_openapi.py -q` — PASS.
- `uv run pytest tests/contract/test_assisted_listing_operations.py tests/contract/test_assisted_listing_v1_runtime.py tests/contract/test_operator_assisted_listing_api.py tests/contract/test_assisted_listing_openapi.py -q` — PASS (44 tests).
- `uv run pytest tests/contract/test_openapi_artifact_and_client.py -q` — PASS (17 tests).
- `uv run python scripts/openapi/check_drift.py --base-ref origin/dev` — PASS; live artifact and generated client are fresh, with 27 additive and zero breaking changes.
- `npm run typecheck --workspace=@oday-plus/openapi-client` — PASS.
- `uv run ruff check tests modules apps shared models solver pipelines infra` — PASS.
- `uv run pytest -m "not requires_live_env" tests modules apps shared models` — PASS (1096 passed, 19 deselected).
- `make security` — PASS (npm/pip audits found no known vulnerabilities; 189 passed, 6 skipped).
- `make node-check` — PASS when run serially after pytest; workspace lint/typechecks and the Next.js production build completed successfully.
- `git diff --check origin/dev...HEAD` — PASS before the evidence-only final commit.

## Second changes-requested remediation

Reviewer follow-up at `09502d45` was implemented at runtime anchor `335b1a5a`:

- every mutation whose target is identified by a path parameter now includes
  that resource ID in its replay identity; an HTTP regression applies the same
  `assignIntake` key and body to two intakes and verifies two independent
  assignment receipts and mutations;
- Expansion staff may not assign an intake they own to another subject, and the
  canonical error normalizer preserves `ASSIGNMENT_SCOPE_DENIED` instead of
  collapsing it to the generic `SCOPE_DENIED` substring;
- a transfer target may claim a `TRANSFERRED` assignment through
  `claimAssignment`; the assignment lifecycle regression now transfers, claims,
  and completes entirely through HTTP and no longer writes `CLAIMED` directly
  into the in-memory store;
- the older If-Match smoke test now uses an explicit Expansion manager for its
  cross-user assignment, leaving staff authorization to the dedicated denial
  regression.

Verified on 2026-07-20 after `335b1a5a`:

- `python scripts/build_validate_assisted_listing_intake_openapi.py` — the
  environment has no `python` binary; the equivalent repo runtime command below
  was used.
- `uv run python scripts/build_validate_assisted_listing_intake_openapi.py` —
  PASS, effective OpenAPI 1.1.3; no artifact drift.
- `uv run pytest tests/contract/test_assisted_listing_operations.py tests/contract/test_assisted_listing_v1_runtime.py tests/contract/test_operator_assisted_listing_api.py tests/contract/test_assisted_listing_openapi.py -q` —
  PASS (46 tests).
- `npm run typecheck --workspace=@oday-plus/openapi-client` — PASS.
- `uv run ruff check apps/api/app/routes/listings.py shared/api/errors.py tests/contract/test_assisted_listing_operations.py tests/contract/test_assisted_listing_v1_runtime.py` —
  PASS.
- `git diff --check origin/dev...HEAD` and `git diff --check` — PASS.
