---
doc_id: ODP-CONSOLIDATED-GAP-AUDIT-2026-08-03
title: ODay Plus 規格 vs dev 合併差異盤點
status: consolidated
audit_date: 2026-08-03
audited_sha: a37283e29a265ce4c0990ff492fb9b788d153996
audited_ref: origin/dev
release_decision: NO-GO
language: zh-TW
supersedes:
  - docs/evidence/DEV_PROGRESS_SPEC_GAP_AUDIT_2026-08-03.md
  - docs/evidence/runtime/ODP-RUNTIME-GCP-001-LIVE-DIAGNOSIS-2026-08-03.md
---

# ODay Plus 規格 vs dev 合併差異盤點

本文件合併兩份獨立盤點：Codex 的
`DEV_PROGRESS_SPEC_GAP_AUDIT_2026-08-03.md`（事實基線與 Gate 框架）與
Claude 的 `ODP-RUNTIME-GCP-001-LIVE-DIAGNOSIS-2026-08-03.md`（live runtime 根因）。
兩份的交叉查證結果與各自的修正一併記錄於 §2。

## 1. 盤點基準

| 項目 | 值 |
|---|---|
| 盤點 SHA | `a37283e29a265ce4c0990ff492fb9b788d153996`（`origin/dev`） |
| 最新 CI | `30820068695` — **success** |
| 最新 Deploy Dev | `30820067751` — **failure** |
| 規格基線 | 9 個 batch、72 份正式文件、28,438 行 |
| FR 基線 | `ODP-SA-06` 共 79 條（71 模組 + 8 平台共用） |
| 畫面基線 | `ODP-UX-03` 共 30 個畫面 |
| 治理基線 | 84-row Gap Matrix、26-task Control Pack、Gate Registry |
| **Release 判定** | **NO-GO** |

### 1.1 證據等級

本文件每項宣稱標示來源等級，避免把不同強度的證據混為一談：

| 等級 | 意義 |
|---|---|
| `L1-live` | 對 live 端點或 GCP 實際探測所得 |
| `L2-ci` | GitHub Actions exact-SHA 執行結果 |
| `L3-local` | 本機重跑命令所得 |
| `L4-repo` | 引用 repository 內既有 evidence，未重跑 |

## 2. 交叉查證與已修正的判定

兩份報告經逐條交叉查證，**未發現事實錯誤**。以下是查證過程中修正的四項判定，
均為 Claude 前次盤點的錯誤：

| # | 前次錯誤判定 | 修正後 | 修正依據 |
|---|---|---|---|
| 1 | 「無可回讀 live 環境」 | **不成立**。live dev 部署存在且 revision 綁定 exact `origin/dev` HEAD | `L1-live` Cloud Run describe |
| 2 | 「ForecastOps 是唯一可訓練模型（1,303 rows）」 | **筆數對、結論錯**。四個模型全部 data-blocked | `L4-repo` task note + `L3-local` 契約推導 |
| 3 | 「live E2E gate 唯一 blocker 是 ForecastOps alias」 | **錯。有兩組獨立 blocker**：external-data ingestion 與 model registry | `L2-ci` 重新完整讀取失敗日誌 |
| 4 | 「工程 RTM 有 11 rows」 | **10 rows** | `L3-local` grep 計數 |

第 3 項最關鍵：前次以帶關鍵字的 grep 讀取失敗日誌，濾掉了 ingestion 相關行，
因而誤判關鍵路徑只有一條。**補齊 ForecastOps 資料並不會讓 gate 通過**，
真實 ingestion 是另一條必須獨立解決的路徑。

## 3. 總結論

現況不是「缺少產品骨架」，而是「主要產品能力已實作，但正式資料、模型啟用、
外部服務、Human/Ops 驗收與 exact-SHA release evidence 尚未閉環」。

工程涵蓋度（`L3-local` 實測）：

- 15 個 domain module、約 57,300 行 Python
- 26 個 API route module、215 個 handler；OpenAPI 213 paths / 226 operations
- 3,441 個 pytest 案例可收集；ruff clean；3 個 TS workspace typecheck 全過
- 16 個 Playwright spec

**可宣稱**：dev code CI 可通過；Cloud Run build/push/candidate deploy/WIF/
secret scan/SAST/SBOM/rollback path 可執行；Operator OIDC/RBAC 最小權限角色已修復；
主要流程有 deterministic E2E；系統在 live 依賴不完整時能 fail closed 並回復
API/Web traffic 與 Scheduler targets。

**不可宣稱**：production-ready；外部 provider／live map／remote staging 已完成；
ForecastOps／HeatZone／SiteScore／AVM／NetPlan 已通過 business/model activation gate；
以 deterministic fixture、repository-local receipt、task done 或 CI green
取代 exact-SHA staging/live proof 與 Human/Ops sign-off。

距離完整上線差**五個**閉環（原四個 + 執行層）：

1. **真實資料閉環** — provider ingestion、lineage、freshness、labels/outcomes
2. **正式模型閉環** — MLflow version/alias、model card、metrics、canary/watch/rollback
3. **業務與法遵閉環** — license、NetPlan baseline、pilot results、跨角色 UAT
4. **發布治理閉環** — 最新 SHA Gate 0-6、84-row RTM、remote staging、final audit、GO
5. **執行層閉環** — task dependency 圖譜可解析、無 owner 能力有歸屬（見 §7、§9）

## 4. 規格要求與實作總差異

| 規格面向 | 最新 dev 狀態 | 差異判定 |
|---|---|---|
| 全模組範圍 | 15 模組皆有程式落點，API 213 paths | 程式範圍齊備，不等於 runtime activation |
| 前端 | 3 入口承載 5 workspace | 大致實作；**但 3 項規格 MUST 完全缺、12 項較規格薄**（§9） |
| API | 核心 domain 與 operator paths 齊備 | 大致實作；缺最新 SHA contract receipt |
| Transactional persistence | PostgreSQL、repository factory、tenant-scoped | 部分完成；72 個檔案仍有 in-memory 路徑，需 exact-SHA 證明未 fallback |
| Analytical data | schema、dbt/model-ready、quality/backfill 存在 | 未過 gate；live ingestion runs = 0 |
| External data | provider registry、live adapter、scheduler、fail-closed | **未啟用**；required provider 無 persisted run |
| Event/job | queue、worker、scheduler、receipt、rollback | 技術面高；缺候選版 live round-trip |
| Model lifecycle | MLflow/GX/Evidently/Dagster adapter 存在 | 未 activation；registry versions=0、alias=0 |
| Solver | compatibility 與 NetPlan technical acceptance 完成 | 技術通過、業務未通過 |
| Security | CI green、secret scan/SAST/SBOM 通過、RBAC live readback | 大致可用未封版；**WIF/IAM 已達成**（§8） |
| Observability | wiring 已合併 | 缺候選版整體 Gate receipt |
| Notification | webhook only | **無 Email／站內實際投遞**（§9） |
| UAT | plan 仍為 `draft` | 未完成 |
| Release | CI green、Deploy Dev failure、rollback 成功 | NO-GO |
| Traceability | 工程 RTM 僅 10 rows，需 84 rows；checklist 為 `[ASSIGNMENT_REQUIRED]` | 治理阻斷 |

## 5. 最新部署實證（`L2-ci`）

### 5.1 通過的部分

checkout、locked dependencies、secret scan、Python SAST、SBOM、WIF 認證、
live runtime preflight、Cloud SDK、Cosign、image build/push、candidate Cloud Run deploy、
migration Job smoke、migration compatibility smoke、scheduler Job smoke、worker Job smoke、
Cloud Run live deployment smoke，以及失敗後的 API/Web traffic 還原與
Cloud Scheduler trigger 還原。

### 5.2 阻斷的部分 — 兩組獨立 blocker

`a37283e2` 的 Live E2E gate 逐字輸出：

```
Live E2E gate failed. Blocking runtime dependencies:
* external-data: Run a real ingestion for the required providers; the deployed
  release has no populated, lineage-complete ingestion run to serve.
  - data:ingestion_runs: runs=0
  - data:admin_boundary.official_dataset:run_exists: no persisted ingestion run
  - data:poi.commercial_api:run_exists: no persisted ingestion run
  - runtime:model_bindings: mode=mlflow-production-unverified ready=False
    error=forecastops: PRODUCTION_MODEL_REGISTRY_UNAVAILABLE
  - runtime:model_capability:forecastops: available=False
  - models:registry: versions=0
  - models:forecastops:production_alias: versionsWithProductionAlias=0
    (exactly one required)
```

| 組別 | Blocker | 解法路徑 |
|---|---|---|
| **A. External data** | ingestion runs=0；admin_boundary 與 poi 皆無 persisted run | 需 provider 憑證 + 授權 + 實際執行 ingestion |
| **B. Model registry** | registry versions=0；forecast alias=0 | 需 ForecastOps 可訓練（見 §6.1） |

**A 與 B 必須各自獨立解決**，補其中一組不會讓 gate 通過。同一組 blocker 在前一版
`40338298` 的 run（`30812452823`）已同樣存在，非新增。

### 5.3 證據一致性風險

`docs/evidence/runtime/ODP-OPERATOR-SMOKE-RBAC-LIVE-002/README.md` 把 deploy run
`30809922826` 列為「Replacement Deploy Dev Run」並列出四個 endpoint 200，
但該 run 的 workflow conclusion 實際是 **failure**（`L2-ci` 查證）。
兩者不必然矛盾——RBAC 子範圍可通過而 live data/model gate 仍失敗——
但 release 文件必須明確區分 step-level pass 與 workflow-level failure。

`a37283e2` 相對前一版只新增 3 個檔案 506 行的 orchestrator source-document
materialization evidence，不解除任何產品 blocker。

## 6. 四個模型的資料真相

| 能力 | 門檻 | 實際 | 判定 | 真正的阻礙 |
|---|---:|---:|---|---|
| HeatZone | 200 | **0** | `GOVERNED_DISABLED` | 無標籤 |
| SiteScore | 200 | **0** | `GOVERNED_DISABLED` | 無 outcome；且需 prediction source 雙收據 |
| DealRoom AVM | 120 | **0** | `GOVERNED_DISABLED` | 無成交 outcome |
| ForecastOps | 90 | **1,303** | **無法訓練** | **時間跨度，不是筆數**（見 §6.1） |

### 6.1 ForecastOps：筆數達標但根本訓練不起來

這是最容易被誤讀的一項。`1,303 > 90` 讓報表看起來資料充足，實際上：

- 那 1,303 筆只涵蓋 `2026-06-19 ~ 2026-06-22`，**四個日曆天**
- `modules/forecastops/model_contract.py`：
  `FORECASTOPS_HORIZON_WEEKS = (4, 8, 12, 24)`、`FORECASTOPS_MIN_HISTORY_DAYS = 28`
- `scripts/models/forecast_training.py` 的 `expand_forecast_horizon_rows()`
  對每個 origin 取固定長度窗口，`len(window) != horizon_days` 就整筆跳過，
  且窗口必須是**連續日**

推導出的實際需求：

| 目標 | 前置歷史 | Horizon | 每店最少連續日數 |
|---|---:|---:|---:|
| 僅 4 週 horizon（最低可行） | 28 | 28 | **56** |
| 完整 4/8/12/24 週（`FR-FCT-001`） | 28 | 168 | **196** |

現有 4 天，距最低門檻差 52 天、距完整規格差 192 天。訓練 execution `2dzlg`
即因此 fail closed，沒有產生 DEV candidate，MLflow registry 因而完全是空的
（`L1-live` 查詢 `registered-models/search` 回傳 `{}`）。

**因此 ForecastOps 不是「已訓練但未驗證」，而是「無法訓練」。**

### 6.2 已完成的 task 不等於已交付的能力

`ODP-PLAN-AVM-OUTCOME-BACKFILL-001` 於 2026-08-03 歸檔為 `completed`，
但其 `DATA_HANDBACK.json` 仍是 `FAIL_CLOSED / shortfall: 120`。
它交付的是 intake packet 與 readback spec，不是資料本身。
閱讀 ledger 時必須分辨「機制完成」與「能力啟用」。

## 7. 執行層阻塞：task dependency 圖譜壞損

`scripts/orchestrator/check_task_dependency_resolvability.py`（`L3-local`）
對 live supervisor 狀態掃描結果：**14 個 dangling dependency，橫跨 4 個 task**。

| 受影響 task | dangling 數 | 後果 |
|---|---:|---|
| `ODP-RUNTIME-GCP-001` | 1 | 無法派工 |
| `ODP-PRODUCTION-MODEL-REGISTRY-001` | 5 | 無法派工 |
| `ODP-LIVE-RUNTIME-DEV-COMPOSE-001` | 6 | 無法派工 |
| `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` | 2 | 無法派工 |

9 個不重複的 dangling id 中，6 個在 `docs/evidence/` 有完成證據但未進官方 archive，
3 個查無任何證據。依 Control Pack §3.1，dependency 必須在 live board 或官方 archive
解析成立才能派工，**這 4 個 task 在圖譜修復前永遠不會被派工**。

另有循環依賴：`ODP-RUNTIME-GCP-001` → `ODP-PRODUCTION-MODEL-REGISTRY-001`，
而後者的 acceptance 要求 remote MLflow 解析 production alias，需要 live GCP runtime。

**這代表 §11 P0 清單中的多數工項，在目前 orchestrator 狀態下無法被自動派工。**
修復程序見 `docs/runbooks/task-dependency-graph-repair.md`。

## 8. live runtime 已達成與未達成的部分

### 8.1 已達成（`L1-live`）

| `ODP-RUNTIME-GCP-001` 驗收項 | 狀態 |
|---|---|
| GitHub dev environment has working WIF variables | **已滿足** — pool `github-actions` ACTIVE，本日部署即由此完成 |
| GCP deploy identity has least-privilege roles | **實質滿足** — 5 個角色，無 primitive role |
| required resources are inventoried | **本文件完成** |
| no long-lived `GCP_SA_KEY` | **已滿足** — 未發現 |

資源盤點：Cloud Run 4 個 ODP 服務（api/web/mlflow/provider-gateway）、
Cloud SQL 2 個（`oday-dev-sql` PG16、`oday-plus-dev-postgres` PG15）、
GCS 4 個 bucket、Secret Manager 12 個 secret。

**因此「provider 憑證未完成」指的是外部資料供應商憑證，不是基礎設施認證。**
兩者不應混為一談。

### 8.2 未達成：provider gateway 冷啟動導致間歇 503

`odp-provider-gateway` 只設 `maxScale=3`，**無 `minScale`** → scale-to-zero →
冷啟動超過 `ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS=3.0`。

| provider | 冷啟動時 | 暖機後 |
|---|---|---|
| `admin_boundary.official_dataset` | 2,953 ms（距 timeout 僅 47 ms） | 133 ms |
| `geocode.primary_api` | 1,646 ms | 114 ms |
| `poi.commercial_api` | **timeout** | 671 ms |

三個 provider 指向同一 gateway 的不同路徑、共用同一把 key，因此是單點冷啟動問題，
非憑證問題。建議設 `minScale=1`；調高 probe timeout 會延後真實故障偵測，較不理想。

## 9. 規格 MUST 但無 owner 的能力

比對 `ODP-SA-06` 與 `ODP-UX-03` 後，30 個規格畫面的落點為
**✅ 15 完整 / ⚠️ 12 較規格薄 / ❌ 3 完全缺**。

以下 5 項同時滿足「不在 canonical runtime」「不在 26-task ledger」「無核准 deviation」
三個條件——既未實作，也未正式排除：

| 代號 | 能力 | 規格來源 | 現況 |
|---|---|---|---|
| U-1 | Model Release Controller UI | `UX-SCR-LEARN-003`、`FR-LH-003` | 後端 10 個 endpoint 齊備，**前端零實作**；promote/canary/rollback 僅能直呼 API |
| U-2 | User & Role Management UI | `UX-SCR-ADMIN-001`、`FR-OPS-003` | 僅唯讀角色列；權限異動需改 Secret 並重新部署 |
| U-3 | **Feature Flag Management UI** | `UX-SCR-ADMIN-002`、`FR-SHARED-004` | **完全不存在**。規格明文「未啟用時 UI、API、Job 均不得執行」 |
| U-4 | Email／站內通知實際投遞 | `FR-SHARED-006` | 僅 console + on-call webhook；全 repo 搜尋 `smtp`/`sendgrid`/`ses` 零命中 |
| U-5 | 任務附件 | `FR-OPS-002` | 全 repo 搜尋 `attachment` 零命中；留言/指派/核准/升級皆有，僅附件缺 |

**U-3 應排最優先**：PriceOps、AdLift、NetPlan、模型發布、決策政策變更全都指定用
feature flag 控管啟用範圍。沒有它，高風險功能在營運時段沒有 kill-switch。
**U-4 次之**：UAT 需要 6 種角色實際收到指派通知才做得下去。

決策請求見 `docs/design/ODAY_PLUS_UNOWNED_CAPABILITY_SCOPE_DECISION_2026-08-03.md`，
每項需在 A（排入 release）／B（正式 deviation）／C（ADR 範圍變更）擇一。
**維持現狀不可接受**——會讓 Final Gate Audit 出現無法歸類的缺口。

## 10. Gate 0-6 與治理 program

### 10.1 Gate 狀態：exact-SHA 通過數 **0/7**

| Gate | 現況 | 上線前必須補齊 |
|---|---|---|
| 0 Code | CI green，但 registry 無 `a37283e2` receipt | exact-SHA immutable build/code receipt |
| 1 Contract | contract tests 存在；registry stale | OpenAPI diff、event/schema/model IO compatibility、migration/rollback attestation |
| 2 Data | live ingestion runs=0 | 啟用 required providers，產生 lineage-complete run 與 data gate report |
| 3 Model/Solver | solver technical pass；alias 0；三模型 governed-disabled | Forecast release；真實 labels/outcomes；NetPlan Human approval |
| 4 Security/Privacy | scans/RBAC 進展高 | OSS legal policy、license-aware attestation、最新 SHA receipt |
| 5 E2E/Perf/UAT | deterministic E2E 高；live E2E failed；UAT draft | staging live E2E、performance receipt、role sign-off |
| 6 Ops/Release/Audit | rollback 可用；metadata 與 GO 缺 | watch window、backup/restore drill、manifest、RTM、Human/Ops GO |

Gate Registry 目前綁 `e496be62`，**已落後 `a37283e2`**，需重綁封版 SHA。

### 10.2 26-task Control Pack：13 done / 13 open

**已完成 13**：GAP-ARCHIVE、GATE-REGISTRY、CANONICAL-SHELL-LIVE、
SOLVER-RUNTIME-COMPAT、DEFERRED-OSS-ADR、HEATZONE-OUTCOME、
LEDGER-NETPLAN-HUMAN-GATE、ACCEPTANCE-REAL-EXEC、OSS-LICENSE-GATE、
NETPLAN-ACCEPTANCE、SITESCORE-OUTCOME、AVM-OUTCOME、AVM-OUTCOME-BACKFILL

**未完成 13**：OBSERVABILITY-LIVE、ENGINEERING-HARDENING、
FORECAST-RELEASE-EVIDENCE、FORECAST-BUSINESS、PRICE-ADLIFT-PILOT、
LIVE-STAGING-PROOF、SITESCORE-PREDICTION-SOURCE（`review_approved`，最接近完成）、
HEATZONE-LABEL-BACKFILL、SITESCORE-OUTCOME-BACKFILL、OSS-LEGAL-POLICY、
NETPLAN-BASELINE-APPROVAL、UAT-SIGNOFF、FINAL-GATE-AUDIT

## 11. 上線必補清單

### 11.0 P-1：執行層前置（不做則以下多數工項無法派工）

| # | 待辦 | 類型 |
|---|---|---|
| E-1 | 修復 task dependency 圖譜（14 個 dangling、9 個 id） | 需停機窗口 |
| E-2 | 拆分 `ODP-PRODUCTION-MODEL-REGISTRY-001` 為 INFRA／GOVERNANCE，打破循環依賴 | 治理決策 |
| E-3 | `save_state()` 加 file lock；CLI 新增 `archive_import` | 工程 |
| E-4 | U-1~U-5 範圍決策（A／B／C） | Product Lead |

### 11.1 P0：不完成就不能部署候選版

| # | 待辦 | 類型 |
|---|---|---|
| 1 | 凍結一個 release candidate SHA，後續所有 receipt 綁同一 SHA | Release Engineering |
| 2 | Provision 真實 external provider 憑證（**非** WIF，見 §8.1） | Platform/Ops + Data Partnerships |
| 3 | 完成 provider 授權與 licensing | Legal + Data Partnerships |
| 4 | 執行真實 ingestion，產生 persisted lineage-complete run | Data Platform |
| 5 | 發布 ForecastOps production model（前提：§6.1 資料回填） | MLOps + Model Owner |
| 6 | `odp-provider-gateway` 設 `minScale=1` | Platform/Ops |
| 7 | 重跑 Deploy Dev／live E2E 全綠 | Platform/Ops |
| 8 | 完成 remote staging proof | Platform/Ops |

### 11.2 P0-DATA：只有 Human/Ops 能解

| # | 待辦 | 差額 | intake packet |
|---|---|---|---|
| D-1 | ForecastOps 權威日歷史 | 4 天 → 需 56（最低）／196（完整） | `docs/evidence/models/forecastops/human-data-gate/` |
| D-2 | HeatZone 成熟 labels | 0 → 200 | `docs/evidence/models/heatzone/human-data-gate/` |
| D-3 | SiteScore M6/M12 outcomes | 0 → 200 | `docs/evidence/models/sitescore/human-data-gate/` |
| D-4 | AVM 成熟成交 outcomes | 0 → 120 | `docs/evidence/models/avm/human-data-gate/` |
| D-5 | OSS license 政策具名核准 | – | Legal/Security/Risk |
| D-6 | NetPlan 管理層 baseline 核准 | – | 具名管理 owner |

### 11.3 P1：模組 Gate

ForecastOps business validation（baseline superiority、segment、alert
precision/recall/lead-time）、PriceOps pilot（0 hard violation、rollback drill）、
AdLift pilot（matched control、pre-trend、incremental GM）、
Learning Hub release governance、Live map（#135/#136）、
Production persistence 證明未 fallback、Notification/admin/franchisee production proof。

### 11.4 P2：發布治理與品質

RTM 由 10 rows 補到 84 rows；Gate Registry 重綁封版 SHA 並補 receipts；
Release Checklist 的 `[ASSIGNMENT_REQUIRED]` 全數填寫；OSS legal gate；
Engineering hardening；Observability closeout；UAT；Final Gate Audit；Human/Ops GO。

亦需清理文件漂移：`WEB_API_BINDING_MATRIX.md` 仍引用 Package 10 已刪除的路由；
`CURRENT_STATE_PRODUCT_GAP_AUDIT.md`（2026-06-28）多處過時；
`docs_archive/README.md` 宣稱的 9 個資料夾不存在。

## 12. 外部 release blockers（`L2-ci` 查證全部 open 且帶 `release-blocker`）

| Issue | 阻斷內容 |
|---|---|
| #132 `ODP-EXT-PROD-001` | production provider credentials |
| #133 `ODP-EXT-PROD-002` | listing/provider license proof |
| #134 `ODP-EXT-PROD-003` | production geocoder 與 low-confidence handling |
| #135 `ODP-MAP-STAGE-001` | remote staging live tile |
| #136 `ODP-MAP-STAGE-002` | remote staging live geocoder |
| #137 `ODP-PV-STAGE-001` | remote staging health/version |
| #138 `ODP-PV-STAGE-002` | staging smoke、backup/restore/rollback drill |

## 13. 建議上線路徑

### 路徑 A：完整功能一次上線

完成 P-1 → P0 → P0-DATA → P1 → P2 → Human/Ops GO。
D-1~D-4 的成熟 outcome 是最長路徑，應**立即**啟動 historical backfill 與 authority review，
不要等工程項做完才開始。

### 路徑 B：受控分階段上線

讓未成熟的 HeatZone model、SiteScore、AVM、NetPlan decision、PriceOps/AdLift
activation 維持 `GOVERNED_DISABLED`，只發布已通過 live gate 的
Operator／read-only／workflow 功能。

但仍**不可繞過**：真實 ingestion（§5.2 組別 A）、ForecastOps model dependency
（組別 B）、remote staging、security、UAT 與 release gates。
且 scope reduction 必須走正式 change control，不可把未完成規格視為取消。

**採路徑 B 時，U-3 Feature Flag 管理 UI 從 P2 升為 P0** ——
分階段上線的前提就是能在營運時段控制哪些能力啟用。

## 14. 本盤點的限制

- 未重跑完整 pytest（3,441 案例）與 Playwright（16 spec / 107 tests），
  僅執行 collection、ruff、typecheck、release gate registry 與 product dev-merge gate。
- Cloud SQL 實際資料量、MLflow artifact readback、第三方 provider 憑證狀態、
  Pub/Sub live round-trip、各角色簽核，均需由環境或責任人補證。
- 所有 GCP 與 live 端點操作均為唯讀（`list` / `describe` / `GET`）。
- `save_state()` 無 file lock 且 supervisor 持續執行，本次未對 live supervisor
  狀態做任何寫入。
