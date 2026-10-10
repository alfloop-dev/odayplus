# Promotion confirmation — bounded owner increment

Pi · reviewer Codex2 · ODP-UI-NETWORK-TABS-PARITY-001 · PR #1440

**Not full acceptance or review resubmission.** Remaining Network tabs/batch/banner,
applicable permission/conflict states, actual-authority Transfer/Pause and independent
VDC outcomes remain due. No live deployment, production approval or merge claim.

## Anchors and source

- Resumed clean at `b949a6f3e317`; verified live task `in_progress` and canonical
  `origin/dev` `2fe3ef933794` already ancestor of HEAD. Re-fetch after checks confirmed
  the same base; no history rewrite or additional merge required.
- `670c780e9018`: design/before 1440/390 pairs and supplemental browser baseline.
- `ee101d6051b5`: product modal density, editable reason/risk, pending-write guards,
  and unit/integration coverage.
- `58ad432bb249`: durable reviewer projection repair and contract regressions.
- Final evidence commit changes only this documentation/evidence and supplemental
  harness assertions, not the verified product renderer.

Reference is the unmodified archived Package 10 standalone HTML. Its external
network/font loading is aborted, as in earlier task pairs. Reference-only DOM
clicks reach clipped mobile tabs and a role menu overlapped by its detail page.
The prototype's own create-listing, request-promotion and distinct-role approval
flows populate the reference; no reference state injection or replacement HTML.
The requested design is absent from tracked `origin/dev` audit paths, so the audit
README was read from the task-specified canonical absolute path.

Product uses actual mounted console clicks, real BFF/FastAPI fixture writes,
explicit expansion-manager subject/tenant-a and a second distinct valid UUID
reviewer with the same allowed role. No product request interception, fabricated
receipt or weakened permissions. The v1 route requires UUID subjects (apart from
its existing fixture allowlist); no allowlist extension was made. Data/timestamps
and actor labels differ from reference and are not live business facts.

## Measured repairs and later-spec integration

| viewport | reference height | before height | after height | width |
| --- | ---: | ---: | ---: | ---: |
| 1440×900 | 393.1 | 565.5 | 412.6 | 520 |
| 390×900 | 461.8 | 713.4 | 480.7 | 350 |

Baseline was an oversized, read-only summary of a reason and risk acknowledgement
already collected outside the modal. The Package 10 confirmation now directly
collects the required reason and risk acknowledgement, with the reference's
context → impact → reason → risk → actions hierarchy. Initial focus goes to the
labelled required textarea, risk has `aria-pressed`, errors are announced, and
Tab/Shift+Tab/Escape/invoker restoration use the existing modal contract.

The six-row technical summary and green control dump no longer dominate the
initial view. They remain reachable in a native **決策前檢視與持久化控制** disclosure,
including authoritative Listing, proposer/reviewer, gate hash, reason, If-Match and
stable Idempotency-Key. This added later-spec disclosure accounts for roughly 19px
extra height versus reference and must be explained in the final PR. The original
inline reason/risk fields remain available for the separate Reject operation;
modal edits share the same draft rather than introducing competing decisions.

Both widths retain 14px radius, centered card, 20px minimum outer margins, body
padding **12px 18px 4px**, body gap **10px** and bounded scrolling. Mobile no longer
reduces modal side padding to 14px. Long actor IDs wrap; close cannot shrink;
contained header/body/footer and no horizontal document overflow are asserted.

The modal-opening CTA is no longer gated on an already completed reason/risk
outside the dialog: opening does **not** authorize a write. The same required
reason (at least three characters), risk, second-actor, role, PENDING_REVIEW,
If-Match and server commit gates still precede the POST. Synchronous pending refs
plus visible busy state prevent duplicate writes and disable close/cancel/inputs;
Escape cannot conceal an in-flight result. Error drafts and retry keys survive.
All saga statuses and commit-gated Candidate/Job IDs are retained.

### Actual readback defect exposed, not waived

The first after browser run received POST200 and displayed a Candidate, but an
independent decision GET returned `reviewer_subject_id=null`. The saga persisted
`reviewer`; the route decorated only the POST response. The adapter now maps and
persists that canonical committed reviewer for both decision GET and intake
hydration, including older records with a `reviewer` field. No actor is invented.
Contract regressions cover plain-v1 and operator-backed repositories and both GET
paths; actual browser reload now retains reviewer, Candidate and Job IDs.

## Evidence and original completion receipts

`shots/` contains design/before/after confirmation pairs at both widths, geometry,
self-review-denied, required-reason, required-risk, expanded control disclosure,
committed and reloaded screenshots, scoped axe and persisted receipt JSON.
The final browser run asserts the actual POST's reason/risk payload and exact
If-Match/Idempotency-Key from the visible controls, POST200, independent GET200,
committed reviewer/Candidate/Job/audit IDs and matching reload IDs. This is isolated
fixture readback across navigation, **not** production restart-durability evidence.
Two scoped axe reports have zero violations; this is not full-page WCAG approval.
Pi inspected before desktop and reference/after mobile and after desktop images;
this owner inspection is not VDC-005 independent signoff.

All listed checks ended via the original synchronous terminal and immediate
shell exit recording (`logs/*.exit`), not summary polling or process inference.

| check | original final receipt |
| --- | --- |
| reference + before pairs | `before-both.log/.exit`: exit0, 2 passed |
| after geometry/axe/keyboard/write-controls/durable/reload | `after-controls.log/.exit`: exit0, 2 passed |
| preceding after durable run | `after-durable.log/.exit`: exit0, 2 passed |
| focused Promotion panel + saga integration | `focused-unit-complete.log/.exit`: exit0, 37 passed |
| full web unit | `unit.log/.exit`: exit0, 68 files / 720 passed |
| API promotion contract + integration | `api-tests-project.log/.exit`: exit0, five tests (original `..... [100%]`) |
| typecheck / lint | respective logs/exit: exit0 |
| code boundaries | `boundaries.log/.exit`: exit0, 1210 files |
| unchanged business inventory | `inventory.log/.exit`: exit0, 125 tests / 18 files |
| whitespace / current-base ancestry | original terminal exit0 |

Retained failed diagnostics are not counted as passes: baseline deep-link used
an incorrect query and tried a hidden tab; arbitrary non-UUID reviewer was denied
by v1 subject validation; reference needed its own create-listing flow and role
menu dispatch due overlap/clipping. First after exposed the genuine null-reviewer
readback. First two focused unit attempts lacked DOM matchers/custom key dispatch;
the corrected tests retain the same assertions. `uv` was unavailable (exit127);
the known project `.venv/bin/python` used by Playwright ran the API tests (exit0).
No host filesystem search or installation was used. Full web stderr retains
existing ECONNREFUSED navigation probes and PostgreSQL TLS warnings. No production
build or full business E2E suite was run for this bounded checkpoint.

Reproduce from repo root:

```sh
OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
NETWORK_PARITY_DESIGN=1 NETWORK_PARITY_CAPTURE_PHASE=after \
NETWORK_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-NETWORK-TABS-PARITY-001/promotion/shots" \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts \
  operator-promotion --workers=1 --retries=0 --project=chromium
.venv/bin/python -m pytest tests/contract/test_assisted_listing_promotion_api.py \
  tests/integration/test_assisted_listing_promotion.py -q
npm test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
python3 delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

`before-source.sha256` binds the baseline renderer; `source.sha256` binds final
product/tests/reference, and `artifacts.sha256` binds this increment's receipts.
Continue remaining scope, explain integrations in the final PR, complete final
verification and only then atomically resubmit using `task_finalize.sh`.
Remains **in_progress**, not review-ready/done.
