# ODP-ENG-OPENAPI-CLIENT-001 — Implementation Record

Task: Close OpenAPI typing and generated client drift
Owner: Antigravity4 · Reviewer: Codex
Phase: P1 Engineering

## Summary & Scope

This task provides completion evidence, contract documentation, drift verification, and comment consistency fixes for the OpenAPI contract generation and client surface of the ODay Plus platform.

## Architectural Architecture & Provenance Boundaries

1. **Generated Surfaces (`packages/openapi-client/src/generated/`)**
   - **Schema Export (`packages/openapi-client/openapi.json`)**: Exported deterministically from live FastAPI application (`apps.api.oday_api.main.create_app`) via `scripts/openapi/export_openapi.py`.
   - **TypeScript Emitter (`packages/openapi-client/src/generated/types.ts`)**: Emitter (`scripts/openapi/generate_client.py`) parses `openapi.json` to generate request DTOs, `ErrorEnvelope`, `ErrorResponse`, `Page` envelopes, and `API_PATHS` versioned path maps.
   - **Assisted Intake OpenAPI Client (`packages/openapi-client/src/generated/assisted_listing_intake.ts`)**: Generated via `scripts/generate_assisted_listing_intake_client.py`.

2. **Hand-Written DTO Boundary (`packages/openapi-client/src/index.ts`)**
   - **Success Response DTOs**: Most backend route handlers are annotated `-> dict[str, Any]`, causing FastAPI to emit `additionalProperties: true` without detailed response schemas in `openapi.json`. Consequently, success response DTOs are hand-written in `packages/openapi-client/src/index.ts`.
   - **Narrowings**: A subset of request DTOs requiring stricter runtime rules than default Pydantic schemas (e.g. `riskAcknowledged`) are narrowed in `src/index.ts` and validated via `AssertAssignable` type checks.
   - **Comment Correction**: Inaccuracies in comments within `packages/openapi-client/src/index.ts` and `scripts/openapi/generate_client.py` referencing a nonexistent `src/handwritten.ts` / `./handwritten` quarantine file have been corrected to accurately cite `src/index.ts`.

3. **CI Gate & Drift Detection (`scripts/openapi/check_drift.py`)**
   - **Check 1: Artifact Freshness**: Validates `openapi.json` against live FastAPI export.
   - **Check 2: Client Freshness**: Validates `src/generated/types.ts` against `openapi.json`.
   - **Check 3: Breaking Change Classifier**: Enforces non-breaking API evolution against `origin/dev` base unless approved in `scripts/openapi/approved_breaking_changes.json`.

4. **Error Contract & Boundary Types**
   - `ErrorEnvelope` (`code`, `message`, `next_action`, `occurred_at`, `details`, `correlation_id`) integrated with Starlette exception handling.
   - `OdpApiError` wraps API error responses with typed envelope extraction.

## Acceptance Verification Summary

| Acceptance Criterion | Status | Evidence |
| --- | --- | --- |
| Generated artifacts are reproducible | PASS | `scripts/openapi/export_openapi.py --check` confirms 0 diff in working tree |
| Affected success and error paths are typed | PASS | Generated request/error/path contracts and hand-written success DTOs in `src/index.ts` provide full client typing |
| Drift check fails on mismatch | PASS | Asserted by 17 contract tests in `tests/contract/test_openapi_artifact_and_client.py` (including mismatch exit code 1) |
| Affected call sites use generated contract | PASS | `@oday-plus/openapi-client` (re-exporting generated surfaces and hand-written DTOs) is consumed across web console components |
| Focused type build and contract evidence delivered | PASS | `tsc --noEmit` across package and web app and `check_drift.py` pass with zero errors |

