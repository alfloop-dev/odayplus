# H07: CDC 需求與來源適用性人工決策請求單 (Human Input Request)

- **Task ID**: `ODP-CDC-SOURCE-CONTRACT-PREP-001`
- **Work Package**: WP-34A (Phase 34A Preparation)
- **決策依據**: [ODP 人工決策落地規畫 (2026-09-08)](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §2.3 (D20)、§4 (H07)
- **歷史查證基準**: [ODP_INT001_CDC_DISPOSITION_2026-09-03.md](../../ODP_INT001_CDC_DISPOSITION_2026-09-03.md) 及 [ODP_INT001_CDC_SOURCE_EVIDENCE_2026-09-03.md](../../ODP_INT001_CDC_SOURCE_EVIDENCE_2026-09-03.md)
- **關聯產物**:
  - [source-applicability-matrix.json](source-applicability-matrix.json) (逐來源適用性矩陣)
  - [event-contract-draft.json](event-contract-draft.json) (CDC 與 Event 契約草案)
  - [implementation-handoff.md](implementation-handoff.md) (34B 實作交接與跟進清單)
- **日期**: 2026-09-08
- **負責人 (Owner)**: Antigravity2
- **審查人 (Reviewer)**: Codex
- **狀態**: `OPEN_PENDING_HUMAN_INPUT`

---

## 1. 背景與任務說明

在 2026-09-08 的人工決策規畫中，使用者對 D20 選擇了 **A：實作／補齊 CDC 適用性與契約**。這確立了工程方向：不因既有稽核點出困難而逕行刪除需求或偽造 AI 豁免，而是完整交付契約草案、能力差距盤點，並精確列出進入 Phase 34B 實作前所需的人工業務與架構輸入。

經過對目前程式碼基準（HEAD `9048161e058becff5a53593a773d3c42238213fb`）的全面盤點：
1. **消費端現況**：下游資料平台（PostgreSQL 落地層及 Dagster 資產）全部採用日粒度分割區（`DailyPartitionsDefinition`）。
2. **供給端現況**：內部 MongoDB `fongniao_prod` 的核心集合已有 15 分鐘輪詢感測器（`dimension_change_sensor`、`operations_change_sensor`、`authoritative_transaction_change_sensor`），供給速度已快於日常消費需求。
3. **外部來源現況**：所有 8 個外部提供者（POI、Geocode、Admin Boundary、Listing、Weather、Demographics 等）均為 HTTP REST API、公開資料集或人工具結，架構上不支援資料庫層級的 CDC。
4. **關鍵實質缺口**：下游落地層（`apps/data_platform/store.py`）目前**完全沒有實體刪除與墓碑清除路徑**（所有寫入均為 upsert）。若不先建立下游刪除路徑，上游 CDC 送出的刪除事件也無法在下游生效。

為了使後續 Phase 34B（CDC 串流配接器與下游消費者實作）能有清晰邊界與安全依據，本請求單提出五項具體問題，提請 **Data Platform Lead**、**架構委員會 (Architecture Board)** 及 **安全負責人** 正式核定。

---

## 2. 需人工確認的決策項目 (H07 Decision Items)

### 問題 1：CDC 目標來源集合與延遲 SLA 定位 (Target Sources & Latency SLA)

- **現狀**：15 個內部集合中，僅 `orders`（交易）與 `device_log`（IoT 狀態日誌）具有潛在的高頻近即時業務價值；其餘主檔（如 `merchant`, `place`, `device`, `product`）或離線批次運算（`ai_revenue_stats`, `ai_consumer_kmeans_v1`）本質即為低頻或批次產生。
- **待決策選項**：
  - **選項 A（推薦）**：**Scoped CDC**。僅針對 `orders` 與 `device_log`（或其對應的 `machine_status_events`）啟用近即時 CDC 串流（SLA < 10 秒），其餘 13 個內部集合維持現行 15 分鐘感測器或每日快照批次。
  - **選項 B**：**全內部集合 CDC**。對 `fongniao_prod` 全量 15 個集合建立 Change Stream 讀取。
  - **選項 C**：**維持現行輪詢，將手動工作排程化**。將目前 manual-only 的 4 個集合（`member`, `transaction`, `trade`, `device_log`）加入自動感測排程，不引入 CDC 串流連線。

---

### 問題 2：上游 MongoDB 拓撲與 ChangeStream 能力確認 (MongoDB Topology & Oplog Availability)

- **現狀**：MongoDB Change Streams 必須運行在 **Replica Set** 或 **Sharded Cluster** 上。單機（Standalone）MongoDB 沒有 oplog，無法使用 `db.collection.watch()`。此事實無法從程式碼推論，需生產 DBA 或基礎設施負責人提供。
- **待確認資訊**：
  1. 生產資料庫 `fongniao_prod` 是否為 Replica Set？
  2. Oplog 的保留時間窗（Retention Window）為何？（建議至少保留 24～48 小時，避免 consumer 斷線時 resume token 迅速過期失效）。
  3. 若 Resume Token 過期失效，是否同意由 Consumer 自動轉為執行全量快照重讀對帳？

---

### 問題 3：生產憑證與安全權限邊界擴大授權 (IAM Credential & Privacy Boundary)

- **現狀**：
  - 現行連線字串（`ODP_DATA_MONGO_URI`）僅具有具名集合之 `find` 讀取權限，並在查詢邊界嚴格限制欄位投影（`SOURCE_PROJECTIONS`），且 `member` 等含個資集合會在讀取端直接進行個資遮蔽（`_minimize_member`）。
  - Change Streams 傳回的是完整文件（Full Document），且需要資料庫或叢集層級的 `changeStream` 動作授權。
- **待確認事項**：
  1. 安全官是否核准為專屬 CDC 帳號（如 `odp_cdc_reader`）開啟目標集合的 `changeStream` 權限？
  2. 是否同意在 CDC 配接器（Adapter）記憶體管線中執行應用層欄位投影與個資遮蔽，以確保不將未授權欄位寫入下游？

---

### 問題 4：下游落地層刪除傳播與墓碑政策 (Delete & Tombstone Propagation Policy)

- **現狀**：目前下游 PostgreSQL（`apps/data_platform/store.py`）只執行 `INSERT ... ON CONFLICT DO UPDATE`。
- **待決策選項**：
  - **選項 A（推薦 - 軟刪除與稽核墓碑並行）**：
    - 業務表：將上游刪除/作廢操作映射為 `status = 'voided'` 或 `is_deleted = TRUE`，保留歷史可追溯性。
    - 隱私清除（GDPR / 忘記我請求）：寫入帶 SHA-256 雜湊的加密墓碑記錄（`data_plane.quarantined_records` 或 `shared.audit.persistence`），並在業務表將個資欄位覆蓋為空值。
    - 實體刪除（Hard Delete）：保留給定期歸檔清理工作（Retention Purge Job），不隨每筆 CDC 即時硬刪除。
  - **選項 B**：**即時連鎖實體刪除 (Cascading Hard Delete)**。收到 CDC delete 操作時，直接自 PostgreSQL 刪除相應主鍵資料。

---

### 問題 5：CDC 傳輸與訊息仲介架構選擇 (Broker & Transport Architecture)

- **現狀**：全系統目前沒有 Kafka, Redpanda, GCP PubSub 或 RabbitMQ 等外部訊息佇列消費者。
- **待確認選項**：
  - **架構方案 1（輕量無額外依賴）**：在既有 Dagster worker 內運行常駐 ChangeStream 感測器（Long-running Sensor），直接寫入 PostgreSQL 暫存表與控制架構。
  - **架構方案 2（標準串流架構）**：引入 Debezium / Kafka Connect 或 Cloud Pub/Sub，將 Mongo CDC 發布至 Topic，再由獨立 Stream Worker 消費。

---

## 3. 人工決策回填表 (Human Decision Response Template)

```markdown
### H07 人工決策簽核回覆

- **簽核人 (Decider/Principal)**: [請填寫具名架構師/負責人]
- **授權角色 (Role)**: [例如：Data Platform Lead / Security Officer]
- **簽核日期 (Date)**: [YYYY-MM-DD]

#### 決策結果：
1. 目標來源與 SLA：[選項 A / B / C；若選 A，列出具體集合與目標延遲]
2. MongoDB 拓撲確認：[Replica Set: 是/否；Oplog 保留窗: XX 小時；過期重讀策略: 同意/不同意]
3. 憑證與權限授權：[核准 odp_cdc_reader 專屬權限 / 拒絕 / 附條件核准]
4. 刪除與墓碑政策：[選項 A / B；保留期限: XX 天]
5. 傳輸架構選擇：[方案 1 (Dagster Sensor) / 方案 2 (Message Broker)]
```

---

## 4. 對後續工程階段之影響

- 本 H07 請求單在獲得權責人回覆前，**不阻塞 Phase 34A 契約準備任務之交付與結案**。
- Phase 34B（實作與生產部署）將以本請求單的確認結果作為嚴格入場條件（Entry Condition）。未獲確認前，不建立未經授權的生產憑證或空白 connector。
