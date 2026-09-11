# ODP-ORCH-REVIEW-DISPATCH-CAS-STARVATION-001: 解決 Review Dispatch CAS 競爭與 Stale Snapshot 導致的派工飢餓問題

- **Task ID**: `ODP-ORCH-REVIEW-DISPATCH-CAS-STARVATION-001`
- **Owner**: `Antigravity3`
- **Reviewer**: `Codex2`
- **Task Branch**: `task/ODP-ORCH-REVIEW-DISPATCH-CAS-STARVATION-001`
- **Base Branch**: `dev`

---

## 1. 缺陷背景與事件分析

### 1.1 事件重現（Incident Evidence）
在 `support/handoffs/parallel-dispatch-20260911/review-dispatch-cas-evidence.json` 的現場日誌中，記錄了多個 review ready 任務（例如 PR #1301，exact-head required CI green）因 CAS 衝突被連續飢餓超過 15 分鐘：
- 控制平面連續記錄 4 次 `stale_status_write_rejected`：
  - `expected_revision`: `cbe401ea64d048479e09fe16fbaefbe6` vs `actual_revision`: `27282b090886470081d59ba2986422ce`
  - `expected_revision`: `27282b090886470081d59ba2986422ce` vs `actual_revision`: `c8f141fa33f9469ab5ca6fa7ee23cb57`
  - `expected_revision`: `704b2a8fe78d46b785d03cbafb2d718b` vs `actual_revision`: `901f465d3ec64a519d115e610ff98a12`
  - `expected_revision`: `696515cb539f4f4699564d2d48074697` vs `actual_revision`: `659ec01be8c9462c8e39265f24254b73`
- 由於前置任務的 advisory diagnostic 寫入（如 `CI checks pending`）遭遇 CAS 遭拒或 canonical sync 推進磁碟 revision，原本準備派工的綠燈 PR 被跳過或 tick 提前退出，造成 Reviewer 閒置且任務延宕。

---

## 2. 根因分析（Root Cause Analysis）

1. **Root Cause 1（Canonical Sync 推進 Revision 導致記憶體狀態脫鉤）**：
   `commit_canonical_task_transition` 執行時，首先透過 `write_status_snapshot_if_current` 寫入 revision A，隨後呼叫 `sync_status_pipeline(config)`。後者透過 CLI `sync_all` 刷新狀態並將 revision B 寫入磁碟。若記憶體內的 `status` 未在 sync 成功後重新自磁碟載入最新 snapshot，同一個 tick 內的下一次 commit 就會持過期的 revision A 進行 CAS 寫入，必然觸發 `stale_status_write_rejected`。

2. **Root Cause 2（Advisory Diagnostic 寫入失敗導致提前退出）**：
   在 `dispatch_ready_tasks` 中，當檢查到 PR CI pending、unresolved 或 head drift 等非致命診斷資訊時，會嘗試更新 `task["next"]` 並執行 `commit_canonical_task_transition(config, status)`。原實作在 commit 回傳 False 時直接 `return changed` 退出整個派工迴圈，使該 tick 之後的所有綠燈待審任務完全失去被評估與派工的機會。

3. **Root Cause 3（Snapshot 重載與候選評估 Iterator / 物件脫鉤）**：
   在 CAS 寫入或 snapshot 刷新後，若僅賦值新變數而使用 `continue` 繼續既有 `enumerate(tasks)` iterator，該 iterator 仍會巡歷過期 list 與 detached 物件。當外部 writer 在 sync 期間修改 reviewer（如改派 Codex）時，過期 iterator 會誤將舊 reviewer（如 Antigravity7）放入派工佇列。此外，若 helper claim 連續派發多筆任務，第一筆 commit 後 snapshot 刷新，第二筆若未重新獲取 live task 物件，會修改 detached 物件導致 lease 未持久化至磁碟。

4. **Root Cause 4（Retry Exhaustion 仍派發過期候選）**：
   當 advisory CAS 寫入連續遭拒並耗盡有界重試（8 次）時，若未清空當輪候選，過期候選仍會進入 dispatch loop，造成 stale event 派發。

---

## 3. 修復方案與實作細節

### 3.1 `commit_canonical_task_transition` 狀態重載
- 在 `.orchestrator/status_transition.py` 與 `.orchestrator/supervisor.py` 中：
  - `commit_canonical_task_transition` 於 `sync_status_pipeline(config)` 成功後，透過 `load_status(config)` 重新載入磁碟上的最新 snapshot。
  - 當 `latest is not status and isinstance(latest, dict) and "tasks" in latest` 時，以 `status.clear(); status.update(latest)` 原地更新 `status`，確保記憶體內的 `_status_write_revision` 與磁碟保持一致。

### 3.2 `dispatch_ready_tasks` 重構候選重試與狀態刷新
- 在 `.orchestrator/dispatch_engine.py` 中：
  - 在每次 snapshot 變更（包含 advisory note 寫入、`re-review_required`、`requeue_task_for_ci_repair` 或 CAS rejection）後，立即以 `resynced = True; break` 中斷當前 iterator，並在下一輪 attempt 重新自 `status` 讀取全新 `tasks` 與 `task_map` 重新評估。
  - 追蹤 `deferred_task_ids`，避免同一 tick 內剛發生 lifecycle 狀態轉移（如轉回 `in_progress` 或 `review`）的任務在同一 tick 被重複派發。
  - 若 `eval_attempt` 達到上限且仍因 CAS 衝突退出（retry exhaustion），強制清空 `candidates = []`，嚴禁派發未經乾淨驗證的 stale 候選。

### 3.3 Candidate Dispatch 重新綁定 Live Task 物件與資格二度驗證
- 在派發候選佇列時：
  - 每次迭代皆透過 `task_id` 從最新 `status` 重獲 `live_task`。
  - 對於 `REASON_HELPER_CLAIM`：在 `live_task` 上施加 lease 變更並 commit，commit 成功後再次自最新 `status` 驗證 lease 是否確實存在於磁碟，確保 event 與磁碟狀態完全一致。
  - 對於非 helper 派發：在 build event 前二度比對 `live_task` 的狀態、owner 與 reviewer，若外部 writer 已變更指派則自動跳過，杜絕 stale reviewer event。

### 3.4 審計其他 commit callers（`advance_approved_prs_to_merge` 與 Recovery Loops）
- 審計並重構 `advance_approved_prs_to_merge`、`recover_conflicted_review_prs` 與 `recover_failed_ci_review_prs`：
  - 改用 `while True:` 與 `processed_ids` 遍歷，確保每次 `requeue_task_for_ci_repair` commit 刷新 `status` 後，後續迭代皆自最新 snapshot 取得未處理的 live task 物件，徹底排除 detached object 問題。

---

## 4. 驗證記錄（Test Receipts）

### 4.1 新增回歸測試（`.orchestrator/test_dispatch_policy.py`）
1. `test_diagnostic_cas_two_pending_two_green_revision_changing_dispatch`:
   - 2 個 CI pending 任務在前、2 個 CI green 任務在後。真實 temp-file 與 sync 推進 revision，驗證 2 個 green 任務皆正常產生正確 reviewer event。
2. `test_diagnostic_cas_canonical_sync_advancing_disk_revision_reloads_status_for_subsequent_writes`:
   - 驗證同一個 tick 內連續兩筆 commit 能在 sync 推進 revision 後正確重載並成功寫入。
3. `test_diagnostic_cas_external_writer_race_preserves_data_and_dispatches_ready_tasks`:
   - 模擬 external writer 在 advisory 寫入時競態推進 revision 並注入新任務與自訂欄位，驗證 CAS rejection 後 dispatcher 成功重載 snapshot、保留外部更新並派發新任務。
4. `test_diagnostic_cas_lifecycle_sync_does_not_enqueue_reassigned_reviewer`:
   - 驗證 lifecycle 轉移後 external sync 將候選 reviewer 改派，dispatcher 重啟評估且不對舊 reviewer 派發 stale event。
5. `test_diagnostic_cas_helper_claims_persist_leases_for_all_queued_helpers`:
   - 驗證同一 tick 內多筆 helper lease 派發時，所有 queued events 在磁碟上皆具備對應的持久化 lease。
6. `test_diagnostic_cas_retry_exhaustion_does_not_enqueue_stale_reviewer`:
   - 驗證 8 次 CAS rejection 耗盡重試時，stale candidate 被乾淨丟棄，不產生 stale reviewer event。

### 4.2 測試執行收據（Test Execution Receipts）
```bash
# 1. Whitespace & diff 檢查
$ git diff --check
(exit code 0)

# 2. Focused Dispatch Policy 測試（41 passed）
$ uv run pytest -q .orchestrator/test_dispatch_policy.py -k 'diagnostic_cas or review_dispatch or stale_wake or cas or release_dead_helper_claims'
.........................................                                [100%]
41 passed in 9.35s (exit code 0)

# 3. Supervisor Concurrency & Recovery 測試（14 passed）
$ uv run pytest -q .orchestrator/test_supervisor.py::DispatchStatusSyncTests .orchestrator/test_supervisor.py::AutomaticRecoveryTests::test_ci_failure_requeue_fails_closed_on_stale_status_snapshot
..............                                                           [100%]
14 passed in 3.61s (exit code 0)

# 4. Codex2 Reproduction Probes 測試（4 passed）
$ uv run pytest -v /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T022943Z-8a15a998/test_review_lifecycle_snapshot.py /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T022943Z-8a15a998/test_reviewer_helper_claims.py /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T022943Z-8a15a998/test_codex2_review_retry_exhaustion.py
============================== 4 passed in 6.58s ===============================
(exit code 0)
```

---

## 5. 上線與發布說明（Rollout Instructions）

1. **隔離 Worktree 限制**：
   - 任務執行與驗證僅在隔離之 task worktree 內進行，未直接修改 live runtime、state 或 supervisor config。
   - 不手動重啟 supervisor、不提高 quota、不觸動 product tests。
2. **協調者統一管理 Rollout**：
   - 本變更經獨立 PR 審查與 CI 通過並正式合併入 `dev` 後，由協調者（Coordinator）在維護窗口內統一重啟 Supervisor 載入最新控制平面程式碼。
3. **資料相容性**：
   - 無需資料庫或狀態遷移，完全向下相容既有 `ai-status.json` 結構。
