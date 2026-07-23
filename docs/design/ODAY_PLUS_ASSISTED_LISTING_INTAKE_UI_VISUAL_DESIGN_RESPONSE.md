---
doc_id: ODP-UXD-003-ADD-002-RESPONSE
title: ODay Plus Assisted Listing Intake UI Visual Design Response
version: 1.1.0
status: implemented-pending-independent-functional-acceptance
canonical_design_tool: Claude Design
canonical_package: operator-console-r7-20260720-package-10
responds_to: ODP-UXD-003-ADD-002
engineering_task: ODP-INTAKE-UX-001
functional_trace: ODP-INTAKE-FUNCTIONAL-TRACE-001
updated_at: 2026-07-23
---

# ODay Plus Assisted Listing Intake UI Visual Design Response

- Canonical artifact: 互動原型 **Oday Plus Operator Console.dc.html
  （R7 · DEMO_STATE_VERSION oday-plus-r7-20260720）**。本專案以 Claude
  Design 互動原型為 canonical visual-design package（產品方 2026-07-18
  決議，Package 10 沿用；不提交 Figma）。
- Runnable artifact: **oday-plus-console-r7-standalone.html**，由同一 R7
  source 打包，見下方 checksum。
- Production reconciliation: 本文件 2026-07-23 更新為正式程式實作對照；
  全功能驗收以
  `ODAY_PLUS_ASSISTED_LISTING_INTAKE_FUNCTIONAL_REQUIREMENT_TRACE_2026-07-23.md`
  的 `FTR-001` 至 `FTR-197` 為準。
- Package update: 2026-07-20 rev 2 修正 heal 版本閘；`__healed` 升為 R7
  並加入 intake 種子指紋檢查，舊 hot-reload session 會重灌 R7 種子並
  保留使用者新建收件 `id >= 3011`。

## Checksums（SHA-256）

- source（Oday Plus Operator Console.dc.html）:
  `cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d`
- standalone（oday-plus-console-r7-standalone.html）:
  `1aefb8068faa39666599ceeafe74ba24f1ddc8abd57ba9a6513a724abaee7d0f`
- Package ZIP：本環境的 ZIP 由平台下載時即時產生，無法預先計算 zip-level checksum；以上兩個 file-level SHA-256 為版本一致性依據（zip 內容即此兩檔＋docs/）。
- 一致性 probe（source＝standalone 皆通過）：R7 版本字串 ✓ · 無「系統排程／每日掃描／掃物件／來源掃描」✓ · EXACT_DUPLICATE 短路徑 ✓ · canonical codes ✓ · seq ink:3011 ✓ · data-screen-label ✓。

## VDR 逐項回覆

- **VDR-001（自動掃描暗示）：已修正。** 來源卡改「核准來源（URL 送件）／最近收件」；591＝SRC-591 v4（效期 2026-12-31，僅限使用者送件之單頁）、樂屋＝SRC-RKY v2（效期 2026-10-31）；僅合作 feed 顯示「核准 feed（推送）· SRC-FEED v3 · 效期 2027-03-31」；Today 卡、Govern 資料源列、追蹤／搜尋條件 toast 全部改寫（「新收件將優先比對此區」等）。證據：Network 物件雷達來源卡＋grep 無「掃描／排程」字樣（probe ✓）。
- **VDR-003（Tablet／Mobile）：已修正。** 移除 min-width:1280/1240 固定值（root min-width 依 viewport 動態為 0）；斷點 <760 mobile、760–1159 tablet、≥1160 desktop。Mobile：URL 送件、佇列（列可點）、狀態追蹤、認領、簡單確認、receipt 檢視全可操作；解析欄位改堆疊 lineage 卡；REVISION 比對改變更欄位摘要列表；僅 POSSIBLE_MATCH side-by-side 顯示 inline DESKTOP_REQUIRED 卡（保留 deep link 與輸入，無全畫面遮罩）。Tablet：雷達改二欄、stepper 3 欄、詳情全功能。驗收尺寸 1440／1024／390 無頁面級水平溢出（intake 流程）。
- **VDR-004（Accessibility）：已修正。** 全部 dialog：role=dialog＋aria-modal＋aria-label＋開啟 initial focus＋Tab focus trap＋關閉 focus return；Esc 明確行為（決策確認 dialog 不受 Esc 關閉並提示，其餘 Esc 關閉）。所有 intake 輸入含 aria-label；「×」按鈕 aria-label=關閉對話框（×17）；「修正」按鈕帶欄位名 accessible name。佇列 role=list＋列 tabIndex=0＋Enter/Space 開啟＋逐列 aria-label 摘要＋排序說明（無互動排序，aria-sort 以文字聲明 descending）。動態階段有 aria-live=polite live region；toast 容器 role=status。錯誤訊息 role=alert＋tabIndex=-1 自動聚焦並指名關聯欄位。html lang=zh-Hant、document.title、prefers-reduced-motion 覆蓋、focus-visible outline。對比：主要灰階文字 token 全域調深（#8A93A8→#6E7891 ≈4.6:1、#98A1B3→#6B7590 ≈4.9:1、#B6BDCC→#737D97 ≈4.5:1），body 文字 #1C2333／#3A4362／#5A6478 均 ≥7:1（WCAG 2.2 AA）。
- **VDR-005（Durable route）：已修正。** 送件成功（含 EXACT_DUPLICATE 攔截）立即寫入 location.hash=#intake/IN-xxxx；hashchange 監聽支援 browser back/forward；reload／direct open 由 hash 還原同一筆 detail；Inbox filters（inkF）、選取（selIntake）、detail 開啟狀態（inkView）持久化於 session state，重載可恢復；receipt／compare 區塊隨 intake 資料還原。非僅 component state。
- **VDR-006（交付決議）：已接受並記錄。** Claude Design
  互動原型是 canonical package（產品方決議 2026-07-18，Package 10
  確認沿用）。Package 10 已經
  `ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`
  審查為 `APPROVED_WITH_CONDITIONS`；`VDC-001` 至 `VDC-005` 已在正式
  程式實作。2026-07-23 functional closure 的 exact commit 與 197 條
  requirement disposition 仍須由未參與開發的 Acceptance Fleet 審查，
  在該結果產生前不得宣稱功能完成。
- **VDR-007（Transfer／Pause／WORM）：已修正。** Transfer：必填 target＋handoff note、無 resume time、成功後顯示新 owner／version bump／receipt（RCPT-ASG-xxxx-T，寫入 Audit 與時間軸）；IN-3003（ESCALATED）首次轉交觸發 409 OWNER_CONFLICT — 顯示目前 owner／版本、輸入保留、重新整理後可重送。Pause：必填核准原因＋resume time（顯示且可編輯，非隱藏預設），成功後 SLA=PAUSED＋歷程＋receipt（RCPT-ASG-xxxx-P）。WORM Evidence 面板 11 列：WORM state／purpose binding／classification／access expiry（IN-3008=PURPOSE_EXPIRED）／retention+legal hold（IN-3005=LEGAL_HOLD）／masking（受限角色=FIELD_MASKED）／export（受限=EXPORT_DENIED 403；privacy=purpose-bound）／verification（快照雜湊）／actor·role·time／snapshot·parser lineage／evidence receipt＋correlation ID。
- **VDR-009（版本一致）：已修正。** standalone 於本輪由 R7 source 重新打包（先修正殘留文案再 build）；上方兩組 SHA-256 與 probe 表為 checksum evidence；舊 R6 standalone 已刪除，package 不含兩套 UI。

## P0 可實測驗收（runnable artifact）

1. 佇列送 591 URL → hash 立即為 #intake/IN-3011+ → reload 仍在同筆 → back 返回雷達。2. 貼 L-2024 既有 URL → EXACT_DUPLICATE，階段僅 3 步。3. IN-3002 加入版本 → 409 → 重新整理 → 成功＋RCPT-REV。4. IN-3003 轉交 → 409 OWNER_CONFLICT → 重新整理 → RCPT-ASG。5. 切受限使用者 → FIELD_MASKED＋EXPORT_DENIED。6. 390px 寬：送件／追蹤／認領／receipt 可操作，POSSIBLE_MATCH 比對顯示 DESKTOP_REQUIRED 卡。7. Tab 進入任一 dialog → focus 受困於 dialog，關閉後回原按鈕；決策 dialog 按 Esc 不關閉。

## Production Route, Screen, and State Index

| Screen | Production route / surface | Production owner | Durable behavior |
|---|---|---|---|
| `UX-SCR-EXP-003` Listing Inbox | `/w/expansion/listings` | `ListingInboxIntakeView.tsx`, `AssistedIntakeSection.tsx`, `AssistedIntakeQueuePanel.tsx`, `IntakeInboxMap.tsx` | Query, sort, view, selected intake, and active section are URL-restorable. |
| `UX-SCR-EXP-003A` Add URL | Inbox modal | `AddListingFromUrlDialog.tsx` | Submission uses an idempotency key; success routes to the durable intake ID; exact duplicate opens the existing Listing. |
| `UX-SCR-EXP-003B` Processing Detail | `/w/expansion/listings/intake/:intakeId` | `IntakeProcessingDetail.tsx`, `IntakeStageTimeline.tsx` | Direct open, reload, back, forward, retry, and controlled reopen retain authoritative state. |
| `UX-SCR-EXP-003C` Parsed Review | Detail `review` section | `ParsedDataReview.tsx`, `FieldLineageRow.tsx`, `IntakeFieldFixDialog.tsx` | Parsed, normalized, corrected, effective, confidence, masking, actor, reason, and lineage remain distinct. |
| `UX-SCR-EXP-003D` Match Review | Detail `compare` section | `ListingCompareTable.tsx`, `MatchEvidencePanel.tsx`, `IdentityDecisionPanel.tsx`, `IdentityGraphPlan.tsx` | New, revision, duplicate, possible match, merge, split, unmerge, and reversal use explicit human decisions and durable receipts. |
| `UX-SCR-EXP-003E` Assisted Entry | Detail `assisted-entry` section | `AssistedEntryForm.tsx`, `useCorrectionDraft.ts` | No retrieval is attempted; draft data survives validation, retryable errors, navigation, and reload. |
| `UX-SCR-EXP-003F` Promotion | Detail `promotion` section | `PromotionReviewPanel.tsx`, `SiteScoreJobStatus.tsx` | Proposal, second-actor approval, Candidate creation, score job, failure, and replay expose committed receipts. |
| Existing Listing | `/w/expansion/listings/:listingId` | `ExistingListingDetailPage.tsx` | Exact duplicate and revision actions open a real Listing route backed by the Listing detail API. |

Canonical intake, source-policy, match, assignment/SLA, decision, promotion,
and job states are rendered from the approved system contract. UI labels may
add Traditional Chinese text but never replace the canonical English code.

## Requirement Disposition

| Requirement group | Disposition | Production binding |
|---|---|---|
| Assignment, intent, non-goals | `ACCEPT` | User-submitted URL/manual/CSV/approved-feed intake only; no scheduled discovery or credential entry. |
| Roles and permission modes | `ACCEPT` | Six role-aware modes use authoritative backend capabilities; read-only and masked structures remain visible without exposing values. |
| Routes and six canonical flows | `ACCEPT` | All routes above are mounted in the production App Router and exercised through the real API runtime. |
| Inbox, submit, detail, review, compare | `ACCEPT` | Server query, durable navigation, correction lineage, match evidence, conflicts, and receipts are integrated. |
| Assignment/SLA and high-risk decisions | `MODIFY` | Package 10 layout is retained; `VDC-001` corrected Transfer and Pause behavior overrides the prototype defect. |
| Promotion and SiteScore | `ACCEPT` | Candidate is visible only after commit; `SCORE_FAILED` retains Candidate and supports authorized replay. |
| Responsive and accessibility | `MODIFY` | Production AppShell fixes `VDC-002` and `VDC-003`; complex mobile work uses a durable desktop-required fallback. |
| Deliverable format | `MODIFY` | Product-approved Claude Design package replaces Figma; archive, checksums, runnable HTML, review, and production mapping are mandatory. |
| Error/recovery and final acceptance | `ACCEPT` | Page-level recovery retains code, time, correlation, retryability, current version, next action, and user input. |

No requirement is deferred. Production rollout remains a separate operational
gate and is not equivalent to functional implementation acceptance.

### Clause-Level Requirement Disposition Register

This register dispositions every clause in
`ODAY_PLUS_ASSISTED_LISTING_INTAKE_FUNCTIONAL_REQUIREMENT_TRACE_2026-07-23.md`.
`ACCEPT` and `MODIFY` mean the requirement is included in the implementation
scope; they do not mean its verification result is already `PASS`. Exact
implementation status and evidence remain in
`docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/FUNCTIONAL_REQUIREMENT_EVIDENCE_MATRIX.{md,json}`.

| FTR range | Requirement area | Disposition | Binding clarification |
|---|---|---|---|
| `FTR-001`–`FTR-020` | Scope, experience questions, prohibited behavior | `ACCEPT` | Claude Design Package 10 is canonical; fixtures and inferred receipts are not production evidence. |
| `FTR-021`–`FTR-028` | Six role-aware modes | `ACCEPT` | Backend actor facts, scope, masking, and second-actor decisions remain authoritative. |
| `FTR-029`–`FTR-038` | Routes and navigation | `ACCEPT` | Durable App Router pages and URL-restorable Inbox state replace prototype hash-only behavior. |
| `FTR-039`–`FTR-044` | Six end-to-end flows | `ACCEPT` | Completion requires persisted Listing, revision, identity, Candidate, job, and receipt readback. |
| `FTR-045`–`FTR-060` | Inbox and Add URL | `ACCEPT` | Server query/cursor/saved-view contracts and exact-duplicate Listing navigation are binding. |
| `FTR-061`–`FTR-077` | Detail, assisted entry, field lineage | `ACCEPT` | Production detail uses authoritative history and correction lineage; drafts are not authoritative values. |
| `FTR-078`–`FTR-100` | Compare, assignment/SLA, high-risk decisions | `MODIFY` | Package composition is retained with VDC-001 conflict, assignment, non-optimistic, and durable-receipt corrections. |
| `FTR-101`–`FTR-113` | Promotion, SiteScore, audit, evidence | `ACCEPT` | Candidate persists across score failure; all identifiers and evidence facts are server-issued. |
| `FTR-114`–`FTR-130` | State and recovery matrices | `ACCEPT` | Canonical English codes and complete recovery envelopes remain visible. |
| `FTR-131`–`FTR-137` | Production UI composition | `MODIFY` | Existing AppShell, tokens, and shared primitives override prototype-only styling or component behavior. |
| `FTR-138`–`FTR-153` | Responsive and accessibility | `MODIFY` | VDC-002/VDC-003 production corrections and durable mobile fallback are binding. |
| `FTR-154`–`FTR-160` | Content and sample data | `ACCEPT` | zh-TW copy coexists with canonical codes; no crawl, credential, or guaranteed-match implication. |
| `FTR-161`–`FTR-173` | Claude Design deliverables | `MODIFY` | Product-approved Claude Design archive, runnable HTML, and checksums replace Figma delivery. |
| `FTR-174`–`FTR-181` | Design response | `MODIFY` | This response, its source links, component decisions, reviewers, and this clause register are canonical. |
| `FTR-182`–`FTR-197` | Functional closure | `ACCEPT` | These remain gated on exact-commit evidence and independent Acceptance Fleet disposition. |

## Component Reuse and New Components

Existing shared application primitives remain authoritative for page headers,
toolbars, tables, forms, dialogs, tabs, timelines, status badges, approval
panels, audit metadata, empty states, toasts, and tooltips. Assisted Intake
adds domain composition rather than a parallel design system:

| Domain component | Purpose and required variants |
|---|---|
| `IntakeStageTimeline` | All canonical intake stages; current/history, retryable, replayable, controlled reopen, terminal cancellation, and read-only variants. |
| `FieldLineageRow` / `ParsedDataReview` | Parsed, normalized, corrected, effective, missing, low-confidence, masked, material-correction, and read-only variants. |
| `ListingCompareTable` | Desktop side-by-side, changed/contradictory summary, revision, possible match, masked value, and mobile desktop-required variants. |
| `MatchEvidencePanel` | Positive and contradictory signals, confidence, source identity, address, commercial and property evidence. |
| `AssignmentSlaSummary` | Owner, queue, due time, assignment/SLA states, claim, transfer, pause, escalation, completion, and conflict variants. |
| `DurableReceiptPanel` | Decision, Listing/revision, assignment, Candidate, score job, evidence, version, actor, time, audit, and correlation receipts. |
| `EvidencePanel` / `StructuredAuditTimeline` | Snapshot/parser lineage, before/after, purpose, classification, WORM state, legal hold, and masked/export variants. |
| `MaskedField` behavior (reused composition) | `FieldLineageRow`, compare cells, Inbox cells, and `EvidencePanel` retain the field label and structure, render `FIELD_MASKED`, and never infer or expose the value; a separate wrapper is intentionally not duplicated. |
| `IdentityDecisionBoundary` / `IdentityGraphPlan` | Proposal, independent review, self-review denial, graph before/after, redirect, lineage, execution, and reversal variants. |
| `PromotionReviewPanel` / `SiteScoreJobStatus` | Request through completion, rejected, failed, score failed, retrying, cancelled, DLQ, and replay variants. |
| `IntakeErrorRecovery` | 403, 409, 422, 428, retrieval/parser failures, stale snapshot, job exhaustion, and reload-safe recovery variants. |

All domain components use existing semantic tokens. They do not introduce an
intake-only status palette. Icon-only controls require an accessible name and
tooltip; status and risk always use text plus icon/pattern.

## Responsive and Accessibility Decisions

| Mode | Functional scope |
|---|---|
| Desktop `lg+` | Complete Inbox, compare, correction, identity graph, high-risk review, promotion, score job, evidence, and audit. |
| Tablet `md` | Submission, queue/detail, assisted entry, unambiguous review, assignment, and simple approval. |
| Mobile `sm` | Submission, status, claim/simple confirmation, recovery, and receipt viewing. Complex compare, graph operations, promotion review, and restricted evidence preserve a deep link and show `DESKTOP_REQUIRED`. |

Production acceptance requires:

- no page-level horizontal overflow at 390, 1024, and 1440 px;
- long URLs, addresses, canonical codes, reasons, and correlation IDs to wrap
  or expose an accessible full value;
- keyboard completion, deterministic focus order, dialog focus trap and return,
  error-summary focus, table semantics, change summaries, and live regions;
- `lang="zh-Hant"`, localized document title, coherent landmarks, WCAG 2.2 AA
  contrast, reduced-motion behavior, and zero serious/critical axe findings on
  the core routes and states.

These are functional acceptance assertions, not visual annotations only.

## Engineering Measurements and Test Mapping

| Surface | Production constraint | Acceptance mapping |
|---|---|---|
| AppShell | Header/sidebar/main use the shared layout tokens; main track is `minmax(0, 1fr)`. At `<=1024px` the sidebar collapses; at `<=720px` the shell is one column. | 390/1024/1440 scroll-width and AppShell route assertions. |
| Inbox filters | Four columns by default, two at `<=960px`, one at `<=560px`; controls keep their stable height and wrap labels. | Filter matrix plus no-overlap screenshots. |
| Inbox table | Semantic table is contained by its own horizontal scroller; minimum data width is `112rem`; header cells remain sticky at the top of the table viewport. Page-level horizontal scrolling is forbidden. | Table semantics, `aria-sort`, keyboard row/action, and document scroll-width assertions. |
| Map | Main map plus `15rem`–`22rem` unlocated rail; one-column stack at `<=960px`; canvas is `min(62vh, 36rem)` with `22rem` minimum height. | Authoritative coordinate/unlocated browser assertions at all viewports. |
| Standard dialog | `560px` target width, `94vw` maximum, `92vh` maximum height; wide detail uses `880px`/`96vw`/`94vh`. | Focus trap/return, busy dismissal lock, long-string and overflow assertions. |
| Detail page | Full-width unframed sections; tabs and active section serialize in the URL; stage/evidence/receipt changes cannot resize the global shell. | Direct open, reload, back/forward, polling and layout-shift assertions. |
| Desktop compare | Fixed field column plus two value columns; changed/contradictory rows keep stable tracks and an accessible change summary. | 1440 side-by-side and screen-reader summary assertions. |
| Mobile complex work | The durable route and draft remain intact; the unavailable work region becomes a `DESKTOP_REQUIRED` state rather than a compressed comparison. | 390 deep-link, draft preservation, reload, and no-overflow assertions. |

Test ownership is split by behavior, not by screenshots:

- API/runtime integration proves queued processing, persisted transition
  history, revisions, identity effects, assignment/SLA, promotion/jobs,
  idempotency, errors, evidence, and reload-stable receipts.
- Browser E2E proves production AppShell reachability, six complete flows,
  roles, navigation, interaction, responsive behavior, and user-visible
  persisted readback.
- Component tests prove field/status variants, control presence/absence,
  keyboard semantics, focus, draft preservation, and non-fabrication.
- Contract tests prove generated client, OpenAPI, state, persistence and event
  compatibility.
- The independent Acceptance Fleet maps those results to every `FTR-001`
  through `FTR-197`; passing a smaller curated scenario set cannot close the
  feature.

## Content Authority

Final interface copy is Traditional Chinese with canonical English state/error
codes retained. System Design owns state names, reason codes, permissions,
timestamps, versions, policy facts, and receipt fields. Package 10 owns visual
hierarchy and operational phrasing where it does not conflict with System
Design. Production copy must not say that the system crawls a provider, that
`robots.txt` grants access, that AI made a final decision, or that a match is
guaranteed.

Samples use synthetic Taiwan locations and `example.com` or an approved
synthetic provider. Credentials and real personal data are prohibited from
prototype, test, UI, and export samples.

Final production copy inventory:

| Copy family | Binding form / source |
|---|---|
| Entry and navigation | `從網址新增物件`, `開啟`, `認領`, `覆核`, `重試`, `要求補正`; route labels remain `UX-SCR-EXP-003` through `003F`. |
| Match decisions | `建立新物件`, `加入既有物件版本`, `標記重複`, `送交資料管理員`; merge/split/unmerge/reversal use those exact explicit verbs and never generic `確認`. |
| Assignment/SLA | `認領`, `轉交`, `暫停 SLA`, `恢復 SLA`, `升級`, `完成`; canonical assignment/SLA code is rendered beside the Chinese label. |
| Promotion | `提出晉升`, `核准晉升`, `拒絕晉升`, `執行晉升`, `重播 SiteScore`; Candidate and score job IDs appear only from committed receipts. |
| State labels | Chinese labels are owned by `intakeTypes.ts`, `IntakeStageTimeline.tsx`, `AssignmentSlaSummary.tsx`, `PromotionReviewPanel.tsx`, and `SiteScoreJobStatus.tsx`; each rendered label retains the canonical English code. |
| Errors | `<中文摘要> (<HTTP status> <canonical code>)`, correlation ID, occurred time with timezone, retryability, current state/version, operation, preserved input, and explicit next action. |
| Source policy | `EvidencePanel.tsx` renders the canonical policy code, version, expiry, reason, and operational next action; it never asks for credentials or calls the operation a crawl. |
| Masking | Field structure and label remain; value is replaced with an understandable `FIELD_MASKED` explanation without inference. |
| Receipts | Receipt/entity/audit/correlation IDs and timestamps use server text exactly; the UI never creates a display fallback that looks authoritative. |

Copy changes outside this inventory require Product review and a corresponding
FTR acceptance update; translation may clarify a fact but cannot alter its
state, permission, or outcome.

## Dependencies, Review, and Closure

| Dependency / reviewer | Current record |
|---|---|
| Product | Claude Design Package 10 selected as canonical; Figma not required. |
| System Design | `ODP-SD-INTAKE-001` v0.2.1 approved baseline; UI cannot redefine its states or mutations. |
| Product Platform / visual review | Package 10 `APPROVED_WITH_CONDITIONS`; `VDC-001` through `VDC-005` are production acceptance gates. |
| Frontend / Accessibility / QA | Historical child-task approvals and runtime evidence are recorded under `docs/evidence/completion/ODP-INTAKE-UX-001/`; the 2026-07-23 composed functional closure still requires exact-commit independent re-acceptance. |
| Functional Acceptance Fleet | `PENDING`; must disposition every `FTR-001` through `FTR-197` against the pushed integration commit. |

If the independent acceptance result finds any unmet requirement, this response
remains `implemented-pending-independent-functional-acceptance`; no partial
result may be relabeled as complete. The only valid functional closure is a
single exact-commit evidence package with every FTR row `PASS` or an explicitly
approved `NOT_APPLICABLE`.

## Engineering Handoff Index

- Complete requirement trace:
  `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_FUNCTIONAL_REQUIREMENT_TRACE_2026-07-23.md`
- Functional audit:
  `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_FUNCTIONAL_COMPLETENESS_AUDIT_2026-07-23.md`
- Execution tasks:
  `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_FUNCTIONAL_CLOSURE_EXECUTION_TASKS_2026-07-23.md`
- Visual review:
  `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`
- System state and authorization authority:
  `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml`
- Production evidence root:
  `docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/`

The production route, API, worker, persistence effect, conflict path, reload
behavior, browser assertion, and artifact evidence for each function are
recorded by the FTR trace and the final Acceptance Fleet report.
