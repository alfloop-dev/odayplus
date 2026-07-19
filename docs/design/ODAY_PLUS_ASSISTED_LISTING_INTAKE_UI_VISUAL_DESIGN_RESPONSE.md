# ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE

- doc_id: ODP-UXD-003-ADD-002-RESPONSE
- responds_to: ODP-UXD-003-ADD-002 v1.0.0（system design baseline ODP-SD-INTAKE-001 v0.2.1）
- engineering_task: ODP-INTAKE-UX-001
- design_owner: Product Design（本 POC 環境）
- canonical_artifact: **互動原型 `Oday Plus Operator Console.dc.html`（R6 · DEMO_STATE_VERSION oday-plus-r6-20260718）**
  — 本環境不產出 Figma；原型即 canonical design package 的等價物。frame = 原型內可到達的實際狀態。
- updated_at: 2026-07-18

## 1. Route / Screen / Frame index

| Screen ID | 原型位置 | 到達方式 |
|---|---|---|
| UX-SCR-EXP-003 Listing Inbox | Network → 物件雷達 → URL 收件佇列（filter chips＋owner/SLA 欄） | 主導航 |
| UX-SCR-EXP-003A Add From URL | 佇列「＋ 從網址新增物件」modal；Find Areas 區域面板帶入本區 | 送出成功 → intake detail deep link |
| UX-SCR-EXP-003B Processing Detail | durable page `/w/expansion/listings/intake/:id`（頂欄下全頁，非 drawer） | 佇列列點擊／送件後自動導向；離開可返回 |
| UX-SCR-EXP-003C Parsed Data Review | Detail 內「解析資料覆核」（來源值/正規化值/人工修正/有效值/低信心/遮罩） | 修正 → 原因 dialog |
| UX-SCR-EXP-003D Duplicate & Revision Review | Detail 內「比對結果 MATCH REVIEW」side-by-side ＋ 變更摘要（SR 可讀） | IN-3002（REVISION）、IN-3003（POSSIBLE_MATCH） |
| UX-SCR-EXP-003E Assisted Entry | Detail 內補錄表單（IN-3004） | 可離開再返回，輸入保留 |
| UX-SCR-EXP-003F Promotion & SiteScore | Detail 內 CANDIDATE PROMOTION section（receipt/job/replay） | IN-3001 create 後 |

## 2. 六條核心 flow（§7）在原型的走法

1. **NEW**：從網址新增（貼 591 URL）→ 階段模擬 → READY/NEW → 建立新物件 → receipt RCPT-CRT-*。種子 IN-3001。
2. **EXACT_DUPLICATE**：貼既有 canonical URL（如 L-2024 的 591 網址）→ 識別檢查於擷取前攔截 → 開啟既有物件。
3. **REVISION**：IN-3002 → 比對 preview（租金 -7.8%）→ 加入既有物件版本 → **首次寫入回 409 VERSION_CONFLICT（輸入保留，重新整理 v3 後成功）** → RCPT-REV-*。
4. **POSSIBLE_MATCH**：IN-3003 → 一致/矛盾訊號 → 關鍵決策僅展店主管/資料管理員可執行（staff 顯示停用原因）→ 四動作分離、原因必填、絕不自動合併。
5. **ASSISTED_ENTRY_ONLY**：IN-3004 → 不擷取說明 → 人工補錄（必填地址/租金/坪數）→ 比對 → NEW。
6. **Promotion**：IN-3001 建立物件後提出 promotion → 驗證 → 待第二人核准（**提出者見 SELF_REVIEW_DENIED**）→ 展店主管核准（原因＋風險確認）→ CANDIDATE_CREATED（commit 後才顯示 CS-ID＋receipt）→ SCORE_QUEUED → **首次必 SCORE_FAILED（Candidate 仍在）** → 授權重放（同 idempotency key）→ COMPLETED。

補充失敗恢復：IN-3006（驗證牆 429 可重試）、IN-3010（PARSE-SCHEMA-DRIFT → DLQ → 資料管理員 mapping 修正 replay）、IN-3007（AUTH_REQUIRED）、IN-3008（SOURCE_BLOCKED 結案）、IN-3009（CANCELLED terminal）、IN-3005（POLICY_UNKNOWN fail-closed）。

## 3. Canonical state 覆蓋

原型內「狀態矩陣」（收件佇列右上）逐格列出：intake stages ×12（含 CANCELLED；QUARANTINED/FAILED 標示受控可重開）、source policy ×5、match ×5、assignment ×6、SLA ×6（文字＋圖標 ●▲✕∥）、decision ×10、job ×7（attempt/timeout/checkpoint/DLQ/replay）、error/conflict contract ×15（428/409×6/403×3/422×2/429/schema-drift/stale），並標註哪些可在原型實測。

## 4. Role × permission（§5）

六個 intake 角色全數可由右上角色選單切換實測：展店經理＝Expansion staff、**展店主管（新增）**＝manager/second actor、**資料管理員（新增）**＝steward（DLQ replay）、PM／稽核＝Governance reviewer（唯讀＋denial reason）、**個資保護官（新增）**、**受限使用者（新增）**＝唯讀＋租金/押金 FIELD_MASKED（保留結構）。前端隱藏不等於授權 — 唯讀與越權操作均顯示 403 SCOPE_DENIED／REVIEW_REQUIRED 文字原因。

## 5. Component inventory

重用：PageHeader／FilterBar（chips）／Table（佇列）／Modal（003A、修正、決策、promotion）／Timeline／status pill／EmptyState／Toast（僅輕量確認）。
新元件（本次引入，均以既有 tokens 構成）：IntakeStageTimeline（真實階段，無百分比）、FieldLineageRow（source→normalized→corrected→effective＋低信心/遮罩）、ListingCompareTable（▲變更＋SR 摘要）、MatchEvidencePanel（一致/矛盾訊號）、AssignmentSlaSummary、DurableReceiptPanel、MaskedField、PromotionStateRail、JobStatusRow、StateMatrixSheet。無新增色票；狀態色沿用既有 semantic tones。

## 6. Responsive / A11y 決策

Desktop-first；detail 頁尾與狀態矩陣載明 responsive contract：tablet 可送件/狀態/補錄/單純核准；mobile 送件/追蹤/簡單確認，POSSIBLE_MATCH 與欄位比對顯示 desktop-required（保留 deep link 與輸入）。所有狀態文字＋icon/pattern；比對含螢幕閱讀器變更摘要；ESC 不會關閉 detail page（durable）；錯誤含 code/corr/時間/可重試性/下一步且不以 Toast 承載。

## 7. Accepted / Modified / Deferred

- Accepted：§4 non-goals 全數（無爬蟲暗示、無憑證、無 auto-merge、無 auto-promotion、真實階段無百分比）；§8 全部畫面；§9–10 狀態與錯誤契約；§11 視覺原則（neutral base、compact inbox、無新色票）。
- **替代交付決議（正式）**：本專案環境無 Figma；產品方（使用者）於 2026-07-18 確認以「升級互動原型 R6 作為 canonical design package 等價物＋完整 canonical 矩陣逐格呈現＋本 Response 文件完整版」替代 §15 Figma deliverables。frame ↔ 狀態對照由原型 data-screen-label 與狀態矩陣承擔。
- Modified：Figma package → 互動原型＋本文件（依上述決議）。
- 已實作（原 DEFER 轉正）：transfer（handoff note 必填）與 pause（原因＋恢復時間）表單；WORM evidence 列（狀態／purpose binding／保存期／審計提示）；`#intake/:id` hash deep link（重新整理保留 detail）；dialog semantics（role=dialog／aria-modal／aria-label＋表單 aria-label）；mobile desktop-required fallback（<760px 遮罩，保留 deep link）。
- DEFER（POC 不阻塞）：完整 focus trap／focus return（owner: FE，gate: production build）；真實 URL router（hash 模擬）；tablet 專屬 frames（僅契約說明）；WORM evidence 深頁（列層級呈現）。上述 DEFER 未在 UI 宣稱已上線。

## 8. Reviewers

Product／System Design／Frontend／Accessibility：待使用者指派（POC 環境單一設計者）。
