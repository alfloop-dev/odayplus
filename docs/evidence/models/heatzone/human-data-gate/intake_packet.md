# HeatZone Label Backfill — Human Data Gate Intake Packet

- **Task ID**: `ODP-PLAN-HEATZONE-LABEL-BACKFILL-001`
- **Gap ID**: `GAP-P1-001-DATA`
- **Class**: `human_gate`
- **Owner**: `Human/Ops`
- **Depends on**: `ODP-PLAN-HEATZONE-OUTCOME-001`（已完成，契約與 benchmark 機制就緒）
- **Generated At**: 2026-08-03

---

## 1. 治理範圍

HeatZone Radar 的正式需求模型（CatBoost/LightGBM demand model）目前
`governed_disabled`。依平台政策，AI agent 不得產生、模擬或 auto-seed 任何
熱區標籤。能力必須維持 fail-closed，直到 Human/Ops 提供真實成熟標籤。

`ODP-PLAN-HEATZONE-OUTCOME-001` 已交付 Gate 1 benchmark 機制與 fail-closed
綁定；本 packet 只缺資料本身。

---

## 2. 目前 Gate 狀態

引自 `docs/evidence/models/ODP-PLAN-HEATZONE-OUTCOME-001/GATE1_BENCHMARK_RECEIPT.json`：

| 檢查項 | 現值 | 門檻 | 狀態 |
|---|---:|---:|---|
| Observed labels | **0** | ≥ 200 | FAIL_CLOSED |
| Eligible labels | **0** | ≥ 200 | FAIL_CLOSED |
| Shortfall | **200** | 0 | PENDING |
| Benchmark evaluated | `false` | `true` | FAIL_CLOSED |
| Observed NDCG | `null` | > baseline 0.5 | 無法評估 |
| Observed Top-K survey rate | `null` | > baseline 0.3 | 無法評估 |
| `auto_seeded` | `false` | `false` | ✅ 符合政策 |
| Verdict | `FAIL_CLOSED` | `PASS` | – |
| Unavailable reason | `DATA_CONTRACT_NOT_MATURE` | – | – |

- Contract version：`heatzone-training-view-v2`
- Relation：`model_ready.heatzone_training_view`
- Inventory 版本：`pg16-production-model-inventory-2026-07-25-v1`
- Inventory 觀測時間：`2026-07-25T15:20:00Z`
- Inventory SHA-256：`3f1c8ec4baa1e2f06f5c4e93e82a6258315012b46aacfd3f3e578221aa8b5f44`

---

## 3. Human/Ops 必須提供的內容

### 3.1 資料本體

1. **至少 200 筆真實成熟 HeatZone 標籤**，寫入
   `model_ready.heatzone_training_view`（contract `heatzone-training-view-v2`）。
2. 每筆須具備穩定 join key（空間單元 / H3 cell、觀測期間、tenant）。
3. 標籤必須是**已實現的外部需求結果**，不得是預測值或代理指標。
4. 成熟度定義必須明確（觀察窗結束、outcome 已可回收）。

### 3.2 治理證據

- Dataset snapshot SHA-256
- Lineage / 來源系統 readback
- 具名資料 owner
- Freshness / cutoff 時間
- 資料分級與存取政策

### 3.3 Benchmark 前提

資料到位後，Gate 1 會自動評估兩個**業務**條件（非僅技術指標）：

| 條件 | Baseline | 要求 |
|---|---:|---|
| 排序品質 NDCG | 0.5（人口密度排序） | 模型 NDCG **必須高於** baseline |
| Top-K 現勘效率 | 0.3 | 模型 Top-K 現勘率**必須改善** |

這代表：即使補滿 200 筆，若模型排序**沒有優於人口密度排序**，Gate 1 仍會 fail closed。
資料 owner 在準備標籤時應同步準備人口密度基準排序，以便對照。

### 3.4 絕對禁止

`synthetic` / `mock` / `auto_seeded` / `simulated` / `fixture` / AI 產出的標籤。

---

## 4. 驗證協定

```sql
-- 4.1 合格標籤筆數
SELECT COUNT(*) FROM model_ready.heatzone_training_view
WHERE is_mature = TRUE AND is_eligible = TRUE AND is_synthetic = FALSE;
-- 要求 >= 200

-- 4.2 join key 完整性與去重
SELECT cell_id, tenant_id, observation_period, COUNT(*)
FROM model_ready.heatzone_training_view
GROUP BY 1,2,3 HAVING COUNT(*) > 1;
-- 要求 0 列

-- 4.3 成熟度
SELECT COUNT(*) FROM model_ready.heatzone_training_view
WHERE is_mature = TRUE AND outcome_observed_at IS NULL;
-- 要求 0 列
```

4.4 重跑 Gate 1 benchmark，確認 `evaluated = true`、
`population_ranking_outperformed = true`、`top_k_survey_rate_improved = true`。

4.5 核對 dataset SHA-256 與 Human/Ops attestation。

---

## 5. 資料來源座標

| 項目 | 值 |
|---|---|
| Cloud SQL | `alfaloop-data-project:asia-east1:oday-dev-sql`（POSTGRES_16） |
| 連線 secret | `oday-plus-dev-api-database-url-pg16:latest` |
| Target view | `model_ready.heatzone_training_view` |
| Contract | `heatzone-training-view-v2` |

---

## 6. Governance invariants（逐字引自 Gate 1 receipt）

1. At least 200 eligible mature real labels required in `model_ready.heatzone_training_view`
2. Model ranking must outperform population density ranking baseline (NDCG > baseline)
3. Model ranking must improve Top-K field site survey efficiency rate
4. No synthetic, mock, auto-seeded, or simulated labels allowed
5. Fail-closed governed-disabled binding enforced when inventory or benchmark criteria fail
6. Gate 1 receipt must bind immutably to the PG16 model-ready inventory receipt lineage

---

## 7. AI 邊界

Auto worker **可以**：準備模板、驗 schema、驗算回傳收據、維持 governed-disabled。
Auto worker **不可以**：產生標籤、代簽、把 fixture 當權威資料。
