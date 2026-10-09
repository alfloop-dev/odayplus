# Transfer / Pause resource-authority increment

Pi · reviewer Codex2 · ODP-UI-NETWORK-TABS-PARITY-001 · existing PR #1440

**Bounded increment, not full parity acceptance or review resubmission.**
These are resource-token/conflict repairs and genuine missing-authority browser
receipts. They are NOT successful Transfer/Pause API writes, backend persistence
proof, design-before-after modal pairs, accessibility certification, or independent
VDC approval. Those requirements remain open.

## Source / repaired defects

Resumed clean task head `73a189e5ebe774a934db9f088b7abadd73454a01` on the expected
branch. Read the live canonical brief/status and Codex2 findings; fetched dev
`2fe3ef933794d0bb293449b74dffe9602d53f767` (already an ancestor). The task's
operator Intake source was compared with origin/dev, not treated as repo truth
merely because it existed in the worktree.

- Anchor `356abaeab220`: Transfer/Pause accept an explicit resource version.
  Previously both showed `record.version || 1`, i.e. the unrelated Intake token,
  even though writes used resource-specific If-Match. Missing IDs/tokens now show
  UNAVAILABLE and disable direct modal submission; no fallback version 1.
- Anchor `7f16fca8c36c`: successful conflict rereads supersede cached assignment
  and SLA receipts; failed rereads retain the unresolved 409, draft, and retry
  path. Both modal submissions stay disabled until conflict resolution. The
  original actor, reason and editable resume-time/risk-ack behavior remain.
- Version helpers reject receipts whose resource ID differs from the selected
  record. Unsafe JavaScript integers are rejected, not rounded into concurrency
  tokens. The final harness refinement only scrolls the actual authority block
  into the viewport before screenshot; `source.sha256` binds all tested source.

Mounted container regressions exercise cached claim/resume receipt v8, concurrent
resource v9, 409, refresh and resubmit using v9 rather than Intake v71/v72 or the
stale receipt. Transfer also proves a failed 503 reread retains v8, disables
submission and allows another refresh. Draft handoff/reason/resume time and risk
ack survive successful refresh. Separate tests cover mismatched receipt IDs,
missing/invalid resource tokens and display-vs-request consistency.

**These mounted tests stub fetch.** Their controlled responses prove UI request
binding and state behavior, not real Assignment/SLA storage. The existing helper
also now answers the client's actual detail URL
`/api/v1/operator/network-listings/intake/:id`, not the unrelated `/intakes/:id`.
Early diagnostics uncovered an incorrect test-ID selector and that wrong detail
URL; both failed logs/exits remain in `logs/`, not relabelled as passes.

## Genuine local API boundary and unresolved integration

`operator-assignment-authority-parity.spec.ts` uses real browser clicks, local
FastAPI and same-origin BFF: reset, submit the declared synthetic source URL,
GET the actual Operator Intake, then navigate Transfer/Pause deep links. No route
interception, resource injection, forced clicks or mocked successful mutation.
The fresh record is `IN-3001`, with a display owner but **no assignmentId,
assignmentVersion, slaInstanceId or slaVersion**. `shots/actual-intake-*.json`
contains the actual response. A human/display owner is not an Assignment ID.

At both 1440x900 and 390x900 the controls/modal stay absent, the authority block
is visible and no Assignment/SLA POST occurs. `shots/` contains four unavailable
screenshots, horizontal document/summary geometry receipts, exact records and
empty-write lists. Owner inspected the four final images. These are current
negative-state captures, NOT repair pairs or modal acceptance; vertical
full-page density, keyboard and axe were not tested by this probe.

Canonical source contact points for the remaining implementation:
- `packages/openapi-client/src/index.ts`: `getIntake` reads Operator Intake;
  claim/transfer/pause/resume instead call the `/api/v1/assignments` and
  `/api/v1/sla-instances` resources. Resource versions are not declared on
  `AssistedIntake` yet; current UI accepts explicit read-model extension fields.
- `modules/opsboard/application/network_listings.py` and
  `apps/api/app/routes/operator_modules/network_listings.py`: actual Operator
  submission/read model used above, without those resource tokens.
- `apps/api/app/routes/listings.py`: separate contract store, UUID resource
  parameters, assignment creation and action endpoints. Its IntakeDetail exposes
  IDs/states but not assignment/SLA versions. Do not infer it is the Operator
  read model, reuse an Intake version, or manufacture IDs from `IN-3001`.

Next must implement/verify genuine provisioning and a shared authoritative
read-model projection (including post-action/reload versions and target identity
scope) before claiming successful mounted Transfer/Pause. Preserve tenant/RBAC,
UUID/If-Match, audit and idempotency gates. Do not bypass those requirements with
static actor targets, synthetic success responses, or test-only resource seeding
presented as production capability. No backend/contract changes were made here.

## Verification receipts

All commands completed synchronously through the original terminal; `*.exit`
records capture that invocation's status, not a summary-polling inference.

| Check | Original result | logs/ |
| --- | --- | --- |
| full final web unit suite | exit 0; 72 files / 790 tests | unit-final.log |
| earlier Intake suite before added SLA/failed-refresh tests | exit 0; 119 tests | intake-suite.log |
| final real-API unavailable authority browser probe | exit 0; 2 tests, four states | browser-final.log |
| initial real-API probe (mobile summary offscreen capture) | exit 0; 2 tests | browser.log |
| final typecheck / lint | exit 0 each | typecheck-final.log / lint-final.log |
| code boundaries | exit 0 | boundaries.log |
| root business inventory | exit 0; unchanged 125 tests / 18 files | inventory.log |
| diff check | exit 0 | diff.log |
| failed early focused diagnostics | exit 1 each; 47 pass / 1 fail | focused.log / focused-final.log |

Unit stderr includes existing jsdom ECONNREFUSED navigation probes; the full
invocation's terminal exit is 0. No build, full business E2E, Python/API suite,
cloud writes, live data or independent VDC verification was run in this increment.
The earlier full-scope evidence and unresolved states are not replaced by these
checks. Reproduce the browser probe with:

```sh
CI=1 OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
ODP_DEPLOY_ENV=e2e ODP_E2E_MODE=true ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
NETWORK_PARITY_EVIDENCE_DIR=/tmp/network-resource-authority-shots \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts \
  operator-assignment-authority --workers=1 --retries=0 --project=chromium
npm test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
python3 delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

**Task remains in_progress.** Complete genuine resource integration and modal
1440/390 design-before-after/permission/conflict pairs, Spatial split/negative
pairs, Find Areas reload-before-back, remaining tab states/density, independent
VDC and final verification before using task_finalize.sh to resubmit exact head.
