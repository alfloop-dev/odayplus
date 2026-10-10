# Find Areas reload-before-back regression

Pi · reviewer Codex2 · `ODP-UI-NETWORK-TABS-PARITY-001` · existing PR #1440

**Bounded verification increment, not full task acceptance or review submission.**
No product code, API authorization, write contract or Package 10 design was changed.

## Scope and source

Resumed clean `e068bc9082e586a72022b42cd89547690d89a7bd` on the expected task
branch. Live canonical task/status and reviewer findings were read; fetched
`origin/dev` remained `2fe3ef933794d0bb293449b74dffe9602d53f767` at final check.
Canonical workspace, Playwright config and package commands were checked against
that ref rather than inferred from the isolated worktree.

[Earlier Find Areas evidence](../findareas-surface/README.md) retained a failed
1024 reload-before-back receipt. This wake first added reload-before-back to the
existing browser flow: **3 passed, exit 0**. The old failure was **not reproduced**
at current source. No product defect or product repair is claimed. Baseline logs
are retained in `logs/baseline-browser.*`; that preliminary harness differs from
the final expanded regression and is not its acceptance receipt.

Anchor `2aab1ab8f48cc1cdabb2d1be7963ffd7e7b84469` expands the existing supplemental
spec at 1440, 1024 and 390 without changing the root business-test inventory:

1. Select HZ-02 and the fit lens using native keyboard controls.
2. Reload **before** waiting for the new API snapshot.
3. Back to HZ-02/demand, then back to HZ-01/demand.
4. Forward to HZ-02/demand, then forward to HZ-02/fit.
5. Reload again and navigate to Radar with the restored HZ-02 filter.

Each of the six restored states checks URL zone/lens, an actual scoped backend
GET200, the matching API zone label and pressed lens, loaded real map canvas,
contained document/map/detail geometry and nontrivial map/detail heights.
`shots/navigation-<width>.json` includes raw URL checkpoints and the hydrated
measurements. A matching fixture ID is not used as hydration proof. No API response
interception, force clicks, fixed sleeps or DOM navigation shortcuts are used.

The UI runs against the genuine local **fixture/test backend**, not deployed/live
business data. Complete backend snapshot equality before/after and empty
`unexpected-writes-<width>.json` prove no Network mutations during restoration and
Radar navigation; the explicit test setup reset is outside that no-write interval.
This is browser history proof, not server restart or durable business-write proof.

## Images and accessibility

`shots/after-<action>-<width>.png` captures every restored state; existing hierarchy
and open-search shots are also retained. Pi inspected desktop `back-zone-1440`
and mobile `reload-before-back-390`: correct zone/lens, real canvas and contained
stacked detail are visible. The other captures are bound to measured assertions;
not all images received independent visual review.

There is no new before/design/after repair claim because rendering is unchanged.
Existing 1440/390 design/before/after pairs remain in `../findareas-surface/shots/`.
Six scoped axe scans (initial and expanded-search at all three widths) have zero
violations. The six history states have geometry assertions, **not separate axe
scans**. No whole-console certification or independent VDC-005 approval is inferred.
`source.sha256` binds tested source; `artifacts.sha256` binds screenshots, geometry,
API snapshots and original terminal logs/exit receipts.

## Verification (original synchronous terminal receipts)

| Check | Result |
| --- | --- |
| preliminary reload-before-back | exit 0; 3 tests |
| final six-state browser regression | exit 0; 3 tests, retries 0 |
| focused URL / workspace route-gate units | exit 0; 32 tests / 2 files |
| web typecheck / lint | exit 0 each |
| code boundaries / root business inventory | exit 0 each; unchanged 125 tests / 18 files |
| diff check | exit 0 |

No full web suite, API suite, production build, full business E2E, remote CI or
cloud action was run in this test-only increment. All completion judgments use
original command exits, not summary polling. Reproduce final browser evidence:

```sh
CI=1 OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
ODP_DEPLOY_ENV=e2e ODP_E2E_MODE=true ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
NETWORK_PARITY_EVIDENCE_DIR=/tmp/findareas-history \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts \
  operator-findareas-surface --workers=1 --retries=0 --project=chromium
npm test --workspace=@oday-plus/web -- \
  features/operator/network/__tests__/networkUrlState.test.ts \
  features/operator/network/__tests__/NetworkFindAreasWorkspace.route-gate.test.tsx
```

## Remaining work

The previously uncertified reload-before-back sequence now has current-source
local browser proof at all three widths. This does not erase the historical
failure or imply general race freedom. Genuine Operator Assignment/SLA provisioning,
shared resource read-model, scoped identity directory/server validation and actual
Transfer/Pause writes/reload remain open. Spatial split/permission and applicable
negative tab/dialog states, full density/independent VDC and final scope verification
remain open. **Do not task_finalize/handoff/re_review/done for this bounded increment.**
