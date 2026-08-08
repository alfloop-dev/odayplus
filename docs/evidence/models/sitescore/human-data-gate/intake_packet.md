# SiteScore Outcome Backfill — Human Data Gate Intake Packet

- **Task ID**: `ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001`
- **Gap ID**: `GAP-P1-002-DATA`
- **Class**: `human_gate`
- **Owner**: `Human/Ops`
- **Depends on**: `ODP-PLAN-SITESCORE-OUTCOME-001`（已完成）
- **Paired with**: `ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001`（`review_approved`，工程側）
- **Generated At**: 2026-08-03

---

## 1. 治理範圍

SiteScore 正式模型目前 `governed_disabled`，Gate 2 receipt verdict 為 FAIL_CLOSED。
與其他資料 gate 不同，SiteScore 需要**兩個**收據才能通過：

1. **Outcome 收據**（本 packet，Human/Ops）：真實 M6/M12 已實現營收
2. **Prediction source 收據**（`ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001`，工程）：
   governed prediction 與 model/version lineage 綁定

**只補資料而 prediction source 未綁定，Gate 2 仍不會通過**，反之亦然。

---

## 2. 目前 Gate 狀態

引自 `docs/evidence/models/sitescore_gate2_receipt.json`：

| 檢查項 | 現值 | 門檻 | 狀態 |
|---|---:|---:|---|
| Eligible count | **0** | ≥ 200 | FAIL_CLOSED |
| Missing labels delta | **200** | 0 | PENDING |
| Mature label count | **0** | ≥ 200 | FAIL_CLOSED |
| M6 mature count | **0** | > 0 | FAIL_CLOSED |
| M12 mature count | **0** | > 0 | FAIL_CLOSED |
| M6 coverage ratio | 0.0 | 達標 | FAIL_CLOSED |
| M12 coverage ratio | 0.0 | 達標 | FAIL_CLOSED |
| Matched prediction count | **0** | > 0 | FAIL_CLOSED |
| Prediction coverage ratio | 0.0 | 達標 | FAIL_CLOSED |
| Interval bounds coverage ratio | 0.0 | 達標 | FAIL_CLOSED |
| P80 coverage ratio | 0.0 | 達標 | FAIL_CLOSED |
| Measured 90d MAE | `null` | 有值 | 無法評估 |
| Dataset snapshot id | `null` | 有值 | UNVERIFIED |
| Dataset snapshot hash | `UNVERIFIED` | 有效 SHA-256 | UNVERIFIED |
| Mature population digest | `UNAVAILABLE` | 有值 | UNVERIFIED |
| Evidence owner | `UNVERIFIED` | 具名 | UNVERIFIED |
| Governed disabled | `true` | `false` | SAFE_DEFAULT |

- Model card hash：`16b3927153f59441b76fc8f150a0d72fa8157761b6090b025a009ede9a585302`
- Handback hash：`293ff10be98395684400ff9d810891d658eb450c7761f0955c89bec5bd96dd21`

---

## 3. Human/Ops 必須提供的內容

### 3.1 資料本體

1. **至少 200 筆真實成熟開店 outcome**，可由
   `model_ready.candidate_site_view` 讀出。
2. 必須同時具備 **M6 與 M12** 的已實現淨營收（不是只有 90 天）。
3. 每筆需具備穩定 prediction join key：
   `entity_id`、`store_id`、`target_format_code`、`opened_on`。
4. 需提供 **interval bounds**（P10/P50/P90 對應的實際落點），
   否則 `interval_bounds_coverage_ratio` 與 `p80_coverage_ratio` 無法計算。
5. 成熟度定義：`store_age_days` 必須足以支撐 M6 / M12 判定。

### 3.2 治理證據

- Dataset snapshot id + SHA-256
- Mature population digest
- 具名 evidence owner
- Freshness / cutoff
- Lineage（來源系統 readback）

### 3.3 絕對禁止

- `synthetic` / `fixture` / `auto_seeded` / `duplicate` / `immature` / AI 產出
- **特別禁止 `y_pred = y_true` fallback**（Gap Matrix `GAP-P1-002` 明列）
- 虛構的 model governance 欄位

---

## 4. 官方 discovery query

Query id：`sitescore_opening_outcome_discovery_query_v1`
Source identity：`model_ready.candidate_site_view`
Eligibility 定義：`is_training_eligible IS True or eligible IS True`

```sql
SELECT entity_id,
       store_id,
       target_format_code,
       opened_on,
       is_training_eligible,
       realized_90d_net_revenue,
       (CURRENT_DATE - opened_on)::integer AS store_age_days
FROM model_ready.candidate_site_view;
```

回填後需額外可查得 M6 / M12 已實現營收與 interval bounds 欄位。

---

## 5. 驗證協定

```sql
-- 5.1 合格成熟筆數
SELECT COUNT(*) FROM model_ready.candidate_site_view
WHERE (is_training_eligible IS TRUE OR eligible IS TRUE)
  AND is_synthetic = FALSE;
-- 要求 >= 200

-- 5.2 M6 / M12 成熟度
SELECT
  COUNT(*) FILTER (WHERE store_age_days >= 180) AS m6_mature,
  COUNT(*) FILTER (WHERE store_age_days >= 365) AS m12_mature
FROM model_ready.candidate_site_view;
-- 兩者皆要求 > 0

-- 5.3 prediction join 覆蓋
-- matched_prediction_count 必須 > 0，且不得以 y_pred = y_true 充數
```

5.4 重跑 Gate 2 benchmark，確認 `eligible_count >= 200`、
`m6_coverage_ratio` / `m12_coverage_ratio` / `p80_coverage_ratio` 皆達標、
`measured_90d_mae` 有實際數值。

5.5 確認 `ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001` 的 prediction lineage
收據已合併，且 join 後 population 對齊。

---

## 6. 資料來源座標

| 項目 | 值 |
|---|---|
| Cloud SQL | `alfaloop-data-project:asia-east1:oday-dev-sql`（POSTGRES_16） |
| 連線 secret | `oday-plus-dev-api-database-url-pg16:latest` |
| Target view | `model_ready.candidate_site_view` |
| Discovery query id | `sitescore_opening_outcome_discovery_query_v1` |

---

## 7. AI 邊界

Auto worker **可以**：準備模板、驗 schema、驗算回傳收據、維持 governed-disabled。
Auto worker **不可以**：產生 outcome、代簽、使用 `y_pred = y_true` fallback、
虛構 model governance 欄位。
