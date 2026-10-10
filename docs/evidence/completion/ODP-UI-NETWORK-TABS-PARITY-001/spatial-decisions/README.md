# Spatial decision authority / acknowledgement — bounded increment

Task `ODP-UI-NETWORK-TABS-PARITY-001` · owner Pi · reviewer Codex2.
**Not full acceptance, not review resubmission, not backend/live durable proof.**

## Exact sources and reference boundary

- Before rendering: `ed196bd00ff12691972df3ddb7bd103aa910aaef` in a detached
  scratch worktree, with only the capture harness from `1974b6aeaaac` copied in.
  Shared existing npm/Python dependencies were linked; no package install or
  backend change. First launch lacked the workspace-local Next dependency link;
  original exit 1 is retained as `before-startup-failed.*`. Linking the existing
  `apps/web/node_modules` repaired that harness setup, without changing source.
- Implementation anchor: `e0b8a5dd89d1`; harness anchor and verified source HEAD:
  `1974b6aeaaac729043f75df2942f9d06675f9836`.
- Fresh `origin/dev` `2fe3ef933794d0bb293449b74dffe9602d53f767` remains an ancestor.
- Package 10 has **no Spatial or Spatial decision-dialog reference**. Same-width
  unchanged Network reference captures and absence assertions remain in
  [`../spatial-availability/`](../spatial-availability/). These modal pairs are
  later-spec design-language integration, not an invented prototype match.

## Repairs

1. Client rejects unsafe list items, duplicate proposal identities, mismatched
   detail/preview identity, nonnumeric metrics and incomplete/overlapping split
   partitions instead of exposing unsafe controls or crashing array/number
   rendering. Genuine zeros and valid complete split topology are retained.
   This is read-model shape validation, not independent policy authorization.
2. Confirmation pins the opened proposal and persona; replacement, terminal
   state, unavailable list or lost permission cannot submit another proposal.
   A superseded persona's late write cannot trigger a stale-persona GET or attach
   an acknowledgement to the new scope. Backend principal authority is unchanged.
3. A synchronous in-flight guard prevents duplicate dispatch. Input, cancel,
   Escape, row selection and filter are frozen during POST/readback. Rejection
   requires nonblank reason; failed requests preserve input in the same modal.
4. POST success and GET success are separate facts. Failed/empty/stale readback
   does not turn an acknowledged POST into a failed write, nor assert that topology
   is active. The acknowledgement explicitly says status is unconfirmed and not
   to repeat the request. Acknowledged proposal IDs cannot be decided again in the
   mounted panel even if a successful GET still says PROPOSED; a read retry is
   reachable. Expected terminal status is checked by ID before confirmed wording.
   This local guard is not durable idempotency; server idempotency remains required.
5. Real compact dialog styling: explicit pinned ID, bordered input, coherent
   label/title/description, dark green/red primary buttons and outlined cancel.
   Errors now appear inside the modal rather than under its backdrop. The shared
   dialog hook gives initial input focus, focus trap, Escape and invoker restoration.
   Dialogs are viewport-contained, scrollable if necessary, with 20px mobile margins.

## Paired browser evidence

`before-` / `after-` images and JSON cover both approve/reject at **1440×900 and
390×900**, each with modal, pending, controlled conflict, acknowledged-POST /
failed-GET, and recovered-terminal states (40 PNGs total).

| Modal | Before width / height | After width / height |
|---|---|---|
| Approve 1440 | 480 / 299.81 | 480 / 319.73 |
| Approve 390 | 351 / 319.95 | 350 / 332.53 |
| Reject 1440 | 480 / 278.67 | 480 / 297.94 |
| Reject 390 | 351 / 298.81 | 350 / 310.73 |

The small height increase intentionally exposes the exact decision target. All
four after modals and their controls are viewport-contained with no document
horizontal overflow. Full-page screenshots retain the surrounding page and its
current scroll position; fixed-modal viewport coordinates are recorded separately
in geometry JSON. Pi inspected the mobile approve pair, desktop reject conflict
and mobile acknowledgement/error captures: target and preserved input are visible,
error is reachable inside the dialog, and POST/GET facts are shown separately.

Twenty after scoped AA scans have zero violations. Four baseline rule/state
findings are retained; no assertion suppression. The axe JSON intentionally stores
violations, incomplete rules, passing rule IDs, timestamp and URL, not every passing
node. These scoped scans are not whole-console or independent VDC-005 approval.

HTTP routes are explicitly controlled: first write receives 409 after a pending
capture, second receives 200 followed by GET500, then a retry returns terminal
status. Exactly two intentional attempts are observed, and retry adds no POST.
Keyboard/focus assertions run against real rendered controls, without force clicks.
**The terminal GET is supplied by the harness; it is NOT a backend durable receipt.**

## Original synchronous exit receipts

| Check | Result | Receipt |
|---|---|---|
| Focused panel/client/workspace | 50 passed / 3 files, exit 0 | `focused.*` |
| Full web Vitest | 785 passed / 72 files, exit 0 | `unit.*` |
| Typecheck / lint | exit 0 each | `typecheck.*`, `lint.*` |
| Before modal captures | 4 passed, exit 0 | `before.*` |
| After modal geometry/keyboard/controlled states | 4 passed, exit 0 | `after.*` |
| Previous Spatial render/list-state regression | 5 passed, exit 0 | `render-regression.*` |
| Boundaries / business inventory / diff | exit 0 each; 125 tests / 18 files unchanged | `boundaries.*`, `inventory.*`, `diff.*` |

Full unit stderr includes expected mocked connection refusals and dependency SSL
warnings; the original Vitest terminal completed exit 0. No test was rerun merely
to obtain counts. All exits were saved immediately in the same synchronous shell.

Reproduction from root, without overlapping build and browser servers:

```sh
npm test --workspace=@oday-plus/web -- --run features/operator/__tests__/HeatZoneMergeSplitPanel.test.tsx features/operator/network/__tests__/heatZoneCompositionClient.test.ts features/operator/network/__tests__/SpatialProposalAvailability.test.tsx
npm test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_PARITY_CAPTURE_PHASE=after NETWORK_PARITY_EVIDENCE_DIR=<output-directory> \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts tests/visual/operator-spatial-surface-parity.spec.ts --grep 'decision acknowledgement'
# The baseline uses the same command with phase=before in the detached source above.
# Regression uses --grep-invert 'decision acknowledgement' with scratch output.
python3 delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
git diff --check
```

## Still required before formal review

Actual Spatial generation, genuine preview and append-only decision/reload proof;
real split/modal/permission and further negative-state pairs; real Intake assignment/
SLA authority for Transfer/Pause; Find Areas reload-before-back and density follow-up;
remaining all-tab/batch states, final full verification/CI integration, final PR
later-spec explanation and independent VDC discipline outcomes. Do not run
`task_finalize.sh`, handoff, `re_review` or `done` for this bounded checkpoint.
