# ODP-PROD-OPS-WATCH-PARAMS-001 — PROD-OPS-05 營運治理參數填入收據

- **Task ID**: `ODP-PROD-OPS-WATCH-PARAMS-001`
- **Title**: 填入 PROD-OPS-05 的四項營運治理參數
- **Owner**: `Antigravity7`
- **Reviewer**: `Claude`
- **Source Plan**: [`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](../../deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md)
- **Target Item**: `PROD-OPS-05`（Operations & Incident Governance）

---

## 1. 概述

本任務依據使用者裁示與架構規範，於 [`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](../../deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md) 既有的 release 狀態機脈絡中填入 `PROD-OPS-05`（Operations & Incident Governance）之四項營運治理參數，補齊 Production 授權檢查表之待決項目。

---

## 2. 四項營運治理參數明細

| 參數項目 | 設定值 | 治理與營運說明 |
|---|---|---|
| **1. Watch Window** | `30 分鐘` | Production 100% 流量切換後之觀察觀察期，在此期間保留 Blue revisions 與舊 job definitions。 |
| **2. SLO 門檻** | `錯誤率 < 5% 且 p95 延遲 < 5 秒` | Watch window 期間之服務水準目標，用以判定發布是否穩定。 |
| **3. Rollback 觸發條件** | 1. 錯誤率 > 10% 持續 5 分鐘；或<br>2. p95 延遲 > 10 秒持續 5 分鐘；或<br>3. Health check 連續失敗 3 次；或<br>4. 發生 auth failure、資料品質異常、job failure、queue lag、audit 缺失或 operator 判定異常。 | 觸發手動回滾之具體條件與閾值。 |
| **4. Rollback & On-Call Owner** | `Human/Ops（bjoe734@gmail.com）` | 由使用者本人擔任 On-call 與 Rollback 負責人，負責 watch window 期間監控與回滾決策執行。 |

---

## 3. 治理與邊界約束宣告

1. **寬鬆初始值標註**：
   前三項數值（Watch window 30 分鐘、SLO 錯誤率 < 5% 且 p95 < 5 秒、Rollback 門檻）均明確標註為**寬鬆初始值**，**無本系統實測流量依據**。首次 production 部署後應以實測數據與真實上線流量指標進行收窄與校準，不得宣稱為已驗證或已校準之標準。
2. **人工判斷依據（非自動化閘門）**：
   此四項參數為**人工監控與決策依據**，非系統自動化閘門：
   - `product_ops/deployment/bluegreen_release.py` 具有 `rollback` 子命令，但**沒有 `watch` 子命令**，亦無任何門檻參數。
   - `.github/workflows/deploy-dev.yml` 的 production blue-green 驗證步驟僅檢查 `release_id` 與 traffic 結構。
   - 系統**不會**自動依門檻觸發回退，須由 rollback / on-call owner 人工判定後執行回滾指令。
3. **未修改 workflow 與部署腳本**：
   本任務嚴格限定於補齊治理參數，未修改 `deploy-dev.yml`、`bluegreen_release.py` 或任何 CI/CD workflow。
4. **PROD-GCP-01~04 保持既存配置**：
   PROD-GCP-01 至 PROD-GCP-04 既有之配置與狀態不受影響，未做順帶修改或重新宣告。

---

## 4. 交付文件與關聯

- [`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](../../deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md)（修改 Section 6.1, Section 8.1, Section 8.3, Section 8.4, Section 16）
- [`docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/prod-ops-governance-parameters.json`](prod-ops-governance-parameters.json)
