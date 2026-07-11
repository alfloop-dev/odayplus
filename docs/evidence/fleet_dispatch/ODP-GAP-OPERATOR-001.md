# Fleet Execution Brief: ODP-GAP-OPERATOR-001

- Parent: ODP-GAP-OPERATOR-001
- Status: done
- Scope boundary: operator
- Owner lane: gcp / ci-cd / runtime-packaging / worker-ops
- Reviewer lane: governance-review
- Suggested branch: `task/ODP-GAP-OPERATOR-001`

## Objective

Route the React `OperatorConsole` as the product `/operator` surface and bind operator workflows to API-backed adapters instead of the static design iframe.

## Implementation Details

- **FastAPI Backend Router**: Added a dedicated FastAPI operator router at [operator.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-gap-operator-001/apps/api/app/routes/operator.py) and registered it under `/api/v1/operator` in [main.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-gap-operator-001/apps/api/oday_api/main.py).
- **API Client Bindings**: Extended the TypeScript OpenAPI client in `packages/openapi-client/src/index.ts` to support fetching operator bootstrap data (`getOperatorBootstrap`), submitting issue workflow transitions (`transitionOperatorIssue`), and deciding approvals (`decideOperatorApproval`).
- **Next.js Rewrite Routing**: Updated `next.config.mjs` to define a rewrite pattern proxying `/api/v1/:path*` to the FastAPI backend API service.
- **Client Integration**: Updated `OperatorConsole.tsx` to load live data on mount using `/api/v1/operator/bootstrap` and wired all transition/decision dialog events to fetch POST endpoints (submitting proper headers like `Idempotency-Key` and `X-Correlation-Id`).
- **Fail-closed posture**:
  - The frontend console defaults to fail-closed state: camera telemetry is strictly locked (`Locked`) under privacy policies until the operator explicitly submits a valid "purpose" statement to the API via `confirmOperatorEvidencePurpose` `/api/v1/operator/evidence/:id/purpose`.
  - In the absence of external API server inputs (e.g. server outage), request failures are caught gracefully to prevent UI crashes, but operations fall back to a local-only demo mode or fail-closed state depending on the context.

## Verification Evidence

- Run typecheck and lint checking:
  ```bash
  npm run typecheck --workspace=@oday-plus/web
  npm run lint
  ```
  Result: Clean compile and lint with no errors.
- Driven Playwright E2E spec:
  ```bash
  ODP_OPERATOR_PRODUCT_GATE=1 npx playwright test tests/e2e/e2e-operator-console.spec.ts
  ```
  Result: All 4 tests PASS. The productization gate check successfully verifies that no static design iframe is used and that all bootstrap API reads and workflow writes (carrying idempotency-key and x-correlation-id headers) hit the correct endpoints.

## Handoff Artifacts

- API Routes definition: [operator.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-gap-operator-001/apps/api/app/routes/operator.py)
- Web console client: [OperatorConsole.tsx](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-gap-operator-001/apps/web/features/operator/OperatorConsole.tsx)
- E2E Tests suite: [e2e-operator-console.spec.ts](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-gap-operator-001/tests/e2e/e2e-operator-console.spec.ts)
