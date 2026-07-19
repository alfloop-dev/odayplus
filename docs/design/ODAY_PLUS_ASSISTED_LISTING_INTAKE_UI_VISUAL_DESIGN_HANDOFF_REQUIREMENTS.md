---
doc_id: ODP-UXD-003-ADD-002
title: ODay Plus Assisted Listing Intake UI and Visual Design Handoff Requirements
version: 1.0.1
status: ready-for-visual-design
document_class: visual-design-handoff
project: ODay Plus
language: zh-TW
owner: Product / Expansion Operations
design_owner: Product Design / UI Visual Design
canonical_design_tool: Claude Design
engineering_task: ODP-INTAKE-UX-001
responds_to: ODP-UXD-003-ADD-001
system_design: ODP-SD-INTAKE-001
system_design_version: 0.2.1
approved_system_design_commit: e644bd0e01a3f9134ee0230490577db4f67b0aa9
approval_review: ODP-SD-INTAKE-REVIEW-005
updated_at: 2026-07-19
---

# ODay Plus Assisted Listing Intake UI and Visual Design Handoff Requirements

## 1. Assignment

本文件是交給 UI／視覺設計團隊的正式設計任務。請設計 ODay Plus
Expansion Workspace 內的 Assisted Listing Intake 完整操作體驗，從 Listing
Inbox 的網址送件入口開始，涵蓋來源政策、解析與人工補件、欄位校正、重複／版本
比對、指派與 SLA、人工決策、Candidate Site promotion、SiteScore job 狀態與完整
audit evidence。

設計團隊負責：

- 畫面資訊層級、版面、元件組合與視覺節奏；
- desktop、tablet、mobile 的 responsive composition；
- 所有狀態、權限、錯誤、衝突與 durable receipt 的視覺表達；
- 繁體中文最終文案與 canonical English state code 的共存方式；
- keyboard、focus、screen-reader、色盲與 reduced-motion 標註；
- Claude Design flow、component variants、互動原型與工程 handoff annotations。

設計團隊不負責重新定義 domain state、權限、API payload、資料擁有權、來源政策或
promotion transaction。這些已由核准的 System Design package 綁定。

## 2. Authority and Conflict Resolution

Product owner 於 2026-07-19 確認：本任務以 **Claude Design 互動原型**作為
canonical visual-design package，不要求 Figma 交付。後續審查不得再以缺少 Figma
作為 finding；應直接驗證 Claude Design source、runnable artifact、screen/state coverage、
responsive、accessibility 與工程 handoff 完整度。

遇到文件不一致時，依下列優先序處理：

1. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE_REVIEW.md`
   的核准範圍與 production gates；
2. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml` 登錄的 system、state、
   authorization、OpenAPI、schema、event、reliability 與 migration contracts；
3. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_DESIGN_REQUIREMENTS.md` 的產品意圖與 non-goals；
4. 本文件的 UI／視覺設計交付要求；
5. `ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md`、`ODAY_PLUS_DESIGN_TOKENS.md` 與
   `ODAY_PLUS_COMPONENT_CONTRACTS.md` 的視覺與元件規則。

不得以目前程式畫面、fixture、mock provider 或舊 Listing Pipeline 狀態覆蓋已核准
契約。System Design 已核准作為 implementation-binding baseline；production rollout
仍受 owner approvals、feature flags、canary、SLO/RPO/RTO 與 runtime evidence gate
約束，設計稿不得把 gated capability 呈現為已上線。

## 3. Product Intent and Experience Outcome

使用者在外部網站發現一筆可能適合展店的物件，主動把網址送進 ODay Plus。系統先
判斷既有身分與來源政策，再依政策進行 retrieval 或要求 assisted entry，接著解析、
正規化、比對並由人處理不確定結果。任何 ambiguous match 與 Candidate Site promotion
都需要明確人工決策。

完成設計後，使用者必須能快速回答：

1. 這筆送件現在在哪個真實處理階段？
2. 系統可以或不可以讀取來源，原因是什麼？
3. 原始值、解析值、正規化值與人工校正值有何差異？
4. 為什麼它被判定為新物件、重複、版本更新或可能相符？
5. 現在由誰負責、何時到期、是否已逾期或升級？
6. 我目前有權做什麼，哪些動作需要第二人審查？
7. 操作成功後留下了哪一份 durable receipt、版本與 audit evidence？

## 4. Non-Goals and Prohibited Implications

- 不設計或暗示持續爬取 591、樂屋或其他外部 listing site。
- 不設計搜尋結果頁 scraping、定期 enumeration 或自動取得外部 listing ID。
- 不要求或顯示 provider 密碼、cookie、bearer token、private API endpoint。
- 不自動合併 `POSSIBLE_MATCH`。
- 不自動把 Listing promotion 成 Candidate Site。
- 不重新設計完整 HeatZone、Candidate Sites、SiteScore 或 AppShell。
- 不用假百分比表示 processing；只能顯示真實 stage 與時間。
- 不以「AI 已替你決定」或「系統保證相符」描述 recommendation／confidence。

## 5. Users and Permission Modes

設計必須提供同一畫面的 role-aware variants，而不是只做全權限版本。

| Role | Primary job | Required design behavior |
|---|---|---|
| Expansion staff | 送件、補資料、校正自己的 intake、提出 promotion | own/assigned scope；不可自行核准 promotion 或 critical identity decision |
| Expansion manager | 指派、比較、決策、核准 promotion | 顯示 second-actor 與 self-review restriction；高風險動作禁 optimistic |
| Data steward | 修正 mapping、處理 parser/source/identity defect、quarantine | source/data scope；merge/split/unmerge 仍需 independent review |
| Governance reviewer | 檢查來源、權限、處理與 audit evidence | read-only；可見 evidence 與 denial reason，不得出現 business mutation action |
| Privacy officer | Restricted evidence、legal hold、restricted export review | purpose-bound；敏感資料與 second-actor requirement 明確可見 |
| Permission-limited user | 只讀或欄位遮罩 | action 隱藏或 disabled with reason；masked field 保留結構，不顯示值 |

Frontend 隱藏動作不等於授權。設計必須能容納後端返回的 denial、masking、workflow
conflict 與 segregation reason code。

## 6. Information Architecture and Routes

| Screen ID | Surface | Required route/deep-link behavior | Primary job |
|---|---|---|---|
| `UX-SCR-EXP-003` | Listing Inbox integration | `/w/expansion/listings`；filters/sort/view/selection 進 URL | 掃描 intake、來源、match、owner、SLA 與待審工作 |
| `UX-SCR-EXP-003A` | Add Listing From URL | Inbox 內短表單 modal；提交成功導向 intake deep link | 驗證 URL、顯示來源辨識與送件 context |
| `UX-SCR-EXP-003B` | Intake Processing Detail | `/w/expansion/listings/intake/:intakeId` | 追蹤真實 stage、來源證據、owner、next action 與 history |
| `UX-SCR-EXP-003C` | Parsed Data Review | Detail 內可編輯 review mode，URL 保留 active section | 比較 parsed／normalized／corrected／effective value |
| `UX-SCR-EXP-003D` | Duplicate and Revision Review | Detail 內 desktop compare mode；可被 task deep link 直接開啟 | 比較 existing listing 與 submission，做 explicit human decision |
| `UX-SCR-EXP-003E` | Assisted Entry Fallback | Detail 內 form mode，可離開再返回 | 在不可 retrieval 時補足必要資料且保留來源 URL |
| `UX-SCR-EXP-003F` | Promotion and SiteScore Status | Detail 內 promotion section／receipt | 提出、審查、執行 promotion 並追蹤 candidate/score job |

Wide Drawer 只用於 Listing Inbox 快速預覽、簡單 claim 或前往 detail；完整 compare、
identity decision、quarantine release 與 promotion review 必須在 durable detail page 完成。

## 7. Required End-to-End Flow

```text
Listing Inbox
-> Add listing from URL
-> URL validation / canonicalization / exact identity check
-> Source policy decision
-> Approved retrieval OR assisted entry OR fail-closed quarantine
-> Retrieval / parsing / normalization
-> Parsed data review and material correction
-> Entity matching
-> NEW / EXACT_DUPLICATE / REVISION / POSSIBLE_MATCH / QUARANTINED
-> Assignment / SLA / independent review where required
-> Create listing / append revision / mark duplicate / quarantine / reject
-> Optional explicit Candidate Site promotion
-> Candidate creation receipt
-> SiteScore job receipt / completion / authorized replay
```

設計 prototype 至少要走通以下六條情境：

1. 新 URL -> approved retrieval -> parsed successfully -> `NEW` -> create Listing。
2. exact URL/source identity -> `EXACT_DUPLICATE`，retrieval 前即導向 existing Listing。
3. 同一物件資料改變 -> `REVISION` -> compare -> append ListingRevision。
4. `POSSIBLE_MATCH` -> independent human review -> create/revise/duplicate/quarantine。
5. `ASSISTED_ENTRY_ONLY` -> manual entry -> correction review -> matching。
6. promotion request -> second-actor approval -> candidate created -> SiteScore queued；另含
   `SCORE_FAILED` 後 authorized replay。

## 8. Screen Requirements

### 8.1 Listing Inbox Integration

保留既有 list/map toggle，不以 intake flow 取代 Listing Inbox。

Page Header primary action：`從網址新增物件`。

Required filters and saved-view dimensions：

- intake method：URL / manual / CSV / approved feed；
- intake stage；match outcome；source；submitted by；owner／assignment；
- needs review；SLA state；last observed；last updated；HeatZone／area；
- masked/restricted-data indicator；quarantined／failed/retryable。

Required table fields：

- Listing / Intake identity；source；intake method；stage；match outcome；
- issue or next action；owner；due time／SLA；submitted by；last observed／updated；
- direct actions：open、claim、review、retry、request correction；
- sensitive commercial/location fields follow field masking contract。

設計 empty、loading、partial/degraded、error、read-only 與 no-results states。Server-side
pagination、stable sort、selected row、drawer 與 filters 必須可在 URL 恢復。

### 8.2 Add Listing From URL

Required content：

- URL input、detected source、original URL、canonical URL when different；
- optional HeatZone／assigned area；submitter、tenant/scope、owner context；
- source-policy expectation 以 operational wording 呈現；
- submit loading state 與防 double submission。

Required variants：invalid URL、unsupported source、exact duplicate before retrieval、
canonical URL differs、permission denied、request in flight、network retry、successful receipt。
成功後使用 intake ID 導向 detail，不用 toast 取代 durable result。

### 8.3 Intake Processing Detail

固定資訊層級：

1. Summary：source、original/canonical URL、submitter、owner、submitted time、scope。
2. Status：current intake stage、assignment/SLA、retryability、freshness。
3. Evidence：snapshot、parser、field confidence、match evidence/contradictions。
4. Recommendation：system match proposal，清楚標示 generated-by-system。
5. Human decision：allowed actions、second actor、reason/risk requirements。
6. Execution/result：listing revision、candidate、SiteScore job、durable receipts。
7. Version/audit：ETag/version、actor、time、reason、before/after、correlation ID。

Processing timeline 顯示真實 stage 與 stage history，不畫假百分比。頁面要能呈現 retry
checkpoint、attempt count、next retry、cancellation、DLQ 與 replay authority。

### 8.4 Assisted Entry and Parsed Data Review

Field groups：Identity、Location、Commercial、Property、Provenance。

每個 field row 必須可同時區分：

- source/parsed value；
- normalized value；
- manually corrected value；
- effective value；
- missing／low-confidence／masked state；
- correction actor、reason、time、snapshot/parser lineage。

identity、address、rent、area 或 match outcome 相關校正必填 reason，並顯示 risk
acknowledgement／independent review requirement。Retryable failure 後使用者輸入不能消失。

`ASSISTED_ENTRY_ONLY` 明確說明「系統不會讀取此來源頁面，請依可用資料補錄」，不得
暗示使用者提供憑證或繞過來源限制。

### 8.5 Duplicate, Revision, and Possible Match Compare

Desktop 採 side-by-side current vs submitted comparison；changed field 以文字、icon/pattern
與 change summary 標記，不只用顏色。比較內容至少包含 source ID、canonical URL、
normalized address、area、floor、listing type、rent/price、status 與 contradictory signals。

Required outcomes and actions：

| Outcome | Required primary behavior |
|---|---|
| `NEW` | `建立新物件`，顯示即將建立的 Listing summary |
| `EXACT_DUPLICATE` | `開啟既有物件`；不得再建立或執行 ambiguous merge |
| `REVISION` | `加入既有物件版本`，preview changed fields |
| `POSSIBLE_MATCH` | `建立新物件`／`加入既有物件版本`／`標記重複`／`送交資料管理員` 明確分開 |
| `QUARANTINED` | 顯示 reason、policy/evidence 與 permitted next action |

`POSSIBLE_MATCH` 絕不 auto-merge。Merge、split、unmerge/reversal 需顯示 before/after graph
plan、lineage impact、reason、risk acknowledgement、proposer/reviewer 與 self-review denial。

### 8.6 Assignment and SLA

在 Inbox 與 Detail 同時呈現：owner、queue、assigned/claimed time、due time、SLA state、
pause／transfer／escalation history。

Canonical assignment states：`UNASSIGNED`、`ASSIGNED`、`CLAIMED`、`TRANSFERRED`、
`ESCALATED`、`COMPLETED`。

Canonical SLA states：`ON_TRACK`、`DUE_SOON`、`OVERDUE`、`BREACHED`、`PAUSED`、
`COMPLETED`。

Transfer 必須有 handoff note；pause 必須顯示 approved reason 與 resume time；逾期／breach
不可只靠紅色。Owner conflict 顯示目前 owner、最新版本與 refresh/retry action。

### 8.7 High-Risk Decision and Durable Receipt

所有 material correction、merge/split/unmerge、quarantine release、promotion、restricted
export/purge/hold 相關畫面遵守：

- system recommendation 與 human decision 視覺分離；
- action 前顯示 affected entities、before/after、risk、reason、required reviewer；
- self-review 時 action unavailable，顯示 `SELF_REVIEW_DENIED`；
- submit 時 loading + submission lock；不得 optimistic update；
- conflict 保留輸入並顯示 current state/version 與 refresh/review action；
- success 顯示 durable decision/receipt ID、actor、time、versions、audit/correlation ID。

### 8.8 Candidate Promotion and SiteScore

Promotion states 不得壓縮為單一 loading：

```text
REQUESTED -> VALIDATING -> APPROVED -> CANDIDATE_CREATING
-> CANDIDATE_CREATED -> SCORE_QUEUED -> COMPLETED
```

另設計 `REJECTED`、`FAILED`、`SCORE_FAILED` 與 authorized replay。Candidate ID 與
SiteScore job ID 只能在 transaction commit 後顯示。`SCORE_FAILED` 時 Candidate 仍存在，
不得在視覺上暗示 candidate 被刪除。Lost response 以同 idempotency key replay 或 decision
lookup 恢復，不建立第二筆 Candidate。

### 8.9 Audit, Evidence, and Sensitive Data

Detail 必須有可掃描的 timeline／audit section，包含 actor、role、timestamp、action、reason、
before/after、snapshot、parser run/version、decision ID、related Listing/Candidate/SiteScore ID、
correlation ID 與 WORM/evidence state。

Masked field 保留 field label 與 layout，顯示 `masked=true` 與 `FIELD_MASKED` 的可理解說明，
不顯示或推測原始值。Sensitive evidence view 需顯示 purpose binding、classification、expiry
與 audit notice。Credential class 永不出現在 UI、export 或 prototype sample data。

## 9. Canonical State Presentation

### 9.1 Intake stages

```text
SUBMITTED
CHECKING_IDENTITY
CHECKING_SOURCE_POLICY
AWAITING_ASSISTED_ENTRY
RETRIEVING
PARSING
MATCHING
NEEDS_REVIEW
READY
QUARANTINED
FAILED
CANCELLED
```

`QUARANTINED` 與 `FAILED` 是受控可重開狀態，不得畫成無條件 terminal。`CANCELLED` 是
terminal。設計稿需保留 English state code，繁中 label 不得取代 canonical code。

### 9.2 Source policy states

| State | UI behavior |
|---|---|
| `APPROVED_RETRIEVAL` | 可繼續 retrieval，顯示 policy version/expiry |
| `ASSISTED_ENTRY_ONLY` | 不 fetch；保留 URL 並提供 manual entry |
| `AUTH_REQUIRED` | 顯示需核准 account access，不要求 raw credentials |
| `SOURCE_BLOCKED` | 停止並顯示 governance reason/next action |
| `POLICY_UNKNOWN` | fail closed，送 governance review |

### 9.3 Decision and job visibility

Decision UI 需容納 `DRAFT`、`PENDING_REVIEW`、`APPROVED`、`REJECTED`、`EXECUTING`、
`EXECUTED`、`FAILED`、`REVERSAL_PENDING`、`REVERSED`、`SUPERSEDED`。

Job UI 至少顯示 queued/running/retrying/succeeded/failed/cancelled/DLQ、attempt、timeout、
checkpoint、next retry 與 replay permission。狀態只用 semantic token，且必須是
文字 + icon/pattern；顏色不是唯一訊號。

## 10. Error, Conflict, and Recovery Contract

每個 error／conflict surface 必須顯示：

- human-readable summary；exact backend code；correlation ID；occurred time；
- retryable/non-retryable；current state/version；affected operation；next action；
- user input preservation；若是 conflict，顯示 server current value/version。

至少設計以下 variants：

- `428 PRECONDITION_REQUIRED`；
- `409 VERSION_CONFLICT`、`IDEMPOTENCY_KEY_REUSED`、`OWNER_CONFLICT`、
  `REVIEW_CONFLICT`、`WORK_INCOMPLETE`、`LEGAL_HOLD_CONFLICT`；
- `403 SELF_REVIEW_DENIED`、`SOURCE_POLICY_DENIED`、scope/ownership denial；
- `422 CORRECTION_INVALID`、`RISK_ACKNOWLEDGEMENT_REQUIRED`；
- retrieval timeout、page removed、authentication wall、bot challenge；
- parser partial/retryable failure/permanent failure；
- stale snapshot、quarantine、job retry exhausted／DLQ。

重大錯誤不可只用 Toast。Toast 只做輕量 confirmation；error summary 與 recovery action 必須
留在 page/section。

## 11. Visual Direction and Component Use

本功能是高頻、資訊密集的營運工具，不是 landing page。設計需遵守：

- neutral base + semantic status/risk accents；不用品牌漸層或裝飾性大色塊；
- Listing Inbox 預設 `compact`，Detail/compare 預設 `comfortable`；
- 資料、證據、差異與 next action 是主角，chrome 退居背景；
- 不做 card-inside-card；page section 不設計成漂浮 card；
- card radius 不超過既有 token；固定 toolbar/table/compare dimensions，避免狀態切換位移；
- icon 使用既有 icon library；icon-only command 必須有 tooltip/accessible name；
- 熟悉的 undo/retry/open/close 動作用 icon，不另造文字膠囊；
- 不新增 intake 專屬色票；狀態色一律使用 semantic tokens；
- 高風險 action 使用明確動詞，不用 `OK`／`確認` 這類含糊 primary label。

優先重用：`PageHeader`、`Toolbar/FilterBar`、`Table`、`Drawer`、`Form`、`Modal`、
`Tabs`、`Timeline`、`DataStatusBadge`、`ApprovalPanel`、`AuditMetadata`、`EvidencePanel`、
`EmptyState`、`Toast`、`Tooltip`。

設計團隊需交付 component reuse/new-component inventory。建議評估的新元件：

- `IntakeStageTimeline`；
- `FieldLineageRow`；
- `ListingCompareTable`；
- `MatchEvidencePanel`；
- `AssignmentSlaSummary`；
- `DurableReceiptPanel`；
- `MaskedField`。

新元件必須列出 variants、states、tokens、responsive、a11y 與既有元件組合關係。

## 12. Responsive Requirements

| Breakpoint | Required capability |
|---|---|
| Desktop `lg+` | 完整 Inbox、side-by-side compare、field correction、identity decision、promotion review、audit |
| Tablet `md` | URL submit、status/detail、assisted entry、unambiguous review、assignment、簡單 approval |
| Mobile `sm` | URL submit、status tracking、simple confirmation、task claim/response、receipt viewing |

Mobile 遇到 `POSSIBLE_MATCH`、merge/split/unmerge、large field compare 或 restricted evidence
review 時，顯示 desktop-required state，保留 deep link 與所有輸入，不嘗試把完整 comparison
壓成不可判讀的卡片堆疊。

最長 URL、地址、reason code、correlation ID 與 English state code 必須在所有 frame 內正常
wrap/truncate with accessible full value，不得溢出或遮住 action。

## 13. Accessibility Requirements

- WCAG 2.2 AA baseline；normal text contrast >= 4.5:1，large text >= 3:1。
- 全部操作可用 keyboard 完成；定義 focus order、focus return、focus trap 與 skip behavior。
- Table 支援 row focus、column header semantics、`aria-sort` 與 screen-reader summary。
- Compare 提供 screen-reader-readable change summary，不只靠 column position 或色彩。
- Status、risk、masking、confidence、SLA 一律文字 + icon/pattern。
- Error summary 可聚焦並連結 field errors；動態 stage/job update 使用適當 live region。
- Modal/Drawer 關閉後焦點回原 trigger；destructive confirmation 不可誤按 Esc 關閉。
- Reduced motion 下移除非必要動畫；processing stage 不使用無限裝飾動畫。
- External link 標明目的地與新視窗行為，且不丟失 intake state。

## 14. Content and Sample Data Requirements

- UI copy 使用繁體中文，canonical state/error code 保留英文原文。
- 日期時間顯示 timezone；相對時間旁提供 absolute timestamp。
- Sample URL 使用 `example.com` 或核准 synthetic provider；不得放真實個資或憑證。
- 使用可信但虛構的台灣地址、租金、面積、樓層與 HeatZone context。
- 不用 `爬蟲成功`、`自動抓取全站`、`AI 已決定`、`100% 相符` 等文案。
- Copy 應說明目前狀態與 next action，不寫 marketing value proposition 或教學式 feature 宣傳。
- `robots.txt` 不得被描述為單獨的 retrieval 授權依據。

## 15. Required Claude Design Deliverables

設計團隊需回傳一個可供工程實作的 canonical Claude Design package。Package 至少包含
Claude Design source、與 source 同步的 runnable standalone artifact，以及以下內容：

1. Cover、scope、source-document links、版本與 design owner。
2. Desktop/mobile end-to-end flow map，含本文件 §7 六條情境。
3. Listing Inbox integration 與 list/map preserved behavior。
4. `UX-SCR-EXP-003A` 至 `003F` 的 desktop final frames。
5. Tablet/mobile responsive frames 與 desktop-required fallback。
6. Intake、source policy、match、assignment/SLA、decision、promotion/job state matrix。
7. Empty/loading/partial/error/permission/masked/stale/quarantined variants。
8. URL validation、field correction、compare、high-risk confirmation、durable receipt prototype。
9. Existing/new component inventory，含 variants、states、tokens 與 component properties。
10. Keyboard/focus、screen-reader、contrast、reduced-motion annotations。
11. Content/copy sheet：labels、helper text、disabled reasons、errors、success receipts。
12. Engineering handoff：measurements、responsive constraints、overflow、sticky regions、URL state、
    testable state/frame mapping。

Screen/state naming：

```text
ODP / Expansion / Assisted Intake / <Screen ID> / <Breakpoint> / <State>
```

每個可到達 screen/state 必須透過 `data-screen-label` 或等價的穩定識別標記所對應的
canonical state、role、permission mode、data classification 與 source requirement section。
不要只以流水號或 `Final Final` 命名。

## 16. Design Response Document

除 Claude Design package 外，請提交：

`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE.md`

Response 至少包含：

- Claude Design source、runnable artifact、版本與 checksum links；
- route/screen/frame index；
- accepted/modified requirement matrix；
- component reuse/new-component decisions；
- responsive/a11y decisions；
- unresolved dependency 與 fail-closed design；
- final copy source；
- Product、System Design、Frontend、Accessibility reviewers。

任何 `DEFER` 必須指出 interim UI、owner、follow-up task 與不允許宣稱完成的 release gate。

## 17. Visual Design Acceptance Criteria

- 六條核心 flow 從 Inbox 到 durable receipt 均可完整操作與返回。
- `NEW`、`EXACT_DUPLICATE`、`REVISION`、`POSSIBLE_MATCH`、`QUARANTINED` 明確不同。
- 使用者能同時判讀 parsed、normalized、corrected、effective、confidence 與 masking。
- 所有 canonical states、source policy、assignment/SLA、decision 與 promotion states 均有設計。
- 高風險動作呈現 affected entities、before/after、reason、risk、second actor 與 durable receipt。
- `POSSIBLE_MATCH` 不會被視覺上引導為自動合併；promotion 不會被視覺上表達為自動完成。
- 權限、self-review、field masking、purpose binding 與 read-only variants 可逐項驗收。
- Errors 含 code、correlation ID、time、retryability、current version 與 next action。
- Desktop、tablet、mobile 不重疊、不溢出；complex review 的 desktop-required fallback 清楚。
- 所有狀態不用顏色作為唯一訊號，keyboard/focus/screen-reader annotations 完整。
- 設計遵守既有 AppShell、tokens 與 component contracts，未發明新的 domain state 或色票。
- Frontend 可以實作 `ODP-INTAKE-UX-001`，不需要自行猜測 layout state、copy、permission、
  responsive、error、receipt 或 accessibility behavior。

## 18. Source Documents

Normative product/system sources：

- `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_DESIGN_REQUIREMENTS.md`
- `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md`
- `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE_REVIEW.md`
- `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_REVIEW_MANIFEST.yaml`
- `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_STATE_CONTRACTS.md`
- `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_AUTHORIZATION_MATRIX.md`
- `docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_RELIABILITY_PRIVACY_CONTRACT.md`
- `docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_MIGRATION_ROLLOUT_RUNBOOK.md`
- `docs/api/openapi/ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml` and registered overlays

Design-system and workflow sources：

- `docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md`
- `docs/design/ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md`
- `docs/design/ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md`
- `docs/design/ODAY_PLUS_DESIGN_TOKENS.md`
- `docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md`
