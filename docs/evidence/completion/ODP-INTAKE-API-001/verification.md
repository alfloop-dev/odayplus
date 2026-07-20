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

## Third changes-requested remediation

Reviewer follow-up at `812ffefd` was implemented at runtime anchor `713f4481`:

- staff ownership now fails closed when either or both owner/submitter fields
  are unassigned; same-tenant unrelated staff receive
  `403 OWNERSHIP_REQUIRED` from both intake detail and cancel endpoints;
- canonical v1 `fields[]` entries are masked from their declared
  classification rather than from legacy camelCase field names, with all four
  value slots removed and `FIELD_MASKED` metadata retained;
- a staff transfer lost-response retry resolves the immutable actor/tenant/
  operation/resource-scoped receipt before mutable current-owner authorization,
  so the exact original receipt and ETag replay after ownership changes;
- `assignIntake` permits only one non-completed assignment per intake and emits
  the declared `409 OWNER_CONFLICT` under a fresh matching ETag; the v1 error
  normalizer preserves that specific code.

Verified on 2026-07-20 after `713f4481`:

- `python scripts/build_validate_assisted_listing_intake_openapi.py` — the
  environment has no `python` binary; `python3` also lacks the project
  dependencies, so the repo-managed command below was used.
- `uv run python scripts/build_validate_assisted_listing_intake_openapi.py` —
  PASS, effective OpenAPI 1.1.3.
- `uv run python scripts/generate_assisted_listing_intake_client.py` — PASS;
  effective artifact and generated client remained unchanged.
- `uv run pytest tests/contract/test_assisted_listing_operations.py -q -k 'unassigned_intake_is_not_owned or restricted_v1_fields or staff_transfer_lost_response or rejects_a_second_active_assignment'` —
  PASS (4 targeted regressions).
- `uv run pytest tests/contract/test_assisted_listing_operations.py -q` — PASS
  (19 tests).
- `uv run pytest tests/contract/test_operator_assisted_listing_api.py tests/contract/test_assisted_listing_openapi.py -q` —
  PASS (26 tests).
- `uv run pytest tests/contract/test_assisted_listing_v1_runtime.py -q` — PASS
  (4 tests); the three assisted-intake suites total 49 passing tests.
- `npm run typecheck --workspace=@oday-plus/openapi-client` — PASS.
- `uv run ruff check apps/api/app/routes/listings.py modules/listing/application/intake_authorization.py shared/api/errors.py tests/contract/test_assisted_listing_operations.py` —
  PASS.
- `git diff --check origin/dev...HEAD`, `git diff --check`, and task-owned
  working-tree inspection — PASS.

## Supply-chain artifact remediation

Reviewer follow-up at `8e17ac83` identified that the retained OpenAPI
development dependencies in `pyproject.toml` and `uv.lock` made the committed
SBOM stale. The SBOM was regenerated from the current `package-lock.json` and
`uv.lock`; it now catalogs 516 components and restores the fail-closed lockfile
parity check without changing the assisted-intake runtime or API contract.

Verified on 2026-07-20 after regeneration:

- `python3 scripts/security/generate_sbom.py` — PASS (516 components).
- `uv run pytest tests/security/test_supply_chain_security_gate.py::test_sbom_and_provenance_present_and_valid -q` —
  PASS (committed component set matches both current lockfiles).
- `uv run python scripts/build_validate_assisted_listing_intake_openapi.py` and
  `uv run python scripts/generate_assisted_listing_intake_client.py` — PASS,
  effective OpenAPI 1.1.3 with no generated artifact drift.
- `uv run pytest tests/contract/test_assisted_listing_operations.py tests/contract/test_assisted_listing_v1_runtime.py tests/contract/test_operator_assisted_listing_api.py tests/contract/test_assisted_listing_openapi.py -q` —
  PASS (49 tests).
- `npm run typecheck --workspace=@oday-plus/openapi-client` — PASS.
- `uv run ruff check apps/api/app/routes/listings.py modules/listing/application/intake_authorization.py shared/api/errors.py tests/contract/test_assisted_listing_operations.py` —
  PASS.

## Fourth changes-requested remediation

Reviewer follow-up at `d39500fe` was implemented at authorization/runtime
anchors `f02d1cd6` and `71873ce6`, then generated-artifact anchor `3806ed73`:

- the v1 and Operator compatibility handlers no longer consume a raw
  `X-Operator-Role`; local and live JWT regressions verify that the header
  cannot grant `expansion-manager` to a principal with no platform role;
- brand, region, assigned-area, and HeatZone scope axes are represented in the
  canonical principal, parsed from local headers and verified claims, and
  enforced against nested or flat intake resources on create/read/mutate/list;
- identity-affecting provider/address/rent/area corrections and quarantine
  release now require an independent second actor, with explicit self-review
  denial and no first-actor application/release;
- `claimAssignment` exact replay precedes mutable ownership checks, while
  `retryJob` receives normalized ownership fields so Expansion staff can retry
  their own failed intake;
- `decideMatchCase`, promotion/identity GETs, and all other declared mutation
  receipts expose their required ETags; runtime drift tests now require exact
  per-operation status sets, response headers, and schemas rather than schema
  subsets;
- the live FastAPI artifact and generated shared TypeScript types were refreshed
  after the exact response declarations changed.

Verified on 2026-07-20 after `3806ed73`:

- `uv run python scripts/build_validate_assisted_listing_intake_openapi.py --json` —
  PASS, effective OpenAPI 1.1.3 with all five overlays in manifest order.
- `uv run python scripts/generate_assisted_listing_intake_client.py` — PASS;
  the effective 1.1.3 artifact and its dedicated generated client remained
  unchanged.
- `uv run pytest -q tests/contract/test_assisted_listing_operations.py tests/contract/test_assisted_listing_v1_runtime.py tests/contract/test_operator_assisted_listing_api.py tests/contract/test_assisted_listing_openapi.py tests/security/test_api_auth_wiring.py tests/security/test_assisted_listing_intake_authorization_matrix.py tests/security/test_opsboard_auth_boundary.py` —
  PASS (113 tests: 56 assisted-intake contract tests and 57 auth/security tests).
- `uv run python scripts/openapi/check_drift.py --base-ref origin/dev` — PASS;
  the live artifact/client are fresh with 27 additive and zero breaking changes.
- `uv run pytest -q tests/contract/test_openapi_artifact_and_client.py` — PASS
  (17 live-artifact and generated-client checks).
- `npm run typecheck --workspace=@oday-plus/openapi-client` — PASS.
- `uv run ruff check apps/api/app/routes/listings.py apps/api/app/routes/operator_modules/network_listings.py apps/api/oday_api/main.py apps/api/oday_api/security/dependencies.py modules/listing/application/intake_authorization.py modules/opsboard/auth/claims.py shared/api/versioning.py shared/auth/identity.py tests/contract/test_assisted_listing_openapi.py tests/contract/test_assisted_listing_operations.py tests/contract/test_assisted_listing_v1_runtime.py tests/security/test_opsboard_auth_boundary.py` —
  PASS.
- `git diff --check origin/dev...HEAD` and `git diff --check` — PASS before
  this evidence-only commit.

## Fifth changes-requested remediation

Reviewer follow-up at `25b10c48` was implemented at trusted-role/retry anchor
`1fdf0cdf`, followed by the generated contract and exhaustive negative-parity
update:

- `expansion_user` now maps only to `expansion-staff`; a verified
  `site_reviewer` or executive claim is required for `expansion-manager`.
  Local-header and live-JWT regressions reproduce the manager-only Operator
  listing merge with an Expansion-user principal and forged
  `X-Operator-Role`, and require HTTP 403;
- `listIntakes` malformed query values return the declared 400, malformed UUID
  resource identifiers on GET return the declared 404, and mutation validation
  (including malformed `If-Match`) returns 422. The effective overlay declares
  the previously omitted `assignIntake` and `createSavedView` validation errors;
- a 27-case matrix executes one negative request for every approved operation,
  asserts that the observed status is declared, and validates the actual JSON
  body against that operation's effective error schema;
- `retryJob` resolves an exact actor/tenant/operation/resource replay before
  mutable intake ownership checks. New retry commands require a linked intake
  authorization resource and fail closed with `409 DEPENDENCY_CONFLICT` when
  the job is orphaned.

Verified on 2026-07-20 after the fifth remediation:

- `uv run python scripts/build_validate_assisted_listing_intake_openapi.py --json` —
  PASS, effective OpenAPI 1.1.3 with all five overlays in manifest order.
- `uv run python scripts/generate_assisted_listing_intake_client.py` — PASS;
  the committed effective artifact was regenerated from the five-overlay bundle.
- `uv run pytest -q tests/contract/test_assisted_listing_operations.py tests/contract/test_assisted_listing_v1_runtime.py tests/contract/test_operator_assisted_listing_api.py tests/contract/test_assisted_listing_openapi.py tests/security/test_api_auth_wiring.py tests/security/test_assisted_listing_intake_authorization_matrix.py tests/security/test_opsboard_auth_boundary.py` —
  PASS (142 tests, including all 27 executed negative operation cases).
- `uv run pytest -q tests/contract/test_api_error_envelope.py tests/contract/test_operator_network_listings_api.py tests/contract/test_operator_network_rebalance_api.py tests/contract/test_operator_network_review_api.py tests/contract/test_operator_network_scoring_api.py tests/contract/test_operator_shell_api.py tests/security/test_assisted_listing_intake_security.py tests/integration/test_operator_shell_persistence.py` —
  PASS for the affected Operator role-mapping surface.
- `uv run pytest -q tests/contract/test_openapi_artifact_and_client.py` — PASS
  (17 live-artifact and generated-client checks).
- `uv run python scripts/openapi/check_drift.py --base-ref origin/dev` — PASS;
  live artifact and generated client are fresh with 27 additive and zero
  breaking changes.
- `npm run typecheck --workspace=@oday-plus/openapi-client` — PASS.
- `uv run ruff check apps/api/app/routes/listings.py apps/api/oday_api/security/dependencies.py modules/opsboard/application/operator_state.py shared/api/errors.py tests/contract/test_assisted_listing_openapi.py tests/contract/test_assisted_listing_operations.py tests/contract/test_assisted_listing_v1_runtime.py tests/contract/test_operator_assisted_listing_api.py tests/security/test_assisted_listing_intake_authorization_matrix.py` — PASS.
- `git diff --check origin/dev...HEAD` and `git diff --check` — PASS before the
  final remediation commit.
