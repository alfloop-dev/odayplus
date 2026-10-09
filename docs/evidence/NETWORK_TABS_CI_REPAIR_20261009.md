# Network tabs: bounded CI repair checkpoint

Task: `ODP-UI-NETWORK-TABS-PARITY-001` · owner Pi · reviewer Codex2
PR: #1440 · original submitted head `7e4318769b031a77ff152d15fd869ec7c218cd08`

This is an incremental repair receipt, **not full Package 10 visual acceptance**.
The original failed CI run is https://github.com/alfloop-dev/odayplus/actions/runs/37934993686.

## Root cause and repairs

`product-node` failed TypeScript validation; `product-e2e-gate` failed the web
Docker build at the same validation stage. The aggregate `product` job failed
because of those lanes. These were deterministic source errors, not a Docker
registry outage (the API pull warning was followed by a successful local build).

- Normalize nullable scoring `listingId` to the optional Candidate contract.
- Memoize scoring-to-Candidate projection; otherwise the state-sync effect
  receives a new array on every render and loops after snapshot hydration.
- Preserve an authoritative empty scoring list rather than resurfacing fixtures.
- Read pending reviews from `reviews`, not the nonexistent `items` property.
- Count actual `new` listings; do not turn zero into the total listing count.
- Render batch rent from the candidate's scorecard `rentAssumption`, with an
  explicit unavailable marker instead of an invented ScoringCandidate property.
- Remove the conditional tabs hook below the intake-detail early return.
- Do not show another candidate's scorecard when a gate-blocked candidate is
  selected; retain the disabled scoring control.
- Update E2E KPI assertions to the Package 10 labels. Stabilize the listing
  conversion test by selecting the expansion-manager role and waiting for the
  actual listings snapshot before inspecting workflow state.

## Verification receipts

Commands completed synchronously; results below use the tools' exit codes.
All commands run in the isolated task worktree.

| Verification | Result |
|---|---|
| `npm run lint --workspace=@oday-plus/web` | exit 0, no warnings/errors |
| `npm run typecheck --workspace=@oday-plus/web` | exit 0 |
| `npm run build --workspace=@oday-plus/web` | exit 0, including Next type validation and postbuild |
| `npm test --workspace=@oday-plus/web` at anchor `2eb84044e8bb` | exit 0, 66 files / 677 tests |
| Focused Vitest after the blocked-report change | exit 0, 2 files / 19 tests |
| Playwright API-binding + scoring specs | exit 0, 11 passed |
| Playwright listing workflow spec after hydration/role stabilization | exit 0, 8 passed |
| Review workflow spec in the initial combined run | 8 passed; combined run exit 1 due to listing conversion timeout |

Focused Vitest command:

```sh
npm test --workspace=@oday-plus/web -- \
  features/operator/network/__tests__/Package10NetworkPanels.test.tsx \
  features/operator/network/__tests__/NetworkFindAreasWorkspace.route-gate.test.tsx
```

Playwright command prefix (the existing project config starts the web and API
servers; these ports isolate the run from other workers):

```sh
OPSBOARD_PORT=3310 ODP_API_PORT=8310 \
ODP_API_BASE_URL=http://127.0.0.1:8310 ODP_E2E_MODE=true \
ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
npx playwright test <specs> --workers=1 --retries=0 --project=chromium
```

Specs:

- First successful run: `tests/e2e/e2e-network-find-areas-api-binding.spec.ts`
  and `tests/e2e/operator-network-scoring.spec.ts`.
- Combined diagnostic run: `tests/e2e/operator-network-listings.spec.ts`
  and `tests/e2e/operator-network-review.spec.ts` (1 failed / 8 passed / 7 not run).
- Listing-only diagnostic rerun initially failed on pre-hydration workflow state
  (1 failed / 7 not run). After explicitly awaiting the API snapshot and selecting
  the manager role, the listing-only suite passed all 8 tests. No API or permission
  assertions were removed to obtain the passing result.

Logs from this wake remain in `/tmp/network-tabs-*.log`; they are local diagnostic
artifacts, not remote CI receipts. Remote CI must validate the pushed head.

## Remaining acceptance work / next owner checkpoint

The inherited layout commit does not contain the task-required paired
1440/390 design/before/after screenshots or E2E geometry evidence for every
Network screen and Intake dialog. This repair does not manufacture those
receipts or claim that text assertions prove geometry.

Before formal resubmission, capture and inspect that visual evidence, add
geometric assertions, and complete the paired Intake detail, field correction,
receipt decision, transfer/pause, Promotion and Review Decision audit. Preserve
permissions and action behavior; inspect the remaining blocked-banner design
integration rather than deleting the underlying gate. Then run
`delivery_toolchain/git/task_finalize.sh` with `AI_NAME=Pi` to atomically resubmit
PR #1440. The task remains `in_progress` at this checkpoint.
