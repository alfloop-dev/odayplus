# 工程交接說明書：WP-33A 到 WP-33B 實作階段交接與驗收規範

- **文件識別碼**：`docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/implementation-handoff.md`
- **當前任務**：`ODP-DURABLE-PARTIAL-CONTRACT-PREP-001`（WP-33A：工程準備與契約草案）
- **承接任務**：`WP-33B`（SHARED-001 PARTIAL 真實寫入與逐項重試實作）
- **關聯需求**：`ODP-FR-SHARED-001`（所有長時間任務都能查詢 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/PARTIAL）
- **交付日期**：2026-09-08
- **作者 / 任務負責人**：Claude2（初版由 Antigravity2 交付；本版依 Codex2 審查意見 R1／R2／R3 修訂）
- **審查人**：Codex2
- **檢驗基準代碼（Inspected HEAD SHA）**：`10113c8cd35444eea721a670dba0f978e60529c2`（採集基準；交付本次更正的 commit 為其後代，且僅改動本證據目錄）
- **最近一次採集時間（UTC）**：`2026-09-08T18:33:09Z`（探針 5～7 之時鐘讀值）
- **初版檢驗基準**：`9048161e058becff5a53593a773d3c42238213fb`（初版交付 commit `7a98bef5` 之實測 parent；初版撰寫時鐘未留收據，記為 unknown，上界為該 commit 之 2026-09-08T16:14:18Z）
- **跨基準行號等價性（實測）**：`git diff --name-only 9048161e 10113c8c -- apps shared modules packages` 輸出空、raw exit code 0（收據 9），故本文件所引用行號於該基準及其後代 PR head 成立
- **溯源更正（Provenance Correction）**：本版撤回兩個無收據支撐的時間宣告，並以實測值取代，不以任何新估計值填補；完整說明見 [producer-inventory.json](./producer-inventory.json) 的 `metadata.provenance_correction`，實測時序見同節 `delivered_chronology_utc`。
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

### 3.0 前置修改：`update_status` 之 `delivery_state` 清除語意（必要且優先）

本節為 WP-33B 之**實作前置條件**，必須先於 §3.1 之批次 handler 落地，否則 PARTIAL 終態會攜帶殘留的 `RETRYING`。

**現行行為（缺陷，非既已足夠）**：`DurableJobQueue.update_status`（`shared/infrastructure/persistence/job_queue.py:384-452`）於 `shared/infrastructure/persistence/job_queue.py:402-406` 的分支為：

- `if status == JobStatus.SUCCEEDED:` → append `"delivery_state = NULL"`；
- `elif delivery_state is not None:` → append `"delivery_state = ?"`。

因此以 `delivery_state=None` 寫入 `JobStatus.PARTIAL` 時，兩個分支皆不成立，UPDATE 語句**完全不含 `delivery_state` 指派**，資料庫既有值原封保留。由於批次任務在 PARTIAL 之前極可能經歷過重試（`apps/worker/oday_worker/main.py:210-222` 會寫入 `JobStatus.QUEUED` + `JobDeliveryState.RETRYING`），實際持久結果會是 `status=PARTIAL, delivery_state=RETRYING`，違反本契約之正交分離規則。

**In-memory twin 具相同缺陷**：`InMemoryJobQueue.update_status`（`shared/jobs/queue.py:338-382`）於 `shared/jobs/queue.py:362-364` 為 `resolved_delivery = delivery_state if delivery_state is not None else record.delivery_state`，其後僅 `if status == JobStatus.SUCCEEDED: resolved_delivery = None`。PARTIAL 同樣沿用舊值。兩個實作必須同步修改以維持 durable／in-memory parity，否則以 in-memory queue 撰寫的測試會假綠。

**必要變更（WP-33B 實作範圍，本 A 階段不修改業務程式碼）**：

1. 使 `update_status` 能夠明確表達「清除 `delivery_state`」。可接受之任一設計：
   - 將自動清除條件由單一 `SUCCEEDED` 擴充為「交付已終結之業務終態集合」`{SUCCEEDED, PARTIAL, CANCELLED}`；或
   - 引入顯式哨兵／旗標（例如 `clear_delivery_state=True`）以區分「未指定」與「明確清為 NULL」，解決 `None` 目前同時代表這兩種意義的多義性。
2. **必須保留現行 FAILED 行為**：`JobStatus.FAILED` 搭配顯式傳入之 `JobDeliveryState.DEAD_LETTER` 仍須寫入並保留，不得被新的清除邏輯波及。
3. `shared/jobs/queue.py:362-364` 之 in-memory 實作同步套用相同語意。

**驗收（必須寫成測試，屬 §4 測試 3 之強制子案例）**：一個先前已處於 `delivery_state=RETRYING` 的 job，於完成並寫入 `JobStatus.PARTIAL` 後，必須：

- 自 durable 持久層直接讀回 `delivery_state` 為 `null`（不得只斷言記憶體物件）；
- 自 API 查詢端點（§`GET /platform/jobs/{job_id}`）讀回之 `delivery_state` 亦為 `null`；
- 同一測試須先實際使該 job 進入 `RETRYING`，否則無法區分「已清除」與「本來就沒有值」。

**注意**：此處僅識別 WP-33B 必須進行的既有佇列語意變更；本 A 階段任務未修改 `shared/` 或 `apps/` 之任何檔案。

---

### 3.1 Worker Registry 與 Batch Handler 實作
- 於 `apps/worker/oday_worker/handlers.py` 註冊經 H06 核定之批次任務處理器（如 `handle_batch_listing_intake`）。
- 處理器接收包含多個 work items 之 Payload，逐一執行處理，並在彙整統計後依規則轉移狀態：
  - $\text{succeeded\_count} > 0 \land \text{failed\_count} > 0 \implies \text{JobStatus.PARTIAL}$。
  - 終態完成時，外層框架呼叫 `job_queue.update_status(job_id, JobStatus.PARTIAL, delivery_state=None, payload=receipt_envelope)`。**注意：在現行寫入器語意下這個呼叫並不會清除 `delivery_state`**（見 §3.0），必須先完成 §3.0 之前置修改，該呼叫才會產生「PARTIAL 且 `delivery_state = null`」之持久狀態。

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
- **Replay 識別與亂序防護（必要，對應 §4 測試 5）**：重試引擎必須以 `(job_id, item_id, attempt)` 三元組作為套用結果之 fencing token，並實作 [partial-retry-contract-draft.json](./partial-retry-contract-draft.json) 之 `core_principles.replay_identity_and_ordering`：
  1. 同一三元組之結果至多套用一次（重複訊息不產生下游寫入、不遞增 `attempt`、不改動 `JobSummary` 計數、不改寫 `last_attempt_at`）。
  2. `attempt` 低於 `ItemReceipt` 現值、或該 item 已為 `SUCCEEDED` 之結果，一律判為 stale 並丟棄；**嚴禁**以舊 attempt 之失敗覆寫 `SUCCEEDED`、清除 `result_ref` 或回填 `error`。
  3. `attempt` 於單一 job run 內非遞減；item 層之 `SUCCEEDED` 為終態；已設定之 `result_ref` 不可變。
  4. `JobSummary` 與聚合 `JobStatus` 必須由持久化 `items` 依優先級規則**重新推導**，不得由訊息處理逐次累加，以確保重啟後與不同到達順序下之最終聚合一致。
  5. 上述為 `FAILED_ONLY` 單次循序重試在結構上無法偵測之情境，必須另立測試（§4 測試 5），不得以測試 2 涵蓋。

### 3.4 嚴格不另建 Job Manager（No Second Job Manager）
- 必須復用既有 `shared/jobs/queue.py`、`shared/infrastructure/persistence/job_queue.py` 與 `apps/worker/oday_worker/main.py` 之架構。
- 嚴禁為 PARTIAL 額外開發獨立的 Scheduler 或第二套 Job Manager。

---

## 4. WP-33B 反事實驗收標準（Counterfactual Acceptance Criteria）

WP-33B 之 PR 必須包含並通過以下五項反事實驗收測試套件：

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
- **強制子案例：RETRYING → PARTIAL 之持久清除回讀（對應 §3.0 前置修改）**：
  1. 先令該 job 實際進入 `delivery_state = RETRYING`（透過可重試異常走 `apps/worker/oday_worker/main.py:210-222` 路徑），並斷言此時持久層讀回確為 `RETRYING`（否則無法區分「已清除」與「本來就沒有值」）。
  2. 隨後令該 job 完成並寫入 `JobStatus.PARTIAL`。
  3. 自 durable 持久層直接讀回，斷言 `status == PARTIAL` 且 `delivery_state IS NULL`；不得僅斷言記憶體物件。
  4. 自 `GET /platform/jobs/{job_id}` 讀回，斷言 `delivery_state` 為 `null`。
  5. 同一套件須以 in-memory 與 durable 兩種 queue 實作各執行一次，驗證 `shared/jobs/queue.py:362-364` 與 `shared/infrastructure/persistence/job_queue.py:402-406` 之 parity。
  6. 反向斷言：`JobStatus.FAILED` + 顯式 `JobDeliveryState.DEAD_LETTER` 之既有行為不得被本變更破壞，仍須讀回 `DEAD_LETTER`。

### 測試 4：重啟回讀與取消／未執行項目驗證（Restart Re-readability & Cancellation Assertion）
- **測試情境**：
  1. 模擬 Worker 程序 Crash 重啟，自持久層重新讀取 Job 收據，驗證能夠完整還原原始明細清單與各項目的 `attempt`、`result_ref` 與 `error`。
  2. 模擬批次任務執行至第 1 筆成功後被 Operator 中途取消：第 1 筆為 `SUCCEEDED`（`attempt=1`），第 2 筆為 `CANCELLED`（`attempt=1`，`code="CANCELLED_MID_EXECUTION"`），第 3 筆未執行為 `CANCELLED`（`attempt=0`，`code="CANCELLED_BEFORE_EXECUTION"`）；整體 JobStatus 依優先級轉移為 `JobStatus.CANCELLED`。

### 測試 5：重複投遞與亂序結果冪等驗證（Duplicate Delivery & Out-of-Order Result Idempotency）

本測試對應 [partial-retry-contract-draft.json](./partial-retry-contract-draft.json) 之 `core_principles.replay_identity_and_ordering`。測試 2 之 `FAILED_ONLY` 重試為單次循序操作，**結構上無法**偵測重複投遞或舊 attempt 結果後到，故必須另立本測試。

- **replay 識別**：item 執行以三元組 `(job_id, item_id, attempt)` 唯一識別；job 層以 `idempotency_key` 去重。

- **子案例 5.1 — 重複投遞同一 attempt（Duplicate Delivery）**：
  - 情境：對已處於 `PARTIAL` 之任務，將同一則 item 完成訊息 `(job_id, item-X, attempt=2)` 投遞 **2 次**。
  - 斷言：底層業務寫入函式對 `item-X` 之調用次數為 **1**（非 2）。
  - 斷言：`item-X` 之 `attempt` 維持 `2`，未因第二則訊息遞增為 `3`。
  - 斷言：`JobSummary` 之 `succeeded_count` / `failed_count` / `cancelled_count` / `pending_count` 與單次投遞時完全相同，無重複計數。
  - 斷言：`last_attempt_at` 未被第二則訊息改寫。

- **子案例 5.2 — 重複 enqueue 同一 idempotency_key（Job-Level Replay）**：
  - 情境：以相同 `idempotency_key` 再次 enqueue 已存在之批次任務。
  - 斷言：`DurableJobQueue.enqueue`（`shared/infrastructure/persistence/job_queue.py:65-110`）回傳 `created == False` 且回傳既有 job_id，未建立第二筆 job。
  - 斷言：既有 `items` 明細與 `JobSummary` 未被重置。

- **子案例 5.3 — 舊 attempt 結果後到（Out-of-Order Stale Result）**：
  - 情境：`item-Y` 之 `attempt=2` 已成功並寫入 `item_status="SUCCEEDED"`、`result_ref="intake-XXXX"`；其後才收到 `attempt=1` 之**失敗**結果訊息。
  - 斷言：`item-Y` 之 `item_status` 維持 `SUCCEEDED`，**不得**回滾為 `FAILED`。
  - 斷言：`result_ref` 維持原值且未被清為 `null`；`error` 維持 `null`，未被舊失敗填回。
  - 斷言：`attempt` 維持 `2`，未回退為 `1`。
  - 斷言：底層業務寫入函式因該則舊訊息之調用次數為 **0**。
  - 斷言：該舊訊息被明確記錄為 stale-delivery 觀測並丟棄，不影響任何計數。

- **子案例 5.4 — 對已取消未執行項目之後到結果**：
  - 情境：`item-Z` 已記錄 `item_status="CANCELLED"`、`attempt=0`（執行前取消），其後收到該 item 之完成結果訊息。
  - 斷言：`item-Z` 維持 `CANCELLED`，不得被復活為 `SUCCEEDED` 或 `FAILED`；`cancelled_count` 不變。

- **子案例 5.5 — 重啟後最終聚合一致（Post-Restart Aggregate Consistency）**：
  - 情境：在 5.1 與 5.3 之訊息全部投遞完成後，模擬 worker 程序 crash 重啟並自持久層重建聚合。
  - 斷言：重啟後之 `JobStatus` 與 `JobSummary` 各項計數，與重啟前完全一致。
  - 斷言：聚合值等同於直接依 `state_transition_rules.batch_multi_item_precedence_rules` 由持久化 `items` 清單重新推導之結果（聚合為 item receipts 之純函數，非由訊息處理逐次累加）。
  - 斷言：將 5.1／5.3 之訊息以不同到達順序重放，最終聚合結果不變。

---

## 5. 治理登錄與結案指引

當 WP-33B 完成上述程式實作與測試後：
1. 執行專屬測試套件確認全綠。
2. 於 `delivery_toolchain/governance/set_valued_requirements.json` 中將 `ODP-FR-SHARED-001` 之 `PARTIAL` member 由 `BLOCKED_BY_EVIDENCE` 更新為 `VERIFIED`（或由 WP-90 統一整合）。
3. 建立 PR 並附上 exact HEAD 之測試執行收據。
