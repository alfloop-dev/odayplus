# ODP-ENG-OPENAPI-CLIENT-001 — Implementation Record

Task: Close OpenAPI typing and generated client drift
Owner: Antigravity · Reviewer: Antigravity2
Phase: P1 Engineering

## Summary & Scope

This task establishes and validates the OpenAPI contract generation, drift prevention gate, error contract typing, and call-site migration for the ODay Plus platform, operating without dependency on external OSS legal decisions.

## Architectural Architecture & Artifact Provenance

1. **Schema Export (`packages/openapi-client/openapi.json`)**
   - Exported deterministically from live FastAPI application (`apps.api.oday_api.main.create_app`).
   - Clears release SHA environment variables prior to export to guarantee byte-level reproducibility across developer workstations and CI runners.
   - Script: `scripts/openapi/export_openapi.py`

2. **TypeScript Client Emitter (`packages/openapi-client/src/generated/types.ts`)**
   - Emitter parses `openapi.json` components and paths to generate strict TypeScript interfaces and versioned `API_PATHS` maps.
   - Script: `scripts/openapi/generate_client.py`

3. **Assisted Intake OpenAPI Client (`packages/openapi-client/src/generated/assisted_listing_intake.ts`)**
   - Emitter parses effective OpenAPI specification to generate typed domain DTOs.
   - Script: `scripts/generate_assisted_listing_intake_client.py`

4. **CI Gate & Drift Detection (`scripts/openapi/check_drift.py`)**
   - **Check 1: Artifact Freshness** — Validates that checked-in `openapi.json` matches live FastAPI schema export.
   - **Check 2: Client Freshness** — Validates that generated `src/generated/types.ts` matches `openapi.json`.
   - **Check 3: Breaking Change Classifier** — Diff against `origin/dev` merge-base to enforce non-breaking API evolution unless explicitly authorized in `scripts/openapi/approved_breaking_changes.json`.

5. **Error Contract & Boundary Types**
   - `ErrorEnvelope` (`code`, `message`, `next_action`, `occurred_at`, `details`, `correlation_id`) integrated with Starlette exception handling.
   - `OdpApiError` wraps API error responses with typed envelope extraction.

## Acceptance Verification Summary

| Acceptance Criterion | Status | Evidence |
| --- | --- | --- |
| Generated artifacts are reproducible | PASS | Re-export execution confirmed 0 diff in working tree |
| Affected success and error paths are typed | PASS | `ErrorEnvelope`, `ApiError`, `OdpApiError`, and schema DTOs fully exported |
| Drift check fails on mismatch | PASS | Asserted by 17 tests in `tests/contract/test_openapi_artifact_and_client.py` |
| Affected call sites use generated contract | PASS | `@oday-plus/openapi-client` used across web console components |
| Focused type build and contract evidence delivered | PASS | `tsc --noEmit` and `check_drift.py` passed with zero errors |
