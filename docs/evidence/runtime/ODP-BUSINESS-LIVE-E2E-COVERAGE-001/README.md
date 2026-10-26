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

## Fourth-review repairs (R18–R19)

- R18: the canonical NetPlan decide producer returns a root ApprovalRecord,
  not a scenario envelope. Capture root approval_id and compare that single POST
  record with the same newly durable GET approval. Positive regression executes
  the actual route `_run` producer, actual offline NetPlanService/solver/authority
  receipt and ApprovalRecord.to_dict; no hand-authored response shape stands in
  for that producer. The authorization/BFF shell of this regression is still
  explicitly offline, not evidence of live authorization or business acceptance.
- R19: capture this governance decision ID; bind its new durable identity,
  approve/return/reject terminal status, canonical finalDecision, reason and
  scoped authenticated actor UUID, plus the corresponding fresh audit's
  actor/action/entity/reason/correlation. Wrong status/decision replacement and
  mismatched audits fail; the sealed receipt verifier checks the same fields.
  Actual offline GovernanceService through its real local HTTP router verifies
  approve/return/reject and the canonical empty-approve-reason fallback. Foreign
  actorName overrides block preflight. No governance access policy is changed.
- Independent review confirmed R16 franchise and R17 transfer contracts; they
  are retained unchanged in this repair. Required CI and a new independent
  review must still approve the exact new head before merging or closing.

## Fifth-review repairs (R20–R21)

- R20 dependency: canonical GET scenario previously exposed no read-only way to
  establish the existing disclosure acknowledgement prerequisite. The owned
  repair extends that existing GET only, via NetPlanService's read-only
  `inspect_approval_disclosure`: reuse exactly the current decision's
  policy/solve/selected-action/acknowledgement enforcement, with no solver,
  authority-verifier callback, save, audit, transition or new endpoint. It
  neither creates an acknowledgement nor grants approval. Missing current solve,
  unresolved/blocking policy or absent/mismatched acknowledgement fails closed.
  A deployment lacking this field also blocks before any write/trigger.
- Runner preflight requires that server-owned proof before `writes_armed`;
  re-solve and durable approval must retain the same problem/action/policy/ack
  binding. Missing real-service acknowledgement is covered with a zero-ledger
  regression; local GET producer tests forbid all mutator/solver/verifier
  callbacks and verify repository storage remains unchanged for both success
  and missing/invalid prerequisites. Actual preparation of an acknowledged draft
  and deployment remain external live prerequisites, never fixture waivers.
- R21: require durable scenario `status=approved`, in addition to this new
  authentic approval. Runtime and independently verified sealed receipts reject
  a retained approval paired with pending_approval terminal state, or missing
  prerequisite/incorrect problem/policy/ack binding.
- R18/R19 are independently confirmed at 5f2736c4 (full CI37803702716 passed),
  not evidence that this new head or actual live acceptance has passed review.

## Sixth-review repair (R22)

- Separate volatile GET observation time from stable business/disclosure identity.
  Exclude only checked_at from the identity's prerequisite object; retain every
  policy/problem/primary-action/ack/readiness field. Capture the actual observed
  before/after timestamps separately and require them in receipt verification.
- The real NetPlan positive regression now invokes the actual consecutive GET
  producer on every scoped read: checked_at genuinely changes, the correctly
  denied role leaves stable business state unchanged, and the positive root
  approval/durable readback/independent receipt still succeeds. The regression's
  BFF/auth/solve shell remains explicitly offline. A policy change during denial
  still fails; missing observation metadata in resealed receipts also fails.
- R20/R21 and full CI37811102073 were independently examined at 00742c9f;
  that previous head was rejected for R22, not approved for merge.

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
