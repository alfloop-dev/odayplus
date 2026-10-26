# ODP-JOB-PARTIAL-PRODUCER-RECONCILIATION-001 — Durable PARTIAL 生產者治理對齊與 Live 缺口保留報告

- **任務識別碼**：`ODP-JOB-PARTIAL-PRODUCER-RECONCILIATION-001`
- **文件路徑**：`docs/evidence/human-decisions/ODP-JOB-PARTIAL-PRODUCER-RECONCILIATION-001/README.md`
- **日期**：2026-09-11
- **觀測與查證時間（UTC Observation Timestamp）**：`2026-09-11T00:47:47Z`
- **查證基準代碼（Inspected Source SHA）**：`b19513a1419497dce2ddd5054df5bbe0bd732a69`（PR #1285 已合併至 `origin/dev`）
- **任務負責人**：Antigravity
- **審查人**：Codex2
- **決策依據**：D19（使用者於 2026-09-08 授權確認實作 Durable PARTIAL、明細收據與逐項重試）
- **關聯需求**：`ODP-FR-SHARED-001`（所有長時間任務都能查詢 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/PARTIAL）
- **前置/依賴任務**：`ODP-DURABLE-PARTIAL-IMPL-001`（PR #1285 @ `b19513a1419497dce2ddd5054df5bbe0bd732a69`，已合併至 `origin/dev`）
- **移交單參照**：`HB-SHARED001-PARTIAL-001`（`docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md`）
- **處置狀態 (Disposition State)**：`BLOCKED_BY_EVIDENCE`

---

## 1. 執行摘要與對齊目的

在 `ODP-DURABLE-PARTIAL-IMPL-001`（PR #1285 @ `b19513a1419497dce2ddd5054df5bbe0bd732a69`）合併交付後，程式庫內已具備真實可執行的批次 Worker (`handle_batch_listing_intake`)、預設 Worker 註冊 (`build_default_registry`)、處理器/佇列三階段 checkpoint 持久化寫入 (`apps/worker/oday_worker/handlers.py::handle_batch_listing_intake` 及 `apps/worker/oday_worker/handlers.py::checkpoint_batch_item_result`)、收據狀態聚合推導純函式 (`shared/jobs/receipts.py::derive_batch_status_and_summary`) 以及差異化重試 (`FAILED_ONLY`)。

然而，現行集合型需求治理清單 (`delivery_toolchain/governance/set_valued_requirements.json`) 與治理測試中仍殘留「專案代碼庫中無任何任務生產 PARTIAL」、「Stage 33B 尚未交付」等過時敘述。

本任務旨在**原子對齊治理清單與治理測試**，精確反映程式碼現況，同時堅定遵守治理防線，明確劃分三個層次的真實邊界：

```
+----------------------------------------------------------------------------------------------------+
|                                    三層邊界嚴格劃分與對齊架構                                      |
+----------------------------------------------------------------------------------------------------+
| 層次 1：Repo 內 durable 生產者 (In-Tree Producer)                                                  |
| - 實作已交付：apps/worker/oday_worker/handlers.py::handle_batch_listing_intake                     |
| - 預設註冊：apps/worker/oday_worker/handlers.py::build_default_registry ("batch-listing-intake")     |
| - 持久寫入：handle_batch_listing_intake 逐項三階段 checkpoint 及 checkpoint_batch_item_result 寫入    |
| - 收據推導：shared/jobs/receipts.py::derive_batch_status_and_summary (純函式推導 JobStatus.PARTIAL)|
| - 差異重試：POST /api/v1/jobs/{job_id}/retries (retry_scope="FAILED_ONLY")                        |
| - 治理狀態：member.status 由 absent 對齊為 satisfied（符號真實存在且可解析）                       |
+----------------------------------------------------------------------------------------------------+
| 層次 2：Repo 內反事實驗收測試 (In-Tree Counterfactual Acceptance)                                  |
| - 測試套件：tests/reliability/test_durable_partial_batch.py (Tests 1-7 及 Subcases 5.1-5.5)         |
| - 驗證範圍：合成資料、暫時性 SQLite、跨程序重啟、Fencing CAS、租戶與 RBAC 隔離                    |
| - 邊界本質：屬隔離環境與合成資料之離線工程測試，不等於 Live Production 驗收收據                     |
+----------------------------------------------------------------------------------------------------+
| 層次 3：未完成的 Live 驗證與 H06 人類簽署 (Live Production & Human Gap)                            |
| - 實體缺失：(1) Live production 隊列/排程/工作者審計收據清單尚未取得                               |
|             (2) Live production 佇列政策套用與正式驗收尚未執行                                     |
|             (3) H06 人類治理權威對真實 Durable Job 候選與業務範圍之具名確認尚未簽署                   |
|                 (參照 docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/         |
|                  human-input-request-H06.md 第 1/4 節；PR #1285 採納之 batch-listing-intake 為可   |
|                  調整工程預設，非 Human 簽署)                                                       |
| - 治理狀態：disposition.state 嚴格維持 BLOCKED_BY_EVIDENCE，絕不冒充 VERIFIED 或 DECIDED            |
| - 移交單維持：HB-SHARED001-PARTIAL-001（負責人：Platform Infrastructure Lead）                      |
+----------------------------------------------------------------------------------------------------+
```

---

## 2. 代碼庫實體生產者盤點與符號解析

經由已合併之 PR #1285（HEAD commit `b19513a1419497dce2ddd5054df5bbe0bd732a69`，於 `2026-09-11T00:47:47Z` 觀測查證），專案代碼庫中新增並落實以下生產者符號：

| 符號路徑 (Symbol Path) | 型態 | 實作責任 | 治理可解析性 |
|---|---|---|---|
| `apps/worker/oday_worker/handlers.py::handle_batch_listing_intake` | Function | 批次房源匯入處理器：執行三階段 checkpoint 持久化寫入（嘗試開始 checkpoint、業務寫入、結果 checkpoint），可產生 `JobStatus.PARTIAL` | `resolve()` 通過 (Exit 0) |
| `apps/worker/oday_worker/handlers.py::build_default_registry` | Function | 預設 Worker 註冊器：註冊 `batch-listing-intake` 至 `JobRegistry` | `resolve()` 通過 (Exit 0) |
| `apps/worker/oday_worker/handlers.py::BATCH_LISTING_INTAKE_JOB_TYPE` | Constant | 註冊常數 `"batch-listing-intake"` | `resolve()` 通過 (Exit 0) |
| `shared/jobs/receipts.py::derive_batch_status_and_summary` | Function | 純函式：自持久化 `ItemReceipt` 集合計算聚合狀態與摘要，於混合結果時確定推導為 `JobStatus.PARTIAL`（持久化由 handler/queue checkpoint 執行） | `resolve()` 通過 (Exit 0) |
| `apps/worker/oday_worker/handlers.py::checkpoint_batch_item_result` | Function | 逐項結果 checkpoint 持久化寫入函式：綁定 job/tenant/lease 與 attempt 執行 CAS 合併寫入 | `resolve()` 通過 (Exit 0) |
| `shared/jobs/receipts.py::DurableJobReceipt` | Class | 結構化持久收據模型（含 `job_id`, `status`, `delivery_state`, `summary`, `items`） | `resolve()` 通過 (Exit 0) |
| `apps/api/oday_api/main.py::create_app` | Function | 平台 API：提供 `POST /api/v1/jobs/{job_id}/retries`（`retry_scope="FAILED_ONLY"`）與收據查詢 | `resolve()` 通過 (Exit 0) |

上述符號均已由 `check_requirement_members.py` 與 `tests/governance/test_job_partial_disposition.py` 驗證真實存在且可解析，而非僅以列舉型別（Enum）宣告充數。

---

## 3. 治理清單更新規範（`set_valued_requirements.json`）

### 3.1 變更內容
1. **成員狀態 (`member.status`)**：
   - 由 `"absent"` 更新為 `"satisfied"`。
   - `evidence` 配置為 `"apps/worker/oday_worker/handlers.py::handle_batch_listing_intake"`。
2. **過時敘述清除與現況補充 (`member.note`)**：
   - 清除「no job in the tree currently reports it」與「Stage 33B pending H06」等陳舊字眼。
   - 具名記錄 PR #1285（`b19513a1419497dce2ddd5054df5bbe0bd732a69`）交付之實體 Worker、三階段 checkpoint 持久化寫入及收據狀態推導。
   - 明確分開並保留未完成之項目：(1) Live production 隊列/排程/工作者審計收據，(2) Live 佇列政策套用與驗收，(3) H06 人類候選與業務範圍確認（`docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/human-input-request-H06.md` 第 1/4 節），以及 `HB-SHARED001-PARTIAL-001` 移交記錄。
3. **處置狀態與法定欄位 (`member.disposition`)**：
   - 狀態嚴格維持 `"BLOCKED_BY_EVIDENCE"`。
   - `history` 新增 `2026-09-11` 觀測記錄（附帶 UTC 時間戳 `2026-09-11T00:47:47Z` 及來源 SHA `b19513a1419497dce2ddd5054df5bbe0bd732a69`），保留既有 2026-09-03 與 2026-09-08 歷史條目。
   - `evidence_needed` 與 `reopen_trigger` 正式將 H06 人類業務範圍確認（參照 `human-input-request-H06.md`）與 Live production 隊列清單/政策分立並列。
   - 保留法定欄位：`evidence_owner`（`Platform Infrastructure Lead`）、`next_review_date`（`2026-10-01`）、`formal_handback_ref`、`handback_id`（`HB-SHARED001-PARTIAL-001`）、`reopen_trigger` 與 `rationale`。
   - 更新 `evidence_ref` 指向本對齊報告。

### 3.2 範圍保護
- 僅修改 `ODP-FR-SHARED-001` 中之 `PARTIAL` 成員。
- 其餘 8 個集合型需求及 `ODP-FR-SHARED-001` 前 5 個成員逐 byte 維持不變，避免全檔格式重排。

---

## 4. 治理測試更新規範（`test_job_partial_disposition.py`）

新增與更新以下治理斷言：
1. **全成員 satisfied 與符號解析**：`ODP-FR-SHARED-001` 6 個成員皆為 `satisfied`，且 `evidence` 均通過 `resolve()` 解析。
2. **預設 Worker 註冊與 PARTIAL 推導驗證**：驗證 `build_default_registry()` 包含 `batch-listing-intake`，且 `derive_batch_status_and_summary` 能在混合結果下計算出 `JobStatus.PARTIAL`。
3. **防偽與防早釋防線 (Anti-Counterfeiting & Anti-Early-Release)**：
   - 拒絕假符號：驗證不存在之符號（如 `NonExistentBatchHandler`）無法通過解析。
   - 拒絕 AI 自簽豁免：驗證 `is_ai_decider` 能精確攔截 AI 代理人角色。
   - 拒絕未經 Live 驗證提前放行：斷言 `disposition.state` 不得為 `VERIFIED`、`DECIDED` 或 `IMPLEMENTATION_READY`。
4. **H06 範圍確認與 UTC 觀測時間完整性**：驗證 manifest 與 README 具名參照 `human-input-request-H06.md`，且觀測時間包含 UTC 時間戳與 source SHA。

---

## 5. 移交單維持與後續路徑（Handback Package Active Status）

| 移交單欄位 | 當前狀態與約定值 |
|---|---|
| **移交單 ID** | `HB-SHARED001-PARTIAL-001` |
| **移交文件** | `docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md` |
| **處置狀態** | `BLOCKED_BY_EVIDENCE` |
| **法定風險負責人** | `Platform Infrastructure Lead` |
| **下次檢視日期** | `2026-10-01` |
| **重啟觸發條件 (Reopen Trigger)** | (1) Live production queue/worker audit receipts demonstrate deployed jobs reporting PARTIAL in production runtime, OR (2) Product Lead, Architecture Board, or Human/Ops formally execute H06 durable job candidate and business scope confirmation per docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/human-input-request-H06.md along with live production queue policy sign-off and requirement closure. |

---

## 6. 驗證與可重現收據

於交付分支工作樹中執行以下驗證全數通過：

1. `git diff --check`（無 trailing whitespace 或格式錯誤，exit 0）
2. `uv run pytest -q tests/governance/test_job_partial_disposition.py`（全數通過，exit 0）
3. `uv run python delivery_toolchain/governance/check_requirement_members.py`（9 個需求、47 個成員校驗通過，exit 0）
