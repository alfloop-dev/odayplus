# Review repair — truthful data and durable scoring actions

ODP-UI-NETWORK-TABS-PARITY-001 · Pi · reviewer Codex2 · PR #1440

**Bounded owner increment, not full acceptance or review resubmission.**
Codex2's full-scope visual/VDC finding remains open. Do not merge or formally
resubmit until the parent README's remaining Intake/all-tab work is complete.

## Source and review findings

Resumed clean at submitted head `7a3fc835a32a10c65735931d7415552ede0f9458`.
Checked fetched `origin/dev` contracts (SiteScore types/routes, EXECUTE RBAC,
active-persona headers, workspace composition and build scripts). The canonical
live task brief supplied the review findings; the original content audit is
untracked on dev and was read from its brief-specified `/home/lupin/odayplus` path.
No base merge, permission/server changes, live writes or status-file edits.

Anchors `822cae811bee`, `b5ef918c9905`, `309ca2df20e0`, `cd951649f2b3` preserve
this repair. Final source is `cd951649f2b38f6571fe8495e1fa24dc25537e96`;
[source.sha256](source.sha256) binds product/test source to the actual receipts.
The final anchor only stabilizes E2E assertions, not product rendering.

- **Candidate facts:** key/value fields reuse authoritative Gate dimension notes.
  The same candidate now has **18坪 in Gate and detail**, not 18/28. Failed rules
  explicitly say 未通過; zero completeness counts survive. Empty checks do not
  synthesize success. Unsupported contact, survey, distances, POI, owner/deadline,
  notes and audit data display 未提供. Missing listing IDs no longer become L-2024.
- **Actions:** Candidate board/detail and SiteScore single/batch Compare buttons
  reach the existing authorized POST /network-scoring/compare; Compare removal
  reaches the same API and reloads its authoritative snapshot. No local success
  toast can claim a failed write. HTTP 403 regressions preserve the previous set.
- **Unsupported operations:** edit/archive, review submission, data-request,
  hold/alternate and report generation controls remain in the design hierarchy
  but are explicitly disabled with explanations. No new mutation API is invented
  and no mock preview is reported as an actual report. Review Decision's existing
  authorized workflow is unchanged; this is not a removal of its decision API.
- **Batch:** restored Candidate pipeline CTA scores gate-passed candidates.
  SiteScore batch submits only checked, gate-passed IDs; blocked entries cannot
  be selected. Empty selections and busy writes disable execution. Server Gate
  enforcement remains authoritative. Each scoring invocation has a fresh
  idempotency key (a prior batch's key cannot replay a different selection).
- **Persona:** only the EXECUTE-capable expansion-manager console persona gets
  scoring/Compare handlers; all requests retain active-role headers and actor.
  Read-only ops-lead, pm-audit and platform-admin do not impersonate expansion.
  These are presentation checks, not claims of granting durable authority.
- **SiteScore:** zero revenue remains NT$0K with a zero-height bar. Missing API
  metadata/risk assumptions display 未提供, never positive prototype defaults.
  Existing demo projection values remain confined to fixture fallback adapters.
- **Maps:** mini-maps are visibly labelled position schematics, not actual
  coordinates/distances; invented named stores are replaced by schematic labels.
  The actual Find Areas map integration is not replaced or newly certified.

## Verification receipts

Every invocation completed synchronously through the original tool terminal;
exit codes below are observed shell receipts, not inferred from grep summaries.
No tests ran during review-approved finalization (the task is in_progress).

| Command | Exit / result | Log in `logs/` |
|---|---|---|
| focused web Vitest (two changed suites) | 0; 36 passed | review-repair-unit-first.log |
| `npm test --workspace=@oday-plus/web` | 0; 67 files / 713 passed | review-repair-unit.log |
| web typecheck | 0 | review-repair-typecheck.log |
| web lint | 0 | review-repair-lint.log |
| scoring Playwright spec, workers=1/retries=0/chromium | 0; 4 passed | review-repair-scoring-e2e-success.log |
| supplemental geometry/config, workers=1/retries=0 | 0; 9 passed | review-repair-geometry.log |
| web production build (before geometry) | 0 | review-repair-build.log |
| production rebuild (after dev captures) | 0 | review-repair-production-build.log |
| production bundle budget | 0 | review-repair-production-budget.log |
| code boundary check | 0 | review-repair-boundaries.log |
| root Playwright inventory | 0; 125 tests / 18 files unchanged | review-repair-inventory.log |
| `git diff --check` | 0 | original terminal receipt |

The existing batch business test now actually clicks selected batch execution,
asserts the request IDs/persona and backend **persisted audit** (only CS-1001
scored, no skips), then proves Compare removals through authoritative GET and UI.
It does not add tests to the exact business inventory. Unit tests cover unsupported
controls, Gate failures/missing records/zero counts, zero revenues, selection,
busy-state disabling, three-tab Compare success/failure wiring and read-only roles.

Retained failed diagnostics (not accepted as passing evidence):

1. `review-repair-scoring-e2e.log`: exit 1, one failure / three not run. Cold
   lazy Candidate panel still loading after the old 5s assertion; now wait 15s,
   consistent with snapshot readiness. No content assertion was deleted.
2. `review-repair-scoring-e2e-second.log`: exit 1, three passed / one failed.
   Initial fixture report preceded role-keyed workspace remount and reset the
   mode selection. Await the active persona and API-backed rescore affordance
   before clicking batch. Do not use sleeps, force clicks or synthetic events.
3. `review-repair-scoring-e2e-final.log`: exit 1, three passed / one timeout.
   Browser proxy response body wait did not complete although backend batch and
   subsequent GET completed. The final test reads the authoritative backend audit
   instead of assuming 200 means persisted success; it preserves request/status
   assertions. The failure's root transport cause is not independently established.
4. `review-repair-bundle-budget.log`: exit 1 against **dev** artifacts after
   geometry. Dev chunks are not production size evidence. Rebuilt production
   serially, then the budget command exited 0. No budgets raised or imports undone.

Reproduction: use parent README commands and port/env settings. Focused unit
paths are `features/operator/network/__tests__/Package10NetworkPanels.test.tsx`
and `features/operator/network/__tests__/NetworkFindAreasWorkspace.route-gate.test.tsx`.
Scoring command is `npx playwright test tests/e2e/operator-network-scoring.spec.ts
--workers=1 --retries=0 --project=chromium`. Run build/bundle only after all
browser dev servers have exited. No build overlapped browser verification.

## Paired visual evidence and remaining scope

[shots/](shots/) preserves 1440/390 same-width **design / before-repair / after**
images for these three panels, plus 1440/1024/390 geometry. Before-repair images
are copied verbatim from the parent's committed prior increment captures (render
`4480acc404b7`), not falsely presented as pre-task audit images. Design remains
unchanged Package 10; mobile reference overflow is retained. After images use
this repaired source, real fixture backend snapshot and real UI clicks. Capture
freezes its real snapshot for determinism, not an invented payload.

Pi inspected all six after images: the Candidate KV/Gate discrepancy is removed,
detail/photo/action hierarchy and mobile stacking survive; SiteScore map/revenue
and risk structure remain reachable; Compare chips/table/map/300px rail survive.
This is owner inspection, **not independent visual approval**. The English flow
banner is still visible and remains an open selected-flow reconciliation defect.
Some desktop Gate notes are ellipsized; complete VDC semantic/contrast/focus
acceptance is not claimed by these geometry assertions.

Still required: Intake detail/field-fix/decision/Transfer/Pause/Promotion/Review
Decision pairs and geometry/focus/permission/conflict receipts; other Network
tabs and batch-state visual pairs; selected-flow banner/gates reconciliation;
risk tone/accessibility checks; later-spec integration explanation in the PR;
independent VDC-005 outcomes. Unsupported buttons are safe, but their integration
must be explicitly discussed at final review. Current exact-head CI approval is
not claimed. Keep task in_progress; only after full scope completion use
`AI_NAME=Pi delivery_toolchain/git/task_finalize.sh` for atomic resubmission.
