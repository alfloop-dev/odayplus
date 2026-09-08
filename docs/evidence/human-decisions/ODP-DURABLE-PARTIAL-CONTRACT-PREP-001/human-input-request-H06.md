# 人工決策請求書：H06 — 真實 Durable Job 候選與 PARTIAL 業務範圍確認

- **文件識別碼**：`docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/human-input-request-H06.md`
- **任務識別碼**：`ODP-DURABLE-PARTIAL-CONTRACT-PREP-001`（WP-33A）
- **關聯決策**：`H06`（Durable job 範圍確認，落實使用者決策 D19）
- **關聯需求**：`ODP-FR-SHARED-001`（所有長時間任務都能查詢 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/PARTIAL）
- **交付日期**：2026-09-08
- **作者 / 任務負責人**：Antigravity2
- **審查人**：Codex2
- **檢驗基準代碼（Inspected HEAD SHA）**：`b6b729d95e575dc3b27ea9e02ce22fb128c3970b`（第一次修訂查證基準；探針 1～4 於 `2026-09-08T16:49:34Z`～`16:49:39Z` 於此採集。本文件初版之基準為 `9048161e058becff5a53593a773d3c42238213fb`）
- **交付基準（Delivered HEAD SHA）**：`10113c8cd35444eea721a670dba0f978e60529c2`（依 Codex2 審查意見 R3 補充 `delivery_state` 清除語意說明之內容於此交付）。實測 `git diff --name-only 9048161e 10113c8c -- apps shared modules packages` 輸出空、raw exit code 0（收據 9），故本文件所引用行號於上述各基準與交付 HEAD 一致。
- **檢驗時間（UTC）**：本文件之查證時鐘以 `producer-inventory.json` 之逐條採集收據為準（探針 1～4：`16:49:34Z`～`16:49:39Z`；探針 5～9：`18:33:02Z`～`18:37:05Z`）。先前此處宣告的 `2026-09-08T16:48:00Z` 無收據支撐，已撤回且未以估計值取代。
- **溯源更正（Provenance Correction）**：本版撤回兩個無收據支撐的時間宣告，並以實測值取代，不以任何新估計值填補；完整說明見 [producer-inventory.json](./producer-inventory.json) 的 `metadata.provenance_correction`，實測時序見同節 `delivered_chronology_utc`。
- **歷史證據參照**：`ODP_JOB_PARTIAL_PRODUCER_EVIDENCE_2026-09-03.md`（基準：`04e1572f802a54c2646ba678fe2975226dfbd7c4`，日期：2026-09-03）
- **關聯產物索引**：
  - [README.md](./README.md)
  - [producer-inventory.json](./producer-inventory.json)
  - [partial-retry-contract-draft.json](./partial-retry-contract-draft.json)
  - [implementation-handoff.md](./implementation-handoff.md)

---

## 1. 背景與決策請求目的

依據使用者在 2026-09-08 決策中確認之 **D19（SHARED-001 PARTIAL 選項 A：實作 partial／receipt／retry）** 及《ODP 人工決策落地與 Supervisor／Auto Worker 執行規畫》WP-33A 階段要求：

1. **工程盤點已完成**：工程團隊已於本機代碼庫完成全量 handler、queue、durable 狀態寫入點及 receipt envelope 排除點之靜態盤點（詳見 [producer-inventory.json](./producer-inventory.json)），確認現行代碼庫無任何可達之 `JobStatus.PARTIAL` 生產者。
2. **離線契約已草擬**：工程團隊已完成逐項明細收據（`ItemReceipt`，支援 `attempt >= 0`、取消語意）、失敗項差異化重試（`retry_scope="FAILED_ONLY"`，僅重試 `retryable: true` 項目）與狀態收斂契約草案（詳見 [partial-retry-contract-draft.json](./partial-retry-contract-draft.json)）。
3. **請求人類權威決策（H06）**：請產品負責人（Product Lead）、架構委員會（Architecture Board）或 Human/Ops 權威決策者，在工程團隊提出之候選清單中，正式指定**至少一個需具備部分成功（PARTIAL）語意與逐項重試能力的真實業務 Durable Job**，作為後續 WP-33B 工程實作的正式標的。

---

## 2. 代碼庫現況與盤點事實摘要

依據對 exact HEAD（`b6b729d95e575dc3b27ea9e02ce22fb128c3970b`，並經收據 8／9 實測等價於交付 HEAD `10113c8c`）之靜態查證：

1. **現行 Default Job Registry 僅有 3 個單一實體任務**：
   - `forecast`（`apps/worker/oday_worker/handlers.py:255`，enqueue 於 `apps/api/oday_api/main.py:997-1044`）：單一門市時序預測，正常返回寫 `SUCCEEDED`（`delivery_state=None`），例外重試超限寫 `FAILED` + `DEAD_LETTER`。
   - `external-fetch`（`apps/worker/oday_worker/handlers.py:256`，enqueue 於 `apps/scheduler/oday_scheduler/main.py:180-195` 及 `apps/api/oday_api/main.py:1013-1044`）：單一 provider/window 排程抓取，排程層回報 `SUCCEEDED` / `FAILED`。
   - `assisted-listing-intake`（`apps/worker/oday_worker/handlers.py:257`，enqueue 於 `modules/opsboard/application/network_listings.py:1243-1249`，1210 行為 correlationId）：單一房源 URL 爬蟲處理，單一管線執行，終態寫 `SUCCEEDED` 或 `FAILED` + `DEAD_LETTER`。
   - 以上皆非多項目批次任務（Multi-item Batch Job），無成員聚合狀態轉移。
2. **排除非 Durable Job 之部分成功操作**：
   - `POST /api/v1/intake-batches`（`apps/api/app/routes/listings.py:2225-2265`，helper 於 1730-1799 行）：具備 207 Multi-Status 與 `BatchIntakeReceipt`，但為**同步 API 指令**，無持久佇列與 `job_id`。
   - `xlsx_import.py`（`modules/external_data/application/xlsx_import.py:816-933`）：XLSX partial commit 為同步 command，非背景 worker job。
   - `ingestion_store.py`（`modules/external_data/application/ingestion_store.py:90-123`）：`accepted_count` / `quarantined_count` 為資料持久層品質標記，排程 job 仍為 `SUCCEEDED` / `FAILED`。
3. **排除收據持久化專用 Envelope**：
   - `TenantScopedJobReceiptStore`（`shared/infrastructure/persistence/job_receipts.py:74-105`，`{service}.receipt`）與 `TenantScopedCommandReceiptStore`（`shared/infrastructure/persistence/command_receipts.py:70-105`，`{service}.command-receipt`）以 `shared/jobs/queue.py:10-18` 排除於 worker 派工之外，僅供讀回。
4. **交付狀態與業務結果之現況與契約分離**：
   - 現行代碼中：`apps/worker/oday_worker/main.py:210-222` 於重試耗盡或非可重試異常時寫入 `JobStatus.FAILED` 並附帶 `JobDeliveryState.DEAD_LETTER`；`shared/infrastructure/persistence/job_queue.py:402-406` 於 `status == JobStatus.SUCCEEDED` 時自動清除 `delivery_state = NULL`，但保留失敗時明確傳入之 `DEAD_LETTER`。惟該清除僅適用於 `SUCCEEDED`：以 `delivery_state=None` 寫入 `PARTIAL` 時不會清除既有 `RETRYING`，須於 WP-33B 先行修改寫入器語意（見 [implementation-handoff.md](./implementation-handoff.md) §3.0）。
   - 契約定義：當 WP-33B 批次長任務完成多項目處理並收斂為 `JobStatus.PARTIAL` 時，基礎設施傳遞已結束，因此外層寫入點必須明確將 `delivery_state` 設為 `None`（`NULL`），徹底區隔業務成果（`PARTIAL`）與傳遞死信（`DEAD_LETTER`）。

---

## 3. 候選業務任務評估與效益／失敗案例

工程團隊提出以下三項候選業務任務，供決策者評估與核定：

### 候選一（推薦）：`batch-listing-intake`（非同步批次房源匯入）

- **業務來源**：將既有同步 `POST /api/v1/intake-batches`（`apps/api/app/routes/listings.py:2225-2265`）擴展並註冊非同步背景長任務 `batch-listing-intake`。
- **單元成員**：每一筆待匯入之房源記錄（Row / Listing Item）。
- **業務效益**：
  - 支援大量房源（如 100~1,000+ 筆）非同步批次處理，避免同步 HTTP 連線超時（Timeout）。
  - 提供細粒度逐項收據（Itemized Receipt），操作者可即時檢視個別房源匯入進度與錯誤碼。
  - 遭遇部分失敗時，系統寫入 `JobStatus.PARTIAL`（`delivery_state = null`）。後續重試（`retry_scope="FAILED_ONLY"`）僅重跑具備 `retryable: true` 之失敗項目，**絕對不重做已成功寫入之房源（0 次調用），亦不無效重試永久資料錯誤項目（0 次調用）**，避免產生重複資料或扣款。
- **典型失敗情境與重試分類**：
  1. *非可重試永久輸入錯誤（Permanent Input Error）*：第 2 列地址欄位缺失或格式錯誤（`MISSING_MANDATORY_ADDRESS`），重試無效，標記為 `retryable: false`。重試時**調用 0 次**，attempt 維持 1，狀態維持 FAILED。
  2. *可重試暫態網路錯誤（Transient Network Error）*：第 12 列呼叫外部 TGOS/Google 地理編碼 API 遭遇暫態 503 或超時（`GEOCODING_UPSTREAM_TIMEOUT`），標記為 `retryable: true`。重試時**調用 1 次**，attempt 遞增為 2，成功後轉為 SUCCEEDED。
  3. *非可重試業務衝突（Permanent Conflict Error）*：第 25 列房源外部 ID 已存在（`DUPLICATE_EXTERNAL_ID`），標記為 `retryable: false`。重試時**調用 0 次**。
- **架構契合度**：**極高（High）**。業務邏輯與 207 資料結構已成熟，昇格為 Durable Worker Job 之路徑最短、風險最低。

---

### 候選二：`multi-partition-external-fetch`（多來源／分區外部資料排程攝取）

- **業務來源**：擴展現有 `apps/scheduler/oday_scheduler/` 之 external-fetch，支援單一 Job 同時排程抓取多個分區（Partitions）或多個第三方來源。
- **單元成員**：各個 Provider / Region 分區抓取單元。
- **業務效益**：
  - 單一排程週期內，當特定第三方資料源（如 CWA 氣象或特定不動產平台）暫態中斷時，其餘資料源仍能正常入庫。
  - 整體 Job 標記為 `PARTIAL`，排程重試僅針對失敗之分區進行補抓，不重複抓取已成功分區。
- **典型失敗情境**：
  1. *暫態錯誤（retryable: true）*：Provider A 遭遇伺服器維護 503，Provider B 與 C 正常。
  2. *永久錯誤（retryable: false）*：特定分區 Payload Schema 變更導致解析失敗。
- **架構契合度**：**中等（Medium）**。需重構 Scheduler 之 Enqueue 機制與 Payload 聚合架構。

---

### 候選三：`batch-sitescore-evaluation`（批次門市選址評估運算）

- **業務來源**：將 `modules/sitescore/` 擴展為可接收多個候選地點（20+ Candidate Locations）的批次運算任務。
- **單元成員**：單一候選門市地點（Store Location Candidate）。
- **業務效益**：
  - 零售展店企劃人員一次提交多個候選地點進行評估。
  - 當部分地點因圖資不足或邊界判定失敗時，其餘成功地點先產出評估分數供決策參考。
  - 前端 UI 現有之 `SiteScoreJobStatus` 元件已內建 `PARTIAL` 視覺呈現，可直接對接。
- **典型失敗情境**：
  1. *永久錯誤（retryable: false）*：候選座標超出目前已載入之人口網格圖資範圍。
  2. *暫態錯誤（retryable: true）*：微商圈最佳化演算法暫態 Timeout。
- **架構契合度**：**中等（Medium）**。

---

## 4. H06 人工決策核定表（Decision Signoff Table）

請權責決策人在下表中勾選或指定 WP-33B 之實作範圍：

| 候選編號 | 候選任務識別碼 | 建議選擇 | 預期業務利益 | 決策簽核（核准 / 排除） | 具名簽核人與日期 |
|---|---|---|---|---|---|
| **CANDIDATE-01** | `batch-listing-intake` | **(Recommended)** | 批次房源非同步化、207 昇格為 Durable PARTIAL、逐項差異化重試 | [ ] 核准納入 33B 實作<br>[ ] 暫緩 | *(待 Human/Ops 填寫)* |
| **CANDIDATE-02** | `multi-partition-external-fetch` | (Optional) | 外部資料多分區容錯攝取、分區補抓 | [ ] 核准納入 33B 實作<br>[ ] 暫緩 | *(待 Human/Ops 填寫)* |
| **CANDIDATE-03** | `batch-sitescore-evaluation` | (Optional) | 多門市選址批次計算、部分產出 | [ ] 核准納入 33B 實作<br>[ ] 暫緩 | *(待 Human/Ops 填寫)* |

*(註：決策者亦可於此處指定其他尚未列出但有明確批次業務需求之 Job Type)*

---

## 5. 治理邊界與非交涉承諾（Non-Negotiable Guardrails）

1. **禁止假實作（No Fake Implementation）**：
   - 決策選定前，AI 不得逕自將未達成的 requirement member 標記為 `VERIFIED` 或 `DECIDED`。
   - 嚴禁以 Queue Delivery State（`RETRYING` / `DEAD_LETTER`）充當 `PARTIAL`。
2. **禁止自造豁免（No Self-Granted Waivers）**：
   - 任何需求變更或豁免必須由具名人類治理角色依法定程序簽署。
3. **無存取標示明確（Static-only Labeling）**：
   - 本請求書所列 inventory 僅代表代碼庫靜態分析事實，未冒充 Production Runtime 實際佇列日誌。

---

## 6. 後續階段入場條件（WP-33B Entry Criteria）

完成 H06 決策簽核後，WP-33B（實作階段）方可入場，其具體交付物與反事實驗收標準已載於 [implementation-handoff.md](./implementation-handoff.md)。
