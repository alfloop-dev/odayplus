---
adr_id: ADR-0002
title: "Deferred and Replaced OSS Runtimes Architecture Decision Record"
version: 1.0.0
status: accepted
document_class: architecture-decision-record
project: ODay Plus
language: zh-TW
decision_date: 2026-07-30
updated_at: 2026-07-30
owner: Antigravity7
approvers: Claude
source_documents:
  - docs/adr/ADR-0001-platform-foundation.md
  - docs/evidence/ODP_OSS_AI_INTEGRATION_EVIDENCE.md
  - docs/evidence/PLATFORM_COMPLETENESS_INVENTORY_2026-07-25.md
  - docs/evidence/PRODUCTION_MODEL_RISK_ACCEPTANCE_2026-07-25.md
  - docs/architecture/ODAY_PLUS_EXECUTION_BASELINE.md
related_requirements:
  - ODP-HLR-GOV-001
  - ODP-HLR-GOV-002
  - ODP-HLR-GOV-005
  - ODP-HLR-GOV-006
  - ODP-HLR-GOV-009
  - ODP-HLR-GOV-010
  - ODP-HLR-INT-001
  - ODP-HLR-INT-004
  - ODP-HLR-INT-007
review_trigger: "Review when production data scale, sub-10ms online feature latency, unconstrained business BI authoring, sidecar policy engine mandates, or deep learning timeseries performance justify introducing dedicated OSS daemons or packages."
---

# ADR-0002: Deferred and Replaced OSS Runtimes Architecture Decision Record

## Executive Summary

本架構決策記錄 (ADR) 針對 ODay Plus 平台中規劃、評估或過往審查提及之 8 項第三方與 OSS 元件進行逐項決策與替代能力規範：**GeoPandas**、**ruptures**、**Superset**、**Temporal**、**OPA (Open Policy Agent)**、**pgvector**、**Feast** 以及 **Stage 7 OSS (DoubleML/EconML, TFT/N-BEATS/LSTM, Pyomo)**。

平台硬性原則：**未安裝或未整合為可執行 Runtime 之 OSS 套件與服務，絕不得直接宣稱為功能完成（Functionality Complete）**。未安裝套件採 `defer` 或 `replace` 治理，並提供經測試驗證之原生 Python/FastAPI/PostgreSQL 替代實作。

---

## 核心原則與通用評估標準

1. **真實可執行性 (Executable Reality)**：所有在生產或 CI 宣稱 active 之能力，必須在 `pyproject.toml` / `uv.lock` 中鎖定版本，且有單元/整合測試可執行驗證。未安裝套件標示為 `governedDisabled` 或 `deferred`。
2. **零假 Ready 與零虛構備援 (No Fake Readiness / No Silent Fallback)**：當功能標示為 `defer` 時，系統呼叫未安裝元件不得靜默回傳假資料，必須明確 fail-closed 或回傳結構化停用原因代碼（`DATA_CONTRACT_NOT_MATURE`，見 `models/shared_ml/production_contracts.py:195`）。
3. **可稽核替代能力 (Auditable Replacement Capability)**：每項替代方案必須指明對應之 Python 模組、PostgreSQL schema/PostGIS 擴充或內建 API/UI 服務。

---

## 逐項元件決策與規範 (Itemized Decisions)

### 1. GeoPandas
- **決策**: `replace`（替換） / `defer`（延後記憶體 GeoDataFrame 全量載入）
- **需求映射**: 門市空間點位分析、環域 (Buffer) 計算、H3 網格與空間距離查詢 (`ODP-HLR-INT-001`，AVM / SiteScore / NetPlan)。
- **可驗證替代能力**:
  - 資料庫端：PostGIS 擴充套件與 `GEOMETRY(Point, 4326)` 空間欄位已於 DB migration 中建立（`infra/db/migrations/000001_baseline_canonical_schema.sql`, `000002_data_domain_canonical_entities.sql`）；`ST_Contains`, `ST_Buffer`, `ST_Distance`, `ST_DWithin` 為規劃中之 PostGIS SQL 查詢介面 (planned SQL surface)。
  - Python 輕量計算端：使用 `h3` (4.5.0) 進行 H3 空間編碼與網格 indexing。
- **替代限制**: 不在 Python API / Worker 記憶體中載入大型 GeoDataFrame 進行 In-memory Spatial Join，避免記憶體爆破；重型空間計算下推至 PostGIS 或 BigQuery GIS。
- **元件 Owner**: Data & Spatial Platform Engineering
- **重新評估觸發條件 (Revisit Trigger)**: 當資深 GIS 分析師需要於 Python Jupyter / Batch Pipeline 中直接對百萬級向量圖層進行 complex geometry batch operations 且 PostGIS SQL 難以表達時。

### 2. ruptures
- **決策**: `defer`（延後） / `replace`（替換）
- **需求映射**: 時間序列結構性斷層/變性點檢測 (Change-point Detection) 與營收趨勢突變識別 (`ODP-HLR-INT-004`，ForecastOps)。
- **可驗證替代能力**:
  - `modules/learninghub/infrastructure/evidently_monitor.py` (Line 42, 56, 72) 提供 Evidently AI 分佈偏移與特徵漂移門檻告警 (drift-share thresholding)，搭配 `modules/forecastops/infrastructure/forecast_engines.py` 之 StatsForecast / MLForecast。
- **替代限制**: 離線複雜變化點分割演算法 (Pelt / Dynp with custom cost functions) 未作為獨立 Python 服務執行；改以 Evidently 分佈漂移告警與 StatsForecast / MLForecast 窗格模型作為替代。
- **元件 Owner**: ForecastOps ML Engineering
- **重新評估觸發條件 (Revisit Trigger)**: 當歷史多年度營收/客流斷點分析需要無監督 Pelts/Dynp 動態規劃分段演算法且既有 Evidently/StatsForecast 窗格統計無法滿足精度需求時。

### 3. Superset
- **決策**: `replace`（替換）
- **需求映射**: 營運儀表板、獨立分析圖表、門市網路展店與展店績效視覺化 (`ODP-HLR-GOV-001`, `ODP-HLR-GOV-002`)。
- **可驗證替代能力**:
  - 前端 OpsBoard：基於 Next.js / React 構建 Package 10 規範之 Operations/Store/NetPlan 專屬 Console，整合 `@deck.gl/*` (9.3.5)、`maplibre-gl` (5.24.0) 與 `h3-js` (4.4.0) 視覺化模組。
  - 後端 API：FastAPI 端點處理結構化聚合、RBAC/ABAC 權限控管與審計日誌。
- **替代限制**: OpsBoard 不提供非技術人員自由拖拉式的任意 SQL 自訂 BI Dashboard 編輯器；所有圖表由前導 API 端點與固定 Design System 呈現。
- **元件 Owner**: Frontend & OpsBoard Product Team
- **重新評估觸發條件 (Revisit Trigger)**: 當內部非工程數據分析師需要不受 restricted REST API 限制之自訂拖拉式 SQL 探索儀表板時。

### 4. Temporal
- **決策**: `replace`（替換）
- **需求映射**: 長時間非同步工作流編排、Job 狀態追蹤、失敗重試、 Quarantine 與 DLQ 審計 (`ODP-HLR-GOV-005`, `ODP-HLR-GOV-006`)。
- **可驗證替代能力**:
  - Pipeline 訓練編排：使用 **Dagster** (`pipelines/orchestration/dagster_training.py`)。
  - 持久化 Job 佇列：使用 PostgreSQL 事務安全 Job 佇列 (`shared/infrastructure/persistence/job_queue.py`，具備 lease/fence tokens, attempts counter, correlation_id 與 JobStatus 狀態變遷)，搭配 Worker 重試邏輯 (`apps/worker/assisted_listing_intake/worker.py:220,262`) 之 Exponential backoff 重試機制，以及 Domain / Data Platform (`modules/listing/domain/intake_states.py`, `apps/data_platform/`) 之 Quarantine 隔離機制。
- **替代限制**: 未採用 Temporal 之 Event-Sourcing 引擎；跨數天之 Wait-for-signal 休眠狀態改由 PostgreSQL 狀態機維護。
- **元件 Owner**: Platform Infrastructure & Worker Ops
- **重新評估觸發條件 (Revisit Trigger)**: 當業務流程發展為跨數個微服務、耗時數天且包含多重人工異步 Signal 回應之分散式 Saga 交易編排時。

### 5. OPA (Open Policy Agent)
- **決策**: `replace`（替換）
- **需求映射**: 細粒度 API 授權控管、決策審查政策評估、多租戶隔離 (RLS) 與合規審計 (`ODP-HLR-GOV-009`, `ODP-HLR-GOV-010`)。
- **可驗證替代能力**:
  - FastAPI 後端授權矩陣 (`shared/auth/` RBAC/ABAC 中間件)。
  - 業務邏輯驗證器 (如 NetPlan 限制條件解算器、Assisted Listing Intake 人工審查規則引擎)。
  - PostgreSQL 資料庫端 Row-Level Security (RLS) 租戶隔離。
- **替代限制**: 政策邏輯以 Python 代碼與 OpenAPI 契約形式維護，未採用 Rego 宣告式語言及獨立 OPA Daemon 評估。
- **元件 Owner**: Core Security & API Architecture
- **重新評估觸發條件 (Revisit Trigger)**: 當政策規則需由非開發人員於線上動態 hot-reload 編輯且不得重新部署 Python code，或跨多語言微服務需要統一 Sidecar 政策 Daemon 時。

### 6. pgvector
- **決策**: `defer`（延後） / `replace`（替換）
- **需求映射**: 候選點位相似度檢索、物件重複去重比對、向量特徵搜尋 (`ODP-HLR-INT-007`)。
- **可驗證替代能力**:
  - `modules/listing/` 去重流程與規則引擎（`IntakeStage.MATCHING` 於 `modules/listing/domain/intake_states.py:17`，`ListingDedupKey` 於 `modules/listing/domain/models.py`，`has_duplicate` 於 `modules/listing/application/pipeline.py` 與 `modules/listing/infrastructure/repositories.py`）。
  - PostgreSQL 多欄位索引、PostGIS 空間距離與相似度權重計算。
- **替代限制**: PostgreSQL 未啟用 HNSW / IVFFlat 向量索引擴充套件；Similarity Search 採用特徵工程與幾何/屬性精確匹配。
- **元件 Owner**: Data Platform & AI Engineering
- **重新評估觸發條件 (Revisit Trigger)**: 當物件去重或門市檢索轉型為大型語言模型 (LLM) Embeddings 或多模態向量 (Multimodal Vector Embeddings) 且數量超過 100K 筆需要 HNSW 向量近似搜尋時。

### 7. Feast
- **決策**: `defer`（延後）
- **需求映射**: Online/Offline Feature Store、 point-in-time (PIT) 特徵時空旅行、即時推論特徵提供 (`ODP-HLR-INT-004`)。
- **可驗證替代能力**:
  - 離線與訓練特徵：BigQuery `model_ready` 視圖（如 `forecast_training_view`）與 PostgreSQL `model_ready` 物化表，具備 Point-in-Time (PIT) 時序分割 `_temporal_split(rows, *, holdout_fraction)`（於 `scripts/models/release.py:983`）與成熟度檢查 `spec.label_maturity_column`（於 `scripts/models/contracts.py`，`label_maturity_time` vs `loaded.as_of_time` 檢查於 `scripts/models/release.py:780`）。
  - 模型履歷與快照：**MLflow** (`modules/learninghub/infrastructure/mlflow_adapter.py`) 記錄 Dataset Artifact 及 Commit SHA。
- **替代限制**: 未部署以 Redis/DynamoDB 為底層之 Feast 即時線上 Feature Store 服務；線上推論讀取經治理之 Cloud SQL / BigQuery 視圖。
- **元件 Owner**: Model Governance & Data Engineering
- **重新評估觸發條件 (Revisit Trigger)**: 當線上推論 SLA 要求亞毫秒 (sub-10ms) 特徵提供，且離線至線上特徵同步需透過低延遲 Key-Value 儲存快取時。

### 8. Stage 7 OSS / 高階 AI & 因果推論套件 (DoubleML/EconML, TFT/N-BEATS/LSTM, Pyomo)
- **決策**: `defer`（延後） / `replace`（替換）
- **具體元件分析**:
  1. **DoubleML / EconML**:
     - **決策**: `defer`
     - **替代能力**: `modules/adlift/domain/incrementality.py` 使用 `statsmodels` WLS matched-control Difference-in-Differences (DiD) 進行因果增量推論。
     - **觸發條件**: 當高維度干擾變數 (High-dimensional confounders) 需要非線性機器學習 Double Machine Learning 估算非均質處置效果 (HTE) 時。
  2. **TFT / N-BEATS / LSTM (PyTorch Forecasting)**:
     - **決策**: `defer`
     - **替代能力**: `modules/forecastops/` 使用 `StatsForecast` (AutoARIMA, AutoETS) 與 `MLForecast` (`LightGBM`, `CatBoost`) 滿足預測需求。
     - **觸發條件**: 當歷史時序資料量與 GPU 算力到位，且深度學習模型在 Backtest 中顯著超越 GBDT/StatsForecast 時。
  3. **Pyomo**:
     - **決策**: `defer`（保留為可選 Capability，預設以替代引擎執行）
     - **替代能力**: 求解器運算主軸採用 **OR-Tools** CP-SAT（離散調度/路徑）、**CVXPY**（穩健情境 NetPlan）與 **pymoo** NSGA-II（多目標投資組合）。
     - **觸發條件**: 當需要特定符號代數建模與外部非線性求解器 (如 IPOPT/Gurobi) 綁定時。

---

## 決策總表 (Decision Summary Matrix)

| 元件名稱 | 決策狀態 | 映射需求編號 | 替代/現行實作能力 | 元件 Owner | 未安裝/Deferred 治理規則 | 重新評估觸發條件 (Revisit Trigger) |
|---|---|---|---|---|---|---|
| **GeoPandas** | `replace` / `defer` | `ODP-HLR-INT-001` | PostGIS SQL (planned surface) + H3-py | Data Platform | In-memory Heavy Join 下推 SQL | 需要大型向量圖層 Python 批次幾何運算 |
| **ruptures** | `replace` / `defer` | `ODP-HLR-INT-004` | Evidently AI (`evidently_monitor.py:42,56,72` drift-share thresholding) + StatsForecast / MLForecast | ForecastOps ML | 不宣稱為 Pelt/Dynp 離線分割 | 歷史多年度斷點無監督動態規劃需求 |
| **Superset** | `replace` | `ODP-HLR-GOV-001/002` | Next.js (`@deck.gl/core`, `maplibre-gl`, `h3-js`) OpsBoard + FastAPI RBAC APIs | Frontend Team | 不開放任意 SQL 拖拉 UI | 業務分析師需要開放式 SQL 自訂 BI 視圖 |
| **Temporal** | `replace` | `ODP-HLR-GOV-005/006` | Dagster + Postgres Job Queue (`job_queue.py`) + Worker backoff (`worker.py:220,262`) + Intake quarantine (`intake_states.py`) | Infra & Ops | 長任務休眠由 DB 狀態機管理 | 跨服務多日人工 Signal 異步 Saga 需求 |
| **OPA** | `replace` | `ODP-HLR-GOV-009/010` | FastAPI Auth Middleware + DB RLS | Security Arch | 規則由 Python / Schema 控管 | 政策需由非開發者 Hot-reload 編輯 |
| **pgvector** | `replace` / `defer` | `ODP-HLR-INT-007` | `modules/listing/` 多訊號去重 (`IntakeStage.MATCHING`, `ListingDedupKey`, `has_duplicate`) + PostGIS | Data Platform | HNSW 索引未於 Cloud SQL 啟用 | 向量比對 >100K 筆且轉型 LLM Embedding |
| **Feast** | `defer` | `ODP-HLR-INT-004` | BigQuery `model_ready` PIT 視圖 (`_temporal_split`, `label_maturity_column`) + MLflow | Model Governance | 標示為 `governedDisabled` | 線上推論 SLA 需 Sub-10ms KV 快取 |
| **DoubleML / EconML** | `defer` | `ODP-HLR-INT-004` | `statsmodels` WLS DiD | ML Engineering | 標示為 `governedDisabled` | 高維干擾變數非線性 HTE 估算 |
| **TFT / LSTM** | `defer` | `ODP-HLR-INT-004` | `StatsForecast` + `MLForecast` (GBDT) | ML Engineering | 標示為 `governedDisabled` | GPU 算力與深層時序 Backtest 效益確立 |
| **Pyomo** | `defer` | `ODP-HLR-INT-001` | OR-Tools CP-SAT + CVXPY + pymoo | Solver Team | 登記為可選能力，非預設解算器 | 需要符號代數建模與特定求解器綁定 |

---

## 驗證與可追溯性 (Verification and Traceability)

1. **套件鎖定驗證**: 現行整合之替代套件 (`statsmodels`, `lifelines`, `pymoo`, `ortools`, `cvxpy`, `statsforecast`, `mlflow`, `evidently`, `dagster`, `great_expectations`, `h3`, `pyomo`) 皆在 `pyproject.toml` 鎖定版本。
2. **能力測試 API**: `GET /api/v1/learninghub/oss-capabilities` 動態回報目前可載入之 OSS 能力，對未安裝套件正確暴露停用狀態。
3. **自動化驗證指令**:
   ```bash
   python3 -m pytest -q tests -k "adr or capability" && git diff --check
   ```

---

## 變更紀錄 (Change Log)

| Version | Date | Change Class | Summary | Author | Approver |
|---|---|---|---|---|---|
| 1.0.0 | 2026-07-30 | C1 | 完成 GeoPandas, ruptures, Superset, Temporal, OPA, pgvector, Feast 與 Stage 7 OSS 逐項決策 ADR 制定 | Antigravity7 | Claude |
