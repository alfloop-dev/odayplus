# INT-001 CDC 處置與治理交付報告 (Disposition & Governance Handback)

- Task: `ODP-INT001-CDC-DISPOSITION-001`
- 需求: `ODP-RTM-INT-001` / `ODP-FR-INT-001`（`docs/rtm/ODAY_PLUS_EXECUTION_RTM.md:65`，`MUST`，`baselined`，owner 記為 `Data Platform Owner`）
- 日期: 2026-09-03
- 負責人 (Owner): Codex
- 審查人 (Reviewer): Antigravity7
- 執行身分 (Worker): Antigravity3 (Helper execution lease)
- 依據文件:
  - `docs/evidence/ODP_INT001_CDC_SOURCE_EVIDENCE_2026-09-03.md` (上游來源系統查證)
  - `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md` (需求處置政策與正式登錄表)
  - `delivery_toolchain/governance/set_valued_requirements.json` (集合型需求可機讀清單)
  - `docs/plans/ODP_REMEDIATION_PLAN_2026-09-03.md` (第 6 批 remediation)

---

## 1. 處置判定與核心決策 (Disposition Verdict)

依據前置任務 `ODP-INT001-CDC-SOURCE-EVIDENCE-001` 之查證結果與本任務之治理要求，做成以下正式處置判定：

1. **CDC 對現行上游來源系統不適用 (N/A)**：
   全系統唯一內部生產來源為 MongoDB `fongniao_prod`，其餘為 6 個外部快照提供者（provider）。沒有任何一個生產上游需要 CDC 才能滿足其變更日誌、延遲、順序或刪除語意。
2. **嚴禁建立裝飾性空 Connector (No Empty/Mock Connectors)**：
   在缺乏真實上游合約與連線依據下，不建立空的 CDC connector 或假串流客戶端，避免製造虛假合規與未經授權的連線邊界。
3. **AI 嚴格禁止自簽豁免 (Prohibition of AI Self-Signed Waivers)**：
   依據架構治理原則，AI 代理人無權代替業務與架構負責人豁免或廢止 `MUST` 需求。因此 `ODP-FR-INT-001::CDC` 在可機讀清單中維持 `absent` 索引，處置狀態設為 `OPEN`，並產出需人類治理授權的 Formal Amendment / Waiver Handback。
4. **可機讀需求清單與治理文檔全面對齊**：
   將 `ODP-FR-INT-001` 納入 `set_valued_requirements.json`（5 個成員：BATCH, API, FILE, EVENT, CDC），並於 `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md` 完成結構化同步，確保 CI 閘門（`check_requirement_members.py`）機械式驗證通過。

---

## 2. 上游來源與邊界查證總結 (Upstream Evidence Summary)

依據五大面向逐一查核，確認無實作 CDC 之技術與業務必要性：

| 評估面向 | 生產現況查證結果 | CDC 必要性判定 | 備註與治理依據 |
|---|---|---|---|
| **變更日誌 (Change Log)** | 內部 MongoDB 採全量快照（8 個 kind）與 `updatedAt` 水位線（7 個 kind）讀取；全樹對 Change Stream / oplog 無任何生產讀取程式碼。外部來源皆為快照覆蓋。 | **不需要** | Batch Envelope 格式已具備 `source_event_type`、`is_deleted`、`payload_hash` 等變更語意載體。 |
| **延遲需求 (Latency SLA)** | 下游消費端全部為 `DailyPartitionsDefinition`（日粒度）；供給端已有 3 個 Change Sensor 提供 15 分鐘輪詢。全系統無任何 sub-15m 延遲需求。 | **不需要** | 供給端速度已快於消費端需求兩個數量級。若需加速，排程 manual-only kinds 比引入 CDC 更便宜有效。 |
| **順序與冪等 (Ordering & Idempotency)** | 讀取透過 `sort("_id", 1)` 與游標續讀；冪等鍵由 `(kind, source_id, content_sha256)` 鎖定；落地端皆為 upsert。亂序到達不影響終態。 | **不需要** | 正確性不依賴 CDC 全域變更順序。 |
| **刪除語意 (Delete Semantics)** | 上游邏輯刪除與作廢（`voided` / `refunded` / `operation`）已映射為狀態欄位；上游實體刪除雖無法由增量水位線偵測，但下游落地層（PostgreSQL）**完全沒有刪除/墓碑路徑**（全為 `INSERT ... ON CONFLICT DO UPDATE`）。 | **不需要** | 即使引入 CDC，下游也無刪除路徑可接；下游刪除傳播為獨立缺陷（見 §5），修復它亦不需 CDC。 |
| **憑證與安全邊界 (Credential Boundary)** | 現行 Mongo 連線字串（`ODP_DATA_MONGO_URI`）僅具備具名 collection 之 `find` 權限並帶欄位投影；Change Stream 需擴大至 cluster 級別授權（`readAnyDatabase`），且無法於讀取邊界進行個資投影最小化。 | **嚴格拒絕** | 擴大生產憑證權限屬重大安全變更，未經人類安全官核准前 fail-closed。 |

---

## 3. 需求成員可機讀清單與 Traceability (ODP-FR-INT-001)

`delivery_toolchain/governance/set_valued_requirements.json` 已正式登錄 `ODP-FR-INT-001`：

```json
{
  "id": "ODP-FR-INT-001",
  "statement": "整合層必須支援 Batch、CDC、API、File 與 Event 來源攝取模式。",
  "member_count": 5,
  "members": [
    {
      "name": "BATCH",
      "status": "satisfied",
      "evidence": "apps/data_platform/source.py::MongoSource",
      "disposition": {
        "state": "VERIFIED"
      }
    },
    {
      "name": "API",
      "status": "satisfied",
      "evidence": "modules/external_data/connectors/provider_registry.py::PROVIDER_REGISTRY",
      "disposition": {
        "state": "VERIFIED"
      }
    },
    {
      "name": "FILE",
      "status": "satisfied",
      "evidence": "modules/external_data/application/xlsx_import.py::XlsxCommitReceipt",
      "disposition": {
        "state": "VERIFIED"
      }
    },
    {
      "name": "EVENT",
      "status": "absent",
      "note": "The machine_status_event schema contract declares integration_mode=event_stream and envelope=event, but in production core.machine_status_events is populated via batch watermark reads of device_log (store.py:394-401). No event broker/stream consumer currently exists in production.",
      "disposition": {
        "state": "OPEN",
        "assigned_to": "Platform Infrastructure Lead",
        "next_review_date": "2026-10-01",
        "rationale": "Schema contract declares event_stream mode but production ingestion path executes via device_log batch ingestion; reconcile contract or implement event consumer."
      }
    },
    {
      "name": "CDC",
      "status": "absent",
      "note": "Audited in ODP_INT001_CDC_SOURCE_EVIDENCE_2026-09-03.md and ODP_INT001_CDC_DISPOSITION_2026-09-03.md. No production upstream requires CDC for change log, ordering, latency, or delete semantics; downstream data plane has no delete propagation path; change stream credentials require expanded cluster-level privileges. Maintained as absent; handback submitted for formal human requirement amendment / waiver governance.",
      "disposition": {
        "state": "OPEN",
        "assigned_to": "Data Platform Lead",
        "next_review_date": "2026-10-01",
        "rationale": "No production upstream requires CDC; downstream sink has no delete path; credential boundary expansion unapproved. AI cannot self-sign waivers; pending human governance amendment/waiver authorization.",
        "formal_handback_ref": "docs/evidence/ODP_INT001_CDC_DISPOSITION_2026-09-03.md"
      }
    }
  ]
}
```

### 成員狀態總覽

| 成員 (Member) | 狀態 (Status) | 處置 (Disposition) | 實作符號或指派負責人 | 說明 |
|---|---|---|---|---|
| **BATCH** | `satisfied` | `VERIFIED` | `apps/data_platform/source.py::MongoSource` | 全量快照與水位線增量讀取已實作且有完整測試保護。 |
| **API** | `satisfied` | `VERIFIED` | `modules/external_data/connectors/provider_registry.py::PROVIDER_REGISTRY` | 外部商用 POI、地理編碼等 API 介接已實作。 |
| **FILE** | `satisfied` | `VERIFIED` | `modules/external_data/application/xlsx_import.py::XlsxCommitReceipt` | XLSX 匯入、Feed 與公開資料集攝取已實作。 |
| **EVENT** | `absent` | `OPEN` | `Platform Infrastructure Lead` (下次檢視: 2026-10-01) | 契約宣告為 `event_stream`，但生產以 `device_log` 批次落地，無 Stream Consumer。 |
| **CDC** | `absent` | `OPEN` | `Data Platform Lead` (下次檢視: 2026-10-01) | 無上游需求、無下游刪除路徑、憑證擴大未核准；維持 absent 並交付人類裁決。 |

---

## 4. 人類治理授權交付包 (Formal Governance Handback Package)

依據治理守則，此交付包提交給 **Data Platform Lead** 及 **Human/Ops (Architecture Board)** 進行正式需求修訂或具期限豁免簽署：

### 方案 A（推薦）：正式需求修訂 (Formal Requirement Amendment)
- **修訂內容**：將 `ODP-RTM-INT-001` 的需求陳述由「必須支援 Batch, CDC, API, File, Event 模式」修訂為對齊實作的資料攝取 Taxonomy：
  > 「Integration Layer must support batch_snapshot, incremental_batch, event_stream, backfill, and api_lookup modes across configured internal and external acquisition methods.」
- **理由**：現行 RTM 混淆了傳輸型態與取得管道；修訂後的 Taxonomy 更嚴謹、且已完全涵蓋真實業務需求。
- **生效後動作**：`ODP-FR-INT-001` 的成員更新為新 Taxonomy，CDC 正式除名，不再列為缺口。

### 方案 B：簽署具期限之架構豁免 (1-Year Formal Waiver)
- **豁免對象**：`ODP-FR-INT-001::CDC`
- **法定欄位填寫建議**：
  - `formal_decision_ref`: `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md#odp-fr-int-001-cdc`
  - `decider`: `Human/Ops (Architecture Board)` 或 `Data Platform Lead`
  - `decision_date`: 簽署當日 (YYYY-MM-DD)
  - `scope`: ODP 整合層與外部資料平台資料攝取架構
  - `risk_owner`: `Data Platform Lead`
  - `expiry`: 簽署日起 1 年（如 `2027-09-01`）
  - `reopen_trigger`:
    1. 當業務出現明確的 sub-15-minute 近即時串流攝取 SLA 需求時；或
    2. 當上游核心資料庫架構演進為具備變更日誌串流之微服務，且下游 PostgreSQL 落地層已實作刪除墓碑（tombstone）傳播路徑時。
  - `rationale`: 引用本報告 §2 之五大查證結論。

---

## 5. 獨立缺陷與次要落差追蹤 (Carried-Forward Gaps)

查證過程中確認之獨立缺陷，需於後續專門 Task 處理，不與本 CDC 處置混淆：

1. **下游落地層無刪除傳播路徑 (Downstream Delete Propagation Gap)**：
   `apps/data_platform/store.py` 與 `pipeline.py` 對 `delete` 命中為 0，所有寫入皆為 upsert。上游實體刪除在下游無墓碑標記或失效清除機制。**嚴重度：高**（需另立資料清理/墓碑機制 Task）。
2. **`event_stream` 契約宣告與生產路徑落差**：
   `machine_status_event` 宣告為事件串流，生產走批次。**嚴重度：中**（由 `Platform Infrastructure Lead` 評估補建生產者或改契約）。
3. **來源 Owner 與 Latency SLA 詮釋資料缺漏**：
   15 個內部集合與 8 個外部提供者未宣告具名資料負責人與延遲 SLA。**嚴重度：低**。

---

## 6. 可重現的驗證收據 (Reproducible Verification Receipts)

於工作區 `/tmp/pantheon-worker-worktrees/pantheon/odp-int001-cdc-disposition-001` 執行：

### 收據 1：集合型需求檢查器（包含 ODP-FR-INT-001）
```bash
python3 delivery_toolchain/governance/check_requirement_members.py
```
**執行結果**：
```text
Requirement member checks passed: 8 set-valued requirements, 41 members (30 satisfied, 11 absent and noted; dispositions: BLOCKED_BY_EVIDENCE=4, DECIDED=1, IMPLEMENTATION_READY=2, OPEN=4, VERIFIED=30).
Exit code: 0
```

### 收據 2：治理單元測試套件
```bash
.venv/bin/pytest delivery_toolchain/governance/test_check_requirement_members.py -q
```
**執行結果**：
```text
..........................................                               [100%]
68 passed
Exit code: 0
```

### 收據 3：INT-001 專屬處置與邊界整合測試
```bash
.venv/bin/pytest tests/integration/test_int001_cdc_disposition.py -q
```
**執行結果**：
```text
......                                                                   [100%]
7 passed
Exit code: 0
```

### 收據 4：攝取合約 Taxonomy 與不含未授權 CDC 斷言
```bash
.venv/bin/pytest tests/contract/test_ingestion_contracts.py -q
```
**執行結果**：
```text
........................................................................................... [100%]
91 passed in 0.18s
Exit code: 0
```

### 收據 5：合併最新 dev 基準後的分支驗證
```bash
git merge-base --is-ancestor origin/dev HEAD
git diff --check HEAD^1 HEAD
```
**執行結果**：
`origin/dev`=`ee05af41b87d80bbc62be1b0f504523c9a3b1a0a` 已由 merge commit
`ec867c65fb50b67dbf10f6eebe69772f04adf6b6` 合入 task branch；兩項命令均 exit code 0。
