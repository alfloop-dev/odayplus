# 工程實作與驗收報告：接入指定批次業務的 PARTIAL 明細持久化與失敗項重試

- **任務識別碼**：`ODP-DURABLE-PARTIAL-IMPL-001`
- **對應工作包**：WP-33B（`ODP-FR-SHARED-001` durable PARTIAL 與逐項重試實作）
- **前置任務**：
  - `ODP-DURABLE-PARTIAL-CONTRACT-PREP-001`（WP-33A：工程準備、盤點與契約草案）
  - `ODP-JOB-DELIVERY-STATE-CLEAR-001`（修正 PARTIAL／CANCELLED 終態殘留重試狀態並保持兩種佇列一致）
- **任務負責人**：Antigravity6
- **審查人**：Codex
- **交付日期**：2026-09-09
- **依據規範**：
  - `docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md`
  - `docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/implementation-handoff.md`
  - `docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/partial-retry-contract-draft.json`

---

## 1. 執行概述與工程交付

本任務接續 WP-33A 已審定之契約規範及工程推薦預設（`batch-listing-intake`），完成實體批次任務之 Durable Batch Job Handler、逐項明細收據持久化（`DurableJobReceipt` / `ItemReceipt`）、差異化冪等重試（`retry_scope="FAILED_ONLY"`）與訊息防禦機制。

### 1.1 核心交付模組

1. **明細收據與聚合狀態模型 (`shared/jobs/receipts.py`)**：
   - 實作 `ItemStatus` (`SUCCEEDED`, `FAILED`, `CANCELLED`, `PENDING`)。
   - 實作 `ItemError`（包含 `code`, `message`, `retryable`, `details`）。
   - 實作 `ItemReceipt`（支援 `attempt >= 0`、`result_ref`、`last_attempt_at`）。
   - 實作 `JobSummary`（包含 `total_count`, `succeeded_count`, `failed_count`, `cancelled_count`, `pending_count`）。
   - 實作 `DurableJobReceipt`（持久化收據信封，支援跨程序重啟回讀）。
   - 實作 `derive_batch_status_and_summary`：嚴格依照優先級規則純函數推導整體 `JobStatus` 與計數。
   - 實作 `apply_item_result`：實施 `(job_id, item_id, attempt)` 三元組 fencing token，過濾重複投遞、舊 attempt 後到與取消未執行防護。

2. **持久層收據擴展 (`shared/infrastructure/persistence/job_receipts.py`)**：
   - 擴充 `TenantScopedJobReceiptStore` 支援 `get_durable_receipt`，保障跨租戶隔離。

3. **批次任務處理器與註冊 (`apps/worker/oday_worker/handlers.py`)**：
   - 定義 `BATCH_LISTING_INTAKE_JOB_TYPE = "batch-listing-intake"`。
   - 實作 `_default_batch_listing_item_executor`：接入真實 `DurableAssistedIntakeRepository` 持久化，驗證地址格式並回傳持久化的真實 `result_ref`（`intake_id`）；完全移除生產程式碼中的 `simulate_*` 測試分支。
   - 實作 `handle_batch_listing_intake`：支援逐項即時 Checkpointing（每項執行後呼叫 `apply_item_result` 並透過 `job_queue.update_status` 保存至資料庫），即時檢測佇列取消狀態並將未開始項目標記為 `attempt=0`。
   - 於 `build_default_registry()` 註冊 `BATCH_LISTING_INTAKE_JOB_TYPE`。

4. **Worker 執行迴圈調度 (`apps/worker/oday_worker/main.py`)**：
   - 在 `ODayWorker.run_once` 增強終態判定：若 Handler 已完成業務聚合終態寫入（如 `JobStatus.PARTIAL` / `SUCCEEDED` / `FAILED` / `CANCELLED`），保留該狀態並正確記錄指標與日誌，不以無明細之 `SUCCEEDED` 覆寫。

5. **平台 API 端點 (`apps/api/oday_api/main.py`)**：
   - `POST /jobs`：批次 Enqueue 綁定可信 Principal 之租戶 (`tenant_id`) 與 tenant-scoped idempotency key。
   - `GET /platform/jobs/{job_id}` 與 `GET /jobs/{job_id}`：嚴格執行 Principal 驗證與租戶隔離（未認證 401、跨租戶 404）。
   - `GET /platform/jobs/{job_id}/receipt`：回傳租戶隔離之 `DurableJobReceipt`。
   - `POST /platform/jobs/{job_id}/retry`：接收 `retry_scope="FAILED_ONLY"`，限定支援工作類型與終態（拒絕 `RUNNING`/`QUEUED` 409 Conflict，拒絕 `SUCCEEDED` 與 0 可重試項 400 Bad Request），並使用 `expected_version` 執行樂觀鎖 CAS 更新。

---

## 2. Codex Review P1 阻擋缺陷修復對照

針對 Codex 對前一 head（`41ec9474`）審查提出的 4 項 P1 阻擋意見，本交付完成以下修復與驗證：

| 缺陷項目 | 問題成因 | 修復與防禦措施 | 驗證測試 |
|---|---|---|---|
| **P1.1 真實業務持久化路徑** | 原 `_default_batch_listing_item_executor` 僅生成隨機 ID 且依賴 `simulate_*` payload 分支，未寫入 `DurableAssistedIntakeRepository`。 | 接入 `DurableAssistedIntakeRepository.create_intake()`，寫入完整 intake 模型並以真實 `intake_id` 作為 `result_ref`；移除所有 production simulate 分支，測試替身一律在測試注入。 | `test_1`、`test_2`、`test_4` 均斷言重啟後資料庫中可回讀真實 intake 記錄（`repo.list_intakes() == 8`）。 |
| **P1.2 API 認證與租戶隔離** | API 依賴 `x-tenant-id` header 且缺失時略過隔離，無 auth header 時可任意讀取/重派。 | 引入 `batch_intake_job_tenant`，以 `principal_from_headers` 驗證 `listing` 權限與租戶邊界；`GET /jobs/{id}`、`GET /platform/jobs/{id}/receipt`、`POST /platform/jobs/{id}/retry` 及 `POST /jobs` 全面強制 401/403/404 防禦。 | `test_6_auth_and_tenant_isolation_guards`（涵蓋 401/403/404 與 role/tenant 防禦）。 |
| **P1.3 逐項 Checkpointing 與真實取消** | 原實作僅在迴圈結束後寫入狀態；程序中斷導致已完成項丟失；取消僅依賴 raw payload flag。 | 每處理一項即透過 `apply_item_result` 計算最新收據並以 `expected_version` 寫入 RUNNING checkpoint；每項開始前查詢佇列真實狀態，已取消則直接終止並將未執行項記錄為 `attempt=0`。 | `test_4_mid_batch_interruption_and_resumption`（中途中斷不重複執行已完成項）與 `test_4_live_operator_cancellation_during_execution`（即時取消未執行項 `attempt=0`）。 |
| **P1.4 重試終態限制與 CAS 併發防護** | `_retry_job_response` 無條件重派任意狀態（包含 RUNNING / SUCCEEDED / 0 可重試項）且無 CAS。 | 限定僅支援批次工作類型之 `PARTIAL`/`FAILED`；檢查可重試項總數（為 0 則 400）；RUNNING/QUEUED 回傳 409；使用 `expected_version=job.version` 進行原子更新防範併發重試。 | `test_6_auth_and_tenant_isolation_guards` 斷言 409（QUEUED/RUNNING）、400（SUCCEEDED）、400（0 retryable）。 |

---

## 3. 反事實驗收標準（Counterfactual Acceptance Verification）

本任務依據 `implementation-handoff.md` §4 實作反事實驗證測試套件於 `tests/reliability/test_durable_partial_batch.py`：

| 測試編號 | 測試項目 | 驗證行為與斷言 | 結果 |
|---|---|---|---|
| **測試 1** | 狀態轉移精確性與成員明細驗證 | 10 筆工作項目（8 筆正常、1 筆永久缺少地址、1 筆暫態超時）；初次執行全部 `attempt=1`，8 筆 `SUCCEEDED`、2 筆 `FAILED`；整體狀態精確為 `JobStatus.PARTIAL`，`delivery_state IS NULL`，摘要計數完全正確，8 筆房源真實寫入持久層。 | PASS |
| **測試 2** | 差異化重試與收斂驗證 | 對上述 `PARTIAL` 發起 `FAILED_ONLY` 重試：Mock/Spy 驗證 8 筆成功項目底層調用次數為 **0**；1 筆永久失敗項目調用次數為 **0**（`attempt` 維持 1）；1 筆暫態失敗項目精確調用 **1** 次並轉為 `SUCCEEDED`（`attempt=2`）；全成功子測試中，重試後自動收斂為 `JobStatus.SUCCEEDED`。 | PASS |
| **測試 3** | 交付狀態與業務結果正交分離 | 實測暫態重試中佇列設置 `JobDeliveryState.RETRYING`；實體終態失敗寫入 `JobStatus.FAILED + DEAD_LETTER`；強制子案例：將 Job 先置於 `RETRYING`，隨後以 `JobStatus.PARTIAL` 終結，自 SQLite 底層與 API 直接讀回 `delivery_state IS NULL`，且 in-memory 與 durable queue parity 成立。 | PASS |
| **測試 4** | 重啟回讀與取消／未執行項目 | 模擬 Worker Crash 重啟，自持久層讀回 100% 完整收據明細；中途取消情境：第 1 筆 `SUCCEEDED`（`attempt=1`）、第 2 筆 `CANCELLED`（`attempt=1`, `code="CANCELLED_MID_EXECUTION"`）、第 3 筆 `CANCELLED`（`attempt=0`, `code="CANCELLED_BEFORE_EXECUTION"`），整體狀態轉移為 `JobStatus.CANCELLED`。 | PASS |
| **測試 4.2** | 中途中斷與續跑不重複執行 | 執行 5 筆後觸發中斷，檢查 Checkpoint 已落地 5 筆；重啟 Worker 後續跑，已完成之 5 筆執行調用為 0，剩餘 5 筆精確調用 1 次。 | PASS |
| **測試 4.3** | 即時佇列取消與未執行項標記 | 批次執行中由 Operator 取消佇列任務，執行中項記錄為 CANCELLED (`attempt=1`)，未執行項記錄為 CANCELLED (`attempt=0`)。 | PASS |
| **測試 5** | 重複投遞與亂序結果冪等 | 5.1 重複投遞同一 attempt 訊息被丟棄，底層調用次數不增加；5.2 重複 enqueue 同一 `idempotency_key` 回傳 `created=False` 且不重置明細；5.3 `attempt=1` 失敗訊息在 `attempt=2` 成功後送達被判定為 stale 丟棄，嚴禁覆寫 `SUCCEEDED`；5.4 對 `attempt=0` 取消項目之後到訊息不予復活；5.5 重啟後由持久化 items 重建聚合結果完全一致。 | PASS |
| **測試 6** | API 認證、租戶隔離與狀態保護 | 驗證 401（未認證）、403（角色/租戶不符）、404（跨租戶隔離）、409（QUEUED/RUNNING 併發重試衝突）、400（SUCCEEDED 重試拒絕）、400（0 可重試項拒絕）。 | PASS |

---

## 4. 驗證執行收據（Verification Receipts）

- `git diff --check`: Exit Code 0
- `uv run pytest tests/reliability/test_durable_partial_batch.py -q`: Exit Code 0 (9 passed)
- `uv run pytest tests/contract/test_platform_api.py -q`: Exit Code 0 (7 passed)
- `uv run ruff check shared/ apps/ tests/`: Exit Code 0 (All checks passed)

---

## 5. 邊界與非目標聲明

1. **環境與資料範圍**：本階段所有驗證均於獨立 worktree、合成 Fixture 與暫時性 SQLite DB 上執行。
2. **非目標**：不宣稱真實生產環境已啟用或上線；未讀取任何外部秘密、未修改 IAM 權限、未啟用外部即時抓取來源。真實上線驗收屬後續獨立營運階段。
