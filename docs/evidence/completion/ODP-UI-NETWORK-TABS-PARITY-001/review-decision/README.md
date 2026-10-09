# Review Decision — bounded owner increment

Pi · reviewer Codex2 · ODP-UI-NETWORK-TABS-PARITY-001 · PR #1440

**Not full acceptance or review resubmission.** Promotion, actual-authority
Transfer/Pause modal/conflict pairs, remaining Network/batch/banner/risk states
and independent VDC outcomes are still due. This increment does not approve them.

## History-preserving base advance

Resumed clean at `0199563a2f23`; live canonical board confirmed `in_progress`.
Merged `origin/dev` `2fe3ef933794` with `4168e4f46b3f`, no conflicts. This composes
current shell data-availability changes without reset/rebase/history rewriting.
Pushed normally; re-fetch after verification confirmed the same base and
`git merge-base --is-ancestor origin/dev HEAD` exited 0.

- Baseline anchor `94eb6f55af81`: complete design/before pairs, actual renderer
  at composed `4168e4f46b3f`; supplemental harness outside business inventory.
- Repair anchor `b009ca5db2a6`: dialog semantics/layout and three unit checks.
  Successful after browser run and full web checks use this product renderer.
- Subsequent business E2E repair only changes session/readiness setup, not
  assertions, backend permission grants or the rendered product.

## Actual repairs and retained later-spec behavior

Desktop remains **520px**; mobile returns from a bottom-aligned **370px / 12px**
card with 10px margins to the reference's centered **350px / 14px** dialog with
20px margins. Header/body/footer padding remains Package 10's
16px 18px 0 / 12px 18px 4px / 14px 18px 16px; body gap remains 11px. Removed the
extra outline border, retained bounded vertical scrolling. Long titles wrap;
close control does not shrink. Reject CTA now matches reference **#B3261E**,
other CTAs retain **#2E3A97**. These are measured rendering changes, not just class
renames or text edits.

The actual card, not its full-screen overlay, owns the named modal role. Reused
existing modal hook for initial reason focus, Tab/Shift+Tab trapping, Escape,
invoker focus restoration and non-dismissible pending writes. Proper required
field labels, announced errors, visible keyboard rings and `aria-pressed` risk
acknowledgement preserve keyboard/assistive access. Pending writes disable close,
cancel, inputs, acknowledgement and duplicate submits; unit test preserves drafts
when a server error arrives. No transaction, role, decision or validation gate
was weakened.

Product intentionally retains RV ID, mapped governance status, Candidate ID and
five-record atomic-sync explanation. Its reasons require 10 characters. WAIT
conditions start empty rather than adopting the prototype's prefilled mock terms.
Product's canonical `isOverride` also requires acknowledgement for rejecting a
WAIT recommendation; prototype only asks it for GO/WAIT overrides. Keep this
stronger existing gate, do not hide the extra warning/acknowledgement for pixel
matching. This explains Reject's taller card and must be included in the final
PR integration explanation.

| action | desktop height: design / before / after | mobile height: design / before / after |
| --- | --- | --- |
| GO over WAIT | 427.1 / 450.9 / 448.9 | 443.4 / 468.5 / 466.5 |
| WAIT | 428.0 / 422.3 / 420.3 | 428.0 / 439.9 / 437.9 |
| Return | 368.6 / 384.3 / 382.3 | 401.9 / 419.0 / 417.0 |
| Reject | 298.5 / 450.9 / 448.9 | 314.8 / 468.5 / 466.5 |

## Paired evidence and durable states

`shots/` includes design/before/after for **GO / WAIT / RETURN / REJECT** at
**1440×900 / 390×900**, geometry JSON, required-reason screenshots, applicable
unacknowledged-override screenshots, committed decision viewports and scoped axe.
All eight final scoped axe reports have **zero violations**; this is not a full
page WCAG certification. Geometry asserts card/margins, radius, scrolling,
contained children and absence of horizontal document overflow at each width.

Product uses actual mounted console clicks and isolated FastAPI/BFF fixture
writes, explicit manager subject/role/tenant-a, no request interception or DOM
navigation. Reference is untouched archived Package 10. It uses its allowed
PM/稽核 persona and original pending RV-701 WAIT selection; reference-only DOM
clicks reach mobile-clipped tabs/actions. No reference data/state was fabricated.
Animations are disabled for stable measurements; archived overflow and role-change
toasts remain visible in reference screenshots. Differing actor labels/timestamps
are fixture differences, not live business claims.

Each final browser case exercises required reason, applicable WAIT/Return data
and risk gates, real keyboard closure/focus restoration, actual **POST 200**, then
an independent durable **GET 200** projection containing exactly one Decision and
one Audit event, followed by UI reload showing the same mapped decision.
`receipt-*.json` stores this persisted projection, not an optimistic UI message.
Pi inspected mobile GO/WAIT/Return, reference/mobile WAIT, and desktop reference/
product Reject images. This is owner inspection, **not independent VDC-005 signoff**.

## Original completion receipts

All commands ended through their original synchronous terminal with immediate
shell exit recording; no summary polling, process inference or counting reruns.

| check | final result / original log in `logs/` |
| --- | --- |
| design + before capture | exit 0; 8 passed; `before-complete.log/.exit` |
| after geometry/axe/keyboard/durable/reload | exit 0; 8 passed; `after-durable.log/.exit` |
| focused dialog unit | exit 0; 3 passed; `focused-unit.log/.exit` |
| full web unit (includes composed shell) | exit 0; 68 files / 718 passed; `unit.log/.exit` |
| business Review suite | exit 0; 8 passed; `review-e2e-final.log/.exit` |
| typecheck / lint | exit 0; `typecheck`, `lint` logs/exit files |
| boundaries | exit 0; 1210 files; `boundaries.log/.exit` |
| business inventory | exit 0; 125 tests / 18 files unchanged; `inventory.log/.exit` |
| diff whitespace / base ancestry | exit 0; original terminal |

Full web stderr retains existing ECONNREFUSED navigation probes and PostgreSQL
TLS warnings. No production build or full business E2E suite was run for this
bounded checkpoint; full-scope final verification remains due.

Failed diagnostics retained and not counted as passes:
- `before.log`: attempted reference Network access with disallowed default role.
- `before-reviewer.log`: expected reference RV-702; original prototype pending
  review is RV-701 and its queue cards are non-semantic divs.
- `before-stable.log`: four desktop captures passed; mobile assertion exposed
  370px/12px bottom-sheet deviation. Baseline harness now records defects rather
  than preventing remaining captures; final after assertions enforce repairs.
- `after.log`: POST 200 and geometry/axe/keyboard completed, but Playwright's
  browser response-body read timed out. Not accepted as successful durable proof.
  Final harness verifies committed UI plus independent persisted projection.
- `review-e2e.log`, `review-e2e-session.log`, `review-e2e-authority.log`: cold Next
  BFF read timed out and showed fallback queue without RV-698. Corrected explicit
  session authority/tenant, cleared broad runner role headers, warmed actual BFF
  route and waited for authoritative requester, retaining all existing assertions.
  `review-e2e-warm.log` passed eight; the final run additionally retains the
  original candidate-title assertion after the authoritative-readiness check.

Reproduce serially from repo root:

```sh
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_PARITY_DESIGN=1 NETWORK_PARITY_CAPTURE_PHASE=after \
NETWORK_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-NETWORK-TABS-PARITY-001/review-decision/shots" \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts \
  operator-review-decision --workers=1 --retries=0 --project=chromium
npm test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
ODP_E2E_MODE=true ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
npx playwright test tests/e2e/operator-network-review.spec.ts \
  --workers=1 --retries=0 --project=chromium
python3 delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

`source.sha256` binds current product, harness, business E2E and archived design;
`artifacts.sha256` binds these receipts/images/docs. Continue Promotion and remaining
scope, resolve genuine assignment/SLA read authority before Transfer/Pause product
modal/conflict proof, and obtain applicable independent VDC outcomes. Only after
complete acceptance/final verification, explain integrations in PR #1440 and
atomically resubmit using `task_finalize.sh`. Remains **in_progress**.
