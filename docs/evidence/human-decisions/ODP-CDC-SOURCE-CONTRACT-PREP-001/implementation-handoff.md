# Phase 34B 實作交接與工程跟進規格書 (Implementation Handoff)

- **Task ID**: `ODP-CDC-SOURCE-CONTRACT-PREP-001`
- **Work Package**: WP-34A (Handoff to WP-34B)
- **前置規畫**: [ODP 人工決策落地規畫 (2026-09-08)](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §6 (WP-34)
- **關聯產物**:
  - [source-applicability-matrix.json](source-applicability-matrix.json)
  - [event-contract-draft.json](event-contract-draft.json)
  - [human-input-request-H07.md](human-input-request-H07.md)
  - [README.md](README.md)
- **交付身分 (Owner)**: Antigravity2
- **審查身分 (Reviewer)**: Codex
- **日期**: 2026-09-08

---

## 1. 階段定位與交接邊界 (Phase Boundary)

本文件定義由 **Phase 34A（工程準備、契約設計與決策請求）** 移交至 **Phase 34B（CDC 串流配接器實作、檢查點恢復與生產驗證）** 的具體邊界、入場條件、驗收矩陣與獨立缺陷追蹤。

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 34A (本 Task 完成交付)                                 │
│ ├─ 逐來源適用性、延遲、排序與權限矩陣 (source-applicability-matrix.json) │
│ ├─ CDC/Event 結構化交換契約草案 (event-contract-draft.json)   │
│ ├─ H07 人工決策請求單 (human-input-request-H07.md)           │
│ └─ 34B 驗收標準與獨立缺陷清單 (implementation-handoff.md)     │
└──────────────────────────────┬──────────────────────────────┘
                               │
                [入場條件判定 (Entry Gate)]
                1. H07 簽核回覆 (來源與 SLA)
                2. MongoDB Replica Set / Oplog 確認
                3. 專屬最小權限憑證到位
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 34B (後續工程任務)                                     │
│ ├─ Scoped CDC Adapter (MongoDB ChangeStream 讀取器)          │
│ ├─ Checkpoint / Resume 游標持久化與過期重讀機制                │
│ ├─ 下游 PostgreSQL 刪除傳播與墓碑清除 (Tombstone Consumer)   │
│ ├─ 冪等、亂序與租戶隔離測試套件                              │
│ └─ 真實延遲與最終一致性端到端驗證                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Phase 34B 入場條件 (Entry Conditions)

在啟動 Phase 34B 的工程編碼前，必須滿足以下四項前置條件（任一未滿足前，不可假稱已具備生產能力）：

1. **H07 決策明確指定目標來源與 SLA**：
   - 取得 [human-input-request-H07.md](human-input-request-H07.md) 之正式回覆，確認實施 CDC 的具體集合（例如 `orders`, `device_log`）與預期延遲指標。
2. **上游資料庫拓撲與 Oplog 支援確認**：
   - 生產 DBA 確認 MongoDB `fongniao_prod` 為 Replica Set 架構，且 Oplog 保留窗具備足夠緩衝（建議 $\ge 24$ 小時）。
3. **專屬最小權限憑證配置**：
   - 配置獨立之 `odp_cdc_reader` 帳號，限定僅能對被選定集合發出 `changeStream` 與 `find`，禁止直接賦予叢集級別 `readAnyDatabase`。
4. **不建立空的 Mock/Stub Connector**：
   - 若上游環境未具備 Change Stream 支援，應如實回報 blocker，嚴禁建立回傳假資料的空 connector 偽造合規。

---

## 3. Phase 34B 詳細驗收標準 (Detailed Acceptance Matrix)

Phase 34B 之交付物必須通過以下六大核心維度驗收：

| 維度 (Dimension) | 驗收規範與預期行為 | 驗證方法與證據 |
|---|---|---|
| **1. 完整變更動詞支援 (Mutation Verbs)** | 支援 `insert`, `update`, `replace`, `delete`, `void`, `refund`, `withdraw`。各動詞依契約映射至下游欄位更新或墓碑寫入。 | 單元與整合測試覆蓋 7 種變更型態，驗證下游資料庫終態正確。 |
| **2. 重複與亂序冪等性 (Duplicate & Out-of-Order)** | 相同 event 重放兩次產生同一結果；較舊 timestamp 之事件到達時不覆蓋較新之狀態（Conditional Upsert）。 | 亂序重放測試套件（Chaos/Jitter Replay），斷言終態與順序到達一致。 |
| **3. 斷線恢復與檢查點 (Checkpoint & Resume)** | 系統重啟或網路中斷時，自 `data_plane.cdc_checkpoints` 讀取 `resume_token` 續讀；若 Token 過期則觸發 fail-closed 告警並啟動快照對帳。 | 模擬 Worker 崩潰與連線中斷重啟，驗證無事件遺漏（Zero-loss）且無重複計算。 |
| **4. 租戶隔離與個資最小化 (Tenant & Privacy)** | 嚴格依 `tenant_id` 路由與隔離；在記憶體反序列化階段即刻執行欄位最小化投影，未授權個資不落地。 | 多租戶資料竄流滲透測試；檢驗 PostgreSQL 落地表記錄不含原始未遮蔽個資。 |
| **5. 真實延遲與最終一致性 (Latency & Consistency)** | 量測自 Mongo 寫入至 PostgreSQL 落地之端到端延遲（$P_{95} < 5\text{s}$）；對帳引擎確認兩端資料總量與校驗和完全一致。 | 執行真實遙測計時，產出含時間戳、處理筆數與校驗和之 `RunSummary`。 |
| **6. 失敗隔離與死信佇列 (DLQ & Quarantine)** | 格式錯誤或無法解析之 Poison Pill 訊息自動隔離至 `data_plane.quarantined_records`，不阻斷後續正常訊息處理。 | 注入惡意/損毀 Payload，驗證串流管線持續運作且記錄隔離原因。 |

---

## 4. 獨立缺陷與跟進任務清單 (Carried-Forward Scoped Follow-ups)

在 34A 盤點過程中確認之獨立缺陷，本質上非 CDC 能單獨解決，需另立專門 Task 推進：

### 跟進項目 1：下游落地層刪除傳播引擎 (Downstream Delete Propagation Gap)
- **現況**：`apps/data_platform/store.py` 與 `pipeline.py` 完全沒有實體刪除、軟刪除標記或墓碑清除路徑。
- **影響**：上游實體刪除的記錄永久殘留在下游 PostgreSQL，造成資料孤島與合規隱患。
- **建議跟進 Task**：`ODP-DATA-PLANE-DELETE-PROPAGATION-001`
- **範疇**：
  1. 在 `apps/data_platform/store.py` 新增 `delete_record()` 與 `tombstone_record()` 介面。
  2. 在 `control_schema.sql` 建立專屬 `tombstones` 稽核資料表。
  3. 修改 `reconcile()` 支援上游與下游實體比對，偵測刪除漂移（Delete Drift）。

### 跟進項目 2：`machine_status_event` 事件契約與生產批次路徑對齊
- **現況**：`packages/schemas/source_contracts/internal/machine_status_event.json` 宣告為 `event_stream`，但生產以 `SourceKind.DEVICE_LOG` 走批次水位線落地。
- **影響**：契約與生產程式不一致（False Readiness）。
- **建議跟進 Task**：`ODP-CONTRACT-EVENT-STREAM-RECONCILIATION-001`
- **範疇**：評估建立真實 IoT Stream Consumer，或將契約正式修訂為 `incremental_batch`。

### 跟進項目 3：資料來源中繼資料與 SLA 字典補齊
- **現況**：15 個內部集合與 8 個外部提供者未於原始碼中宣告具名之業務資料負責人（Data Owner）與延遲 SLA。
- **建議跟進 Task**：`ODP-DATA-CATALOG-METADATA-ALIGNMENT-001`
- **範疇**：在 Source Contract Registry 擴充 `data_owner`、`target_latency_sla` 與 `contact_channel` 欄位。

### 跟進項目 4：`store_opening_authority_snapshot` 契約缺漏修復
- **現況**：`modules/external_data/connectors/provider_registry.py:293` 引用了 `store_opening_authority_snapshot`，但該契約未收錄於 `packages/schemas/source_contracts/` 內。
- **建議跟進 Task**：`ODP-SCHEMA-STORE-OPENING-AUTHORITY-001`
- **範疇**：建立標準 JSON 契約並納入 `packages/schemas/source_contracts/index.json`。

---

## 5. Phase 34B 測試與驗證策略 (Testing Strategy)

Phase 34B 開發時應採用的測試工具鏈與測試架構：

1. **單元測試 (Unit Tests)**：
   - 針對 CDC Envelope 反序列化、欄位投影遮蔽、冪等鍵生成函式進行單元測試。
   - 工具：`pytest tests/unit/data_platform/test_cdc_envelope.py`
2. **整合測試 (Integration Tests with Mock/Testcontainers)**：
   - 使用 MongoDB Replica Set 測試容器，模擬即時寫入、`db.collection.watch()` 事件捕捉、中斷連線與 Resume Token 續讀。
   - 工具：`pytest tests/integration/data_platform/test_mongo_cdc_stream.py`
3. **終態對帳測試 (Reconciliation Tests)**：
   - 模擬插入 10,000 筆、更新 5,000 筆、刪除 1,000 筆，驗證 PostgreSQL 最終資料與 Mongo 完全對齊。
