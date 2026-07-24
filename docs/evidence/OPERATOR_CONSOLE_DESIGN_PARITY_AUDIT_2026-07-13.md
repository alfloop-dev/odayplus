# Operator Console Design Parity Audit

Date: 2026-07-13
Scope: `/operator` management console vs canonical package `(6)` design archive.

## 2026-07-14 Status Update

The runtime evidence below captured the older local checkout at `e3f0fb84`.
After that audit, `origin/main` (`52fc9cd3`) and `origin/dev` (`08cd7869`) moved
the route to React `OperatorConsole`; `R4-001..003` subsequently completed on
`dev`. This route-level progress does not close the remaining R4 page gaps:
`R4-004/005` are in progress and `R4-006..012` remain dependency-gated.

The exact package 6 ZIP, extracted interactive source, this audit, package diff,
and Fleet task pack are published to `dev` by `ODP-OC-R4-014`. Any later visual
completion decision must use that tracked source packet and fresh browser
evidence rather than treating the 2026-07-13 iframe screenshot as current.

## Executive Verdict

At the audited commit, `/operator` did not run a productized management system UI. It rendered the static design prototype in an iframe:

- Route evidence: `apps/web/app/operator/page.tsx` returns `<iframe data-testid="operator-design-frame" src="/operator-design/index.html" />`.
- Browser evidence: only `/operator-design/index.html`, `/operator-design/support.js`, and `/operator-design/oday-map.js` were requested; no `/api/v1/operator/*` calls were observed.
- Existing product gate failed as expected: iframe present, no operator read API proof, no workflow write API proof.

Design parity depends on which design archive is the target:

- Against `Oday Plus 營運管理後台 (3).zip`: visually high parity, because `apps/web/public/operator-design/index.html` is byte-identical to the HTML inside that zip (`md5 c1574f865c956dddd0b0b4ad0529a16d`).
- Against canonical `Oday Plus 營運管理後台 (6).zip` / R4: not aligned. The current app is still the older R3/v3 prototype and misses R4 additions such as the R4 label, Store Ops all-store four-light summary, Growth three-entry create area, Network expansion flow stepper, candidate data gate, and review-decision dialog.
- Against a real management system acceptance standard: not aligned. State is mock/session-only, role control is demo-only, and actions are not API-backed or durable.

Package `(6)` is a provenance refresh of `(5)`: all five extracted files are
byte-identical, so it introduces no new screen or implementation delta beyond
the R4 gaps already listed below.

## Input Artifact Check

The latest user-supplied path was URL-encoded. Percent-decoding it resolves to
the existing local file:

`/home/lupin/oday-plus/Oday Plus 營運管理後台 (6).zip`

Verified and archived facts:

- Original and archived ZIP SHA-256: `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76`.
- ZIP integrity test: passed; five entries, no compressed-data errors.
- Canonical archive: `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/`.
- Stable latest-source pointer: `docs_archive/00_source_zips/operator_console/LATEST.json`.
- Full `(6)` versus `(5)` receipt: `docs/evidence/OPERATOR_CONSOLE_DESIGN_PACKAGE_6_DIFF_2026-07-13.md`.
- All five extracted `(6)` files match `(5)` by SHA-256; design delta count is zero.

This audit now uses archived package `(6)` as its sole latest design source of
truth. The earlier unavailable-artifact blocker is resolved and removed.

## Runtime Evidence

Local app used for audit:

- Next dev server: `http://127.0.0.1:4317/operator`
- Screenshots and DOM summaries: `/tmp/operator-audit/`
- Key screenshots:
  - `/tmp/operator-audit/01-today.png`
  - `/tmp/operator-audit/02-store-ops.png`
  - `/tmp/operator-audit/03-growth-marketing.png`
  - `/tmp/operator-audit/03-growth-segments.png`
  - `/tmp/operator-audit/03-growth-priceops.png`
  - `/tmp/operator-audit/03-growth-new-action-dialog.png`
  - `/tmp/operator-audit/04-network-areas-netrole.png`
  - `/tmp/operator-audit/04-network-radar.png`
  - `/tmp/operator-audit/04-network-candidates.png`
  - `/tmp/operator-audit/04-network-lab.png`
  - `/tmp/operator-audit/04-network-compare.png`
  - `/tmp/operator-audit/04-network-review.png`
  - `/tmp/operator-audit/04-network-rebalance.png`
  - `/tmp/operator-audit/05-govern-auditor-approval.png`
  - `/tmp/operator-audit/05-govern-auditor-decision.png`
  - `/tmp/operator-audit/05-govern-auditor-audit.png`
  - `/tmp/operator-audit/06-dialog-triage.png`

Test results:

- Preview smoke passed: `ODP-OC-PREVIEW-001`.
- Productization gate failed: `ODP-OC-PROD-014`.
- Failure reasons:
  - `/operator still renders the design iframe (operator-design-frame or /operator-design/), so it is preview-only.`
  - `No Operator Console read API proof observed; expected a GET to /api/v1/operator/bootstrap, /today, /issues, or /approvals.`
  - `No API-backed workflow proof observed; expected a POST to an Operator Console workflow endpoint during the gate.`

## Design Version Alignment

### Current App Equals v3

`unzip -p "Oday Plus 營運管理後台 (3).zip" "Oday Plus Operator Console.dc.html" | md5sum`:

`c1574f865c956dddd0b0b4ad0529a16d`

`md5sum apps/web/public/operator-design/index.html`:

`c1574f865c956dddd0b0b4ad0529a16d`

Conclusion: current `/operator` loads the v3 design archive exactly.

### Current App Does Not Equal R4

R4 archive facts:

- R4 main HTML: `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/extracted/Oday Plus Operator Console.dc.html`
- R4 main HTML SHA-256: `65d359f4abaf82b39eb16f67da8e91e7ad1b030628bc15f8f45ce7c18c0e2f48`.
- R4 main HTML md5: `78b65c33fac19dc33ba241e640df5cd1`.
- R4 summary states: `DEMO_STATE_VERSION: oday-plus-r4-20260707`.
- Package `(6)` and `(5)` carry the same R4 HTML and support assets.

R4-only items found in archive but missing in current runtime:

- `OPERATOR CONSOLE · R4` top nav label.
- `Store Ops 全店四燈摘要`.
- `Growth 建立入口`.
- `Network Expansion Flow Stepper`.
- `資料完整度 GATE`.
- `Dialog Review Decision`.
- R4 unified mock IDs/data: `A-100x`, `L-202x`, `CS-100x` centered around `CS-1001 信義松仁`.

## Page-by-Page Audit

| Page / Area | Current Runtime | v3 Design Parity | R4 Parity | Productization |
|---|---|---:|---:|---:|
| Shell / Top Navigation | Header, nav, search, notification, approval chip, role switch render in iframe. Role switch is demo-only. | High | Partial: missing R4 label/state version. | Low: iframe only, no auth/RBAC API. |
| Today 今日工作 | KPI cards, prioritized queue, decisions, risk snapshot, audit feed render. Role-specific Today works. | High | Mostly partial: current data/state remains v3, not R4. | Low: mock/session data only. |
| Store Ops 門市營運 | Issue queue, source/status filters, selected issue detail, evidence fusion, forecast chart, action rail, audit timeline, triage dialog render. | High | Partial: missing R4 all-store four-light summary and quick filter strip. | Low: workflow actions do not call backend. |
| Store Ops Dialogs | Triage dialog verified. HTML contains other workflow dialog labels, but only prototype state is used. | Partial to high | Partial | Low: not durable, not API-backed. |
| Growth 活動與機會 | Marketing role can enter. Activity list, filters, detail rail, conflict checks render. | High | Partial: R4 three-card create entry area missing. | Low: mock actions only. |
| Growth 會員分群 | Segment cards and `建立活動草稿` CTAs render. | High | Partial | Low: no CRM/model integration. |
| Growth PriceOps | PriceOps table, current/suggested price, utilization, revenue, margin risk, rollback conditions render. | High | Partial | Low: no pricing engine or approval API. |
| Growth Draft Dialog | `Dialog Growth Draft` opens from `+ 新增 Growth Action`; fields and conflict warning render. | High | Partial: R4 describes more explicit five-step builder. | Low: session-only draft. |
| Network 找區域 | HeatZone map, lens selector, ranking cards, selected zone detail render under 展店 role. | High | Partial: missing R4 expansion flow stepper above tabs. | Low: mock map/data. |
| Network 物件雷達 | Source compliance cards, listing inbox, source filters, list/map toggle render. | High | Partial: current IDs/data are v3, not R4 unified dataset. | Low: no live connector/API proof. |
| Network 候選點 | Candidate pipeline/detail render with stages and compare actions. | High | Partial: missing R4 data completeness gate. | Low: mock candidate state only. |
| Network SiteScore Lab | Single score view, map, score, revenue path, risk metrics, actions render. | High | Partial: missing R4 expanded risk breakdown/gate behavior. | Low: no real SiteScore service call. |
| Network 比較 | Candidate comparison table and recommendation render. | High | Partial: R4 adds richer best/alternate/avoid CTAs. | Low: mock comparison only. |
| Network 審核 | Review queue/detail render. 展店 role sees decision restrictions. | Partial: review is visible, but decision flow is awkward/incomplete from Network because decision roles cannot directly enter Network through nav. | Low to partial: R4 decision dialog and state sync are missing. | Low: decisions not API-backed. |
| Network 低效重配 | Rebalance candidates, low-efficiency summary, AVM request entry render. | Partial to high for v3 prototype. | Partial | Low: no AVM/NetPlan backend. |
| Govern 核准中心 | Approval queue/detail, reason box, approve/return/reject buttons render under PM/稽核. | High | High for visible v3 scope. | Low: no approval API writes. |
| Govern Decision Log | Existing decision cards with system recommendation, final decision, reason, model/snapshot render. | High | High for visible v3 scope. | Low: no durable decision store. |
| Govern Audit Trail | Category filters and audit rows render. | High | High for visible v3 scope. | Low: no immutable audit persistence. |
| Govern Evidence / SLA / Data Quality / Model / Users | Template/value code exists in HTML, but `gvTabs` exposes only `核准中心`, `Decision Log`, `Audit Trail`; these areas are not reachable by UI. | Missing as reachable screens. | Missing as reachable screens. | Low. |

## Main Gaps

1. Current route is a design preview, not product UI
   `/operator` embeds `/operator-design/index.html`. The React `OperatorConsole` exists in source but is not exported from `features/operator/index.ts` and is not mounted by the route.

2. The app uses v3, not latest R4
   The currently mounted HTML equals `(3).zip`. Canonical `(6).zip` R4 has newer UX additions that are not deployed.

3. No API-backed workflow proof
   Browser traffic did not include operator read/write APIs. Product gate fails for the correct reasons.

4. Some governance sections are present but unreachable
   Evidence Package, SLA, Data Quality, Model, and Users data exist in value builders, but the tab list exposes only three tabs.

5. Network Review role model is inconsistent in the prototype
   The Review screen says ops/gov should decide, but role navigation blocks ops/gov from Network. Governance approval can handle approval decisions, but the Network Review page itself is not a complete decision surface.

## Practical Answer

If the question is "does the current page visually match the v3 design archive?", the answer is yes, because it literally embeds that archive.

If the question is "does the current management system follow the latest R4 design?", the answer is no. The current route mounts v3 while canonical package `(6)` contains R4 and the multiple R4 screen additions listed above.

If the question is "is the management system implemented as a real product according to design?", the answer is also no. It is a high-fidelity prototype preview, not a productized, API-backed, durable management console.

## Recommended Fix Order

1. Use `docs_archive/00_source_zips/operator_console/LATEST.json` and archived package `(6)` as the fixed R4 source of truth.
2. Replace `/operator` iframe with the React `OperatorConsole`, keeping `/operator-design` only as reference.
3. Port the archived R4 additions into React:
   - Store Ops all-store four-light summary.
   - Growth create-entry area / fuller builder.
   - Network flow stepper and candidate data gate.
   - Review decision dialog and synchronized candidate/review states.
4. Expose or remove unreachable Govern sections:
   - Evidence Package, SLA, Data Quality, Model, Users.
5. Bind product workflows to `/api/v1/operator/*` with idempotency/correlation headers.
6. Re-run `ODP-OC-PROD-014` and require it to pass before calling Operator Console productized.
