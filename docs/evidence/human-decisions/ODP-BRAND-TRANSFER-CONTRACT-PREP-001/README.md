# ODP-BRAND-TRANSFER-CONTRACT-PREP-001 — Brand Transfer 資料契約準備

- **Task ID**: `ODP-BRAND-TRANSFER-CONTRACT-PREP-001`
- **Phase**: A — 工程準備（契約草案、來源盤點、H03 請求）
- **Status**: Phase A 交付完成；Phase B 等待 H03 回覆
- **Owner**: Antigravity2
- **Reviewer**: Codex
- **Decision Reference**: D16 — SITE-001 BRAND_TRANSFER: 實作／補齊真實資料與契約
- **Inspected Source SHA**: `9048161e058becff5a53593a773d3c42238213fb`
- **Inspected At**: 2026-09-08T15:23Z

---

## 交付產物索引

| # | 檔案 | 說明 |
|---|---|---|
| 1 | [contract-draft.json](contract-draft.json) | 品牌轉移資料契約 JSON Schema 草案 (v0.1.0-draft)。定義所有欄位型別、約束、反模式規則與 Phase B 入場條件。 |
| 2 | [field-dictionary.md](field-dictionary.md) | 逐欄位字典：型別、來源、目前 repo 現狀、需資料 owner 確認的項目。包含四類資料缺失狀態的消歧定義。 |
| 3 | [source-consumer-map.json](source-consumer-map.json) | 來源至消費端 6 層路徑圖 (L0 外部生產者 → L5 文件)。逐層列出檔案路徑、行號、查證事實與缺口。 |
| 4 | [human-input-request-H03.md](human-input-request-H03.md) | H03 最小資料請求：11 道問題、取得方案與 Phase B 驗收矩陣。 |
| 5 | [implementation-handoff.md](implementation-handoff.md) | 實作交接：Phase A 摘要、源碼盤點結論、Phase B 入場條件、禁止事項、實作順序與已知風險。 |

---

## 關鍵發現

1. **品牌轉移資料端對端不存在**：Repo 內 6 層（外部生產者 → 持久層 → dbt → Signal Store → SiteScore → 文件）無任何真實資料流動。
2. **現有 `brand_transfer_view.sql` 為合成假視圖**：CROSS JOIN `core.brands` 笛卡兒積，硬編碼 `transfer_ratio = 0.15`、`data_quality_score = 1.0`。已被 `MODEL_READY_VIEWS_BASELINE.md` 明確標記為 mock baseline。
3. **SiteScore 從未消費品牌轉移資料**：`SiteScoreFeatureInput` dataclass 無任何 `brand_transfer` 相關欄位。
4. **Signal Store 僅有 mock payload**：`client.py:319` 含 `brand_transfer_confidence: 0.76`，但無任何生產模組引用。

---

## Phase B 入場條件

Phase B（WP-30B 真實資料實作）需要 H03 回覆後方可啟動。具體條件列於 [implementation-handoff.md](implementation-handoff.md) §3。

---

## 相關文件

- 決策來源: [ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md](../../../../.orchestrator/source-doc-cache/alfloop-dev__odayplus/be04fe7954d3414f024901e034caacd95ae81538/docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §2.3 D16, §6 WP-30
- 前置處置報告: [ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md](../../ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md)
- 前置資料查證: [ODP_SITE001_DATA_READINESS_2026-09-03.md](../../ODP_SITE001_DATA_READINESS_2026-09-03.md)
- Handback 單: `HB-SITE001-BRAND-TRANSFER-001` (在上述處置報告 §2.3)
