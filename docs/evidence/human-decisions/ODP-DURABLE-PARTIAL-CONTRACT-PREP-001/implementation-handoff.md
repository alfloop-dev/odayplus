# 工程交接說明書：WP-33A 到 WP-33B 實作階段交接與驗收規範

- **文件識別碼**：`docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/implementation-handoff.md`
- **當前任務**：`ODP-DURABLE-PARTIAL-CONTRACT-PREP-001`（WP-33A：工程準備與契約草案）
- **承接任務**：`WP-33B`（SHARED-001 PARTIAL 真實寫入與逐項重試實作）
- **關聯需求**：`ODP-FR-SHARED-001`（所有長時間任務都能查詢 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/PARTIAL）
- **交付日期**：2026-09-08
- **作者 / 任務負責人**：Antigravity2
- **審查人**：Codex2
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
   - 確認採用 [partial-retry-contract-draft.json](./partial-retry-contract-draft.json) 作為持久收據與重試 API 之正式 Schema。
3. **工作目錄與 Worktree 隔離**：
   - 承接 WP-33B 之 auto worker 必須從當時最新 `origin/dev` 開立全新 task branch（如 `task/ODP-DURABLE-PARTIAL-IMPL-001`），並於獨立 worktree 執行。

---

## 3. WP-33B 核心實作交付清單

承接 WP-33B 之工程團隊必須交付以下具體模組改動：

### 3.1 Worker Registry 與 Batch Handler 實作
- 於 `apps/worker/oday_worker/handlers.py` 註冊經 H06 核定之批次任務處理器（如 `handle_batch_listing_intake`）。
- 處理器接收包含多個 work items 之 Payload，逐一執行處理，並在彙整統計後依規則轉移狀態：
  - $	ext{succeeded\_count} > 0 \land 	ext{failed\_count} > 0 \implies 	ext{JobStatus.PARTIAL}$。
  - 終態完成時，外層框架更新 `job_queue.update_status(job_id, JobStatus.PARTIAL, delivery_state=None, payload=receipt_envelope)`。

### 3.2 明細收據持久化（Itemized Job Receipt Store）
- 擴展 `shared/infrastructure/persistence/job_receipts.py` 或 Durable twin，支援儲存包含 `items` 清單（`item_id`, `item_status`, `attempt`, `result_ref`, `error`）之 `DurableJobReceipt`。
- 支援服務重啟後完整回讀（Re-readability after restart），收據不丟失。

### 3.3 差異化冪等重試引擎（Member-Level Scoped Retry）
- 實作重試端點 `POST /platform/jobs/{job_id}/retry`，支援參數 `retry_scope="FAILED_ONLY"`。
- 重試執行時：
  1. 自持久層讀取原 Job 之 `ItemReceipt` 清單。
  2. 對於 `item_status == "SUCCEEDED"` 之項目，**直接跳過，調用次數嚴格為 0**。
  3. 僅針對 `item_status == "FAILED"` 且 `error.retryable == true` 之項目重新調用執行邏輯。
  4. 成功項更新其 `item_status` 為 `SUCCEEDED`；若所有項目皆轉為成功，整體 JobStatus 自動收斂轉移為 `JobStatus.SUCCEEDED`。

### 3.4 嚴格不另建 Job Manager（No Second Job Manager）
- 必須復用既有 `shared/jobs/queue.py`、`shared/infrastructure/persistence/job_queue.py` 與 `apps/worker/oday_worker/main.py` 之架構。
- 嚴禁為 PARTIAL 額外開發獨立的 Scheduler 或第二套 Job Manager。

---

## 4. WP-33B 反事實驗收標準（Counterfactual Acceptance Criteria）

WP-33B 之 PR 必須包含並通過以下四項反事實驗收測試套件：

### 測試 1：狀態轉移精確性驗證（State Transition Assertion）
- **測試情境**：輸入 10 筆工作項目（8 筆成功，2 筆校驗失敗）。
- **斷言要求**：
  - 任務終態必須為 `JobStatus.PARTIAL`。
  - 斷言不得為 `JobStatus.SUCCEEDED` 亦不得為 `JobStatus.FAILED`。
  - `delivery_state` 必須為 `None`。

### 測試 2：差異化重試不重複執行驗證（Retry Idempotency & Zero Duplication）
- **測試情境**：對上述處於 `PARTIAL` 狀態之任務發起 `retry_scope="FAILED_ONLY"` 重試。
- **斷言要求**：
  - 使用 Mock/Spy 驗證 8 筆已成功項目之底層業務寫入函式調用次數為 **0**。
  - 2 筆失敗項目之底層函式被精確調用 **1** 次。

### 測試 3：交付狀態與業務結果正交分離驗證（Orthogonality Verification）
- **測試情境**：任務重試過程中遭遇網路暫態斷線（Network Timeout）。
- **斷言要求**：
  - 基礎設施隊列層設置 `JobDeliveryState.RETRYING`，但業務聚合狀態維持 `JobStatus.RUNNING` 或 `QUEUED`。
  - 嚴禁在隊列重試期間將業務狀態錯誤寫入為 `PARTIAL`。

### 測試 4：重啟回讀與亂序冪等驗證（Restart Re-readability & Out-of-Order Idempotency）
- **測試情境**：模擬 Worker 程序 Crash 重啟，並自持久層重新讀取 Job 收據。
- **斷言要求**：
  - 能夠完整還原原始明細清單與各項目的 `result_ref` / `error`。
  - 重複抵達的訊息或亂序 Ack 不破壞已寫入之終態。

---

## 5. 治理登錄與結案指引

當 WP-33B 完成上述程式實作與測試後：
1. 執行專屬測試套件確認全綠。
2. 於 `delivery_toolchain/governance/set_valued_requirements.json` 中將 `ODP-FR-SHARED-001` 之 `PARTIAL` member 由 `BLOCKED_BY_EVIDENCE` 更新為 `VERIFIED`（或由 WP-90 統一整合）。
3. 建立 PR 並附上 exact HEAD 之測試執行收據。
