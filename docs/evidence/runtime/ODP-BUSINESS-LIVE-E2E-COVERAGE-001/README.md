# PR #1431 engineering review handoff

This is **offline engineering evidence**, not six-journey live acceptance.
The actual live run remains ODP-BUSINESS-LIVE-E2E-ACCEPTANCE-001. No cloud
business rows/accounts, source activation, model training/promotion, deployment,
IAM/egress or legal/governance holds are changed by this repair.

## Second-review repairs (R9–R14)

- R9: admission uses the new local `/api/v1/platform/release-identity` metadata
  endpoint, never `/readiness`; a real ASGI regression makes provider probes
  fatal and checks refusal with zero business writes/triggers.
- R10: the actual authenticated BFF uses its normal business upstream resolver
  and reports both API and Web SHA/profile/manifest digest. A differing Web
  destination blocks before business reads/writes. Server-owned missing digest
  is blocked, not filled from caller settings or a guessed release.
- R11: each PriceOps action matches the canonical route's exact audit event;
  offline regressions execute compiled real route handlers, not a fabricated
  `priceops.plan_submitted` event.
- R12: use the existing operator NetPlan solve, not a new solve API. Preflight
  requires a scoped store and uniquely bound draft canonical scenario. Bind the
  returned projection timestamp to the current canonical solve, then read the
  actual browser's disclosure and timestamp before submit/approve. The UI only
  opens a card and reads disclosures, never selects a scenario. A real local
  HTTP/CP-SAT regression proves projection creation and canonical binding.
- R13: intake decisions use fresh decision/audit and target-listing outcomes,
  including READY→READY without version increments. The minimal producer repair
  preserves intake tenantId on create. A real local SQLite service/repository
  restart regression proves the created row survives tenant-filtered readback.
- R14: launch/close local Chromium before arming writes. Missing executable or
  dependencies block before NetPlan solve. No target-page/provider calls are
  needed for this tool preflight.
- Scope documentation now specifies v2 authoritative `account_ids`; OpenAPI
  and generated path inventory include the side-effect-free identity endpoint.
- Hosted CI run 37786698311 found one API-versioning failure (6802 other tests
  passed): the identity route was registered inline without its compatibility
  alias. It now uses the existing `mount_versioned` platform router, preserving
  the same canonical URL and handler. A real-app offline regression proves
  paired routes, identical metadata, alias deprecation headers, schema exclusion
  of the alias, and zero provider dispatch. The existing API-versioning suite
  is an additional focused check; no contract assertion is weakened.

## Third-review repairs (R15–R17)

- R15: preflight requires distinct authoritative account UUIDs and a canonical
  NetPlanDecisionPayload with `actor_id=account_ids.approver` and authored reason.
  Bind the newly captured approval ID, actor/principal/authority receipt, reason,
  authenticity and current disclosure to POST and durable GET. Missing/spoofed
  actor, primary-as-approver, unknown `comment` or different receipt blocks
  before writes. The canonical NetPlan API's access policy is not modified.
- R16: capture the actual POST report ID; require exactly one new durable report
  with matching store, account, authored category/message, received status,
  created time and write correlation. A concurrent unrelated report cannot make
  a lost write pass. An offline real ShellService/repository restart checks the
  producer/consumer contract in addition to malformed/lost-report counterexamples.
- R17: StoreOps expected outcomes are action-specific; transfer binds actual
  ownerRoleId/ownerName rather than nonexistent history or a status change.
  Noop/inadmissible transitions block preflight. Real offline StoreOpsService
  transfer regressions prove unchanged status/history but changed durable owner;
  wrong-owner/concurrent outcomes fail. Timestamps are not business outcomes.
- Receipt verification requires the newly captured report/approval identities
  and scoped durable fields even after a receipt is resealed. Authorized report
  content/approval reason are digest-bound without adding credential material.

## Verification and limits

Final checks must run on the committed exact head and emit canonical
verification receipts (head SHA, exact command, exit, duration, selection).
The primary selection is:

```sh
NODE_ENV=test uv run --frozen --python 3.12 pytest tests/e2e/test_live_e2e_gate.py tests/e2e/test_live_business_journeys.py -q
NODE_ENV=test npm run test --workspace=@oday-plus/web -- src/app/__tests__/netplanDiagnosticsUx.test.tsx
NODE_ENV=test npm run typecheck --workspace=@oday-plus/web
NODE_ENV=test uv run --frozen --python 3.12 pytest tests/contract/test_api_versioning.py -q
```

Also verify OpenAPI/client drift, boundary inventory, scoped Ruff and
`git diff --check`. `NODE_ENV=test` explicitly selects offline local contract
composition; the host's production default otherwise removes local-only routes
from generated schemas. It does not change deployed runtime admission.

Independent Codex2 exact-head review and required CI are still necessary.
Tests and code merge cannot prove live business data/models/roles/admission.
Current dev-admin admission is not upgraded. Deployment workflow wiring for a
server-owned admitted manifest digest is outside this repair's owned scope;
without actual immutable binding, live journeys must remain BLOCKED.
