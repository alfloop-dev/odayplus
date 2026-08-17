# ForecastOps Authoritative History Backfill — Human Data Gate Intake Packet

- **Task ID**: `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001`
- **Blocks**: `ODP-PRODUCTION-MODEL-REGISTRY-001` → `ODP-RUNTIME-GCP-001` /
  `ODP-LIVE-RUNTIME-DEV-COMPOSE-001` / `ODP-P10-DEV-REDEPLOY-VERIFY-001`
- **Class**: `human_gate`
- **Owner**: `Human/Ops`（資料所有者）
- **Generated At**: 2026-08-03
- **Priority**: **P0 — 上線鏈的兩條關鍵路徑之一**（另一條是 required provider
  的真實 ingestion，見 `ODAY_PLUS_CONSOLIDATED_GAP_AUDIT_2026-08-03.md` §5.2）

---

## 1. 為什麼這份 packet 是最高優先

`Deploy Dev` workflow 的 build / push / deploy / migration / scheduler / worker /
Cloud Run smoke 全部通過，只有最後的 Live E2E acceptance gate 失敗。
該 gate 有**兩組獨立 blocker**，本 packet 負責解除其中的 model registry 這一組：

```
[本 packet 負責]
  - runtime:model_bindings: forecastops: PRODUCTION_MODEL_REGISTRY_UNAVAILABLE
  - models:registry: versions=0
  - models:forecastops:production_alias: versionsWithProductionAlias=0

[另一組，需獨立解決 — 見 §7]
  - data:ingestion_runs: runs=0
  - data:admin_boundary.official_dataset:run_exists
  - data:poi.commercial_api:run_exists
```

model registry 這組追根究柢：資料的**時間跨度**不足，不是筆數不足。

---

## 2. 目前 Gate 狀態

| 檢查項 | 現值 | 需求 | 狀態 |
|---|---|---|---|
| Eligible / labeled rows | **1,303** | ≥ 90 | **PASS**（筆數已達標） |
| 日曆天涵蓋範圍 | **2026-06-19 ~ 2026-06-22（4 天）** | 見 §3 | **FAIL_CLOSED** |
| 可形成的 horizon 訓練樣本 | **0** | > 0 | **FAIL_CLOSED** |
| MLflow registered model | **不存在** | 存在並有 production alias | **FAIL_CLOSED** |
| Inventory execution | `pmb8m` — 成功 | – | – |
| Training execution | `2dzlg` — **fail closed** | 成功 | **FAIL_CLOSED** |
| Evidence anchor | `950b852c` | – | – |

**關鍵誤解澄清**：1,303 筆「已達門檻 90」是真的，但那是**列數**門檻。
訓練實際失敗於**時間窗口**無法形成。兩者是不同的條件。

---

## 3. 精確的資料需求（由程式碼契約推導，非估計）

來源：`modules/forecastops/model_contract.py`、`product_ops/modeling/forecast_training.py`

```python
FORECASTOPS_HORIZON_WEEKS        = (4, 8, 12, 24)   # → 28 / 56 / 84 / 168 天
FORECASTOPS_MIN_HISTORY_DAYS     = 28
FORECASTOPS_LATEST_OBSERVATION_LAG_DAYS = 1
FORECASTOPS_LABEL_NAME           = "horizon_average_daily_net_revenue"
FORECASTOPS_FEATURE_SCHEMA_ID    = "forecast-training-view-v2"
```

`expand_forecast_horizon_rows()` 對每個 origin 取
`window = ordered[origin_index : origin_index + horizon_days]`，
並且 **`len(window) != horizon_days` 就整筆跳過**。窗口必須是**連續日**，不可有缺口。

因此每一「店」所需的連續日資料為：

| 目標 | 前置歷史 | Horizon | **每店最少連續日數** |
|---|---:|---:|---:|
| 只訓練 4 週 horizon（最低可行） | 28 | 28 | **56** |
| 加上 8 週 | 28 | 56 | **84** |
| 加上 12 週 | 28 | 84 | **112** |
| **完整 4/8/12/24 週（規格 FR-FCT-001 要求）** | 28 | 168 | **196** |

再加上要產生**多個** origin 才有足夠訓練樣本：若要每店 N 個 origin，
需 `196 + (N-1)` 天。

**目前有 4 天。距離最低可行門檻（56 天）還差 52 天，距離完整規格（196 天）還差 192 天。**

---

## 4. Human/Ops 必須提供的內容

### 4.1 資料本體

1. **權威每日交易歷史**，落在 `model_ready.forecast_training_view`
   （schema id `forecast-training-view-v2`）。
2. 每店**連續無缺口**的日資料，至少 56 天（最低可行）；建議 196 天以上以支援完整
   4/8/12/24 週 horizon。
3. 必要欄位（依 `FORECASTOPS_MODEL_FEATURES` 推導）：
   `tenant_id`、`store_id`、日期、每日淨營收（可導出
   `revenue_lag_1`、`revenue_lag_7`、`rolling_mean_7`、`rolling_mean_28`）。
4. Label：`horizon_average_daily_net_revenue` 必須可由實績導出，且
   **label maturity 不得早於 prediction origin**（程式會 fail closed）。
5. 每筆必須帶 **source snapshot lineage**（`forecast_training.py` 明確要求，缺 lineage 直接拒收）。

### 4.2 治理證據

- Dataset snapshot SHA-256
- Lineage / run id
- 具名資料 owner
- Freshness / cutoff 時間
- 來源系統 readback（非 AI 產出）

### 4.3 絕對禁止（會 fail closed）

`fixture` / `synthetic` / `auto_seeded` / `mock` / `research-only` / `duplicate` /
`immature` / AI 產生的列或收據。

---

## 5. 驗證協定（Reviewer 逐條重跑）

```sql
-- 5.1 時間涵蓋
SELECT tenant_id, store_id,
       MIN(observation_date) AS first_day,
       MAX(observation_date) AS last_day,
       COUNT(*)                       AS row_count,
       COUNT(DISTINCT observation_date) AS distinct_days,
       (MAX(observation_date) - MIN(observation_date))::int + 1 AS calendar_span
FROM model_ready.forecast_training_view
GROUP BY tenant_id, store_id
ORDER BY calendar_span DESC;
```

通過條件：`distinct_days = calendar_span`（無缺口）**且** `calendar_span >= 56`
的店數 > 0；完整規格需 `calendar_span >= 196`。

```sql
-- 5.2 合格筆數
SELECT COUNT(*) FROM model_ready.forecast_training_view
WHERE is_training_eligible IS TRUE;
```

```sql
-- 5.3 缺口偵測（任何一店出現缺口即 fail closed）
SELECT tenant_id, store_id, observation_date,
       observation_date - LAG(observation_date)
         OVER (PARTITION BY tenant_id, store_id ORDER BY observation_date) AS gap_days
FROM model_ready.forecast_training_view
QUALIFY gap_days > 1;
```

5.4 重跑訓練並確認產生 > 0 個 horizon 樣本（`expand_forecast_horizon_rows` 不再拋
`daily forecast rows do not contain a complete canonical horizon window`）。

5.5 確認 MLflow `forecast_revenue_interval` 出現 registered model + 經核准的
production alias（目前 registry 為空）。

---

## 6. 資料來源座標

| 項目 | 值 |
|---|---|
| Cloud SQL | `alfaloop-data-project:asia-east1:oday-dev-sql`（POSTGRES_16） |
| 連線 secret | `oday-plus-dev-api-database-url-pg16:latest` |
| Target view | `model_ready.forecast_training_view` |
| MLflow | `https://oday-mlflow-7sxbjoeozq-de.a.run.app` |
| Model name | `forecast_revenue_interval` |
| Artifact bucket | `alfaloop-data-project-oday-plus-model-artifacts` |

---

## 7. 完成後解鎖的內容

```
ForecastOps 歷史回填
  → 訓練可產生 horizon 樣本
  → DEV → SHADOW → production alias
  → productionBindingsReady = true
  → models:registry / production_alias blocker 解除
```

**但這還不足以讓 Live E2E gate 通過。** 必須同時完成另一組 external-data blocker：

```
required provider 憑證 + 授權 + 實際執行 ingestion
  → data:ingestion_runs > 0
  → admin_boundary.official_dataset / poi.commercial_api 有 persisted run
```

兩組都解除後才會：

```
  → modes.data.liveReady = true → /health 200
  → Live E2E acceptance gate 通過 → Deploy Dev workflow 綠燈
  → 三個部署 task 可解除 blocked（另需先修 dependency 圖譜，見
    docs/runbooks/task-dependency-graph-repair.md）
  → Gate 2 / 3 / 5 / 6 具備取得 receipt 的前提
```

---

## 8. AI 邊界

Auto worker **可以**：準備模板、驗 schema、驗算回傳收據、在資料未到位時維持
governed-disabled / NO-GO。

Auto worker **不可以**：產生任何真實營收列、代替資料 owner 簽核、
把 fixture 或 research 資料寫進 production view。
