# ODP-FORMAT-CONVERSION-CONTRACT-PREP-001 — 店型轉換事件／財務契約草案與 H04 請求

- **Task ID**: `ODP-FORMAT-CONVERSION-CONTRACT-PREP-001`
- **Work Package**: WP-31A（[ODP 人工決策執行規畫](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §6）
- **Requirement**: `ODP-FR-SITE-001 / FORMAT_CONVERSION`
- **Owner**: Claude
- **Reviewer**: Codex2
- **Stage**: A（工程準備） — 獨立證據目錄交付
- **Date**: 2026-09-08
- **Inspected Head**: `944f4d719b29e765605f55a5fbc366aca3430c36`（本輪重新查證；早期查證於 `cf04c046`，兩者相異處以本輪為準）
- **Per-observation receipts**: [implementation-handoff.md](implementation-handoff.md) §5.2–§5.3（每項觀測附命令、UTC、原始 exit code 與 blob SHA）

---

## Task Summary

交付 FORMAT_CONVERSION 的 Stage A 工程準備包：事件契約草案、欄位字典、來源消費者地圖、H04 人工資料請求與實作交接文件。此階段可在資料未齊時完成，但不聲稱 producer 已可用、runtime 已驗證或 FORMAT_CONVERSION 已實作。

### Disposition Status

`FORMAT_CONVERSION` 維持 `BLOCKED_BY_EVIDENCE`（`set_valued_requirements.json`），Stage B 入場條件見 [implementation-handoff.md](implementation-handoff.md) §3。

---

## Artifact Index

| # | File | Description |
|---|------|-------------|
| 1 | [event-contract-draft.json](event-contract-draft.json) | 店型轉換事件 JSON Schema 草案（12 必要欄位 + 13 可選欄位） |
| 2 | [field-dictionary.md](field-dictionary.md) | 逐欄位字典：型別、來源、負責人、設計理由、已查證證據 |
| 3 | [source-consumer-map.json](source-consumer-map.json) | 4 來源 × 4 消費者地圖：現況、缺口與 Stage B 行動 |
| 4 | [human-input-request-H04.md](human-input-request-H04.md) | H04 人工輸入請求：真實事件、財務參數、資料來源與驗收案例 |
| 5 | [implementation-handoff.md](implementation-handoff.md) | 實作交接：已完成項目、未完成項目、Stage B 檢核清單 |

---

## Key Facts

### What Exists in the Repo (Inspected Evidence)

- `core.stores.store_format_code` — static column only (000001, 000002, 000004 migrations)
- `TargetFormatRegistry` — selects new-store format by area (G2, G3_COMPACT, FLAGSHIP)
- `SimulationInput` — greenfield-only financial simulation
- `FORMAT_CONVERSION` governance: `absent` / `BLOCKED_BY_EVIDENCE` with formal handback `HB-SITE001-FORMAT-CONVERSION-001`

### What Does NOT Exist

- No `core.store_format_conversions` table
- No conversion event producer or ingestion pipeline
- No `ConversionSimulationInput` or brownfield financial model
- No conversion ramp or downtime logic in `simulator.py`（該檔已有 **新店** 設備殘值／殘餘價值模型，缺的是 brownfield 舊資產路徑，不是殘值本身）
- No conversion/from-format/downtime field in SiteScore（`SiteScoreFeatureInput` 只有 greenfield 的 `target_format_code`）
- No conversion event table anywhere in the migrations tree（遞迴查證 50 個 migration 檔）

### New Store Selection ≠ Brownfield Conversion

This contract is strictly for **brownfield conversion of existing stores**. The existing `SimulationInput` and `TargetFormatRegistry.find_best_format_for_area()` are for **greenfield new stores**. The two must not be conflated — see [field-dictionary.md](field-dictionary.md) §Key Design Distinctions.

---

## Source References

- [ODP 人工決策執行規畫](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) — WP-31, D17, H04
- [SITE001 處置報告](../../ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md) — §3 FORMAT_CONVERSION 處置, §4.2 實作契約
- [SITE001 資料準備度報告](../../ODP_SITE001_DATA_READINESS_2026-09-03.md) — §4 FORMAT_CONVERSION 查證
- [需求處置治理清單](../../../../delivery_toolchain/governance/set_valued_requirements.json) — ODP-FR-SITE-001 / FORMAT_CONVERSION
- Source documents pinned at `alfloop-dev/odayplus@04e1572f` (2026-09-03 evidence) and `@be04fe79` (2026-09-08 plan)
