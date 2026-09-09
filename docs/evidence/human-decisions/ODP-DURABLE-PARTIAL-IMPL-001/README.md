# 工程實作與驗收報告：接入指定批次業務的 PARTIAL 明細持久化與失敗項重試

- **任務識別碼**：`ODP-DURABLE-PARTIAL-IMPL-001`
- **對應工作包**：WP-33B（`ODP-FR-SHARED-001` durable PARTIAL 與逐項重試實作）
- **前置任務**：
  - `ODP-DURABLE-PARTIAL-CONTRACT-PREP-001`（WP-33A：工程準備、盤點與契約草案）
  - `ODP-JOB-DELIVERY-STATE-CLEAR-001`（修正 PARTIAL／CANCELLED 終態殘留重試狀態並保持兩種佇列一致）
- **任務負責人**：Claude（前 owner Antigravity6 因 provider quota 終止被改派，非因完成）
- **審查人**：Codex
- **依據規範**：
  - `docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md`
  - `docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/implementation-handoff.md`
  - `docs/evidence/human-decisions/ODP-DURABLE-PARTIAL-CONTRACT-PREP-001/partial-retry-contract-draft.json`

---

## 1. 交付內容

`batch-listing-intake` 是 WP-33A 推薦、Codex 於本輪採為工程預設的批次業務。本任務把它做成可執行的實體批次任務：逐項收據持久化、逐項 checkpoint、`FAILED_ONLY` 差異化重試，以及重複投遞／亂序結果的 fencing。

### 1.1 明細收據與聚合推導（`shared/jobs/receipts.py`）

- `ItemStatus`、`ItemError`、`ItemReceipt`、`JobSummary`、`DurableJobReceipt`。
- `derive_batch_status_and_summary`：純函式，由持久化 items 依優先級規則重新推導聚合狀態與計數，不由訊息逐次累加。
- `apply_item_result`：以 `(job_id, item_id, attempt)` 三元組 fencing 套用結果。本輪修正其 attempt 判定：結果只能「完成目前記錄為執行中（`PENDING`）的該次 attempt」或「回報更後面的 attempt」；已有結果的 attempt 再收到同號結果一律丟棄。原本的「attempt 必須嚴格遞增」在導入開始 checkpoint 後會把該次 attempt 自己的結果誤判為 stale。

### 1.2 真實業務路徑（`modules/opsboard/application/network_listings.py`）

新增 `NetworkListingService.record_batch_assisted_entry`，把一列批次匯入資料落成既有的協助輸入（assisted entry）intake 記錄：

- **記錄鍵由呼叫端以租戶＋任務＋成員推導，不採用提交端提供的 `intake_id`**。這同時解決兩件事：不同租戶送出同一個 `intake_id` 會落在不同記錄，彼此不可覆寫；崩潰後重放會定址到上一次未完成 attempt 寫過的那一筆，而不是新增第二筆。
- 寫入前先讀既有記錄，若 `tenantId` 不符則拒絕（`NetworkListingPolicyError`），作為 id 推導被繞過時的第二道防線。
- 階段（stage）由該列資料自己決定，規則與既有 `correct_intake` 相同（抽出共用的 `_missing_assisted_entry_fields`）：缺任一必填協助輸入欄位就停在 `AWAITING_ASSISTED_ENTRY`，齊備才跑既有 `match_listing` 並依比對結果落在 `NEEDS_REVIEW` 或 `READY`。**不以 `0` 補缺漏的租金／坪數，也不硬寫 `READY`**。
- 批次列沒有來源 URL 與快照，`originalUrl`／`canonicalUrl`／`rawSnapshot`／`snapshotId` 均為 `None`，不填造出來的網址。

### 1.3 批次處理器（`apps/worker/oday_worker/handlers.py`）

- `batch_listing_intake_id(tenant_id, job_id, item_id)`：穩定、租戶綁定的 intake id。
- `build_batch_listing_intake_service`：每個 job 建一次既有 `NetworkListingService`，listing 語料優先取該租戶的 scoped repository（與 operator API 同一取法），避免跨租戶比對。
- `_default_batch_listing_item_executor`：走上述真實業務路徑，回傳可回讀的 `result_ref`；production 程式碼中沒有任何 `simulate_*` 分支，測試替身只在測試注入。
- `handle_batch_listing_intake`：每一項有三次持久寫入——**開始 attempt 的 checkpoint、業務寫入、結果 checkpoint**。開始 checkpoint 讓崩潰後可分辨「沒跑過」與「跑到一半」，也讓取消時能分辨 `attempt=0` 與 `attempt>=1`。
- checkpoint 失敗不再被 `except Exception: pass` 吞掉。fence token 位移或 job 離開 `RUNNING` 直接拋出；只有本 job 自己的 lease heartbeat 會合法地推進 row version，因此僅對「純 version 不符」重讀重試（上限 3 次）。
- 取消一律以佇列中的真實 job 狀態判定，payload 內的 `cancelled_before_execution` / `cancelled_mid_execution` 旗標分支已移除。取消結算會保留已落地的結果，未執行成員記為 `attempt=0`，已開始但無結果的成員記為 `attempt>=1`。

### 1.4 平台 API（`apps/api/oday_api/main.py`）

- `POST /jobs`（batch enqueue）：以 `principal_from_headers` + RBAC 驗證身分與 `listing` 權限，租戶取自可信 principal；payload 帶不同租戶則 403；idempotency key 以租戶作用域。
- `GET /jobs/{job_id}`：批次任務要求已認證 principal 且租戶相符，否則 401／404。**durable 收據隨 job payload 一併回傳，這就是收據讀取端點**。
- `POST /jobs/{job_id}/retries`：`retry_scope="FAILED_ONLY"`，限定支援的工作類型與終態（`RUNNING`/`QUEUED` → 409；`SUCCEEDED` → 400；可重試項為 0 → 400），並以 `expected_version` 做 CAS 更新。

#### 與 handoff 端點命名的差異（明列，非默默改動）

handoff §3.0／§3.3 以 `GET /platform/jobs/{job_id}` 與 `POST /platform/jobs/{job_id}/retry` 稱呼這兩個端點。本 repo 的實際平台介面不是這兩條路徑：

1. `platform_router` 經 `mount_versioned` 掛在 `/api/v1/...` 與不帶版本的相容別名，平台 job 的既有路徑是 `/api/v1/jobs/{job_id}`。前一版新增的 `/platform/jobs/...` 是額外的第三條路徑，且以 `include_in_schema=False` 掛上，會讓 `test_alias_and_versioned_surfaces_are_exactly_paired` 的別名／版本配對失衡。本輪已全數移除。
2. `/jobs/{job_id}/retry` 與 `/jobs/{job_id}/receipt` 已屬 `apps/api/app/routes/listings.py` 的 assisted-listing-intake router（operation `retryJob`／`getJobReceipt`，走 checkpoint replay 與 `Idempotency-Key`/`If-Match` 契約）。在 `platform_router` 上再掛同名路徑會 shadow 既有端點而非復用它，因此批次重試改用 `POST /api/v1/jobs/{job_id}/retries`（在該 job 底下建立一次重試），收據讀取則復用既有的 `GET /api/v1/jobs/{job_id}`，不另開端點。

`packages/openapi-client/openapi.json` 與產生的 client 已依此重新輸出。

---

## 2. 對 Codex 前一輪 review 的處置

| Codex 指出的缺陷 | 本輪處置 | 對應測試 |
|---|---|---|
| default executor 信任呼叫端 `intake_id`，在共用 repository upsert，不同租戶送同一 id 只會剩後寫的一筆 | 記錄鍵改由 `(tenant_id, job_id, item_id)` 推導，提交端的 `intake_id` 不再參與定址；寫入前另做 `tenantId` 擁有權檢查 | `test_7_same_submitted_intake_id_cannot_cross_tenants` |
| 業務寫入成功但 receipt checkpoint 前崩潰，重放後同一成員產生兩個隨機 intake id，job 仍 succeeded | 同上的穩定 id 使重放定址到同一筆；另加開始 attempt 的 checkpoint，讓重放看得到「這次 attempt 已開始」 | `test_7_crash_between_business_write_and_receipt_leaves_one_record` |
| checkpoint `except Exception: pass` 吞例外繼續 | 移除。fence 位移／離開 RUNNING 直接拋；僅對本 job heartbeat 造成的 version 競爭重讀重試 | `test_4_live_operator_cancellation_during_execution`、`test_4_restart_re_readability_and_cancellation` |
| 取消只讀 payload 的 `cancelled_*` 旗標，沒查真實取消狀態 | 旗標分支移除，取消一律讀佇列 job 狀態 | `test_4_restart_re_readability_and_cancellation`、`test_4_live_operator_cancellation_during_execution` |
| 隨機 ID／硬寫 `READY`／缺資料填 0 偽裝業務完成 | 走既有協助輸入業務規則決定 stage，缺必填欄位停在 `AWAITING_ASSISTED_ENTRY`，不填 0、不造 URL | `test_1_state_transition_and_itemized_receipt`、`test_7_*` |
| `tests/architecture/test_external_data_boundary.py` 六項失敗：fixture 內出現未申報的 provider host | 測試 fixture 的 `details` 不再寫入 provider host（該欄位對斷言無作用） | `tests/architecture/test_external_data_boundary.py` |
| `test_alias_and_versioned_surfaces_are_exactly_paired` 失敗：新增 `/platform/jobs` 別名未配對 | 三條 `/platform/jobs/...` 路徑全部移除，見 §1.4 | `tests/contract/test_api_versioning.py` |

---

## 3. 反事實驗收測試（`tests/reliability/test_durable_partial_batch.py`）

| 測試 | 對應 handoff | 內容 |
|---|---|---|
| `test_1_state_transition_and_itemized_receipt` | §4 測試 1 | 10 筆（8 成功／1 永久缺地址／1 暫態超時）→ `JobStatus.PARTIAL`、`delivery_state` 為 `None`、摘要計數精確；8 筆房源以 default registry 真實寫入持久層，重開 bundle 仍可回讀 |
| `test_2_scoped_retry_and_zero_duplication` | §4 測試 2 | `FAILED_ONLY` 重試：8 筆已成功與 1 筆永久失敗底層調用 0 次，1 筆可重試項精確 1 次、`attempt` 遞增為 2 |
| `test_2_full_convergence_subtest` | §4 測試 2 子測試 | 2 筆暫態失敗重試後整體收斂為 `SUCCEEDED` |
| `test_3_orthogonality_and_delivery_state_clear` | §4 測試 3 | 先實際進入 `RETRYING` 再寫 `PARTIAL`，自 SQLite 與 API 皆讀回 `delivery_state` 為 `null`；`FAILED` + `DEAD_LETTER` 行為保留；in-memory 與 durable 各跑一次 |
| `test_4_restart_re_readability_and_cancellation` | §4 測試 4 | 重啟後完整回讀收據；取消情境以真實崩潰＋真實 DB 取消驅動：第 1 筆 `SUCCEEDED`(attempt=1)、第 2 筆 `CANCELLED`(attempt=1, `CANCELLED_MID_EXECUTION`)、第 3 筆 `CANCELLED`(attempt=0, `CANCELLED_BEFORE_EXECUTION`) |
| `test_4_mid_batch_interruption_and_resumption` | §4 測試 4 | 中途崩潰後重啟續跑，已完成項不重跑 |
| `test_4_live_operator_cancellation_during_execution` | §4 測試 4 | 執行中由 operator 在 DB 取消，已落地結果保留、未執行項 `attempt=0` |
| `test_5_duplicate_delivery_and_out_of_order` | §4 測試 5 | 重複投遞同一 attempt、重複 enqueue 同一 idempotency key、舊 attempt 失敗後到不得覆寫 `SUCCEEDED`、對 `attempt=0` 取消項的後到結果不復活、重排順序後聚合不變 |
| `test_6_auth_and_tenant_isolation_guards` | §3.3／隔離要求 | 401（未認證）、403（角色與租戶不符）、404（跨租戶）、409（QUEUED/RUNNING 重試）、400（SUCCEEDED 與 0 可重試項） |
| `test_7_same_submitted_intake_id_cannot_cross_tenants` | 本輪 review 反例 | 兩租戶各送同一 `intake_id`：留下兩筆記錄、各自租戶、各自資料，且皆非提交端給的那個 id |
| `test_7_crash_between_business_write_and_receipt_leaves_one_record` | 本輪 review 反例 | 業務寫入成功後崩潰、重放後只有一筆業務記錄，且等於收據的 `result_ref` |

後兩項不使用任何 executor 替身或只跑一次；`test_7_same_submitted_intake_id_cannot_cross_tenants` 完全走 default registry，讀回的是業務實體而非收據。

---

## 4. 驗證

任務宣告的 verification 命令：

- `git diff --check`
- `uv run pytest tests/reliability/test_durable_partial_batch.py -q`
- `uv run pytest tests/contract/test_platform_api.py -q`
- `uv run pytest tests/architecture/test_external_data_boundary.py tests/contract/test_api_versioning.py tests/contract/test_assisted_listing_operations.py tests/contract/test_assisted_listing_promotion_api.py tests/contract/test_openapi_artifact_and_client.py -q`

具約束力的收據由 `delivery_toolchain/git/task_verification.py` 在交付 head 上產生，內含 head SHA、原始命令、真實 exit code 與耗時，存於 `.orchestrator/evidence/`（不在本 repo 追蹤範圍）。本文件不重述那些數字，以免文件宣稱的結果早於它自己所在的 commit。

額外於同一工作樹執行、非任務宣告命令：`uv run ruff check`（涵蓋本次變更的五個檔案）exit 0。

---

## 5. 範圍與非目標

1. **資料與環境**：所有驗證在隔離 worktree、合成 fixture 與暫時性 SQLite 上執行。批次列資料為合成資料。
2. **非目標**：不宣稱生產環境已啟用或已上線；未讀取任何秘密、未連線 production、未修改 IAM／release lease／provider 設定、未降低任何 required check。
3. **未由本任務取證**：真實來源資料匯入、live 佇列政策套用、production 驗收，仍屬後續獨立階段。
4. H06 未被本任務視為已由使用者逐項回覆；`batch-listing-intake` 是 Codex 採用的可調整工程預設，不記為 Human 簽署。
