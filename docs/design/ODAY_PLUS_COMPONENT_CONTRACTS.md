---
doc_id: ODP-R0-COMPONENT-CONTRACTS
title: "ODay Plus Component Contracts"
version: 0.1.0
status: draft
document_class: design-system
project: ODay Plus
language: zh-TW
updated_at: 2026-06-26
owner: "Product Design / Frontend"
approvers: "Product Lead / Frontend Lead"
content_format: markdown
source_documents:
  - ODP-UX-01_INFORMATION_ARCHITECTURE_AND_NAVIGATION.md
  - ODP-UX-02_DESIGN_SYSTEM.md
  - ODP-UX-04_MAP_AND_DATA_VISUALIZATION_SPECIFICATION.md
  - ODP-UX-05_FRONTEND_TECHNICAL_DESIGN.md
related_documents:
  - docs/design/ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md
  - docs/design/ODAY_PLUS_DESIGN_TOKENS.md
---

# ODay Plus Component Contracts

## 1. Purpose & How to Read

本文件是 ODay Plus 前端的**唯一元件契約來源**。每個元件給出：Purpose / Props / States / Variants / Accessibility / Do-&-Don't / Related。工程 worker 實作或擴充任何元件時，**以本文件為準**；缺欄位先擴充契約，再寫碼。

規則（normative）：

- 元件**只引用** `ODAY_PLUS_DESIGN_TOKENS.md` 的 semantic token，不硬編值。
- Domain 元件的**必備欄位不得刪減**（來源於 `ODP-UX-02 §6`、`ODP-UX-04`）。
- 高風險元件（approval / override / price / netplan / valuation / model release / rollback / data-quality override）**不得 optimistic update**，且須觸發後端 Audit（`ODP-UX-05 §14.4`）。
- Props 以 TypeScript 形態描述語意；實際命名以 `packages/ui` 與 `packages/ui-domain` 實作為準，但語意不得偏離。

元件分三層，對應 `ODP-UX-05 §9`：

```text
Layout 元件     §3   shell / sidebar / toolbar / drawer / page-header
Core 元件       §4   button / badge / card / table / form / modal / tabs / timeline /
                     toast / tooltip / command-palette / empty-state / data-status-badge /
                     model-version-badge / approval-panel / audit-metadata / alert-chip /
                     evidence-panel
Domain 元件     §5   HeatZoneScoreCard / CandidateSiteCard / SiteScoreReportSummary /
                     ForecastBandChart / FourLightBadge / RootCauseEvidenceCard /
                     InterventionTimeline / PricingPlanComparison / AdLiftReportCard /
                     ValuationRangeChart / NetPlanScenarioCard / ModelReleaseCard /
                     DecisionAuditTimeline
```

通用 prop 慣例：`density?: 'comfortable'|'compact'|'presentation'`、`className?`、`data-testid?`、`loading?`、`error?: ApiError`、`emptyState?`。所有資料型元件接受 `dataQuality?: { status: DataStatus; snapshotTime: string; sources: string[]; warnings: string[] }`。

---

## 2. Shared Types

```ts
type DataStatus   = 'FRESH'|'STALE'|'PARTIAL'|'MISSING'|'LOW_CONFIDENCE'|'FAILED_QA'|'BLOCKED';
type JobStatus    = 'QUEUED'|'RUNNING'|'SUCCEEDED'|'FAILED'|'CANCELLED'|'PARTIAL'|'RETRYING'|'EXPIRED';
type DecisionStatus = 'DRAFT'|'SYSTEM_RECOMMENDED'|'PENDING_REVIEW'|'APPROVED'|'REJECTED'
                    |'OVERRIDDEN'|'EXECUTED'|'OBSERVING'|'OUTCOME_READY'|'CLOSED';
type ModelStatus  = 'EXPERIMENTAL'|'CANDIDATE'|'CHALLENGER'|'CHAMPION'|'SHADOW'|'CANARY'
                    |'PRODUCTION'|'DEPRECATED'|'ROLLED_BACK'|'BLOCKED';
type FourLight    = 'GREEN'|'YELLOW'|'ORANGE'|'RED';
type RiskLevel    = 'low'|'medium'|'high'|'critical';

type Interval = { p10: number; p50: number; p90: number; unit?: string };
type Confidence = { level: 'high'|'medium'|'low'; reasons: string[] };

type ApiError = {
  code: string; message: string; correlation_id: string;
  retryable: boolean; details?: unknown; field_errors?: { field: string; message: string }[];
};

type AuditMeta = {
  actor: string; timestamp: string; reason?: string;
  modelVersion?: string; policyVersion?: string;
  featureSnapshotTime?: string; before?: unknown; after?: unknown;
};
```

---

## 3. Layout Components

### 3.1 AppShell

- **Purpose.** 固定全站骨架：Global Header + Sidebar + Main（Page Header / Filter Bar / Content / 可選 Right Drawer）+ overlay 層。架構見 `ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md §4.1`。
- **Props.** `header: ReactNode`、`sidebar: ReactNode`、`children`、`drawer?: ReactNode`、`commandPalette?: ReactNode`、`environment: 'dev'|'staging'|'production'`。
- **States.** sidebar collapsed / expanded（存 local UI state）；drawer open / closed；offline / degraded（部分服務不可用時切 degraded mode）。
- **A11y.** landmark roles（`banner`/`navigation`/`main`/`complementary`）；skip-to-content；焦點順序 header → sidebar → main。
- **Do.** 讓 content 區是主角；sidebar 收合不重繪地圖。
- **Don't.** 不在 shell 注入頁面業務邏輯；不改 header 高度（用 `layout.header-height`）。

### 3.2 GlobalHeader

- **Purpose.** logo、Workspace Switcher、Global Search、Task Center icon（待辦數）、Notification icon（新警示/核准/失敗 Job）、Environment Badge、User Menu。
- **Props.** `workspaces: WorkspaceRef[]`、`activeWorkspace`、`taskCount: number`、`notificationCount: number`、`user: UserRef`、`onSearch`、`environment`。
- **A11y.** search 有 label 與 `Cmd/Ctrl+K` 可達；env badge 非裝飾，須有文字。
- **Don't.** env badge 不只靠顏色區分（dev/staging/production 須有文字）。

### 3.3 Sidebar

- **Purpose.** 隨 workspace 改變的導覽。第一層工作區主要任務、第二層模組頁面；當頁高亮；無權限項目不顯示；可讀不可寫項目顯示唯讀標記。
- **Props.** `items: NavItem[]`（`NavItem` 含 `label`/`href`/`icon`/`permission?`/`readOnly?`/`children?`）、`collapsed`、`onToggle`、`activeHref`。
- **States.** collapsed（寬 `layout.sidebar-collapsed`）/ expanded（`layout.sidebar-width`）；item active / readonly / disabled。
- **A11y.** `nav` landmark；鍵盤可達；active 以 `aria-current="page"`。
- **Don't.** 無權限項目**不渲染**（不是 disabled）；唯讀項目不顯示操作按鈕。

### 3.4 Toolbar / FilterBar

- **Purpose.** 工作頁的篩選與批次操作列：filters、saved views、column visibility、batch actions、export、density 切換。
- **Props.** `filters: FilterSpec[]`、`savedViews?`、`actions?: ActionSpec[]`（含 `permission?`）、`onExport?`、`density`、`onDensityChange`。
- **States.** filter active count badge；批次選取數；export in-progress。
- **A11y.** filter 控制有 label；批次列為 `region` 並宣告選取數。
- **Do.** filter / sort / page / selected tab / date range 與 URL 同步（`ODP-UX-05 §7.3`）。
- **Don't.** export 高敏感資料不可省略二次確認 + 理由 + Audit + watermark（`ODP-UX-04 §17.3`）。

### 3.5 PageHeader

- **Purpose.** 每個工作頁頂部：Title、Subtitle/Summary（一句話重點）、Status Badge、Primary Action、Secondary Actions、Breadcrumb、Last Updated。
- **Props.** `title`、`summary?`、`status?: BadgeSpec`、`primaryAction?`、`secondaryActions?`、`breadcrumb: BreadcrumbItem[]`、`lastUpdated?`。
- **A11y.** Title 為 `h1`；breadcrumb 為 `nav` + ordered list。
- **Don't.** Detail 頁不可省略 breadcrumb 與 deep link。

### 3.6 Drawer

- **Purpose.** 列表中的快速查看與次要操作（listing preview、task detail、alert preview、model version detail、data quality event detail）。
- **Props.** `open`、`onClose`、`title`、`children`、`width?: 'default'|'wide'`、`onPrev?`、`onNext?`、`deepLinkHref?`。
- **States.** open / closed；可切上一筆/下一筆；保留列表狀態。
- **A11y.** focus trap；Esc 關閉；開啟時把焦點移入、關閉時還原。
- **Don't.** 不放需要完整審查的大型模型報告（那用 Page）。

---

## 4. Core Components

### 4.1 Button

- **Variants.** `primary`、`secondary`、`tertiary`、`danger`、`warning`、`success`、`ghost`、`link`。
- **Props.** `variant`、`size?: 'sm'|'md'|'lg'`、`loading?`、`disabled?`、`disabledReason?`、`icon?`、`onClick`、`type`。
- **States.** default / hover / active / focus（`color.border.focus`）/ disabled / loading。
- **Rules.** 高風險動作用 `danger` 並需二次確認；核准類按鈕文字明確（例：`核准此調價方案`，不可只寫 `OK`）；disabled 須有 tooltip 說明原因（`disabledReason`）；loading 防重複提交。
- **A11y.** `aria-disabled` + tooltip；loading 時 `aria-busy`。

### 4.2 Badge / Chip

- **Types.** `status`、`priority`、`role`、`model-version`、`data-quality`、`confidence`、`sla`、`permission`。
- **Props.** `type`、`label: string`（**必填**，不可只靠顏色）、`tone?`（映射 status/risk/model token）、`icon?`、`pattern?`（色盲用）。
- **A11y.** 文字 + icon/pattern；tone 只是輔助。
- **Don't.** 不出無文字 badge；不自創 tone。

### 4.3 Card

- **Purpose.** ODay Plus 主要資訊承載元件。
- **Card types.** Summary / Decision / Evidence / KPI / Risk / Model / DataQuality / Task / Store / CandidateSite / Valuation / Scenario。
- **Props.** `title`、`status?`、`actions?`、`children`、`elevation?: 'card'|'none'`、`dataQuality?`。
- **Decision Card 必備區塊.** Decision Title、System Recommendation、Human Decision Status、Evidence Summary、Risk/Confidence、Required Approval、Primary Action、Audit Metadata（缺一不可）。
- **A11y.** title 為 heading；卡片可聚焦時提供 label。
- **Don't.** 不在卡片內硬編顏色；風險不只靠卡片邊框色。

### 4.4 Table

- **Purpose.** 高密度列表（Listing Inbox、Task List、Audit Log…）。
- **Capabilities.** sorting、filtering、column visibility、column pinning、row selection、batch action、inline status、export、saved view、server-side pagination、row detail drawer。
- **Props.** `columns: ColumnSpec[]`、`data`、`density`、`sort`、`onSortChange`、`pagination: { server: true; page; pageSize; total }`、`selection?`、`onRowOpen?`（→ drawer）、`maskedFields?: string[]`。
- **必備欄位模式（列表類）.** `entity_name`、`status`、`priority/score`、`owner`、`updated_at`、`primary_action`。
- **敏感欄位.** 交易金額、加盟主個資、會員資料、精細成本、估值底價**預設遮罩**；依後端 field permission 顯示。
- **A11y.** sticky header、column resize、keyboard navigation、row focus、screen reader summary；排序狀態以 `aria-sort`。
- **Perf.** 虛擬化 + server-side pagination（`ODP-UX-05 §16`）。
- **Don't.** 不前端假設敏感欄位可見；不一次渲染超量列。

### 4.5 Form（含 Approval / Override）

- **Patterns.** Simple / Wizard / Review / Approval / BulkImport / Filter / Policy。
- **Props.** `schema: ZodSchema`、`onSubmit`、`affectedEntities: EntityRef[]`（**所有提交須顯示即將影響的實體**）、`previewBeforeSubmit?: boolean`、`fieldErrors?`。
- **Rules.** 高風險表單 preview-before-submit；所有人工 override 需 reason；驗證顯示欄位級錯誤 + 整體摘要；失敗保留輸入。
- **Approval form schema（固定）.**
  ```ts
  z.object({ decision: z.enum(['APPROVE','REJECT','REQUEST_REVISION']),
             reason: z.string().min(10), riskAcknowledged: z.boolean(),
             attachments: z.array(fileSchema).optional() })
  ```
- **Override form schema（固定）.**
  ```ts
  z.object({ overrideDecision: z.string(), originalRecommendation: z.string(),
             reason: z.string().min(20), riskAcknowledged: z.literal(true) })
  ```
- **A11y.** error 關聯欄位（`aria-describedby`）；summary 可被 screen reader 讀取。
- **Don't.** approval/override **不得 optimistic update**；成功須顯示 `decision_id` / `job_id` 並觸發後端 Audit。

### 4.6 Modal

- **Purpose.** 僅用於確認、短表單、危險操作、不可中斷提示。
- **Props.** `open`、`onClose`、`title`、`children`、`primaryAction`、`destructive?`、`requireConfirmText?: string`。
- **A11y.** focus trap、Esc（非破壞性才可 Esc 關）、`role="dialog"` + `aria-modal`。
- **Don't.** 禁止裝大型模型報告或複雜表格（用 Page / Drawer）。

### 4.7 Tabs

- **Purpose.** 同一實體的不同資訊面向：Summary / Evidence / History / Decision / Execution / Outcome / Audit。
- **Props.** `tabs: TabSpec[]`、`active`、`onChange`（與 URL `selected tab` 同步）。
- **Don't.** 不用 Tabs 切換不同工作流程。

### 4.8 Timeline

- **Purpose.** 流程歷程（開店、警示處理、干預觀察窗、價格發布/rollback、AVM、NetPlan、Model Release）。
- **Node 必含.** `timestamp`、`actor`、`event_type`、`status`、`description`、`related_artifact`。
- **A11y.** ordered list 語意；節點可鍵盤聚焦並 deep link。

### 4.9 Toast

- **Purpose.** 輕量回饋（儲存成功、Job 建立、匯出開始、指派完成）。
- **Props.** `tone`、`message`、`action?`、`duration?`。
- **A11y.** `role="status"`/`aria-live="polite"`。
- **Don't.** 重大錯誤不只用 Toast，必須有 inline error 或 error page。

### 4.10 Tooltip

- **Props.** `content`、`trigger`、`delay?`。
- **A11y.** 鍵盤可觸發；不可把唯一關鍵資訊只放在 tooltip（風險狀態須有可見文字）。

### 4.11 CommandPalette

- **Purpose.** `Cmd/Ctrl+K`：搜尋頁面/實體、建立任務、跳轉最近瀏覽、執行有權限的快速動作。
- **Props.** `commands: CommandSpec[]`（含 `permission?`）、`recent`、`onSelect`。
- **z-index.** `z.command-palette`（最上層）。
- **Don't.** 不列出無權限動作。

### 4.12 EmptyState

- **Props（固定）.** `title`、`description`、`nextActions: ActionSpec[]`、`docLink?`。
- **Rule.** 不只顯示「沒有資料」，必須提供下一步（`ODP-UX-01 §14.1`）。

### 4.13 DataStatusBadge

- **Purpose.** 視覺化/卡片右上角資料品質徽章。
- **Props.** `status: DataStatus`、`snapshotTime`、`ingestedAt?`、`sources: string[]`、`qualityChecks?`、`knownLimitations?`。
- **Behavior.** 點擊展開 source / snapshot_time / ingested_at / quality_checks / known_limitations。
- **Tones.** FRESH→green、STALE→yellow、PARTIAL→orange、LOW_CONFIDENCE→orange、FAILED_QA→red、MISSING/BLOCKED→gray/red。文字 + icon 必備。

### 4.14 ModelVersionBadge

- **Props.** `modelId`、`version`、`stage: ModelStatus`。
- **Style.** purple 家族（`color.model.*`）；version 以 mono；hover 顯示 stage 與 release time。

### 4.15 ApprovalPanel

- **Purpose.** 統一的人工核准面板，內嵌 §4.5 approval form。
- **Props.** `decisionStatus: DecisionStatus`、`recommendation: { text; modelVersion; policyVersion; generatedAt; requiresApproval: boolean }`、`onSubmit`、`audit: AuditMeta`、`disabledReason?`。
- **Rules.** 分離 system recommendation 與 human decision；提交不 optimistic；提交後觸發後端 Audit 並顯示 decision_id。
- **A11y.** 決策按鈕文字明確；reason 必填驗證可被 screen reader 讀取。

### 4.16 AuditMetadata

- **Purpose.** Detail 頁 Version/Audit 區塊的標準呈現。
- **Props.** `meta: AuditMeta`（actor / timestamp / reason / modelVersion / policyVersion / featureSnapshotTime / before / after）。
- **Rule.** 任何決策頁都需呈現 feature snapshot time、model version、policy version、actor、decision time、reason、override reason、outcome time。

### 4.17 AlertChip

- **Purpose.** 緊湊的警示/狀態片段，用於列表 inline、地圖 tooltip、卡片角標（含四燈與資料品質縮影）。
- **Props.** `tone: 'green'|'yellow'|'orange'|'red'|'gray'|'blue'|'purple'`、`label: string`（**必填**）、`icon`、`pattern?`、`onClick?`（→ 來源 detail）、`severity?: RiskLevel`。
- **A11y.** 顏色 + 文字 + icon/pattern；可鍵盤聚焦並導向來源。
- **Don't.** 不閃爍；不無文字；不作為唯一風險訊號。

### 4.18 EvidencePanel

- **Purpose.** 統一呈現「可被人理解的證據」，對應資訊層級第 3 層（Evidence）。
- **Props.** `positiveFactors: Factor[]`、`negativeFactors: Factor[]`、`comparables?: Comparable[]`、`trend?`、`confidence: Confidence`、`limitations: string[]`、`dataQuality?`。
- **Rule.** 證據需含正向/負向因子、comparable、trend、confidence 與資料限制；confidence 附原因，不只一個顏色。
- **A11y.** 因子清單為 list；圖示證據附文字。

---

## 5. Domain Components

Domain 元件的**必備欄位來自 `ODP-UX-02 §6` 與 `ODP-UX-04`，不得刪減**。所有預測/估值元件必須顯示不確定性（P10/P50/P90 + confidence + data freshness + model version）。

### 5.1 HeatZoneScoreCard

必備欄位：`heatZoneId`、行政區/商圈、`h3Resolution`、`heatZoneScore`、`priorityRank`、`unmetDemandScore`、`formatFitScore`（ODay G2 Fit）、`cannibalizationRisk`、`rentFeasibility`、`listingAvailability`、`confidence`、`lastScoredAt`。Props 另含 `onOpen?`（→ HeatZone Detail Drawer）。

### 5.2 CandidateSiteCard

必備欄位：`candidateSiteId`、`address`、`geocodeConfidence`、`rent`、`area`、`frontage`、`floor`、`parkingOrTemporaryStop`、`feasibilityFlags`、`heatZone`、`listingSource`、`status`（`RAW|PARSED|GEOCODED|DUPLICATE|FAILED_HARD_RULE|CANDIDATE|SCORED|REJECTED`）。敏感欄位依權限遮罩。

### 5.3 SiteScoreReportSummary

必備欄位：`recommendation`（`GO|WAIT|REJECT|INVESTIGATE`）、`m1/m3/m6/m12` 各自 `Interval`（P10/P50/P90）、`paybackPeriod: Interval`、`rentReasonableness`、`cannibalizationRisk`、`comparableStores`、`keyPositiveFactors`、`keyNegativeFactors`、`modelVersion`、`featureSnapshotTime`。
Rules：不可只顯示 P50；低 confidence 顯 warning；過期顯 stale。內嵌 EvidencePanel + ApprovalPanel（送審/核准/退回/補件）。

### 5.4 ForecastBandChart

呈現：`actual`、`forecastP50`、P10–P90 band、`siteScoreBaseline`、intervention markers、anomaly markers。
Controls：`horizon: 4w|8w|12w|24w`、`metric: revenue|gross_margin|transactions|utilization`、`granularity: daily|weekly`、`showSiteScoreBaseline`、`showInterventions`、`showWeather`。
Props 走 `ChartProps<TData>`（`data/loading/error/emptyState/height/onPointClick/showLegend/exportable`）+ `dataQuality`。
A11y：附資料表替代；tooltip 含值/單位/時間/來源/模型版本/不確定性。

### 5.5 FourLightBadge

`light: FourLight`（GREEN/YELLOW/ORANGE/RED）。
Rules：badge 帶文字（不只顏色）；hover 顯示觸發條件；click 進入 Alert Evidence；色盲模式以 icon/pattern 區分。Tone 綁 `color.status.green/yellow/orange/red`。

### 5.6 RootCauseEvidenceCard

必備欄位：`causeCandidate`、`evidenceStrength`、`supportingSignals`、`contradictingSignals`、`dataConfidence`、`recommendedNextCheck`。Cause categories 取自 `ODP-UX-04 §7.4`（Revenue Residual / Store-age Ramp / Seasonality / Equipment / Cost Unit / CX / Price / Ad / Promotion / Competitor / External Shock）。

### 5.7 InterventionTimeline

節點（固定序）：`Triggered → Eligibility checked → Action built → Conflict checked → Approved → Executed → Observation started → Outcome collected → Effect evaluated → Closed`。每節點走 §4.8 Timeline node 必備欄位。另呈現 Eligibility / Conflict / Approval / Execution / Observation Window / Outcome / Evidence Level 狀態。

### 5.8 PricingPlanComparison

欄位：`plan`、`priceChange`、`expectedDemand`、`expectedRevenue`、`expectedGrossMargin`、`risk`、`constraintStatus`（hard constraint violations 必明顯）、`rollbackPlan`、`approvalStatus`。
Rules：現行價與候選價同時呈現；不支援自動執行，只支援人工核准；hard constraint 違反明顯標示。

### 5.9 AdLiftReportCard

欄位：`campaign`、`treatmentStores`、`controlStores`、`preTrendStatus`、`incrementalRevenue`、`incrementalGrossMargin`、`iromi`、`evidenceLevel`、`continueStopRecommendation`。
Rules：無對照組不得宣稱因果；pre-trend failed 顯警示；重疊干預顯 contamination。

### 5.10 ValuationRangeChart

呈現：Fair Value `Interval`（P10/P50/P90）、Reserve price marker、Asking price marker、comparable transaction markers（若有）。另支援 lens 比較（Income / Asset / Market / blended）。欄位另含 `liquidityScore`、`dataRoomCompleteness`、`financeApprovalStatus`。敏感（reserve/asking）依權限遮罩 + 匯出限制。

### 5.11 NetPlanScenarioCard

欄位：`scenarioName`、`objectiveValue`、`OPEN/KEEP/IMPROVE/MOVE/EXIT count`、`budgetUsage`、`expectedGM`、`risk`、`bindingConstraints`、`solverStatus`、`alternativePlanAvailable`、`approvalStatus`。
Rules：solver 無可行解時呈現 Infeasibility Diagnosis（violated_constraint / affected_stores / required_relaxation / business_impact / suggested_action）；UI 不自動放寬限制；大型 solver 不顯假進度百分比。

### 5.12 ModelReleaseCard

欄位：`modelId`、`version`、`championOrChallenger`、`metricSummary`、`segmentRegression`、`dataQualityStatus`、`driftStatus`、`releaseStage`（ModelStatus）、`rollbackTarget`、`approvalStatus`。release/rollback 須觸發後端 Audit。

### 5.13 DecisionAuditTimeline

固定節點：`Prediction generated → Recommendation generated → Human review requested → Human decision submitted → Execution started → Outcome observed → Feedback written to label registry`。每個 Decision Detail 顯示；可匯出 Evidence（decision_id / entity / model_version / feature_snapshot_time / actor / decision_time / execution_status / outcome_status / audit_status），匯出記 Audit。

---

## 6. Cross-Cutting Contracts

### 6.1 Loading / Error / Empty（每個資料型元件三態必備）

- **Loading.** API → skeleton；大型報告 → Job progress；Solver → queue/running/elapsed；地圖 → layer loading。大型 Job 不顯假百分比。
- **Error.** 顯示錯誤摘要 / 錯誤代碼 / 是否可重試 / 建議下一步 / `correlation_id` / 發生時間。禁止只顯示 `Something went wrong`。degraded mode：地圖失敗但列表可用、圖表失敗但表格可用、模型報告失敗但歷史可讀。
- **Empty.** 走 §4.12 EmptyState（含 next actions）。

### 6.2 Permissions

所有可操作元件接受 `permission?` 與 `scope?`；無權限不渲染操作（不是 disabled），必要時以唯讀摘要呈現。最終權限由後端判斷（`ODP-UX-05 §5`）。

### 6.3 High-risk actions（不得 optimistic、必觸發 Audit）

`approval`、`override`、`export`、`permission change`、`model release`、`rollback`、`data quality override`、`price approval`、`netplan approval`、`valuation approval`。

### 6.4 Testing hooks

每個元件提供 `data-testid`；core 與 domain 元件納入 Storybook + 視覺回歸（light/dark/high-contrast）。關鍵 flow 走 Playwright E2E（`ODP-UX-05 §17`）。

---

## 7. 驗收條件

- 涵蓋 shell、sidebar、toolbar、table、map panel（透過 ForecastBandChart/ValuationRangeChart/NetPlanScenarioCard 與 maps 套件對接）、chart panel、card、drawer、modal、approval form、evidence panel、alert chip、audit trail，以及全部 13 個 domain 元件。
- 高風險決策元件內建 Audit 與 Approval 表達，且不得 optimistic update。
- 預測/估值元件可呈現不確定性（P10/P50/P90 + confidence + data freshness + model version）。
- 每個元件有 Purpose / Props / States / Variants / A11y / Do-Don't / Related，並只引用 `ODAY_PLUS_DESIGN_TOKENS.md` 的 semantic token。
- Domain 元件必備欄位與 `ODP-UX-02 §6`、`ODP-UX-04` 對齊，不刪減。
- 工程 worker 可據此直接建立 `packages/ui`、`packages/ui-domain` 與對應測試。
