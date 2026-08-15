---
title: Development Plan Gap Execution Tasks
date: 2026-07-30
program_id: ODP-PLAN-GAP-CLOSEOUT-2026-07-30
source_audit: docs/evidence/DEVELOPMENT_PLAN_IMPLEMENTATION_GAP_MATRIX_2026-07-30.md
source_plan: Google Drive 1RH1XOd7_3VEUIdSEwnNeSXDg379gwZJROvj8EsuAxhU
status: registered-for-supervisor-execution
release_claim: no-go-until-final-gate-audit
governance_addendum: ODP-PLAN-LEDGER-NETPLAN-HUMAN-GATE-001
---

# Development Plan Gap Execution Tasks

## 1. 執行原則

本 ledger 將原始完整 WBS／RTM 對照盤點轉成 supervisor／autoworker
可執行工作。權威缺口定義以
`DEVELOPMENT_PLAN_IMPLEMENTATION_GAP_MATRIX_2026-07-30.md` 為準。
該矩陣已逐條登錄 84 個不重複 RTM 項目；本 ledger 的 Wave A–E
是缺口閉環工作，不是用較少的 task 數量取代 84 項 coverage。
下表共列出 **26 個治理 task**（含已完成並歸檔的 solver task、本文件
本身的 archive task，以及 2026-07-30 re-audit 補登的 NetPlan ledger
correction 與 Human/Ops baseline approval gate）。新增 task 不改變 84 個
RTM coverage rows；它修正的是原 ledger 把技術比較器與真實管理核准混在
同一 task 的治理缺口。執行中的即時狀態與後續 owner/reviewer 調整仍以
`ai-status.json` 與 task archive 為準。

所有 task 必須遵守以下規則：

1. 從 `origin/dev` 建立 `task/<TASK-ID>`，以 task-scoped PR 合併回 `dev`。
2. owner 與 reviewer 不得相同；只有 reviewer 可核准，只有 owner 可在合併後
   將 task 標記 `done`。
3. 驗收命令、輸出、artifact、commit SHA、PR 與 merge SHA 都必須留證。
4. 不得以 fixture、synthetic data、mock provider 或本機 static check 冒充
   production proof。
5. 需要真實標籤、角色簽核、財務／法務決策、credential 或 production
   access 時，必須 fail closed 並交給 `Human/Ops`，不得由 AI 自行代簽。
6. `ODP-PLAN-FINAL-GATE-AUDIT-001` 通過前，產品 release claim 維持
   `NO-GO`。

## 2. 執行波次

| Wave | Task | Gap | Owner | Reviewer | Depends on |
|---|---|---|---|---|---|
| A | `ODP-PLAN-GAP-ARCHIVE-001` | 84-item WBS/RTM archive | CodexCoordinator | Claude | — |
| A | `ODP-PLAN-GATE-REGISTRY-001` | P0-001 | Claude | Antigravity2 | — |
| A | `ODP-PLAN-CANONICAL-SHELL-LIVE-001` | P0-003 | Codex | Codex2 | — |
| A | `ODP-PLAN-SOLVER-RUNTIME-COMPAT-001` | P0-007 | Antigravity2 | Codex2 | — |
| A | `ODP-PLAN-OSS-LICENSE-GATE-001` | P1-007 | Antigravity5 | Claude | — |
| A | `ODP-PLAN-OBSERVABILITY-LIVE-001` | P1-008 | Antigravity6 | Codex2 | — |
| A | `ODP-PLAN-DEFERRED-OSS-ADR-001` | P2 | Antigravity7 | Claude | — |
| A | `ODP-PLAN-LEDGER-NETPLAN-HUMAN-GATE-001` | P1-006 governance correction | CodexCoordinator | Codex | — |
| B | `ODP-PLAN-ACCEPTANCE-REAL-EXEC-001` | P0-002 | Codex2 | Claude | `ODP-PLAN-GATE-REGISTRY-001` |
| B | `ODP-PLAN-HEATZONE-OUTCOME-001` | P1-001 | Antigravity3 | Codex2 | — |
| B | `ODP-PLAN-SITESCORE-OUTCOME-001` | P1-002 | Antigravity4 | Claude | — |
| B | `ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001` | P1-002 | Antigravity4 | Claude | `ODP-PLAN-SITESCORE-OUTCOME-001` |
| B | `ODP-PLAN-AVM-OUTCOME-001` | P1-003 | Antigravity5 | Codex2 | `ODP-PLAN-OSS-LICENSE-GATE-001` |
| B | `ODP-PLAN-NETPLAN-ACCEPTANCE-001` | P1-006 technical gate | Codex2 | Codex | `ODP-PLAN-SOLVER-RUNTIME-COMPAT-001` |
| B | `ODP-PLAN-ENGINEERING-HARDENING-001` | P2 | Antigravity7 | Codex2 | `ODP-PLAN-DEFERRED-OSS-ADR-001` |
| C | `ODP-PLAN-HEATZONE-LABEL-BACKFILL-001` | P1-001 data gate | Human/Ops | Codex2 | `ODP-PLAN-HEATZONE-OUTCOME-001` |
| C | `ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001` | P1-002 data gate | Human/Ops | Claude | `ODP-PLAN-SITESCORE-OUTCOME-001` |
| C | `ODP-PLAN-AVM-OUTCOME-BACKFILL-001` | P1-003 data gate | Human/Ops | Codex2 | `ODP-PLAN-AVM-OUTCOME-001` |
| C | `ODP-PLAN-OSS-LEGAL-POLICY-001` | P1-007 legal gate | Human/Ops | Claude | — |
| C | `ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001` | P1-006 business gate | Human/Ops | Codex | `ODP-PLAN-LEDGER-NETPLAN-HUMAN-GATE-001` |
| C | `ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001` | P0-004 | Claude3 | Codex2 | existing Forecast inventory/backfill/model-registry tasks |
| C | `ODP-PLAN-FORECAST-BUSINESS-001` | P1-004 | Antigravity3 | Claude | `ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001` |
| C | `ODP-PLAN-PRICE-ADLIFT-PILOT-001` | P1-005 | Antigravity6 | Claude | `ODP-PLAN-OBSERVABILITY-LIVE-001` |
| D | `ODP-PLAN-LIVE-STAGING-PROOF-001` | P0-006 | Antigravity | Codex2 | shell, acceptance, forecast release, existing GCP/deploy tasks |
| D | `ODP-PLAN-UAT-SIGNOFF-001` | P0-005 | Claude | Human/Ops | all module acceptance tasks + `ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001` |
| E | `ODP-PLAN-FINAL-GATE-AUDIT-001` | final | Codex2 | Human/Ops | all prior tasks + `ODP-PLAN-LEDGER-NETPLAN-HUMAN-GATE-001` + `ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001` |

## 3. Task contracts

### ODP-PLAN-GAP-ARCHIVE-001

- Scope: 將 Google Drive 原始規劃第 69–81 章逐條對照為 84-item
  WBS/RTM gap matrix，並保存本 execution ledger、coverage 重算方法、
  evidence path 與 `NO-GO` release claim。
- Acceptance:
  matrix 可重算為 `rows=84 unique=84`，Stage 分布為
  `12/12/10/9/11/12/11/7`；matrix 與 ledger 經獨立 reviewer
  精確 head 核准、CI 通過並合併至 `dev`。未合併前 final gate
  不得完成。

### ODP-PLAN-GATE-REGISTRY-001

- Scope: 建立 Gate 0–6 machine-readable registry、schema、owner、日期、
  evidence URI、exact release SHA 與 fail-closed validator。
- Writable: `docs/evidence/**`, `delivery_toolchain/e2e/**`, `tests/e2e/**`.
- Acceptance:
  registry 可驗證每個 Gate；缺 evidence、owner、SHA、日期或未知狀態時
  validator 非零退出；現況不得被誤判為全通過。

### ODP-PLAN-ACCEPTANCE-REAL-EXEC-001

- Scope: 移除五個已刪除 spec 的 stale acceptance references，將 release gate
  由 static string presence 改成 executable scenario/result validation。
- Writable: `tests/e2e/**`, `delivery_toolchain/e2e/**`, acceptance evidence docs.
- Acceptance:
  不引用不存在檔案；16 個 canonical spec／107 tests inventory 一致；
  缺 execution receipt 時 release gate fail closed。

### ODP-PLAN-CANONICAL-SHELL-LIVE-001

- Scope: 在 Package 10 canonical runtime 補齊 assignment/SLA、
  notifications、admin/settings 與 franchisee live repository wiring。
- Writable: `apps/api/app/routes/operator.py`, related application/domain
  modules, canonical web surfaces, focused tests.
- Acceptance:
  production mode 不再對規劃內能力回 503；RBAC、audit、durable receipt、
  error recovery 與 canonical E2E 通過；不得復活已淘汰 routes。

### ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001

- Scope: 對接既有 Forecast authoritative-history、model registry 與 deploy
  tasks，完成 dataset hash、model card、metrics、MLflow alias、shadow/canary、
  rollback receipt。
- Acceptance:
  只接受 authoritative data；exact dataset/model/image/release SHA 可追溯；
  alias readback、metrics threshold、canary watch 與 rollback drill 皆有 receipt。

### ODP-PLAN-UAT-SIGNOFF-001

- Class: `human_gate`.
- Scope: 執行 Admin/Ops/Expansion/Analyst/Finance/Franchisee 角色 UAT，
  登錄 defect 與正式 sign-off。
- Acceptance:
  規劃涉及角色均簽核；P0/P1 defect 為 0 或有具名風險接受、owner 與期限；
  AI 不得代簽。

### ODP-PLAN-LIVE-STAGING-PROOF-001

- Scope: 收集 exact release SHA 的 Cloud Run、Cloud SQL/PostGIS、OIDC、
  provider、Pub/Sub/DLQ、model alias、audit chain、backup/rollback live proof。
- Acceptance:
  每項為 requestable production/staging receipt；mock/local/fixture 證據被
  validator 拒絕；watch window 無未處理 P0/P1。

### ODP-PLAN-SOLVER-RUNTIME-COMPAT-001

- Scope: 修正 OR-Tools/CVXPY/HiGHS native ABI/load-order 衝突。
- Writable: dependency locks, `models/shared_ml/oss_capabilities.py`, solver
  runtime boundary, focused tests and deployment smoke.
- Acceptance:
  同 process 雙向 import order 與 OR-Tools/CVXPY 最小 solve 都通過；若採
  process isolation，contract、timeout、failure propagation 與 health probe
  有測試；capability endpoint 不再僅用 `find_spec` 誤報。

### ODP-PLAN-HEATZONE-OUTCOME-001

- Scope: 建立 HeatZone authoritative label inventory、benchmark 與 Gate 1
  receipt；資料不足時維持 governed-disabled 並產生 human/data handback。
- Acceptance:
  `>=200` labels、優於人口排序、Top-K 現勘率改善，或明確 fail-closed
  blocker；不得補 synthetic labels。

### ODP-PLAN-SITESCORE-OUTCOME-001

- Scope: 建立 opening outcome M6/M12 inventory、coverage/calibration
  benchmark、model card 與 Gate 2 receipt。
- Acceptance:
  `>=200` 成熟 labels 且 M6/M12 與 coverage threshold 通過，否則
  governed-disabled 並產生具體 backfill owner/SQL/receipt。

### ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001

- Scope: 將 SiteScore PG16/live inventory query 綁定可稽核的 prediction
  source、model version 與 dataset lineage；修正 outcome 已存在但 query
  未選 prediction 欄位、因此永遠無法進入 `ACTIVE` 的缺口。
- Writable: `models/**`, `product_ops/modeling/**`, `docs/evidence/models/**`,
  focused tests.
- Acceptance:
  query 能以明確 model/version lineage 取得 prediction 並與 M6/M12 outcome
  正確 join；missing/unmatched prediction 必須 fail closed，禁止 `y_pred =
  y_true` 或其他自我填補；mutation tests 證明只有真實 prediction evidence
  才能使 Gate 2 進入 `ACTIVE`。

### ODP-PLAN-AVM-OUTCOME-001

- Scope: 建立交易 outcome inventory、估值帶 coverage/calibration 與
  confidential access audit。
- Acceptance:
  `>=120` 成熟成交 outcomes、coverage/價值帶校準達標且 RBAC/audit 通過；
  資料不足時維持 governed-disabled。

### ODP-PLAN-HEATZONE-LABEL-BACKFILL-001

- Class: `human_gate`; owner: `Human/Ops`.
- Scope: 依 HeatZone data handback contract 提供權威成熟 labels。
- Acceptance:
  `>=200` eligible mature labels；dataset hash、lineage、owner、freshness
  可回讀；禁止 synthetic、fixture 與 auto-seed。

### ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001

- Class: `human_gate`; owner: `Human/Ops`.
- Scope: 提供權威開店 outcomes 與 M6/M12 maturity 欄位。
- Acceptance:
  `>=200` eligible mature outcomes；M6/M12、dataset hash、lineage、owner、
  freshness 可回讀；禁止 synthetic、fixture 與 auto-seed。

### ODP-PLAN-AVM-OUTCOME-BACKFILL-001

- Class: `human_gate`; owner: `Human/Ops`.
- Scope: 提供權威成熟成交 outcomes 與 confidential access receipt。
- Acceptance:
  `>=120` eligible mature transactions；dataset hash、lineage、RBAC、owner、
  freshness 可回讀；禁止 synthetic、fixture 與 auto-seed。

### ODP-PLAN-FORECAST-BUSINESS-001

- Scope: baseline superiority、segment metrics、四燈 alert
  precision/recall/lead-time 與 operator actionability。
- Acceptance:
  與 seasonal-naive 等基準在正式資料比較；不合格 segment fail closed；
  dashboard、threshold 與 evidence 綁定同一 model version。

### ODP-PLAN-PRICE-ADLIFT-PILOT-001

- Scope: 價格 hard constraints、AdLift pre-trend、incremental gross margin、
  guardrail、pilot watch 與 rollback。
- Acceptance:
  0 hard violation；pre-trend 與財務指標有正式 receipt；kill switch、
  rollback、Finance/Product acceptance 可驗證。

### ODP-PLAN-NETPLAN-ACCEPTANCE-001

- Scope: hard constraints、baseline comparison、alternatives、infeasibility
  explanation、scenario provenance 與 management approval packet 的
  fail-closed technical capability。
- Acceptance:
  100% hard constraints；替代方案與 infeasibility 可解釋；solver
  provenance、scenario data hash、comparison input/output hash 可回讀；
  未取得 `ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001` 的 authoritative
  receipt 前，只能輸出 `BUSINESS_UAT_UNVERIFIED`／`GOVERNED_DISABLED`，
  不得宣稱「優於核准 baseline」。

### ODP-PLAN-LEDGER-NETPLAN-HUMAN-GATE-001

- Class: `governance_correction`; owner: `CodexCoordinator`; reviewer: `Codex`.
- Scope:
  修正原 24-task ledger 漏拆 NetPlan 真實 management baseline approval
  的問題；新增 Human/Ops gate，並補上 UAT／final gate 直接依賴。
- Acceptance:
  matrix 的 84 個 RTM rows 與 Stage 分布維持不變；ledger 可重算為
  26 個 task；NetPlan technical capability 與 authentic business approval
  明確分離；文件、`ai-status.json`、UAT 與 final dependencies 一致。

### ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001

- Class: `human_gate`; owner: `Human/Ops`; reviewer: `Codex`.
- Scope:
  由具名且可追責的管理 owner 核准 immutable NetPlan baseline、適用
  scenario/entity domain、policy/constraint/objective/risk penalty 與資料快照。
- Acceptance:
  receipt 必須可回讀具名 approver 與角色、`active` status、issued/expiry、
  approval reference、baseline content hash、solver problem hash 與適用 scope；
  缺欄、過期、hash mismatch、無法解析或 AI 代簽時必須 fail closed。

### ODP-PLAN-OSS-LICENSE-GATE-001

- Scope: license-aware CycloneDX SBOM、dependency graph、hash、supplier、
  scope、license policy、NOTICE/THIRD_PARTY_NOTICES、attestation/readback。
- Acceptance:
  allow/deny/review policy fail closed；SBOM 綁 image/release digest；
  prod 與 dev toolchain 都掃描，13 個 high dev findings 有修復或具名豁免；
  未取得 `ODP-PLAN-OSS-LEGAL-POLICY-001` 權威 receipt 前，所有需要法務
  判斷的 license／exemption 必須維持 `review_required` 並 fail closed，
  不接受 AI 自行核准。技術 task 可先完成 fail-closed 能力，不阻塞其他
  工程 task；production final gate 仍直接依賴 legal gate。

### ODP-PLAN-OSS-LEGAL-POLICY-001

- Class: `human_gate`; owner: `Human/Ops`.
- Scope: 由具名 Legal/Security/Risk owner 核准 OSS license
  allow/deny/review policy、LGPL 等條款處置、例外格式與有效期限。
- Acceptance:
  policy version、核准人、角色、日期、決策理由、適用 release 與
  expiry/review date 可回讀；AI agent 不得成為 legal approver；缺 receipt
  時 OSS release gate 必須 fail closed。

### ODP-PLAN-OBSERVABILITY-LIVE-001

- Scope: production metrics exporter、dashboard、alert routes、SLO owner、
  runbook 與 watch-window receipt。
- Acceptance:
  API、worker、event/DLQ、model、solver、business KPI 可觀測；測試告警送達
  真實 on-call route；未配置 route 時 release gate fail closed。

### ODP-PLAN-DEFERRED-OSS-ADR-001

- Scope: 對 GeoPandas/H3-SQL、ruptures/CUSUM-EWMA、Superset/OpsBoard、
  Temporal/job framework、OPA/RBAC-ABAC、pgvector、Feast 與 Stage 7 OSS
  建立逐項 adopt/defer/replace ADR。
- Acceptance:
  每項有需求映射、替代能力、限制、owner、revisit trigger 與 Stage；
  不能以「套件未安裝」直接等同功能完成。

### ODP-PLAN-ENGINEERING-HARDENING-001

- Scope: OpenAPI response typing、CSS/build warnings、bundle、13 個 high dev
  vulnerabilities、大型 route/workspace 拆分與 stale docs 修正。
- Acceptance:
  OpenAPI/client drift gate 通過；build 無既知 warning；dependency audit
  無未處理 high；拆分不改 canonical behavior；Ruff/typecheck/unit/build 通過。

### ODP-PLAN-FINAL-GATE-AUDIT-001

- Class: `human_gate`.
- Scope: 重新執行原始 Stage 0–7／Gate 0–6 RTM，對每項填入 merged SHA、
  deployed SHA、evidence、owner、reviewer 與判定。
- Acceptance:
  所有 P0/P1 task（包含 SiteScore prediction source）、三個 Human Data
  Gate、`ODP-PLAN-OSS-LEGAL-POLICY-001` 與
  `ODP-PLAN-GAP-ARCHIVE-001` done；所有 production proof 可回讀且
  exact-SHA；Human/Ops 正式核准；否則維持 NO-GO 並列出唯一剩餘
  blockers。

## 4. 共通 verification

每個 implementation task 至少執行其 focused tests 加上：

```text
git diff --check
ruff check <changed-python-paths>
npm run typecheck --workspace=@oday-plus/web
npm test --workspace=@oday-plus/web
npm run build --workspace=@oday-plus/web
```

全套命令應依 touched scope 執行；不得用未執行的命令作為 receipt。最終
audit 必須另外執行 OpenAPI drift、security/SBOM、canonical Playwright、
live data、deployment health、model release 與 rollback gates。
