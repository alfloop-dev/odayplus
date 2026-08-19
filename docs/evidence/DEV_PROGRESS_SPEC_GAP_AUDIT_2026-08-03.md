# ODay Plus 最新 dev 開發進度與規格差異盤點

## 1. 盤點結論

| 項目 | 判定 |
|---|---|
| 盤點快照 | `origin/dev@a37283e29a265ce4c0990ff492fb9b788d153996` |
| 快照時間 | 2026-08-03 21:53:36 +08:00 |
| 最新 CI | `30820068695`，成功 |
| 最新 Deploy Dev | `30820067751`，失敗 |
| 正式發布判定 | **NO-GO** |
| 主要原因 | live data ingestion 為 0、ForecastOps MLflow production alias 不存在、Gate 0-6 無最新 SHA receipt、UAT/人工作業與外部 proof 未完成 |

目前不是「缺少產品骨架」，而是「主要產品能力已實作，但正式資料、模型啟用、外部服務、Human/Ops 驗收與 exact-SHA release evidence 尚未閉環」。

程式涵蓋度高：目前 OpenAPI 有 213 個 paths，主要產品模組皆有 API/domain/application/UI 或 worker 實作；測試目錄也已分成 contract、integration、E2E、security、performance、reliability、model、solver 與 ops。可是目前 7 個 machine-readable release gates 沒有任何一個對最新候選 SHA 形成通過 receipt，最新 Cloud Run promotion 也因真實資料與模型 registry 未就緒而自動回滾。

因此，現在可宣稱的是：

- `dev` code CI 可通過。
- Cloud Run build、push、candidate deploy、WIF、secret scan、SAST、SBOM 與 rollback path 可執行。
- Operator OIDC/RBAC smoke 的最小權限角色組合已修復。
- 主要產品流程具有 deterministic E2E 與大量技術 acceptance evidence。
- 系統能在 live dependencies 不完整時 fail closed，並回復 API/Web traffic 與 Scheduler targets。

目前不可宣稱的是：

- 不可宣稱 production-ready 或可一般上線。
- 不可宣稱外部 provider、live map、remote staging 已完成。
- 不可宣稱 ForecastOps、HeatZone、SiteScore、AVM、NetPlan 已通過正式 business/model activation gate。
- 不可用 deterministic fixture、repository-local receipt、task done 或 CI green 取代 exact-SHA staging/live proof 與 Human/Ops sign-off。

## 2. 盤點範圍與權威來源

本報告比對以下七層來源：

| 層級 | 權威來源 | 用途 |
|---|---|---|
| 正式規格 | 72 份 ODP governance、SA、DATA、SD、MOD、ML/OR、UX、QA、OPS 文件 | 最終產品範圍與驗收要求 |
| 工程基線 | `docs/architecture/ODAY_PLUS_EXECUTION_BASELINE.md` | 架構、模組、資料、模型、部署與 gate 原則 |
| 需求追蹤 | `docs/rtm/ODAY_PLUS_EXECUTION_RTM.md` | requirement -> implementation -> test -> evidence -> acceptance |
| 開發差異基線 | `docs/evidence/DEVELOPMENT_PLAN_IMPLEMENTATION_GAP_MATRIX_2026-07-30.md` | Stage 0-7、84 RTM rows 與 P0/P1/P2 缺口 |
| 執行控制 | `docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.md` 與 `.json` | 26 個治理 task、依賴、驗收及 Human Gate |
| 發布狀態 | `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` | Gate 0-6 machine-readable 狀態 |
| 即時 runtime | GitHub Actions `CI`、`Deploy Dev` 與 `docs/evidence/runtime/` | exact-SHA 實際執行結果 |

本次未重跑本地測試；測試結果只引用 repository evidence 與 GitHub Actions。盤點期間 `dev` 從 `40338298` 前進到 `a37283e2`，最終結論以後者為準。

## 3. 規格要求與目前實作總差異

| 規格面向 | 規格要求 | 最新 dev 狀態 | 差異判定 |
|---|---|---|---|
| 全模組範圍 | Integration、External Data、HeatZone、Listing、SiteScore、ForecastOps、InterventionOps、PriceOps、AdLift、DealRoomAVM、NetPlan、Learning Hub、OpsBoard、Governance、Audit 全部保留 | 所有主要模組已有程式落點，API 共 213 paths | **程式範圍大致齊備**，不代表 runtime activation 完成 |
| 前端 | Next.js OpsBoard shell 與角色工作區 | `/operator`、`/franchisee`、`/intake/[intakeId]` 三入口承載主要 workspace；大量 UI/component/E2E 已存在 | **大致實作**，仍有 fixture/fallback 雙路徑與 live map proof 缺口 |
| API | FastAPI、版本化 OpenAPI、明確 response/contract | 核心 domain 與 operator paths 齊備，OpenAPI client 已生成 | **大致實作**，仍需最新 SHA contract receipt 與 production composition proof |
| Transactional persistence | Cloud SQL，必要時 PostGIS；不可把 in-memory 當 production | 已有 PostgreSQL、document store、repository factory、tenant-scoped persistence | **部分完成**；多數模組仍保留 in-memory repository，需 exact-SHA 證明 production 未 fallback |
| Analytical data | BigQuery raw/canonical/mart/model-ready、dbt、PIT、lineage | schema、dbt/model-ready、quality/backfill 程式與文件存在 | **未正式過 gate**；最新 live ingestion runs 為 0 |
| External data | 真實 credentials、授權、排程、freshness、quota、quarantine、backfill | provider registry、live adapters、scheduler 與 fail-closed 機制存在 | **未啟用**；required providers 無 persisted ingestion run |
| Event/job | Pub/Sub、durable queue、retry、DLQ、status、receipt | queue、worker、scheduler、job receipt、rollback 皆有實作與 runtime evidence | **技術面進展高**，仍需候選版 live round-trip 與 final gate |
| Model lifecycle | dataset snapshot、model card、MLflow version/alias、shadow/canary、watch、rollback | MLflow/GX/Evidently/Dagster adapter 與 release code 存在 | **未完成正式 activation**；最新 registry versions=0，ForecastOps production alias=0 |
| Solver | hard constraints、infeasibility、runtime compatibility、human approval | solver compatibility 與 NetPlan technical acceptance 已完成 | **技術通過、業務未通過**；Human baseline approval/UAT 仍缺 |
| Security | OIDC、RBAC/ABAC、secret/SAST/dependency、privacy、export、IAM | 最新 CI green；deploy 的 secret scan/SAST/SBOM 通過；Operator composite roles 已 live readback | **大致可用但未封版**；OSS legal policy 與 exact-SHA Gate 4 receipt 尚缺 |
| Observability | logs、metrics、traces、dashboards、alerts、on-call、watch window | observability wiring 與多輪 review 已合併 | **尚無最新候選版整體 Gate receipt** |
| UAT | 角色 scripts、真實 staging、P0/P1 defect=0、正式簽核 | UAT plan 仍為 draft | **未完成** |
| Release | Gate 0-6、release metadata、rollback、DR、manifest、Human/Ops GO | CI green、Deploy Dev failure、rollback 成功 | **NO-GO** |
| Traceability | 84 RTM rows完整連到 implementation/test/evidence/acceptance | 工程 RTM 仍是 2026-06-26 foundation seed，只列 10 rows；release checklist 仍是空白模板 | **治理阻斷** |

## 4. Stage 0-7 詳細進度

### Stage 0：共同語意與平台基礎

**已完成或可用**

- canonical schemas、domain types、source contracts、migrations 已存在。
- FastAPI、Next.js、worker、scheduler、CLI、Terraform、Docker 與 Cloud Run workflow 已建立。
- OIDC、tenant isolation、RBAC/ABAC、audit、job receipt、rollback 與 observability primitives 已實作。
- 最新 CI `30820068695` 在 `a37283e2` 成功。

**仍缺**

- Gate Registry 仍綁舊候選 `e496be62`，不是最新 `a37283e2`。
- RTM 沒有從 10 個 foundation rows 更新到規劃盤點要求的 84 rows。
- Release checklist 的 release ID、SHA、data snapshot、model versions、owners 與所有 gate status 仍未填。
- 缺最新候選的 Code、Contract、Data、Security、Ops exact-SHA receipts。

**判定：PARTIAL / NO-GO**

### Stage 1：HeatZone 與 Listing MVP

**已完成或可用**

- HeatZone API、map、score jobs、H3、listing intake、assignment/state、provider contracts 與 UI 已存在。
- MapLibre/deck/H3 deterministic E2E、URL persistence、picking、resilience、tooltip 與 keyboard accessibility 已有證據。
- HeatZone outcome gate mechanics 可 fail closed。

**仍缺**

- 真實 HeatZone eligible labels 為 `0/200`，Gate receipt 是 `FAIL_CLOSED`。
- 最新部署的 `admin_boundary.official_dataset` 與 `poi.commercial_api` 都沒有 persisted live ingestion run。
- production credentials、listing/provider license、geocoder response proof 尚未完成。
- remote live tile 與 geocoder proof 尚未完成。
- Top-K survey rate 與優於 population ranking 的 business benchmark 尚無真實資料證據。

**判定：BLOCKED_DATA_AND_EXTERNAL**

### Stage 2：SiteScore 預測閉環

**已完成或可用**

- prediction run、score job、report、decision、realized outcome、promotion 與 PIT contract 路徑存在。
- 2026-08-02 已合併 SiteScore outcome mechanics 與 Gate 2 receipt 相關實作。
- 不足資料或不可信 prediction source 時會保持 governed-disabled。

**仍缺**

- 目前沒有權威 DB candidate/outcome inventory，eligible labels 仍是 0，規格門檻為至少 200。
- 需要真實 M6/M12 realized net revenue、interval bounds、model/version prediction lineage。
- `ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001` 與 prediction-source handback 尚未由權威資料提供。
- coverage/calibration、segment 與 business acceptance 不能以 `y_pred=y_true` 或 synthetic fallback 取代。

**判定：TECHNICAL_MECHANICS_DONE / GOVERNED_DISABLED**

### Stage 3：ForecastOps 成長軌跡與四燈

**已完成或可用**

- StatsForecast、MLForecast、LightGBM runtime、forecast jobs、outputs、alerts、acknowledgement 與 intervention handoff 路徑存在。
- 舊盤點曾觀察到 1,303 history rows，高於 90 rows 的最小資料門檻。

**仍缺**

- 最新 live deployment 明確回報 MLflow `versions=0`。
- `forecast_revenue_interval` 沒有任何 `production` alias；規格要求 exactly one。
- 缺同版綁定的 authoritative dataset hash、feature/label contract、model artifact、model card、metrics、MLflow readback、shadow/canary/watch/rollback receipts。
- 缺 baseline superiority、segment non-regression、alert precision/recall/lead-time 的正式 business validation。

**判定：CODE_READY / LIVE_MODEL_UNAVAILABLE**

### Stage 4：Learning Hub 與 OpsBoard 治理

**已完成或可用**

- Learning Hub model/version/release/monitor/evidence APIs 已存在。
- MLflow、Great Expectations、Evidently、Dagster、Optuna integration code 已存在。
- Operator live provenance、tenant scope、health mode 與 least-privilege composite RBAC 已補強。
- Dev smoke principal 對 Operator bootstrap、Learning Hub models、External Data ingestion runs、Audit events 的權限已可 200，匿名維持 401/403。

**仍缺**

- RBAC evidence 綁的是 `5a1aee5`，而目前候選為 `a37283e2`；且該 deploy run 的整體結論其實是 failure。
- RBAC endpoint smoke 通過不能取代 data/model live gate。
- 四個核心 production model cards、alias/canary/rollback 與正式 watch window 仍未形成最新候選 receipt。
- 全角色 UAT 與 Model Owner、Data Owner、Product、Security、SRE sign-off 未完成。

**判定：OPERATOR_CORE_AVAILABLE / MODEL_GOVERNANCE_NOT_RELEASED**

### Stage 5：InterventionOps、PriceOps、AdLift

**已完成或可用**

- Intervention state flow、eligibility、conflict、approval、execution、outcome 路徑完整。
- PriceOps simulate/optimize/submit/approve/activate/evaluate/observation/rollback API 存在。
- AdLift campaign、matched control、statsmodels WLS DiD、pre-trend 與 incrementality code 存在。
- 相關 deterministic product E2E 與 technical tests 已有 evidence。

**仍缺**

- 沒有 live pilot dataset 與 business owner approval。
- PriceOps 尚需證明 zero hard-constraint violation、真實 observation、rollback drill 與收益安全邊界。
- AdLift 尚需真實 matched-control、pre-trend pass、incremental GM 與 continue/stop decision proof。
- 需確認 production repository、audit/outcome 與 notification 沒有落回 in-memory/fallback。

**判定：TECHNICAL_PARTIAL / PILOT_UNVERIFIED**

### Stage 6：DealRoomAVM 與 NetPlan

**已完成或可用**

- AVM case、normalization、valuation、report、Data Room、finance approval、masked export 路徑存在。
- AVM outcome/calibration/confidential-access mechanics 已完成，無資料時會 fail closed。
- NetPlan hard constraints、alternatives、infeasibility diagnostics、result recomputation、immutable hashes 與 forged approval rejection 已 technical pass。
- NetPlan technical suites 的既有 evidence 包含 89 個 focused cases 與 OpenAPI/client checks。

**仍缺**

- AVM eligible mature labels 為 `0/120`，目前 receipt 是 `FAIL_CLOSED`、`GOVERNED_DISABLED`。
- 需要權威成交 outcome、prediction lineage、interval coverage、calibration、value-band separation 與 confidentiality audit。
- NetPlan 仍缺 immutable management baseline 的具名 Human/Ops authoritative approval receipt。
- NetPlan business UAT 仍是 `BUSINESS_UAT_UNVERIFIED`，不得宣稱優於管理 baseline 或啟用治理決策。

**判定：TECHNICAL_ACCEPTANCE_HIGH / BUSINESS_AND_DATA_BLOCKED**

### Stage 7：高階因果、Bandit、Deep、Robust

**已完成或可用**

- 計畫明確把 Contextual Bandit、Deep uplift、進階策略最佳化放在前置資料與 outcome 成熟後。

**仍缺**

- 尚未建立 production bandit runtime 等延後能力。

**判定：DEFERRED_BY_PLAN，非近期 release blocker**

## 5. 最新部署實證

### 5.1 成功部分

最新 `Deploy Dev` workflow（run `30820067751`，整體 conclusion 為 failure）在失敗前已成功完成下列步驟：

- checkout、locked dependencies、secret scan、Python SAST、SBOM。
- WIF authentication 與 live runtime preflight。
- Cloud SDK、Cosign、image build/push 與 candidate Cloud Run deploy。
- live gate failure 後的 API/Web traffic split restoration。
- live gate failure 後的 Cloud Scheduler trigger restoration。

### 5.2 阻斷部分

最新 `a37283e2` 在 live E2E gate 回報：

| Blocker | Runtime evidence |
|---|---|
| 沒有真實 ingestion | `data:ingestion_runs: runs=0` |
| Admin boundary 未入庫 | `admin_boundary.official_dataset:run_exists` 不存在 |
| POI 未入庫 | `poi.commercial_api:run_exists` 不存在 |
| ForecastOps registry 不可用 | `PRODUCTION_MODEL_REGISTRY_UNAVAILABLE` |
| MLflow 沒有版本 | `models:registry: versions=0` |
| Production alias 不存在 | `versionsWithProductionAlias=0`，要求 exactly one |

部署因此以 exit code 1 結束，候選版沒有取得 production traffic。這是正確的 fail-closed 行為，但也是目前無法上線的直接證據。

### 5.3 證據一致性風險

`ODP-OPERATOR-SMOKE-RBAC-LIVE-002` 文件把 deploy run `30809922826` 描述為 completed，並列出四個 endpoint 200；GitHub Actions 的整體 run conclusion 實際是 failure。兩者不一定互相矛盾，因為 endpoint/RBAC 子範圍可以通過，而完整 live data/model gate 仍失敗；但 release 文件必須明確區分 step-level pass 與 workflow-level failure，否則容易造成錯誤的 readiness claim。

最新 `a37283e2` 只新增 orchestrator source-document materialization runtime evidence，receipt 仍綁 `5a1aee5` 的 control-plane內容。它改善開發治理，不會解除產品 data/model/UAT release blockers。

## 6. Gate 0-6 差異

| Gate | 規格通過條件 | 現況 | 上線前必須補齊 |
|---|---|---|---|
| Gate 0 Code | lint/type/unit/component/build exact SHA | 最新 CI green，但 registry 無 `a37283e2` receipt | 生成 exact-SHA immutable build/code receipt，封版後不得再漂移 |
| Gate 1 Contract | OpenAPI/event/data/model compatibility | contract tests/client 存在；registry stale | 對封版 SHA 做 OpenAPI diff、event/schema/model IO compatibility 與 migration/rollback attestation |
| Gate 2 Data | real ingestion、freshness、lineage、PIT、backfill、quarantine | live ingestion runs=0 | 啟用 required providers，產生 persisted lineage-complete runs 與 data gate report |
| Gate 3 Model/Solver | dataset/model card/metrics/alias/canary/rollback；solver/human approval | solver technical pass；Forecast alias 0；HeatZone/SiteScore/AVM governed-disabled | 完成 Forecast release；補真實 labels/outcomes；NetPlan Human approval |
| Gate 4 Security/Privacy | scans、RBAC/ABAC、export、IAM、OSS license | scans/RBAC 進展高 | OSS legal policy、license-aware attestation、最新 SHA security receipt |
| Gate 5 E2E/Perf/UAT | live E2E、budgets、role UAT、P0/P1=0 | deterministic E2E 高；live E2E failed；UAT draft | staging live E2E、performance receipt、完整 role sign-off 與 defect closure |
| Gate 6 Ops/Release/Audit | deploy/rollback/monitor/on-call/DR/evidence manifest/Human GO | rollback 可用；release metadata、final audit、GO 缺失 | watch window、backup/restore drill、manifest、RTM、release note、Human/Ops final decision |

**目前 exact-SHA gate 通過數：0/7。**

## 7. 完整上線仍需補齊的功能與證據

### P0：不完成就不能部署候選版

| 順序 | 待辦 | 類型 | 完成條件 |
|---|---|---|---|
| 1 | 凍結一個 release candidate SHA | Release Engineering | 後續所有 data/model/security/E2E/UAT/ops receipts 綁同一 SHA |
| 2 | Provision 真實 external provider credentials | Platform/Ops + Data Partnerships | Secret Manager/GitHub environment readback；無 secret 洩漏 |
| 3 | 完成 provider contract 與 licensing | Legal + Data Partnerships | listing/POI/geocoder/admin boundary 的 allowed-use、expiry、quota、attribution、downstream use 已核准 |
| 4 | 執行真實 ingestion | Data Platform | `admin_boundary.official_dataset`、`poi.commercial_api` 及 release 所需 providers 都有 persisted run、lineage、freshness、quality、quarantine、retry/backfill receipt |
| 5 | 發布 ForecastOps production model | MLOps + Model Owner | MLflow 有 model version，`forecast_revenue_interval` exactly one production alias，且 dataset/card/metrics/shadow/canary/watch/rollback 同版綁定 |
| 6 | 重跑 Deploy Dev/live E2E | Platform/Ops | 最新候選 build/deploy/live gate 全綠，不觸發 rollback |
| 7 | 完成 remote staging proof | Platform/Ops | health/version、OIDC、DB、provider、event、model、audit、backup/restore/rollback drill 全部綁候選 SHA |

### P1：完整產品功能啟用前必須完成

| 待辦 | 目前差額 | 完成條件 |
|---|---|---|
| HeatZone label backfill | 0/200 | >=200 真實成熟 labels、lineage/hash/owner/freshness，且 ranking 優於 population baseline、Top-K survey rate 改善 |
| SiteScore outcome backfill | 0/200 | >=200 真實 M6/M12 outcomes、governed prediction source、coverage/calibration/segment metrics |
| AVM outcome backfill | 0/120 | >=120 成熟成交 outcomes、prediction join、coverage/calibration/value-band 與 confidential access proof |
| ForecastOps business validation | alias 0、正式 evidence 缺 | 優於 baseline、segment 無退化、alerts precision/recall/lead time 達標 |
| PriceOps pilot | 無 live pilot | 0 hard violation、真實 observation、收益/風險門檻、approval 與 rollback |
| AdLift pilot | 無 live campaign proof | matched control、pre-trend pass、incremental GM、continue/stop decision |
| NetPlan management gate | technical pass / human pending | 具名 authority receipt 綁 exact baseline/problem/source/policy/scope/release；Business UAT 通過 |
| Learning Hub release governance | code 有、正式 artifacts 缺 | 核心 model cards、alias、canary、monitor、rollback 與 owner sign-off |
| Live map | issues open | real tile endpoint、geocoder、low-confidence handling、keyboard/a11y 與 remote staging proof |
| Production persistence | 雙路徑存在 | 所有關鍵 module 證明使用 durable tenant-scoped stores，不落回 in-memory/fixture/fallback |
| Notification/admin/franchisee | API 已有，整體 production proof 缺 | live delivery、preferences、ack、role isolation、audit receipt 與 failure recovery |

### P2：正式發布治理與品質

| 待辦 | 完成條件 |
|---|---|
| 更新 RTM | 將 84 個規格 rows 完整連到 implementation、tests、evidence、owner、acceptance |
| 更新 Gate Registry | candidate SHA 改為封版 SHA；Gate 0-6 receipts 不可為空 |
| 完成 Release Checklist | release ID、build、data snapshot、model versions、flags、owners、decision 全部填寫 |
| OSS legal gate | license-aware SBOM、NOTICE reconciliation、allow/deny/review policy、authoritative legal approval |
| Engineering hardening | OpenAPI/client drift、dependency highs、build warnings、bundle/performance、stale docs 全部關閉或具名接受 |
| Observability closeout | dashboards、alerts、on-call route、SLO owner、完整 watch window 與 test alert delivery receipt |
| UAT | 所有受影響角色完成 scripts；P0/P1 defects 為 0 或正式 accepted-with-actions |
| Final Gate Audit | 重新盤點 Stage 0-7、84 RTM rows、26 tasks、Gate 0-6、所有 live proof |
| Human/Ops GO | Product、QA、Security、SRE、Business/Finance/Legal/Model Validation 依範圍正式簽核 |

## 8. 尚開啟的外部 release blockers

| Issue | 阻斷內容 |
|---|---|
| #132 `ODP-EXT-PROD-001` | production provider credentials |
| #133 `ODP-EXT-PROD-002` | listing/provider license proof |
| #134 `ODP-EXT-PROD-003` | production geocoder 與 low-confidence handling |
| #135 `ODP-MAP-STAGE-001` | remote staging live tile |
| #136 `ODP-MAP-STAGE-002` | remote staging live geocoder |
| #137 `ODP-PV-STAGE-001` | remote staging health/version |
| #138 `ODP-PV-STAGE-002` | staging smoke、backup/restore/rollback drill |

上述 7 項在本次盤點時全部仍為 open，且帶有 `release-blocker` label。

## 9. 建議上線路徑

### 路徑 A：完整功能一次上線

這是本報告對「完整上線」的標準。必須先完成 P0、所有 P1 模組 activation、P2 governance/UAT/final audit，再做 Human/Ops GO。HeatZone、SiteScore、AVM 的成熟 outcome 數量是長路徑，應優先啟動 historical backfill 與 authority review。

### 路徑 B：受控分階段上線

若業務允許先上非模型核心，可讓尚未成熟的 HeatZone model、SiteScore、AVM、NetPlan decision、PriceOps/AdLift activation 維持 `GOVERNED_DISABLED`，只發布已通過 live gate 的 Operator/read-only/workflow 功能。但仍不可繞過真實 ingestion、Forecast model dependency、remote staging、security、UAT 與 release gates；且 scope reduction 必須走正式 change control，不可直接把未完成規格視為取消。

## 10. 最終判定

ODay Plus 已從早期 product skeleton 前進到「功能面廣泛實作、fail-closed 與 rollback 可運作、CI 可綠」的階段；但距離「完整上線」仍差四個決定性閉環：

1. **真實資料閉環**：required provider ingestion、lineage、freshness、labels/outcomes。
2. **正式模型閉環**：MLflow version/alias、model card、metrics、canary/watch/rollback。
3. **業務與法遵閉環**：license、NetPlan baseline、pilot results、跨角色 UAT/sign-off。
4. **發布治理閉環**：最新 SHA Gate 0-6、84-row RTM、remote staging、final audit、Human/Ops GO。

在上述四個閉環完成前，最新 `dev` 的正確 release decision 仍為 **NO-GO**。
