# 工程交接說明書：WP-33A 到 WP-33B 實作階段交接與驗收規範

- **文件識別碼**：`docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/implementation-handoff.md`
- **當前任務**：`ODP-DURABLE-PARTIAL-CONTRACT-PREP-001`（WP-33A：工程準備與契約草案）
- **承接任務**：`WP-33B`（SHARED-001 PARTIAL 真實寫入與逐項重試實作）
- **關聯需求**：`ODP-FR-SHARED-001`（所有長時間任務都能查詢 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/PARTIAL）
- **交付日期**：2026-09-08
- **作者 / 任務負責人**：Antigravity2
- **審查人**：Codex2
- **檢驗基準代碼（Inspected HEAD SHA）**：`b6b729d95e575dc3b27ea9e02ce22fb128c3970b`
- **檢驗時間（UTC）**：`2026-09-08T16:48:00Z`
- **歷史證據參照**：`ODP_JOB_PARTIAL_PRODUCER_EVIDENCE_2026-09-03.md`（基準：`04e1572f802a54c2646ba678fe2975226dfbd7c4`，日期：2026-09-03）
- **關聯產物索引**：
  - [README.md](./README.md)
  - [producer-inventory.json](./producer-inventory.json)
  - [partial-retry-contract-draft.json](./partial-retry-contract-draft.json)
  - [human-input-request-H06.md](./human-input-request-H06.md)

---

## 1. 任務階段交接總覽與邊界劃分

本文件定義由 **WP-33A（本準備任務）** 轉交至 **WP-33B（正式實作任務）** 之工程規格、入場前置條件、實作交付清單與反事實驗收標準。

### 1.1 階段邊界定義

| 階段識別 | 階段名稱 | 核心職責與邊界 | 交付產物 | 狀態定義 |
|---|---|---|---|---|
| **WP-33A** | 工程盤點與契約準備 | 盤點代碼庫全部生產者、草擬 JSON Schema 契約、建立 H06 決策請求；**不修改業務程式碼、不捏造執行期收據**。 | 獨立證據目錄下之 5 份契約與盤點產物 | `review_approved` → `done`（本任務） |
| **WP-33B** | 實作與反事實驗收 | 在 H06 決策簽核後，實作指定之 Durable Batch Job Handler、明細收據持久化、差異化重試機制與反事實驗證套件。 | Production Code、Migration、API 端點、反事實驗證測試 | `IMPLEMENTATION_READY` → `VERIFIED` |

---

## 2. WP-33B 入場條件（Entry Prerequisites）

WP-33B 啟動派工前，必須滿足以下所有前置條件：

1. **H06 決策完成簽核**：
   - 產品負責人或架構委員會已於 [human-input-request-H06.md](./human-input-request-H06.md) 中正式勾選並核准至少一個業務 Job 候選（例如推薦之 `batch-listing-intake`）。
2. **契約規格鎖定**：
   - 確認採用 [partial-retry-contract-draft.json](./partial-retry-contract-draft.json) 作為持久收據與重試 API 之正式 Schema（包含 `attempt >= 0`、取消明細與狀態優先級規範）。
3. **工作目錄與 Worktree 隔離**：
   - 承接 WP-33B 之 auto worker 必須從當時最新 `origin/dev` 開立全新 task branch（如 `task/ODP-DURABLE-PARTIAL-IMPL-001`），並於獨立 worktree 執行。

---

## 3. WP-33B 核心實作交付清單

承接 WP-33B 之工程團隊必須交付以下具體模組改動：

### 3.1 Worker Registry 與 Batch Handler 實作
- 於 `apps/worker/oday_worker/handlers.py` 註冊經 H06 核定之批次任務處理器（如 `handle_batch_listing_intake`）。
- 處理器接收包含多個 work items 之 Payload，逐一執行處理，並在彙整統計後依規則轉移狀態：
  - $\text{succeeded\_count} > 0 \land \text{failed\_count} > 0 \implies \text{JobStatus.PARTIAL}$。
  - 終態完成時，外層框架更新 `job_queue.update_status(job_id, JobStatus.PARTIAL, delivery_state=None, payload=receipt_envelope)`，明確清除 `delivery_state` 為 `None`。

### 3.2 明細收據持久化（Itemized Job Receipt Store）
- 擴展 `shared/infrastructure/persistence/job_receipts.py` 或 Durable twin，支援儲存包含 `items` 清單（`item_id`, `item_status`, `attempt`, `result_ref`, `error`）之 `DurableJobReceipt`。
- 支援服務重啟後完整回讀（Re-readability after restart），收據不丟失。
- 支援未執行之取消項目記錄（`item_status="CANCELLED"`, `attempt=0`）。

### 3.3 差異化冪等重試引擎（Member-Level Scoped Retry）
- 實作重試端點 `POST /platform/jobs/{job_id}/retry`，支援參數 `retry_scope="FAILED_ONLY"`。
- 重試執行時：
  1. 自持久層讀取原 Job 之 `ItemReceipt` 清單。
  2. 對於 `item_status == "SUCCEEDED"` 之項目，**直接跳過，調用次數嚴格為 0**。
  3. 對於 `item_status == "FAILED"` 且 `error.retryable == false` 之永久資料錯誤項目，**直接跳過，調用次數嚴格為 0**（attempt 次數不變）。
  4. 僅針對 `item_status == "FAILED"` 且 `error.retryable == true` 之暫態錯誤項目重新調用執行邏輯（調用次數為 **1**，attempt 遞增）。
  5. 成功項更新其 `item_status` 為 `SUCCEEDED`；若所有原本失敗之項目皆轉為成功，整體 JobStatus 自動收斂轉移為 `JobStatus.SUCCEEDED`。若仍有非可重試失敗項，則維持 `JobStatus.PARTIAL`。

### 3.4 嚴格不另建 Job Manager（No Second Job Manager）
- 必須復用既有 `shared/jobs/queue.py`、`shared/infrastructure/persistence/job_queue.py` 與 `apps/worker/oday_worker/main.py` 之架構。
- 嚴禁為 PARTIAL 額外開發獨立的 Scheduler 或第二套 Job Manager。

---

## 4. WP-33B 反事實驗收標準（Counterfactual Acceptance Criteria）

WP-33B 之 PR 必須包含並通過以下四項反事實驗收測試套件：

### 測試 1：狀態轉移精確性與成員明細驗證（State Transition & Itemized Receipt Assertion）
- **測試情境與 Fixtures**：
  - 輸入 10 筆工作項目：8 筆正常房源資料、1 筆永久地址缺失資料（`MISSING_MANDATORY_ADDRESS`，`retryable: false`）、1 筆暫態地理編碼超時資料（`GEOCODING_UPSTREAM_TIMEOUT`，`retryable: true`）。
- **初次執行斷言要求**：
  - 任務初次執行全部 10 筆項目（每筆 `attempt == 1`）。
  - 8 筆成功項目 `item_status == "SUCCEEDED"`, `result_ref != null`, `error == null`。
  - 1 筆永久錯誤項目 `item_status == "FAILED"`, `error.code == "MISSING_MANDATORY_ADDRESS"`, `error.retryable == false`。
  - 1 筆暫態錯誤項目 `item_status == "FAILED"`, `error.code == "GEOCODING_UPSTREAM_TIMEOUT"`, `error.retryable == true`。
  - 任務整體終態必須為 `JobStatus.PARTIAL`。
  - 斷言不得為 `JobStatus.SUCCEEDED` 亦不得為 `JobStatus.FAILED`。
  - `delivery_state` 必須為 `None`（`null`）。
  - `summary` 斷言：`total_count: 10, succeeded_count: 8, failed_count: 2, cancelled_count: 0, pending_count: 0`。

### 測試 2：差異化重試不重複執行與收斂驗證（Scoped Retry & Zero Duplication Assertion）
- **測試情境**：對上述處於 `PARTIAL` 狀態之任務發起 `retry_scope="FAILED_ONLY"` 重試。
- **重試執行斷言要求**：
  - 使用 Mock/Spy 驗證 8 筆已成功項目之底層業務寫入函式調用次數為 **0**。
  - 1 筆永久錯誤項目（`retryable: false`）之底層函式調用次數為 **0**（`attempt` 維持 1，狀態維持 `FAILED`）。
  - 1 筆暫態錯誤項目（`retryable: true`）之底層函式被精確調用 **1** 次（`attempt` 遞增為 2，成功轉為 `SUCCEEDED`）。
  - 重試後收據匯總斷言：`succeeded_count: 9, failed_count: 1, cancelled_count: 0, pending_count: 0`，整體狀態維持 `JobStatus.PARTIAL`，`delivery_state: None`。
- **完全收斂子測試（Full Convergence Sub-test）**：
  - 若一 `PARTIAL` 任務包含 8 筆成功與 2 筆暫態錯誤（皆 `retryable: true`），發起重試後僅該 2 筆暫態項目各調用 1 次；成功後整體 JobStatus 由 `PARTIAL` 自動收斂轉移為 `JobStatus.SUCCEEDED`。

### 測試 3：交付狀態與業務結果正交分離驗證（Orthogonality Verification）
- **測試情境**：
  1. 任務重試過程中遭遇網路暫態斷線（Network Timeout），佇列層設置 `JobDeliveryState.RETRYING`，業務聚合狀態維持 `JobStatus.RUNNING` 或 `QUEUED`（嚴禁在此階段寫入 `PARTIAL`）。
  2. 單一實體任務重試次數耗盡或遭遇非可重試異常時，寫入 `JobStatus.FAILED` 並附帶 `JobDeliveryState.DEAD_LETTER`。
  3. 批次多項目任務處理完成並收斂為部分成功時，寫入 `JobStatus.PARTIAL` 並明確清除 `delivery_state = None`。

### 測試 4：重啟回讀與取消／未執行項目驗證（Restart Re-readability & Cancellation Assertion）
- **測試情境**：
  1. 模擬 Worker 程序 Crash 重啟，自持久層重新讀取 Job 收據，驗證能夠完整還原原始明細清單與各項目的 `attempt`、`result_ref` 與 `error`。
  2. 模擬批次任務執行至第 1 筆成功後被 Operator 中途取消：第 1 筆為 `SUCCEEDED`（`attempt=1`），第 2 筆為 `CANCELLED`（`attempt=1`，`code="CANCELLED_MID_EXECUTION"`），第 3 筆未執行為 `CANCELLED`（`attempt=0`，`code="CANCELLED_BEFORE_EXECUTION"`）；整體 JobStatus 依優先級轉移為 `JobStatus.CANCELLED`。

---

## 5. 治理登錄與結案指引

當 WP-33B 完成上述程式實作與測試後：
1. 執行專屬測試套件確認全綠。
2. 於 `delivery_toolchain/governance/set_valued_requirements.json` 中將 `ODP-FR-SHARED-001` 之 `PARTIAL` member 由 `BLOCKED_BY_EVIDENCE` 更新為 `VERIFIED`（或由 WP-90 統一整合）。
3. 建立 PR 並附上 exact HEAD 之測試執行收據。
