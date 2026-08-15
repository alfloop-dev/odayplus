---
doc_id: ODP-SD-03
title: "服務與元件邊界設計"
version: 0.1.0
status: draft-for-review
document_class: system-design
batch: 4
formal_deliverable: true
project: ODay Plus
language: zh-TW
updated_at: 2026-06-26
owner: "Architecture Owner / Backend Lead"
approvers: "Architecture Owner / Product Lead / Security Owner / SRE Owner"
content_format: markdown
---


# ODay Plus 服務與元件邊界設計

## 1. 文件目的

本文件定義 ODay Plus 的服務邊界、元件責任、部署單元、通訊模式與拆分準則。它避免兩種錯誤：一是把所有功能塞成難以維護的單體；二是一開始把每個模型、每個演算法、每個 Worker 都拆成獨立微服務，造成整合、監控、版本與維運負擔過高。


## 0. 文件基線與引用規則

本文件屬於第 4 批「平台 SD 主文件」，必須與第 1 至第 3 批文件共同使用：

| 參照文件 | 用途 |
|---|---|
| `ODP-00-01_SCOPE_AND_BOUNDARIES.md` | 系統範圍、完整目標架構、上游 IoT／內部資料底座責任邊界 |
| `ODP-00-02_GLOSSARY_AND_SEMANTICS.md` | 全平台名詞、狀態、代碼與共用業務語意 |
| `ODP-00-03_STAKEHOLDERS_AND_RACI.md` | Ownership、RACI、責任分離與治理會議 |
| `ODP-00-04_DOCUMENT_VERSION_AND_ADR_GOVERNANCE.md` | 文件版本、ADR、Deviation、變更與核准流程 |
| `ODP-00-05_REQUIREMENTS_TRACEABILITY_MATRIX.md` | HLR、FR、SD、Test、Evidence 的追蹤骨架 |
| `ODP-SA-01` 至 `ODP-SA-10` | 業務架構、流程、角色、FR、NFR、整合與 KPI 基線 |
| `ODP-DATA-01` 至 `ODP-DATA-07` | 資料來源、交換契約、Canonical Model、Model-ready Views、資料品質與時間語意 |

來源基線：`SRC-ODP-ARCH`、`SRC-ODP-PLAN`、`SRC-ODP-REVIEW`、`SRC-LEGACY-CLOUD`、`SRC-WORKING-DECISIONS`。若本文件與第 1 批治理文件衝突，以第 1 批治理文件及已核准 ADR 為準。

規範用語：本文中的「必須」表示驗收必要條件；「不得」表示禁止；「應」表示預設要求，若不採用必須有核准 Deviation；「可」表示可選或延伸能力。


## 2. 邊界設計原則

| 原則 | 說明 |
|---|---|
| 業務責任優先 | 依展店、營運、干預、估值、店網、治理等領域切邊界 |
| 執行型態優先 | Sync API、Async Worker、Batch Job、Training Job、Solver Job 分開 |
| 資源差異才拆 | Geo、ML training、Solver 與 API 的 CPU/Memory/timeout 差異大，應拆 runtime |
| 發布頻率才拆 | UI、API、模型、資料管線發布週期不同時才拆部署 |
| 團隊責任才拆 | 若仍由同一人或同一小組維護，不為形式上微服務而拆 |
| 契約穩定才拆 | API/Event/Schema 尚未穩定前，避免過度分散 |
| 共用治理集中 | Identity、Audit、Decision Log、Job、Workflow、Notification 不各模組自行重做 |

## 3. Workload 類型

| 類型 | Runtime | 例子 | 設計要求 |
|---|---|---|---|
| Frontend | Cloud Run / Static Hosting | `opsboard-web` | 不直接讀 DB；只呼叫 API/BFF |
| BFF/API | Cloud Run Service | `core-api`、domain routers | 快速回應、驗證權限、建立 job、查結果 |
| Event Consumer | Cloud Run worker | `event-consumer` | idempotency、retry、DLQ、schema validation |
| Async Worker | Cloud Run Job/Worker | `report-worker`、`geocode-worker` | 可重試、持久化結果、job status |
| Scheduled Batch | Cloud Scheduler + Job | `forecast-daily-score` | 可重跑、具 snapshot、產出 run record |
| Training Job | Vertex AI / Cloud Run Job | `sitescore-training` | dataset snapshot、MLflow run、model card |
| Optimization Job | Cloud Run Job / GKE | `netplan-solver` | 長時間、可取消、solver status、explain |
| Platform Stateful | Managed service | Cloud SQL、BigQuery、Redis | 優先用 managed service，不自架資料庫 |

## 4. 第一版部署單元

| 部署單元 | 類型 | 包含元件 | 不包含 |
|---|---|---|---|
| `opsboard-web` | Frontend | React/Next UI、地圖、表格、圖表、工作台 | 模型邏輯、資料庫連線 |
| `core-api` | API | Auth middleware、domain routers、OpenAPI、Job/Approval/Audit APIs | 長時間重算、模型訓練、Solver |
| `worker` | Worker | Integration、Geocode、Report、Effect Eval、Notification、Event Consumer | 同步使用者互動 |
| `scheduler` | Scheduler | 定時觸發資料品質、Forecast、HeatZone、Model Monitor | 業務處理本身 |
| `training-jobs` | Training | SiteScore、Forecast、Price、AdLift、AVM 模型訓練 | 線上 API |
| `solver-jobs` | Solver | Pricing Optimizer、NetPlan Solver、Infeasibility explain | 一般 API |
| `dbt-pipelines` | Data Transform | Canonical、Feature Mart、Model-ready Views | App business logic |

> 這是建議第一版物理部署。程式碼內部仍需以 domain module 清楚切分，確保日後可拆。

## 5. Domain Component Map

| Domain | API Component | Worker/Job Component | 主要資料 | 主要事件 |
|---|---|---|---|---|
| Integration | `integration_router` | `source_adapter_worker`、`canonical_mapper`、`dq_worker` | Raw、Canonical、DQ | `source.ingested`、`canonical.updated`、`data_quality.failed` |
| External Data | `external_data_router` | `geo_feature_worker`、`geocode_worker`、`connector_worker` | Geo snapshots、H3、POI | `external.snapshot.created` |
| HeatZone | `heatzone_router` | `heatzone_score_job` | `geo_grid_view`、heatzone scores | `heatzone.scored` |
| Listing | `listing_router` | `listing_parser`、`dedup_worker` | listing、candidate site | `listing.created`、`listing.deduplicated` |
| SiteScore | `sitescore_router` | `sitescore_score_worker`、`report_worker`、`realization_worker` | score runs、reports | `sitescore.requested`、`sitescore.completed`、`site.approved` |
| ForecastOps | `forecast_router` | `forecast_score_job`、`alert_engine` | forecasts、alerts | `forecast.scored`、`alert.created` |
| InterventionOps | `intervention_router` | `eligibility_worker`、`effect_eval_worker` | interventions、outcomes | `intervention.approved`、`intervention.completed` |
| PriceOps | `pricing_router` | `elasticity_training`、`pricing_optimizer` | price actions | `price_action.proposed` |
| AdLift | `adlift_router` | `control_matching_worker`、`did_worker` | campaigns、effects | `campaign.started`、`adlift.evaluated` |
| AVM | `avm_router` | `valuation_worker`、`dataroom_builder` | valuations、data rooms | `valuation.completed` |
| NetPlan | `netplan_router` | `scenario_builder`、`solver_job` | network plans | `netplan.solved` |
| Learning Hub | `learninghub_router` | `backtest_worker`、`drift_monitor`、`release_worker` | feature/label/model registry | `model.registered`、`model.promoted` |
| OpsBoard | `opsboard_router` | `notification_worker` | tasks、approvals、audit | `approval.requested`、`task.assigned` |

## 6. 通訊模式

| 情境 | 模式 | 規則 |
|---|---|---|
| 使用者查列表、查已完成結果 | Sync REST | P95 ≤ NFR；不得現場跑長模型 |
| 使用者要求重算、報告、Solver | REST 建立 Job + Async Worker | 回傳 `job_id`；由 Job API 查狀態 |
| 模組間通知 | Pub/Sub Event | 使用事件 envelope、schema version、idempotency |
| 跨模組查資料 | API 或 Model-ready View | 交易型狀態走 API；分析型輸入走 View |
| 人工核准 | Workflow + API | 必須有 actor、decision_time、policy_version |
| 模型訓練 | Batch/Training Job | 必須產出 Dataset Snapshot、MLflow run、Model Card |
| Solver | Job + Persisted Result | 不在 API request 中同步求解 |

## 7. 不得跨越的邊界

1. Frontend 不得直接查 Cloud SQL、BigQuery 或 GCS private object。
2. Domain module 不得直接讀 Raw source table。
3. ForecastOps 不得直接執行干預；只能建議進入 InterventionOps。
4. PriceOps／AdLift 不得繞過 InterventionOps approval lifecycle。
5. NetPlan 不得直接修改門市狀態；必須產生決策方案，經管理層核准後由 workflow 執行。
6. 模型 Serving 不得自行決定 release；必須經 Learning Hub Release Controller。
7. 任何服務不得自行寫無版本的 Audit Log；必須使用共用 Audit API/Library。

## 8. 共用平台元件

| 元件 | 責任 | 使用者 |
|---|---|---|
| Auth Middleware | JWT/OIDC、role/scope extraction | 所有 API |
| Permission Service | RBAC/ABAC 決策 | API、Workflow、Frontend |
| Job Service | Job 建立、狀態、重試、取消、結果 | 所有長任務 |
| Workflow Service | 狀態機、人工核准、timeout、escalation | SiteScore、Intervention、NetPlan、Model Release |
| Decision Log | 預測、建議、決策、覆核、執行、結果 | 全模組 |
| Audit Logger | Append-only audit event | 全模組 |
| Notification Service | Email/LINE/Webhook/in-app | OpsBoard、Workflow |
| Report Service | PDF/HTML/CSV/JSON 報告生成 | SiteScore、AVM、NetPlan、Audit |
| Feature/Model Client | 模型版本、特徵版本、release alias | 模型與 API |

## 9. 拆分準則

某元件符合任兩項，應評估獨立部署：

| 準則 | 例子 |
|---|---|
| 資源型態差異大 | NetPlan solver 需多 CPU、Geo worker 需 GDAL、大記憶體 |
| 任務時間長 | 超過 Cloud Run API timeout 或需 checkpoint |
| 發布頻率不同 | UI 每週、模型每月、Geo tool 半年 |
| 權限敏感 | PII、估值、模型發布、財務資料 |
| 錯誤隔離需求 | Solver 失敗不能拖垮 API |
| 團隊獨立 | Data/ML/SRE/Frontend 分別維護 |
| 法規或稽核要求 | Data Room、估值、個資匯出 |

## 10. 開發時的模組邊界

即使第一版用同一個 `core-api`，程式碼仍必須採下列依賴方向：

```text
apps/api
→ modules/<domain>/application
→ modules/<domain>/domain
→ shared/domain
→ shared/infrastructure
```

禁止：

```text
modules/sitescore 直接 import modules/forecastops/infrastructure
modules/priceops 直接改 modules/intervention 的資料表
frontend 直接 import backend domain model
```

應使用：

```text
Application service
Domain event
Repository interface
Shared DTO / OpenAPI client
```

## 11. 驗收條件

| AC ID | 驗收條件 | 驗證方式 |
|---|---|---|
| `ODP-AC-SD03-001` | 所有模組均有 API、Worker/Job、Data、Event 的邊界定義 | Design Review |
| `ODP-AC-SD03-002` | 長任務不得由同步 API 執行 | Code/Architecture Review |
| `ODP-AC-SD03-003` | 共用 Job、Workflow、Audit、Notification 不得被各模組重複實作 | Code Review |
| `ODP-AC-SD03-004` | 至少一條展店、營運、估值 E2E 流程跨服務邊界可追蹤 | E2E Test |
| `ODP-AC-SD03-005` | 拆分或合併服務需有 ADR | Governance Review |


## 追蹤與驗收

| Trace 類型 | 對應 |
|---|---|
| HLR | `ODP-HLR-GOV-*`、`ODP-HLR-INT-*`、`ODP-HLR-HZ-*`、`ODP-HLR-SITE-*`、`ODP-HLR-FCT-*`、`ODP-HLR-INTV-*`、`ODP-HLR-PRICE-*`、`ODP-HLR-AD-*`、`ODP-HLR-AVM-*`、`ODP-HLR-NET-*`、`ODP-HLR-LH-*`、`ODP-HLR-OPS-*` |
| FR | `ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION.md` 中列出的全平台 FR |
| NFR | `ODP-SA-08_NON_FUNCTIONAL_REQUIREMENTS.md` 中的效能、可用性、資安、資料品質、模型治理與可觀測性要求 |
| Data | `ODP-DATA-04_CANONICAL_DATA_MODEL.md`、`ODP-DATA-06_MODEL_READY_VIEWS_SPECIFICATION.md` |
| QA | 第 8 批 QA 文件需將本文件的架構決策轉為 Contract Test、Integration Test、E2E、Security Test、Performance Test 與 Audit Evidence |

本文件中列為 `MUST` 的架構與設計項目，後續必須在程式碼、IaC、OpenAPI、AsyncAPI、dbt、Migration、Test Case 或 Runbook 中至少有一項可查證交付物。
