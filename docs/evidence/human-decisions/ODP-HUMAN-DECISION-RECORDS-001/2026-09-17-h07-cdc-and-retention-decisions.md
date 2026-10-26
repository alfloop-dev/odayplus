# H07 人工決策簽核回覆

- **對應請求單**: [H07-human-input-request-H07.md](H07-human-input-request-H07.md)
- **對應 task**: `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001`
- **記錄方式**: 由操作者於 2026-09-17 互動工作階段中逐項裁示，Claude 代為謄錄
- **狀態**: `COMPLETE` — 五項決策全數裁示、具名，硬前提（Replica Set）已確認

- **簽核人 (Decider/Principal)**: **蔡尚志**
- **授權角色 (Role)**: 負責人
- **裁示日期 (Date)**: 2026-09-17

> [!NOTE]
> 具名來源：由本機操作者於 2026-09-17 互動工作階段中提供，Claude 代為謄錄，
> 非簽核人本人在此檔案上的數位簽署。若後續閘門要求可驗證的簽署憑據
> （principal_id、簽章或其他身分系統憑證），需由簽核人本人補齊，本檔不足以替代。

---

## 決策結果

### 1. 目標來源與 SLA — **選項 A（Scoped CDC）**

僅對下列集合啟用近即時 CDC 串流：

| 集合 | 目標延遲 SLA |
|---|---|
| `orders` | < 10 秒 |
| `device_log`（或其對應 `machine_status_events`） | < 10 秒 |

其餘 13 個內部集合維持現行 15 分鐘感測器或每日快照批次，不建立 Change Stream 連線。

### 2. MongoDB 拓撲確認 — **已確認**

| 項目 | 結果 |
|---|---|
| `fongniao_prod` 部署拓撲 | **Replica Set** |
| Oplog 保留時間窗 | **24–48 小時** |
| Resume Token 失效時自動轉全量快照重讀對帳 | **同意** |

> **來源與確認程度**：由操作者於 2026-09-17 對話中確認，非生產 DBA 的書面回覆。
> 拓撲一項為明確答覆；oplog 保留窗為區間而非實測數值。若後續閘門要求可回讀的
> 量測憑據（如 `rs.status()`、`db.getReplicationInfo()` 輸出），需另行取得。
> 未轉發的 DBA 問題單保留於 [H07-dba-query.md](H07-dba-query.md)。

**選項 A 的硬前提已滿足**：Replica Set 具備 oplog，`db.collection.watch()` 可用，
第 1、3、5 項的裁示成立，無須退回改選。

> **本項「同意」所帶的實作約束**（同上文件第 257 行）：resume token 超出 oplog
> 保留窗即永久失效，唯一出路是退回全量重讀。因此 **CDC 只能疊在批次路徑上，
> 不能取代它**——現行全量快照讀取必須保留為 fallback。任何宣稱「改用 CDC
> 之後就不需要批次」的實作都違反本裁示。

### 3. 憑證與權限授權 — **兩項均核准**

- 核准為專屬帳號 `odp_cdc_reader` 開啟目標集合（`orders`、`device_log`/`machine_status_events`）的 `changeStream` 權限。
- 核准在 CDC 配接器記憶體管線中執行應用層欄位投影與個資遮蔽，確保未授權欄位不寫入下游。

> 實作注意：Change Streams 傳回完整文件（Full Document），邊界比現行
> `find` + `SOURCE_PROJECTIONS` 寬。現行 `member` 等集合在讀取端的 `_minimize_member`
> 遮蔽必須在 CDC 路徑上有等價實作，否則此核准的前提不成立。

### 4. 刪除與墓碑政策 — **選項 A（軟刪除與稽核墓碑並行）**

- 業務表：上游刪除／作廢映射為 `status = 'voided'` 或 `is_deleted = TRUE`，保留歷史可追溯性。
- 隱私清除（GDPR／忘記我）：寫入帶 SHA-256 雜湊的加密墓碑記錄，並將業務表個資欄位覆蓋為空值。
- 實體刪除：保留給定期 Retention Purge Job，不隨每筆 CDC 即時硬刪除。
- **保留期限**: **90 天**（一個季度，涵蓋季報、對帳與爭議追溯）

### 5. 傳輸架構選擇 — **方案 1（Dagster 內常駐 ChangeStream Sensor）**

在既有 Dagster worker 內運行 long-running ChangeStream sensor，直接寫入 PostgreSQL
暫存表與控制架構。不引入 Kafka／Redpanda／Pub/Sub／RabbitMQ 等外部訊息仲介。

---

## 入場條件狀態

五項決策全數裁示完畢，`fongniao_prod` 為 Replica Set 的硬前提已確認，
Phase 34B 的人工輸入缺口已關閉。

唯一保留的品質註記：第 2 項來自操作者口頭確認而非 DBA 書面回覆，
oplog 保留窗為區間（24–48 小時）而非實測值。若實作階段的驗收要求可回讀的
量測憑據，需補 `rs.status()` 或 `db.getReplicationInfo()` 的實際輸出——
這不影響方案選擇，只影響「重讀頻率評估」這一項的證據強度。
