# Intake field-fix / receipt-decision parity increment

Pi · reviewer Codex2 · task ODP-UI-NETWORK-TABS-PARITY-001 · PR #1440

**Increment only. Not full acceptance, independent VDC approval, or review
resubmission.** The remaining scope below must be completed before finalization.

## Source and scope

Resumed clean at `7ba37ed467175e7818a33e46e823efb7efda3a96`, on the assigned task
branch. Read the canonical live task brief/review findings and fetched `origin/dev`
contracts/configuration. Intake product source was unchanged relative to dev.
The audit and generated guide/brief are untracked context, not dev artifacts.

Anchors:
- `76f5623f4471`: supplemental paired capture harness (outside business inventory).
- `bcdc8a2f736f`: dialog density, sizing/focus and stabilized capture harness.
- `b0dd088744ea`: existing Intake E2E helper waits for authoritative inbox/persona.

Before captures use the original Intake CSS (identical to fetched dev), before
`bcdc8a2f736f`; after captures use that anchor's product source. The subsequent
anchor changes only the existing E2E helper, not rendering. `source.sha256` binds
all changed source and the archived design; `artifacts.sha256` binds evidence.

Product changes are confined to `intake.module.css`:
- Retain 460/520px desktop dialogs and 14px radius; match design body padding
  `12px 18px 4px`, 10px gap and footer `12px 18px 16px`.
- Restore review-summary row hierarchy (70px key, 10px gap, 6px/12px row padding,
  11px text). Wrap long authoritative evidence tokens without arbitrary clipping.
- Keep narrow/decision title and close button in separate grid columns; the Add
  URL and inbox headers retain their existing badge-capable flex layout.
- Use explicit border-box controls and viewport-bounded local scrolling with
  20px overlay gutters. Keyboard focus rings remain visible on buttons/checkboxes.
- Compact required risk disclosure to the design's note density (10.5px / 1.55).
  **Do not remove the summary, acknowledgement, reason or backend gates merely
  because the prototype lacks them.** No TypeScript product logic/API/RBAC edits.

The required later-spec risk acknowledgement remains a distinct bordered block
between reason and footer, rather than an unrelated banner. This is an intentional
Package 10-language integration, not an assertion of pixel-identical prototype
height. For example, field-fix height at 390 is 504px versus reference 343px;
decision is 745px versus 541px. The longer real snapshot/correlation tokens and
required disclosure remain readable. Desktop field-fix drops 470→455px; mobile
field-fix 519→504px. Mobile decision grows 716→745px because 11px rows and natural
word wrapping replace dense arbitrary character breaks, without page overflow.
Include this integration explanation in the final PR submission.

## Paired capture and VDC boundaries

`shots/` contains same-width **design / before / after** for field-fix and receipt
decision at **1440×900 / 390×900**, plus measured JSON and required-reason states.
The implementation is driven by real clicks and the isolated fixture FastAPI
service: reset, URL submission, typed same-origin BFF reads, continuous detail,
then field-fix/create-decision. No mocked intake payloads or optimistic success.
All records are explicitly local synthetic fixtures, not live/source-approved data.

The archived reference is untouched. Reference-only navigation dispatches clicks
for clipped mobile navigation. Its two parsed-field grids incorrectly use the
same `inkFieldsMob` condition, hiding both at desktop width; desktop field-fix
navigation therefore temporarily uses 390px, opens the actual reference dialog,
then restores 1440px **before capture/measurement**. Reference animation/transition
is disabled before measuring; no content/style geometry is reconstructed.
The reference's known mobile page overflow is retained in the viewport screenshot.

Assertions cover desktop width/radius, 350px mobile modal width, local scroll
bounds, no document overflow/control clipping, title/body/footer/field/close
geometry, design desktop width, exact body/footer spacing, autofocus, Tab and
Shift+Tab wrapping, Escape close and trigger-focus restoration. After captures
also assert required-reason errors leave the dialog open and unchecked risk
acknowledgement/disclosure present. Four axe JSON receipts have no serious or
critical violations in the tested modal scopes (not a full-page/accessibility
certification). Pi inspected all four after images; this is owner inspection,
**not independent VDC-005 approval**. No browser permission/409 paired-state
acceptance is claimed by these two baseline captures.

## Verification (original terminal completion receipts)

Every invocation returned through the original synchronous tool terminal; no
summary polling or inferred process-liveness success was used.

| Check | Observed exit/result | Receipt under `logs/` |
|---|---|---|
| stabilized before paired geometry/focus | 0; 4 passed | before-stable.log |
| after paired geometry/focus/reason gates + scoped axe | 0; 4 passed | after.log |
| existing Intake unit suites | 0; 10 files / 113 passed | unit.log |
| web typecheck | 0 | typecheck.log |
| web lint | 0 | lint.log |
| code boundaries | 0 | boundaries.log |
| existing product E2E: reason, correction/audit, reload/fresh context | 0; 3 passed | durable-e2e-ready.log |
| root business inventory | 0; 125 tests / 18 files unchanged | inventory.log |
| `git diff --check` | 0 | original terminal |

Unit stderr contains ECONNREFUSED from existing jsdom navigation probes; the
actual unit invocation exited 0. Production build/full web/all business E2E
were not rerun in this bounded CSS increment; final full-scope verification
remains due before formal review submission.

Failed diagnostics retained, not accepted as passing receipts:
- `before.log` (exit 1): submitted before the role-keyed remount/inbox readiness;
  typed same-origin request timed out and no detail appeared.
- `before-direct.log` (exit 1): attempted local direct API configuration; browser
  OPTIONS were observed but CORS requests did not complete. Final verification
  uses the existing same-origin BFF, not modified server/CORS configuration.
- `before-warm.log` (exit 1): reference tab selector omitted “Listing”.
- `before-navigation.log`, `before-reference-diagnostic.log` (exit 1): archived
  reference desktop field grid absent; diagnostic lists the actual buttons.
- `before-capture.log` (exit 1): reference measured during its scale animation.
  Stable capture disables only animations/transitions before measuring.
- `durable-e2e.log` (exit 1; 1 failed / 2 not run): old product helper similarly
  submitted before inbox readiness. `b0dd088744ea` prewarms the real BFF, awaits
  active manager and authoritative empty inbox; no business assertions removed,
  no client timeout widened, no sleeps, force clicks or mocked responses.

Reproduce from repo root, serially (do not overlap browser runs and builds):

```sh
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_PARITY_DESIGN=1 NETWORK_PARITY_CAPTURE_PHASE=after \
NETWORK_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-NETWORK-TABS-PARITY-001/intake-dialogs/shots" \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts \
  operator-intake-dialogs --workers=1 --retries=0 --project=chromium
npm test --workspace=@oday-plus/web -- features/operator/network/intake/__tests__
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
ODP_E2E_MODE=true ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
npx playwright test tests/e2e/operator-network-assisted-intake.spec.ts \
  --grep 'identity-field correction demands|possible match requires|decisions and corrections survive' \
  --workers=1 --retries=0 --project=chromium
python3 delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

## Next (task remains in_progress)

1. Complete continuous Intake detail and Transfer/Pause/Promotion/Review Decision
   design-before-after pairs, applicable geometry/focus and permission/conflict
   states. Extend the supplemental harness; retain the exact business inventory.
2. Complete the other Network tabs/batch-state pairs, selected-flow blocked-banner
   reconciliation, risk tones/accessibility and independent VDC-005 outcomes.
3. Explain later-spec integrations/unsupported controls in the PR; perform final
   full-scope verification and use `task_finalize.sh` to atomically resubmit the
   exact new head. Prior PR submission/green CI is not approval of this increment.
