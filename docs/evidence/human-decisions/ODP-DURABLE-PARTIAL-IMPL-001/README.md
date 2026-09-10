# 工程實作與驗收報告：接入指定批次業務的 PARTIAL 明細持久化與失敗項重試

- **任務識別碼**：`ODP-DURABLE-PARTIAL-IMPL-001`
- **對應工作包**：WP-33B（`ODP-FR-SHARED-001` durable PARTIAL 與逐項重試實作）
- **前置任務**：
  - `ODP-DURABLE-PARTIAL-CONTRACT-PREP-001`（WP-33A：工程準備、盤點與契約草案）
  - `ODP-JOB-DELIVERY-STATE-CLEAR-001`（修正 PARTIAL／CANCELLED 終態殘留重試狀態並保持兩種佇列一致）
- **任務負責人**：Codex（2026-09-10 依使用者授權前景接手；保留先前實作及六次退回歷史）
- **審查人**：Claude（本輪與實作者不同帳號）
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
- `apply_item_result` 是 item 結果合併的純函式；真實結果交付由 `checkpoint_batch_item_result` 綁定持久 job/tenant/lease 與已保存的 item attempt，要求 attempt 精確相等，再執行合併及 CAS。未來 attempt 不能憑傳入的數字取得執行權。

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

## 2. 對 Codex 審查意見（PR #1285 與前置輪次）的處置

### 2.1 PR #1285 Codex 審查意見修復

| 審查意見（PR #1285 Findings） | 處置方式 | 對應測試 |
|---|---|---|
| **Finding 1 (Scope Aliases Normalization & Conflict Rejection)**: Snake_case 與 camelCase 範圍別名若同列出現且值衝突（如 `heatZoneId` 與 `heat_zone_id` 衝突）缺乏檢驗 | 新增 `_normalize_batch_item_scope`，跨四軸（`heatZoneId`/`heat_zone_id`、`regionId`/`region_id`、`brandId`/`brand_id`、`assignedAreaId`/`assigned_area_id`）偵測衝突，衝突時回傳 HTTP 422 `CONFLICTING_SCOPE_ALIAS`；無衝突時正規化為 canonical camelCase。Enqueue、GET 與 Retries 一致使用該正規化 | `test_review_finding_r3_r4_scope_and_submitter_preservation` |
| **Finding 2 (Multi-axis Scope Preservation on Intake)**: 批次匯入落入 intake 記錄時，僅寫入 `heatZoneId`，遺失 `regionId`、`brandId`、`assignedAreaId`，導致受限範圍之創建者讀取時被 403 `SCOPE_DENIED` 拒絕 | 在 `NetworkListingService.record_batch_assisted_entry` 補齊 `heatZoneId`、`regionId`、`brandId`、`assignedAreaId` 的完整保存，確保與 `correct_intake` 及授權檢驗一致 | `test_review_finding_r3_r4_scope_and_submitter_preservation` |
| **Finding 3 (Cancellation Races & Status Derivation Parity)**: 終態寫入與 Attempt 開始競爭時，取消未保留全成功狀態或跳過收據持久化 | 修正 `_write_receipt` 與 `settle_cancelled_batch_receipt`：當所有項目於取消前已成功時，推導結果保持 `SUCCEEDED`；終態結算時持久化已落地的項目成果與取消項目，不提早 return 跳過寫入 | `test_4_live_operator_cancellation_during_execution`、`test_review_finding_3_cancellation_races` |
| **Finding 4 (Test 5 Durable Replay Integration)**: Test 5 原為 in-memory mock 測試，未能驗證持久化 SQLite 與跨程序重啟的真實 fencing | 將 Test 5 重構為完整的 durable SQLite 整合測試（Subcases 5.1–5.5），驗證重送 attempt=0 業務調用、重複 enqueue 冪等防護、過期 attempt 失敗不覆寫成功、未啟動取消項不復活、以及訊息抵達順序置換等價性 | `test_5_duplicate_delivery_and_out_of_order` |

### 2.2 前置輪次審查意見（R1–R5）處置

| 審查意見（Finding） | 本輪處置 | 對應測試 |
|---|---|---|
| **R1**: `save_intake` 使用盲目的 `UPSERT`，在 multi-worker / stale worker 競態下會覆寫人工已修正的房源資料 | `DocumentStore.put_if_absent` 改為利用 SQLite `INSERT ... ON CONFLICT DO NOTHING` 並檢查 `cur.rowcount > 0` 確保跨連線原子性；`NetworkListingService.record_batch_assisted_entry` 改採 `_create_intake_if_absent`，已存在則回傳既有記錄，不覆寫 | `test_review_finding_r1_distinct_engine_stale_worker_cannot_overwrite_correction`、`test_7_crash_between_business_write_and_receipt_leaves_one_record` |
| **R2**: `DurableJobQueue.update_status` 在取消時直接寫入 DB，缺乏 CAS 重試，與執行完畢 worker 的 `SUCCEEDED` 競爭時可能造成 lost update | `DurableJobQueue.update_status` 加入內部 CAS 重試循環（最多 10 次），每次重新讀取 row version 並驗證受影響行數，確保取消與終態更新不被丟棄 | `test_review_finding_r2_cancellation_race_distinct_engine` |
| **R3**: `batch-listing-intake` 缺少 `heatZoneId` 階層式範圍授權檢查，任何具備 `listing` 權限的角色可提交任意 scope | `POST /jobs` 加入 `authorize_intake_action(principal, "submit_csv", collection_scope=...)` 逐項校驗，非允許 heat zone 立即拒絕並回傳 403 `SCOPE_DENIED`；`GET` 與 `retries` 端點一併檢驗 staff ownership 與 scope 授權 | `test_review_finding_r3_r4_scope_and_submitter_preservation` |
| **R4**: `submitter` 欄位被呼叫端任意偽造，且未保留操作者主體資訊 | `POST /jobs` 強制從經過驗證的 `principal_from_headers` 提取 `submitter`、`actor_name` 及 `actor_role_id` 寫入 payload，不可由 client 偽造；worker handler 執行時完整傳遞該主體資訊至業務層 | `test_review_finding_r3_r4_scope_and_submitter_preservation` |
| **R5**: 執行前取消（Pre-execution cancellation）產生了缺失 `job_id`/`tenant_id`/`correlation_id`/`idempotency_key` 的 placeholder envelope | `settle_cancelled_batch_receipt` 與佇列 `update_status` 重構，由資料庫 row 取得正規 envelope 元資料，生成完整的 canonical `DurableJobReceipt` | `test_review_finding_r5_pre_execution_cancellation_canonical_envelope` |

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
| `test_4_live_operator_cancellation_during_execution` | §4 測試 4 | 執行中由 operator 在 DB 取消：已開始但尚未 checkpoint 的項保留 `attempt=1` 並取消，未執行項 `attempt=0`；晚到結果不得覆寫取消狀態 |
| `test_5_duplicate_delivery_and_out_of_order` | §4 測試 5 | 真實持久化 SQLite 整合測試（Subcases 5.1–5.5）：重複投遞同一 attempt、重複 enqueue 同一 idempotency key、舊 attempt 失敗後到不得覆寫 `SUCCEEDED`、對 `attempt=0` 取消項的後到結果不復活、重排順序後聚合不變 |
| `test_6_auth_and_tenant_isolation_guards` | §3.3／隔離要求 | 401（未認證）、403（角色與租戶不符）、404（跨租戶）、409（QUEUED/RUNNING 重試）、400（SUCCEEDED 與 0 可重試項） |
| `test_7_same_submitted_intake_id_cannot_cross_tenants` | 本輪 review 反例 | 兩租戶各送同一 `intake_id`：留下兩筆記錄、各自租戶、各自資料，且皆非提交端給的那個 id |
| `test_7_crash_between_business_write_and_receipt_leaves_one_record` | 本輪 review 反例 | 業務寫入成功後崩潰、重放後只有一筆業務記錄，且等於收據的 `result_ref` |
| `test_7_row_completeness_decides_stage_without_zero_fill` | 本輪 review 反例 | 齊備的一列跑既有 matcher 落在 `READY` 並保留真實租金／坪數；只有地址的一列停在 `AWAITING_ASSISTED_ENTRY`，缺的欄位被列名而非填 0，兩列都不造 URL 或快照 |
| `test_review_finding_r1_distinct_engine_stale_worker_cannot_overwrite_correction` | R1 驗證 | 跨獨立 engine 連線模擬過期 worker 重試寫入，確認不會覆寫人工修正 |
| `test_review_finding_r2_cancellation_race_distinct_engine` | R2 驗證 | 跨獨立 engine 連線模擬取消與工作者成功提交之 CAS 競爭 |
| `test_review_finding_r3_r4_scope_and_submitter_preservation` | R3/R4 驗證 | 驗證 heatZoneId 越權拒絕（403 `SCOPE_DENIED`）與主體身分防偽保存 |
| `test_review_finding_r5_pre_execution_cancellation_canonical_envelope` | R5 驗證 | 驗證執行前取消生成正規 Envelope 與欄位完整性 |

三項 `test_7_*` 及 `test_review_finding_*` 皆走 default registry 與持久化 SQLite，讀回的是持久層中的業務實體而非僅記憶體收據。

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


## 2026-09-10：第六次退回後的前景修復

本輪交付狀態為 `PENDING_INDEPENDENT_REVIEW`；既有 task/PR 延續，前景 Codex 接手，Claude 獨立審查。真實來源、H06 與 production 驗收仍依原任務界線另行取證。

| 最後退回 | 本輪修復及可執行反例 |
|---|---|
| R1：idempotency replay 回傳受保護 job | enqueue 對 queue 實際返回的 record 套用與 GET 相同的 tenant/ownership/scope 授權；拒絕一般 job 使用保護類型的 key namespace，並核對 replay 的實際 job type。測試同租戶非 owner、匿名 legacy collision、租戶 key collision 與合法 replay |
| R2：batch response 未套欄位遮罩 | enqueue/replay/GET 共用 `mask_batch_intake_job`，已知原始欄位套既有 intake 分類，未知欄位與無分類 error message/details 限 restricted；複製 response 後遮罩，避免改寫 in-memory queue 的權威 payload。測試 PUBLIC/RESTRICTED 回讀、原始 rows、error details、retry response 與 storage 不受影響 |
| R3：old worker 覆写 API 已接受 retry | outer worker 成功／例外收尾都要求最新 row 仍為同一 fence 的 RUNNING，CAS 使用該次確認的 version；heartbeat stop 不採納另一生命週期的 version。正常 handler → retry API → old worker 收尾交錯後，QUEUED 與成功項 receipt 保留，正常新 worker 只做失敗項 |
| R4：持久重播驗收不足 | production result 路徑改用 `checkpoint_batch_item_result`，每次 CAS retry 從持久 receipt 讀取 items，檢查 job lease 與精確 item attempt，保留 server-owned timestamp/key，記錄拒絕觀測。Test 5 在真正 RUNNING job 中投遞 duplicate/舊結果，驗證三種抵達順序、取消後晚到結果、業務調用次數、version/payload 不變與 SQLite 重啟聚合；另以兩個獨立 engine 驗證 lease reclaim 前後晚到結果 |

取消的判定以持久 checkpoint 為準：已存成功結果保留；取消時尚未保存的結果不可由舊 worker 的本機變數補寫成功。測試中「取消在成功結果保存之後」的 hook 已改為實際先完成原始 update，再取消；另保留取消在結果保存之前的反例。

六個新的 API／lost-retry 反例覆蓋 memory/durable，修正版 focused selection 全部通過。以原 head `48da14aa` 的四個 production 檔案作負向對照時，六項均在預期的授權／值遮罩／QUEUED 被覆寫斷言失敗（exit 1），不是環境或 import 錯誤。原始對照收據：`/tmp/odp-foreground-repair-20260910/partial-negative.json` 與 JUnit XML。

精確交付 head 的完整四條 verification 由 `delivery_toolchain/git/task_verification.py run` 留存原始 exit code、命令、耗時與 SHA，獨立審查及合併以這些收據和 required CI 為準。

### 2026-09-10：獨立審查 B1–B3 接續修復

Claude 對 `05756dae01aedd0139c29926fe7408e300e82787` 的公開程式獨立審查認列原 R1–R4 大部分修复，另退回三項具體邊界，本次由 Codex 接續：

- B1：在 queue 建立之前套用與讀取相同的授權，建立後仍檢查 queue 實際返回的 record。enqueue 拒絕路徑透過既有 audit log 留下 denied 事件；API 回 401／403／404 時不再留下可執行的新 job。一般無 tenant 的既有通用 job 契約保留。
- B2：正常 item 結果因取消或 lease 變更被 checkpoint 拒絕時，若成功結果尚未被持久 receipt 記錄，既有 audit log 保存 `batch.item_result.uncheckpointed`／`reconciliation_required`，包含 tenant、job、item、attempt 與 `landed_result_ref`。取消收據保持 CANCELLED、`result_ref=null`；這筆補充稽核讓已落地副作用可回查，不是覆寫取消或自動重跑。
- B3：受保護 job 使用 v2 長度前綴編碼 tenant 與 client key，兩個成分含冒號也不會互相佔用 namespace。遇到既有 v1 key，只在實際 record 的 tenant／job type 相符時沿原 key 交給 queue 重播，並保留 owner／scope／clearance 與 feature-flag 檢查；generic job 不得使用 v1／v2 保留 prefix。

新增 memory／SQLite 的拒絕建立與拒絕稽核、合法 v1 replay／跨 tenant 共存測試；取消測試在真實 result CAS 注入取消，完整使用 default registry／business executor，確認實際 intake 落地後的補充 audit 在 SQLite 重啟後仍可查回。七項聚焦反例在前一版 production code 全部失敗，在本次修復後全部通過（原始負向／正向收據：`/tmp/odp-foreground-repair-20260910/partial-round2-negative.*`、`partial-round2-focused.*`）。完整指定驗證與新 head 的獨立審查仍以正式 exact-head receipts／canonical review 為準；前一版審查退回不當作新版本批准。
