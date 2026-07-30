---
doc_id: ODP-DEVELOPMENT-PLAN-IMPLEMENTATION-GAP-MATRIX-2026-07-30
title: ODay Plus Development Plan to Implementation Gap Matrix
status: code-audit-complete-live-proof-pending
audit_date: 2026-07-30
audited_commit: e6e12324617d
language: zh-TW
---

# ODay Plus 開發規劃—實作—證據—缺口完整矩陣

## 1. 結論

ODay Plus 已具備相當完整的工程底座、domain service、AI/ML runtime、
solver、資料品質、MLOps 與治理元件；問題不是「只有畫面」或「只有
Notebook」。真正尚未完成的是：

1. 規劃中的 Stage 0–6 商業驗收 Gate 尚無足夠 production 證據。
2. HeatZone、SiteScore、DealRoomAVM 的正式模型因 model-ready label
   為 0 而治理式停用。
3. ForecastOps 是目前唯一具非零正式訓練 inventory 的核心模型，但
   repository 內仍沒有足以證明 production alias、實際指標、canary
   與 rollback 已完成的環境證據。
4. Learning Hub、PriceOps、AdLift、AVM、NetPlan 等有真實 OSS
   implementation，但多數只證明「程式能執行」，尚未證明「資料有效、
   商業 Gate 通過、使用者接受、production 可營運」。
5. OSS SBOM 已存在，但缺 license、dependency graph、supplier、
   hash 與 policy decision；目前沒有完整 OSS 授權合規 gate。
6. Package 10 刪除舊 routes 是規劃行為，不是缺漏；應稽核的是能力
   是否已併回 canonical runtime，以及本期規劃是否要求對應 UI。
7. OR-Tools 與 CVXPY/highspy 在同一 Python process 具有可重現的
   native-library load-order/ABI 衝突；目前 capability API 只檢查
   package 是否存在，會把這種不可共存狀態誤報為 available。

因此目前最合理的整體判定是：

- 工程能力：Stage 0–6 多數已有實作。
- 模型／商業成熟度：Stage 1–6 Gate 尚未證實通過。
- production readiness：`NO-GO`，直到 live evidence、UAT、model
  release evidence 與正式 sign-off 補齊。

## 2. 權威來源與版本

### 2.1 原始產品與實作規劃

本次直接讀取 Google Drive 原始文件：

- 名稱：`Oday Plus展店 管理系統`
- Drive ID：`1RH1XOd7_3VEUIdSEwnNeSXDg379gwZJROvj8EsuAxhU`
- 建立時間：`2026-06-19T13:53:27.986Z`
- 修改時間：`2026-07-02T03:22:08.726Z`
- URL：
  `https://docs.google.com/document/d/1RH1XOd7_3VEUIdSEwnNeSXDg379gwZJROvj8EsuAxhU`

本次逐條採用其：

- 第 69 章 OSS 主選、替代方案與階段。
- 第 70 章三條平行工作流與 Stage 0–7 路線圖。
- 第 71–78 章各 Stage 交付物與驗收門檻。
- 第 79 章模組成熟度。
- 第 80 章 Production Readiness Checklist。
- 第 81 章最小可正式運作架構。

本矩陣共登錄 **84 個不重複 RTM 項目**（含各 Stage Gate），逐 Stage
數量為：Stage 0 = 12、Stage 1 = 12、Stage 2 = 10、Stage 3 = 9、
Stage 4 = 11、Stage 5 = 12、Stage 6 = 11、Stage 7 = 7。這 84 項是
本次「完整 WBS／RTM 對照」的 coverage baseline；P0/P1/P2 缺口與
execution tasks 均由這些逐條判定彙整，不以模組名稱或現有測試數量
替代原始規劃項目。

可用下列唯讀檢查重算 coverage，預期 `rows=84` 且 `unique=84`：

```bash
python3 - <<'PY'
import re
from pathlib import Path

text = Path(
    "docs/evidence/DEVELOPMENT_PLAN_IMPLEMENTATION_GAP_MATRIX_2026-07-30.md"
).read_text()
ids = re.findall(r"\| (PLAN-S[0-7]-(?:\d{3}|GATE)) \|", text)
print(f"rows={len(ids)} unique={len(set(ids))}")
PY
```

### 2.2 Repository 衍生治理來源

原始 Drive 規劃定義產品目的與能力順序；以下較晚、已核准文件可調整
工程實現方式，但不得默默縮小最終產品範圍：

- `docs/architecture/ODAY_PLUS_EXECUTION_BASELINE.md`
- `docs/adr/ADR-0001-platform-foundation.md`
- `docs/design/PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md`
- `docs/release/RELEASE_GATE_CHECKLIST.md`
- `docs/uat/UAT_ACCEPTANCE_PLAN.md`

已接受的技術替代包括：

- 原規劃 NATS → ADR 核准的 Pub/Sub-style bus。
- 原規劃 Keycloak → 標準 OIDC/JWKS provider boundary。
- Airflow/Dagster → 實作選用 Dagster。
- 模組獨立服務 → 初期採 modular-monolith-first，加 deployable workers。

這些是架構替代，不是功能完成證據。

## 3. 狀態定義

| 狀態 | 定義 |
|---|---|
| `IMPLEMENTED` | 有實際 production-path 程式、不是只有 README／fixture |
| `PARTIAL` | 有主要程式，但規劃要求的一部分尚未完成 |
| `BLOCKED_DATA` | 程式存在，但正式資料或標籤未達啟動門檻 |
| `LIVE_UNVERIFIED` | production composition 已設計，但此次無 live 環境證據 |
| `DEFERRED` | 原規劃明確要求較晚 Stage 或前置 Gate 後再啟用 |
| `NOT_IMPLEMENTED` | 未找到對應 runtime 或可執行 product path |
| `DOC_DRIFT` | 文件、gate 或證據與目前程式樹不一致 |

## 4. Stage 總覽

| Stage | 原始規劃目標 | 工程實作 | Gate 證據 | 綜合判定 |
|---|---|---|---|---|
| 0 | 共同語意、資料、MLflow、Decision Log、OpsBoard、環境與監控 | 大部分存在 | 無完整 staging/live 驗收 | `PARTIAL` |
| 1 | HeatZone + Listing MVP | API、worker、地圖、intake、provider、H3 均存在 | 無正式模型與業務 Top-K 成效 | `BLOCKED_DATA` |
| 2 | SiteScore 預測閉環 | API、report、decision、promotion、PIT contract 存在 | 0 eligible labels；PG16 outcome query 未選 governed prediction 欄位，模型即使取得 outcome 仍無法進入 ACTIVE | `BLOCKED_DATA_AND_WIRING` |
| 3 | ForecastOps 成長軌跡與四燈 | 真實 StatsForecast/MLForecast/LightGBM runtime 存在 | 1,303 rows；live alias／商業指標未證明 | `LIVE_UNVERIFIED` |
| 4 | Learning Hub + OpsBoard 治理 | MLflow、GX、Evidently、Dagster、release/rollback code 存在 | 正式 model cards、canary、rollback drill、UAT 未齊 | `PARTIAL` |
| 5 | Intervention + PriceOps + AdLift | state flow、solver、statsmodels DiD 均存在 | 無 live pilot／incremental GM business proof | `PARTIAL` |
| 6 | AVM + NetPlan | 估值、lifelines、OR-Tools、CVXPY、infeasibility 均存在 | AVM 0 labels；無管理層 acceptance/outcome | `BLOCKED_DATA` |
| 7 | 高階因果、Bandit、Deep、Robust | Robust/Optuna/pymoo 已整合；其他多數未啟用 | 前置 Gate 未成立 | `DEFERRED` |

## 5. Stage 0：共同語意與平台基礎

| RTM ID | 規劃要求 | 現行實作／證據 | 狀態 | 缺口 |
|---|---|---|---|---|
| PLAN-S0-001 | 品牌、店型、設備、店齡與干預共同語意 | canonical schemas、domain types、source contracts、migrations | `IMPLEMENTED` | 原始業務 owner 核准紀錄未在 repo |
| PLAN-S0-002 | Feature Marts / model-ready views | `pipelines/dbt/models/model_ready/*`、`scripts/models/sql/model_ready_views.sql` | `IMPLEMENTED` | 未取得 production dbt run、lineage 與 freshness report |
| PLAN-S0-003 | Label Registry / Outcome Maturity | Learning Hub domain 與 release service | `IMPLEMENTED` | 正式 label owner sign-off 與成熟度營運證據不足 |
| PLAN-S0-004 | Dataset Snapshot 可重現 | dataset snapshot、artifact checksum、model-ready receipt | `IMPLEMENTED` | 只有 receipt；缺 production artifact store readback evidence |
| PLAN-S0-005 | MLflow Tracking / Registry | MLflow adapter、獨立 image、alias reconcile、release service | `LIVE_UNVERIFIED` | 未驗證目前 production `MLFLOW_TRACKING_URI`、artifact storage、alias |
| PLAN-S0-006 | Decision Log / Prediction trace | shared audit、decision records、model lineage、WORM evidence | `IMPLEMENTED` | 需 production audit export 與 restore/replay proof |
| PLAN-S0-007 | OpsBoard Skeleton | canonical `/operator` 與五大 workspace | `IMPLEMENTED` | live Shell 多個資源尚未接妥 |
| PLAN-S0-008 | Docker / Dev / Staging | API/Web/Worker/Scheduler images、Terraform、Cloud Run workflows | `LIVE_UNVERIFIED` | 此次未取得目前 staging deployment readback |
| PLAN-S0-009 | Prometheus / Grafana / OpenTelemetry | metrics/tracing abstraction、monitoring JSON | `PARTIAL` | 未見 Prometheus/Grafana deployable runtime 或 Cloud Monitoring exporter wiring |
| PLAN-S0-010 | Event bus | transactional outbox、Pub/Sub Terraform、DLQ | `IMPLEMENTED` | NATS 已由 ADR 合理替代；仍需 live publish/DLQ/replay proof |
| PLAN-S0-011 | Identity / authorization | OIDC/JWKS、RBAC/ABAC、service identity、Terraform contract | `IMPLEMENTED` | Keycloak 已合理替代；需 live IdP acceptance 與角色 UAT |
| PLAN-S0-GATE | 可重建、可追溯、Dev/Staging 可部署、監控可用 | local tests 與 IaC 多數齊備 | `LIVE_UNVERIFIED` | 尚不能宣稱 Gate 0 通過 |

## 6. Stage 1：HeatZone 與 Listing MVP

| RTM ID | 規劃要求 | 現行實作／證據 | 狀態 | 缺口 |
|---|---|---|---|---|
| PLAN-S1-001 | H3 全台格網 | H3 Python/JS、geo model-ready view、map layer | `IMPLEMENTED` | production 全台 materialization/freshness 未驗證 |
| PLAN-S1-002 | 人口、POI、租金、競店、自家店 | provider registry、canonical connectors、geo feature snapshot | `PARTIAL` | competitor 仍是人工來源；listing partner 尚未簽約 |
| PLAN-S1-003 | 透明加權 Baseline | `modules/heatzone/domain/scoring.py` | `IMPLEMENTED` | 缺對人口排序的正式 benchmark |
| PLAN-S1-004 | CatBoost/LightGBM Demand Model | 通用 trainer與 production contract 存在 | `BLOCKED_DATA` | eligible label = 0；啟動門檻 200 |
| PLAN-S1-005 | 熱區地圖、Priority Rank、Confidence | canonical Network map 與 HeatZone result | `IMPLEMENTED` | frontend geocoder URL 只顯示狀態，沒有地址搜尋請求 |
| PLAN-S1-006 | 人工輸入 Listing | assisted intake | `IMPLEMENTED` | 無缺口 |
| PLAN-S1-007 | CSV/Excel Listing | CSV import path 存在 | `PARTIAL` | Excel/xlsx 直接 ingestion 與 browser proof 不明確 |
| PLAN-S1-008 | 合作仲介 feed / API | live partner feed adapter 完整 | `BLOCKED_DATA` | 無簽約 endpoint/credential；不是程式缺口 |
| PLAN-S1-009 | 地址正規化、Geocode、H3 | backend live/replay geocoder、H3、confidence | `IMPLEMENTED` | map geocoder UX 未接；live credential proof 未驗證 |
| PLAN-S1-010 | 去重、Hard Rules、HeatZone Filter | Listing pipeline、identity/dedup、promotion gates | `IMPLEMENTED` | 需 production precision/recall 與人工抽驗 |
| PLAN-S1-011 | 區域指派與物件狀態管理 | Network workspace/intake assignment/state machine | `PARTIAL` | production live Shell assignment endpoint unavailable |
| PLAN-S1-GATE | 排序優於人口、Geocode/去重達標、Top-K 現勘率改善 | 只有 contract/unit/E2E | `NOT_IMPLEMENTED` | 缺業務 benchmark、抽樣報告與 adoption outcome |

## 7. Stage 2：SiteScore 預測閉環

| RTM ID | 規劃要求 | 現行實作／證據 | 狀態 | 缺口 |
|---|---|---|---|---|
| PLAN-S2-001 | External Demand / ODay 校正 / G2 配置 | feature contract、candidate-site view、domain scoring | `PARTIAL` | 無成熟 ODay outcomes 支援正式訓練 |
| PLAN-S2-002 | Ramp / Seasonality | model-ready ramp views與 report fields | `PARTIAL` | 未證明 production model 真正學到 Ramp/Seasonality |
| PLAN-S2-003 | M1/M3/M6/M12 P10/P50/P90 | report/output contracts支援 interval | `PARTIAL` | 目前 model spec label 為 90-day revenue；完整多 horizon validation 未證明 |
| PLAN-S2-004 | 回本期 / Feasibility Rules | SiteScore domain report、decision workflow | `IMPLEMENTED` | 財務基線與 production calibration 未簽核 |
| PLAN-S2-005 | Brand Transfer / Hierarchical Shrinkage | model-ready view與設計 contract | `PARTIAL` | 未見正式 PyMC/階層模型 runtime；無資料驗證 |
| PLAN-S2-006 | Comparable / Cannibalization | comparable與 cannibalization fields/flows | `PARTIAL` | 缺 live retrieval quality proof |
| PLAN-S2-007 | Conformal Calibration | shared OSS estimator interval支援 | `IMPLEMENTED` | SiteScore 無有效資料執行 calibration |
| PLAN-S2-008 | 人工 GO/WAIT/REJECT | Network Review workflow、audit | `IMPLEMENTED` | role UAT 未完成 |
| PLAN-S2-009 | Realization Ratio 回饋 | schema/view/outcome contracts | `PARTIAL` | 沒有成熟 M3/M6 outcome inventory |
| PLAN-S2-GATE | Holdout、誤差、coverage、人工核准、outcome 回收 | eligible label = 0 | `BLOCKED_DATA` | Gate 2 未通過 |

## 8. Stage 3：ForecastOps 成長軌跡與四燈

| RTM ID | 規劃要求 | 現行實作／證據 | 狀態 | 缺口 |
|---|---|---|---|---|
| PLAN-S3-001 | 4/8/12/24 週 forecast | Forecast domain/output contract | `IMPLEMENTED` | live API payload需環境 readback |
| PLAN-S3-002 | Seasonal Naive baseline | StatsForecast adapter | `IMPLEMENTED` | 無正式 baseline comparison report |
| PLAN-S3-003 | MLForecast + CatBoost/LightGBM Champion | MLForecast adapter與 LightGBM production training spec | `IMPLEMENTED` | production alias/metric evidence未在 repo |
| PLAN-S3-004 | P10/P50/P90 / calibration | interval contract、quantile/conformal utilities | `IMPLEMENTED` | 正式 coverage/width 指標未附 |
| PLAN-S3-005 | Growth stage / trajectory | application/domain state outputs | `IMPLEMENTED` | 未見 production confusion/segment report |
| PLAN-S3-006 | Change point：ruptures / CUSUM / EWMA | alert/root-cause code存在 | `PARTIAL` | `ruptures` 未安裝；需確認現行替代算法與 ADR |
| PLAN-S3-007 | 四燈與 Root Cause Evidence | ForecastOps + Store Ops integration | `IMPLEMENTED` | 橙/紅燈 precision、recall、lead-time 未證明 |
| PLAN-S3-008 | 模型可從正式資料訓練 | receipt = 1,303 eligible rows | `IMPLEMENTED` | 仍需當前 production dataset與MLflow readback |
| PLAN-S3-GATE | 優於 Seasonal Naive、校準、segment 無退化、告警可營運 | repository 無正式 gate report | `LIVE_UNVERIFIED` | Gate 3 尚不能宣稱通過 |

## 9. Stage 4：Learning Hub 與 OpsBoard 治理

| RTM ID | 規劃要求 | 現行實作／證據 | 狀態 | 缺口 |
|---|---|---|---|---|
| PLAN-S4-001 | Feature / Label Contract | shared ML + Learning Hub | `IMPLEMENTED` | production ownership sign-off 未附 |
| PLAN-S4-002 | Outcome Maturity | dataset/label maturity contracts | `IMPLEMENTED` | 實際營運 maturity queue 未驗證 |
| PLAN-S4-003 | Data Quality Gate 阻擋訓練 | Great Expectations + Dagster flow | `IMPLEMENTED` | live scheduled run 與 alert proof 未附 |
| PLAN-S4-004 | Model Card | model-card domain與release validation | `PARTIAL` | repo 未保存四個核心 production model card artifact |
| PLAN-S4-005 | Decision Card | decision/audit primitives | `PARTIAL` | 缺可查核的正式 decision card bundle |
| PLAN-S4-006 | Champion / Challenger | registry、validation、alias contract | `IMPLEMENTED` | 多數服務無可用 champion data |
| PLAN-S4-007 | Shadow / Canary | release service與監控 guardrails | `IMPLEMENTED` | 缺 production canary receipt |
| PLAN-S4-008 | Rollback | alias rollback、guardrail evaluation | `IMPLEMENTED` | 缺實際 production rollback drill |
| PLAN-S4-009 | 人工核准與 Audit | OpsBoard governance/audit | `IMPLEMENTED` | live Shell/admin/settings/franchisee部分不可用 |
| PLAN-S4-010 | Drift monitoring | Evidently adapter | `IMPLEMENTED` | live drift schedule/dashboard/owner evidence未附 |
| PLAN-S4-GATE | production模型皆有card、可追溯、alias/canary/rollback可用 | 只有程式／測試證據 | `LIVE_UNVERIFIED` | Gate 4 未通過 |

## 10. Stage 5：InterventionOps、PriceOps、AdLift

| RTM ID | 規劃要求 | 現行實作／證據 | 狀態 | 缺口 |
|---|---|---|---|---|
| PLAN-S5-001 | Eligibility / Action Set / Conflict | Intervention domain/application | `IMPLEMENTED` | canonical完整UI與角色UAT不足 |
| PLAN-S5-002 | Approval / Execution / Reward Window / Outcome | intervention workflow與audit | `IMPLEMENTED` | live production閉環未驗證 |
| PLAN-S5-003 | Evidence Level | Intervention/AdLift evidence contract | `IMPLEMENTED` | live outcome成熟度不足 |
| PLAN-S5-004 | Price elasticity | `models/priceops/elasticity.py` | `IMPLEMENTED` | 模型較基礎；無正式 pilot calibration |
| PLAN-S5-005 | Safe Action Set / Hard Constraints | pricing constraints、hold/infeasible paths | `IMPLEMENTED` | live constraint audit 未附 |
| PLAN-S5-006 | Demand Simulation / OR-Tools optimization | pricing solver與production optimizer | `PARTIAL` | 無實際 price pilot；CVXPY先載入後可能使後續OR-Tools載入失敗 |
| PLAN-S5-007 | Price人工核准與rollback | PriceOps workflow | `PARTIAL` | browser完整旅程與live rollback drill不足 |
| PLAN-S5-008 | Ad Need Score / Matched Controls | matching與campaign domain | `IMPLEMENTED` | live campaign selection evidence未附 |
| PLAN-S5-009 | DiD / Pre-trend | 真實 statsmodels WLS matched-control DiD | `IMPLEMENTED` | production dataset與pre-trend pass未附 |
| PLAN-S5-010 | Incremental Revenue/GM、iROMI、Continue/Stop | report contract與recommendation | `IMPLEMENTED` | 無business sign-off |
| PLAN-S5-011 | 不應提早使用 Bandit/MMM | Bandit/PyMC 未啟用 | `DEFERRED` | 符合規劃 |
| PLAN-S5-GATE | 可追蹤、零hard violation、pilot安全、pre-trend與增量毛利成立 | 無production pilot evidence | `LIVE_UNVERIFIED` | Gate 5 未通過 |

## 11. Stage 6：DealRoomAVM 與 NetPlan

| RTM ID | 規劃要求 | 現行實作／證據 | 狀態 | 缺口 |
|---|---|---|---|---|
| PLAN-S6-001 | Normalized GM / Income / Asset Approach | AVM domain/service | `IMPLEMENTED` | 財務核准與live input quality未附 |
| PLAN-S6-002 | Manual Comparable / P10/P50/P90 / Reserve | AVM contracts與API | `IMPLEMENTED` | 正式區間校準不可用 |
| PLAN-S6-003 | Quantile GBDT / Conformal | LightGBM quantile training spec | `BLOCKED_DATA` | AVM eligible label = 0；門檻120 |
| PLAN-S6-004 | Comparable Retrieval | AVM data contracts | `PARTIAL` | live comparable retrieval quality未證明 |
| PLAN-S6-005 | Liquidity Survival | lifelines CoxPH adapter | `IMPLEMENTED` | production artifact/data activation未驗證 |
| PLAN-S6-006 | Data Room | backend/domain能力與歷史UI證據 | `PARTIAL` | canonical完整finance/legal旅程與E2E不足 |
| PLAN-S6-007 | Deterministic CP-SAT NetPlan | OR-Tools solver | `PARTIAL` | 隔離測試通過，但與CVXPY/highspy同process有load-order ABI衝突 |
| PLAN-S6-008 | OPEN/KEEP/IMPROVE/MOVE/EXIT + constraints | NetPlan model/solver | `IMPLEMENTED` | 管理層UAT未完成 |
| PLAN-S6-009 | Scenario、Alternative、Binding、Infeasibility | OR-Tools/CVXPY diagnostics | `IMPLEMENTED` | browser detail與production solver report不足 |
| PLAN-S6-010 | 90/180/365 day outcome | outcome contracts | `PARTIAL` | 無成熟 production outcomes |
| PLAN-S6-GATE | AVM coverage、價值帶分離、硬限制100%、優於baseline、outcome回收 | AVM資料未成熟、管理驗收缺失 | `BLOCKED_DATA` | Gate 6 未通過 |

## 12. Stage 7：高階因果、Bandit、Deep、Robust

| RTM ID | 規劃要求／啟動條件 | 現況 | 狀態 | 判定 |
|---|---|---|---|---|
| PLAN-S7-001 | Contextual Bandit：propensity/action/reward成熟後 | 無 Vowpal Wabbit / bandit runtime | `DEFERRED` | 正確，不應現在補成production |
| PLAN-S7-002 | Uplift/HTE：多treatment且CATE可驗證 | DoubleML/EconML adapter contract；dependency未選 | `DEFERRED` | 正確 |
| PLAN-S7-003 | Bayesian MMM：長期多渠道資料後 | PyMC未安裝 | `DEFERRED` | 正確 |
| PLAN-S7-004 | Deep Forecast：穩定優於Champion後 | NeuralForecast/TFT/LSTM未啟用 | `DEFERRED` | 正確 |
| PLAN-S7-005 | Robust/Stochastic NetPlan | CVXPY robust solver已實作 | `PARTIAL` | 可作研究/challenger；前置Gate未成立，不應production activate |
| PLAN-S7-006 | Multi-objective portfolio | pymoo NSGA-II已實作 | `PARTIAL` | 可用能力，不代表Stage 7通過 |
| PLAN-S7-GATE | 前置模型、資料、policy outcome全部成熟 | 尚未成立 | `DEFERRED` | Stage 7 不構成近期release blocker |

## 13. AI／模型實作矩陣

| 能力 | 演算法／runtime | 程式狀態 | 正式資料 | production 判定 |
|---|---|---|---:|---|
| HeatZone | transparent score + CatBoost contract | worker/scoring/trainer存在 | 0 / 200 | `GOVERNED_DISABLED` |
| SiteScore | CatBoost + interval transform | report/worker/trainer存在 | 0 / 200 | `GOVERNED_DISABLED` |
| ForecastOps | StatsForecast、MLForecast、LightGBM | 真實engine、trainer、serving binding存在 | 1,303 / 90 | `LIVE_ALIAS_UNVERIFIED` |
| PriceOps | elasticity + OR-Tools/CVXPY/Optuna | 真實optimizer存在 | 不以model-ready receipt控制 | `PILOT_UNVERIFIED` |
| AdLift | matched control + statsmodels WLS DiD | 真實causal estimator存在 | 無live campaign proof | `PILOT_UNVERIFIED` |
| AVM | income/asset + LightGBM quantile | baseline與trainer存在 | 0 / 120 | `GOVERNED_DISABLED` |
| AVM Liquidity | lifelines CoxPH | 真實fit/predict/artifact存在 | 未見正式activation receipt | `LIVE_UNVERIFIED` |
| NetPlan | OR-Tools deterministic、CVXPY robust | 真實solver與diagnostic存在 | scenario-driven | `BUSINESS_UAT_UNVERIFIED` |
| Learning Hub | MLflow/GX/Evidently/Dagster/Optuna | 真實integration存在 | 依各模型 | `LIVE_ENV_UNVERIFIED` |

關鍵判斷：

- 「套件已安裝」不等於「模型已訓練」。
- 「模型可訓練」不等於「Production alias 已核准」。
- 「alias 可解析」不等於「Shadow/Canary/UAT 已通過」。
- readiness 接受 `governed_disabled` 是安全政策，不代表產品功能已交付。

## 14. OSS 選型與實際接線

### 14.1 已有可執行整合

| OSS | 原規劃用途 | 現況 |
|---|---|---|
| PostgreSQL/PostGIS | transactional + geo | image、migration、production persistence |
| dbt Core | model-ready marts | project/models存在；production run未驗證 |
| MLflow | tracking/registry | adapter/image/release code完整；live未驗證 |
| H3 | geo grid | Python/JS皆實際使用 |
| CatBoost/LightGBM | tabular models | trainer/artifact/serving支援 |
| StatsForecast/MLForecast | forecast | production-selectable adapter |
| OR-Tools | pricing/netplan/scheduling | 多個真實solver |
| Great Expectations | data quality | fail-closed training gate |
| Evidently | drift | adapter存在 |
| Dagster | orchestration | executable pipeline |
| statsmodels | AdLift DiD | 真實WLS estimator |
| lifelines | AVM liquidity | CoxPH artifact runtime |
| CVXPY | robust/price optimization | executable |
| Pyomo | alternate solver model | installed/capability-only |
| Optuna | hyperparameter search | executable |
| pymoo | multi-objective | executable |

### 14.1.1 OR-Tools / HiGHS load-order 缺陷

本次在 locked `.venv` 可重現：

```text
import cvxpy
from ortools.sat.python import cp_model
→ ImportError: libortools.so.9 undefined symbol setLocalOptionValue(...)
```

反向順序：

```text
from ortools.sat.python import cp_model
import cvxpy
→ OR-Tools 可載入，但 CVXPY 匯入 HiGHS 時出現
  highspy/_core... undefined symbol Highs::releaseMemory()
```

每組 solver 測試在獨立 process 皆可通過，但把 PriceOps/CVXPY 與
NetPlan/OR-Tools 放在同一 pytest process 時，會在 collection 階段失敗。
這是實際 runtime composition 缺陷，不是單純測試寫法問題。

`models/shared_ml/oss_capabilities.py` 目前只用 `find_spec` 與 package
metadata 判斷 available，沒有做 import/probe/solve，因此 capability
endpoint 會誤報。修復需包含：

1. pin 一組 ABI 相容的 `ortools` / `highspy` / `cvxpy` / `scipy`；
2. 新增同 process、雙向 import order test；
3. capability probe 實際 import 並執行最小 solve；
4. production image 啟動前執行 solver smoke；
5. 若無法共存，將 solver 拆成隔離 worker/process並以contract連接。

### 14.2 合理替代

| 原規劃 | 現行替代 | 判定 |
|---|---|---|
| NATS | GCP Pub/Sub + transactional outbox | ADR已核准，非缺漏 |
| Keycloak | provider-neutral OIDC/JWKS | 合理替代；需live IdP proof |
| Airflow | Dagster | 原規劃允許二擇一 |
| 獨立微服務 | modular monolith + deployable workers | ADR已核准 |

### 14.3 尚未實作或依規劃延後

| OSS | 原規劃階段 | 現況 | 是否近期缺口 |
|---|---|---|---|
| GeoPandas | Phase 1 | 未安裝 | 需確認H3/SQL替代是否滿足全部geo分析 |
| ruptures | Phase 1 | 未安裝 | Stage 3缺口，除非正式指定CUSUM/EWMA替代 |
| pgvector | Phase 2 | 未安裝 | 依comparable/search需求決定 |
| DoWhy / EconML | Phase 2/3 | adapter contract有，runtime未選 | 正確延後 |
| PyMC | Phase 2/3 | 未安裝 | 正確延後 |
| Feast | Phase 2 | 未安裝 | 原規劃明確可延後 |
| Temporal / Camunda | Phase 2 | 未安裝 | durable job framework可替代，需ADR明記 |
| OPA engine | Phase 2 | 未安裝 | 現以後端RBAC/ABAC替代 |
| NeuralForecast/TFT | Phase 3 | 未安裝 | 正確延後 |
| Vowpal Wabbit | Phase 3 | 未安裝 | 正確延後 |
| Superset | Phase 1 | 未安裝 | 若OpsBoard已涵蓋BI需求，可做範圍決策 |

## 15. OSS 安全與授權治理

### 15.1 已有

- `uv.lock`、`package-lock.json` 固定版本。
- CycloneDX 1.5 SBOM 產生器。
- CI執行 Python `pip-audit` 與 production npm audit。
- secret scan、Python SAST。
- 本次 Python `pip-audit --local`：0 known vulnerabilities。
- 本次 npm production audit：0。

### 15.2 不足

1. SBOM 只有 name/version/purl，缺：
   - license expression；
   - supplier/author；
   - dependency graph；
   - package hash；
   - component scope；
   - vulnerability/analysis state。
2. 無自動 license policy gate：
   - 未見 allowlist/denylist；
   - 未見 copyleft/AGPL/SSPL 檢查；
   - 未見 Apache NOTICE 聚合；
   - 未見 THIRD_PARTY_NOTICES。
3. dev dependencies 有 13 個 high npm vulnerabilities；production audit
   會忽略它們。雖不進runtime，仍是CI/supply-chain風險。
4. SBOM於 deploy workflow生成，但尚未看到：
   - 與 image attestation 綁定；
   - signature/provenance readback；
   - release artifact retention；
   - policy failure條件。
5. OSS license allow/deny/review policy 與例外屬法務／風險決策；AI owner
   或 reviewer 不得自行把 LGPL 等條款核准為 production allowlist，也
   不得以 AI 名義簽署 exemption。缺 Human/Ops/Legal 具名政策 owner、
   決策版本與核准 receipt 時必須 fail closed。

OSS governance 因此判定為 `PARTIAL`，不是 production-complete。

## 16. Canonical 前端範圍修正

Package 10 明確要求只保留：

- `apps/web/src/app/operator/page.tsx`
- `apps/web/src/app/intake/[intakeId]/page.tsx`
- `apps/web/src/app/franchisee/page.tsx`

因此舊 routes 被刪除本身是 `IMPLEMENTED`，不應列為缺漏。

仍需追蹤的是：

1. 本期 Package 10 要求的能力是否完整併入 canonical runtime。
2. 原始最終產品範圍中的 Learning Hub、AdLift、AVM Data Room 等，
   是否被正式排到後續 release，而不是因刪除 route 而失去 owner。
3. Acceptance registry 仍引用五個已刪除 spec，屬 `DOC_DRIFT`。
4. 現行 107 個 canonical Playwright tests 不等於原始 Stage 0–6
   所有商業 Gate 都已驗證。

## 17. Release blockers 與優先順序

### P0：阻擋 production release claim

| ID | 工作 | 完成條件 |
|---|---|---|
| GAP-P0-001 | 建立 Gate 0–6 machine-readable status與owner | 每Gate有pass/fail、evidence、owner、日期、release SHA |
| GAP-P0-002 | 修正 acceptance registry / release gate | 不得引用不存在spec；每P0 scenario真的執行 |
| GAP-P0-003 | 補production live Shell wiring | task assignment/SLA、notification、admin/settings、franchisee不再503 |
| GAP-P0-004 | 完成ForecastOps正式release evidence | dataset hash、model card、metrics、alias、shadow/canary、rollback |
| GAP-P0-005 | 完成角色UAT與正式sign-off | Stage涉及角色皆有簽核；P0/P1 defect為0或正式接受 |
| GAP-P0-006 | 取得live staging proof | database/provider/model/event/audit/rollback均綁exact release SHA |
| GAP-P0-007 | 修正solver native ABI/load-order衝突 | OR-Tools/CVXPY雙向載入與最小solve在同process或正式隔離架構中通過 |

### P1：阻擋各模組Gate

| ID | 工作 | 完成條件 |
|---|---|---|
| GAP-P1-001 | HeatZone label與benchmark | >=200 labels；優於人口排序；Top-K現勘率改善 |
| GAP-P1-002 | SiteScore outcome／prediction閉環 | governed prediction source 綁 model/version lineage；>=200 labels；M6/M12與coverage達標；禁止 `y_pred=y_true` fallback |
| GAP-P1-003 | AVM outcome閉環 | >=120成熟成交outcomes；coverage與價值帶校準 |
| GAP-P1-004 | ForecastOps business validation | baseline superiority、segment、alert precision/recall/lead time |
| GAP-P1-005 | Price/AdLift pilot | 0 hard violation、pre-trend通過、incremental GM與rollback |
| GAP-P1-006 | NetPlan管理驗收 | hard constraints 100%、優於baseline、alternative/infeasibility可解釋 |
| GAP-P1-007 | OSS license gate | license-aware SBOM、policy、notice、release attestation，以及 Human/Ops/Legal 具名政策核准 |
| GAP-P1-008 | Observability production wiring | exporter、dashboard、alert routing、owner與watch window |

### P2：工程與文件品質

- OpenAPI response models全面型別化。
- 更新Package 10 ledger、README、舊evidence與實際merge狀態。
- 將大型route/workspace拆分。
- 修復CSS warning與前端bundle。
- 解決dev dependency high vulnerabilities。
- 為 GeoPandas、ruptures、Temporal/OPA等偏離補ADR或正式替代說明。

## 18. 驗證結果

本次盤點執行：

- Python Ruff：通過。
- TypeScript typecheck：通過。
- Vitest：34 files、259 tests通過。
- Next production build：通過，3個CSS compatibility warnings。
- Playwright collect：107 tests、16 specs。
- Product release static gate：通過，但有驗收證據漂移。
- Python dependency audit：0 known vulnerabilities。
- npm production dependency audit：0。
- npm all-dependency audit：13 high，均在dev toolchain。
- AI/ML/solver focused suite：另附本次執行結果；應以命令輸出為準。

AI/ML/OSS 詳細驗證：

- `tests/integration/test_oss_ai_execution_flow.py`：4 tests通過。
- ForecastOps/AdLift/AVM/SiteScore production runtime focused group：
  43 tests通過。
- Learning Hub/MLflow focused group：9 tests通過。
- Production model lifecycle、binding、OSS estimator、Dagster、
  Evidently、Optuna、GX focused group：通過。
- OR-Tools solver isolated group：10 tests通過。
- PriceOps isolated group：2 tests通過。
- CVXPY robust NetPlan/pymoo isolated group：6 tests通過。
- 合併 PriceOps/CVXPY 與 NetPlan/OR-Tools 的同process group：
  collection失敗，重現上述native ABI/load-order問題。
- 較大的AI/ML/solver集合執行300秒到約45%後由timeout終止；在終止前
  沒有test assertion failure，但這不能當成全套通過。

## 19. 本盤點的剩餘限制

本報告已完成原始規劃與本機程式碼的逐 Stage／Gate 對照，但沒有冒充
live production 驗證。下列項目必須由部署環境或責任人補證：

- Cloud Run目前release SHA與服務狀態。
- Cloud SQL/PostGIS資料量與freshness。
- MLflow目前production aliases與artifact readback。
- 第三方provider credential/license實際狀態。
- Pub/Sub/DLQ live round-trip。
- 正式model metrics、shadow/canary與rollback receipt。
- UAT、財務、法務、Product、Model Validation與Release Owner簽核。

在上述證據完成前，任何「全部功能完成」或「production-ready」宣稱
都不符合原始規劃第70章的能力閉環原則與第80章readiness checklist。
