# ODP-CDC-SOURCE-CONTRACT-PREP-001: CDC 逐來源適用性、刪除傳播矩陣與 H07 請求包

- **Task ID**: `ODP-CDC-SOURCE-CONTRACT-PREP-001`
- **Work Package**: WP-34A ([ODP 人工決策落地規畫](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §6 WP-34)
- **決策依據**: 決策編號 `D20`（A：實作／補齊 CDC 適用性與契約）
- **查證基準 SHA**: `9048161e058becff5a53593a773d3c42238213fb`
- **查證時間**: `2026-09-08T16:15:00Z`
- **負責人 (Owner)**: Antigravity2
- **審查人 (Reviewer)**: Codex
- **交付狀態**: `STAGE_A_COMPLETED` (Phase 34A 工程準備交付)

---

## 1. 任務目標與交付產物索引 (Artifacts Index)

本 Task 依據 2026-09-08 人工決策落地規畫，在獨立證據目錄中完成 Phase 34A 之工程準備工作。本任務交付四份核心工程與決策產物：

| 檔案名稱 | 說明與核心內容 |
|---|---|
| [source-applicability-matrix.json](source-applicability-matrix.json) | **逐來源適用性矩陣**：涵蓋全系統 15 個內部 MongoDB 集合、6 個外部資料提供者、2 個即時 API 與 8 份內部契約之模式、Owner、延遲、排序、刪除/撤回、冪等、權限邊界及批次能力差距。 |
| [event-contract-draft.json](event-contract-draft.json) | **CDC 與 Event 結構化契約草案**：定義 `cdc_stream` 與 `event_stream` 交換信封、檢查點恢復協定（`data_plane.cdc_checkpoints`）、墓碑與刪除傳播規範、冪等鍵與排序不變量、最小權限 IAM 需求及死信隔離分類。 |
| [human-input-request-H07.md](human-input-request-H07.md) | **H07 人工決策請求單**：針對目標來源與 SLA、MongoDB Replica Set 拓撲與 Oplog 窗、IAM 憑證擴大授權、下游刪除與墓碑政策、傳輸架構等五大關鍵問題，向業務與架構負責人提出正式決策請求。 |
| [implementation-handoff.md](implementation-handoff.md) | **Phase 34B 實作交接規格**：明列進入 Phase 34B 實作的入場條件、六大詳細驗收維度（變更動詞、亂序冪等、斷線恢復、租戶隔離、真實延遲、無假 connector），以及四項獨立缺陷跟進清單。 |

---

## 2. 核心查證結論 (Summary of Findings)

1. **消費端與供給端延遲對比**：
   - 下游分析與決策消費端全部為日粒度資產（`DailyPartitionsDefinition`）。
   - 供給端已有 3 個 Change Sensor 提供 15 分鐘輪詢（`minimum_interval_seconds=900`），供給速度已領先消費需求兩個數量級。
2. **候選串流來源**：
   - 15 個內部集合中，`orders`（交易）與 `device_log`（IoT 狀態日誌）為近即時 CDC 串流的高價值候選來源；其餘主檔（如 `merchant`, `place`, `device`）每日或 15 分鐘快照已完全滿足業務需求。
3. **外部來源皆為快照型態**：
   - 8 個外部提供者（POI、Geocode、Admin Boundary、Listing、Weather、Demographics 等）本質為 HTTP REST API 或公開快照，技術上無資料庫層級 CDC 之適用性。
4. **下游刪除傳播之獨立缺陷**：
   - 下游 PostgreSQL 落地層（`apps/data_platform/store.py`）目前全部執行 `INSERT ... ON CONFLICT DO UPDATE`，完全沒有實體刪除或墓碑清除路徑。必須另立專門 Task（`ODP-DATA-PLANE-DELETE-PROPAGATION-001`）修復。
5. **合約與生產路徑之落差**：
   - `packages/schemas/source_contracts/internal/machine_status_event.json` 宣告為 `event_stream`，但生產以 `device_log` 批次水位線落地，無 Stream Consumer。列入獨立跟進。

---

## 3. 獨立跟進項目清單 (Carried-Forward Follow-ups)

- **跟進 1**：`ODP-DATA-PLANE-DELETE-PROPAGATION-001` — 下游 PostgreSQL 刪除與墓碑清除引擎。
- **跟進 2**：`ODP-CONTRACT-EVENT-STREAM-RECONCILIATION-001` — `machine_status_event` 契約宣告與生產批次對齊。
- **跟進 3**：`ODP-DATA-CATALOG-METADATA-ALIGNMENT-001` — 來源資料擁有者（Data Owner）與延遲 SLA 字典補齊。
- **跟進 4**：`ODP-SCHEMA-STORE-OPENING-AUTHORITY-001` — `store_opening_authority_snapshot` 契約註冊補齊。

---

## 4. 交付與驗收合規證明 (Verification Receipts)

本證據包在交付 exact HEAD `9048161e058becff5a53593a773d3c42238213fb` 通過以下檢驗：

1. **`git diff --check`**：通過（無空白或換行格式問題）。
2. **產物結構、JSON 語法、README 索引與本地 Markdown 連結驗證腳本**：
   - 驗證包含 `README.md`、`source-applicability-matrix.json`、`event-contract-draft.json`、`human-input-request-H07.md`、`implementation-handoff.md` 全部 5 份檔案。
   - 所有 JSON 均為非空合法物件，所有 Markdown 本地相對連結均可正確解析。
   - 驗證退出碼：`0`。
