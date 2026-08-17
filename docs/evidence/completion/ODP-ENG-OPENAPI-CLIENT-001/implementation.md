# ODP-ENG-OPENAPI-CLIENT-001 — Implementation Record

Task: Close OpenAPI typing and generated client drift
Owner: Claude · Reviewer: Claude3
Phase: P1 Engineering

The task was helper-claimed by `Claude` on 2026-08-10; earlier revisions of this
record carried the superseded `Antigravity4` / `Codex` assignment.

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
   - **Negative Probes**: The acceptance sidecar found that the suite asserted the classifier's failure verdicts but never the gate's exit code, so a gate that had stopped failing would still have looked green. Five tests now point the real checks at deliberately stale inputs:
     - `test_client_check_cli_exits_non_zero_on_a_stale_generated_client` copies `generate_client.py` and the real artifact into a temporary repo root, removes one operation from the emitted client, and runs the CLI as a subprocess — exit `1`, `is stale` on stderr. The sandbox is what keeps the probe from leaving a modified generated file in the checked-in tree.
     - `test_client_check_cli_exits_zero_on_a_faithfully_generated_client` is the positive control in the same sandbox.
     - `test_client_check_cli_exits_non_zero_when_the_client_was_never_generated` covers the deleted-output path.
     - `test_artifact_check_exits_non_zero_when_the_artifact_no_longer_matches_the_app` points `export_openapi.py --check` at a path-less artifact.
     - `test_the_contract_gate_fails_the_build_when_one_stage_fails` asserts `check_drift.main` returns `1` when a single stage is stale, since it runs all stages before reporting.

4. **Error Contract & Boundary Types**
   - `ErrorEnvelope` (`code`, `message`, `next_action`, `occurred_at`, `details`, `correlation_id`) integrated with Starlette exception handling.
   - `OdpApiError` wraps API error responses with typed envelope extraction.

## Acceptance Verification Summary

| Acceptance Criterion | Status | Evidence |
| --- | --- | --- |
| Generated artifacts are reproducible | PASS | `scripts/openapi/export_openapi.py --check` and `generate_client.py --check` confirm 0 diff in the working tree; `test_artifact_export_is_deterministic` pins byte-stability across runs |
| Affected success and error paths are typed | PASS, with an explicit provenance boundary | Request, component, error, and `API_PATHS` surfaces are **generated**; `OdpApiError` is hand-written but typed by the generated `ErrorEnvelope`. Most success-response DTOs are typed but **hand-written** in `src/index.ts`, because the routes return `dict[str, Any]` and the artifact carries no response shape to generate from. Consumers are fully typed either way; only the provenance differs |
| Drift check fails on mismatch | PASS | 22 contract tests in `tests/contract/test_openapi_artifact_and_client.py`. 12 assert the classifier's breaking/additive verdicts; 5 are negative probes that observe the real non-zero exit, including a subprocess run of `generate_client.py --check` against deliberately stale output (see §3 above). Before this task the exit code itself was never asserted |
| Affected call sites use generated contract | PASS, scoped to package adoption | 38 TypeScript/TSX files under `apps/web` import `@oday-plus/openapi-client`, and `tsc --noEmit` on the web project passes against it. The demonstrated claim is adoption of the package contract — which re-exports the generated surfaces *and* owns the hand-written success DTOs — not that every DTO a call site touches is generated |
| Focused type build and contract evidence delivered | PASS | `tsc --noEmit` across package and web app and `check_drift.py` pass with zero errors; receipts in `verification.md` |

## Deferred, and why it is out of this task's scope

Generating the success-response DTOs requires declaring `response_model=` on
each route. That is not a mechanical change: `response_model` *filters* the
response to the declared fields, so an incomplete model silently drops data the
console already renders. It has to be done per route with its own tests, and is
tracked as a follow-up rather than folded in here. Until then, the
generated-versus-hand-written boundary above is the honest description of the
contract, and the drift gate guards the generated half of it.
