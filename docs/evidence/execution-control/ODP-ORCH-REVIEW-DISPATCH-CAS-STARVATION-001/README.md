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

5. **Root Cause 5（Helper Commit 刷新狀態後未重新評估剩餘候選且覆寫他人租約）**：
   在同一輪派工多個 slot 時，前一筆 helper claim commit 刷新了 canonical snapshot，但剩餘 slot 仍沿用舊 snapshot 計算出的 candidate 列表與 reason，且 helper claim 條件判斷將「其他 agent 持有有效租約」誤判為「可覆寫」，導致在 external writer 於 sync 期間為任務寫入其他 agent 的有效租約（如 Codex generation 17）時，被 dispatcher 覆寫為新租約（如 Antigravity7 generation 18）並誤派發；同樣地，當任務在 sync 期間轉為 blocked 或產生未完成依賴時，舊 candidate 仍被派發。

6. **Root Cause 6（Advisory Sync / Reload 失敗誤標記為成功刷新）**：
   `commit_canonical_task_transition` 在 `load_status` 拋出例外時使用 `pass` 吞沒錯誤並回傳 `True`；而在 advisory 路徑中無論 commit 成功與否皆設 `resynced = True`，若 `sync_status_pipeline` 回傳 False 或 reload 失敗，記憶體內的 `status` 仍停留在舊 revision，導致後續候選評估讀取過期 reviewer 造成 stale event 派發。

---

## 3. 修復方案與實作細節

### 3.1 `commit_canonical_task_transition` 嚴格狀態重載與 Fail-Closed
- 在 `.orchestrator/status_transition.py` 與 `.orchestrator/supervisor.py` 中：
  - `commit_canonical_task_transition` 於 `sync_status_pipeline(config)` 成功後，透過 `load_status(config)` 重新載入磁碟上的最新 snapshot。
  - 當 `latest is not status and isinstance(latest, dict) and "tasks" in latest` 時，以 `status.clear(); status.update(latest)` 原地更新 `status`。
  - 若 `load_status` 失敗（拋出例外）或回傳無效資料，不再吞沒錯誤，一律回傳 `False`，嚴守 fail-closed 原則。

### 3.2 `_commit_advisory_status_transition` 確保新鮮度與隔離失敗
- 在 `.orchestrator/dispatch_engine.py` 中引入 `_commit_advisory_status_transition`：
  - 嘗試執行 `commit_canonical_task_transition(config, status)`。
  - 若 commit 失敗（CAS 衝突、sync 失敗或 reload 失敗），主動嘗試自 canonical 磁碟重新載入最新狀態。
  - 若能成功載入新鮮 snapshot 則回傳 `True` 並觸發候選重新評估（`resynced = True; break`）；若無法確認 snapshot 新鮮度，回傳 `False` 並立即終止當前評估且清空候選（`candidates = []; break`），防止從 stale/unconfirmed snapshot 派發任何任務。

### 3.3 候選評估 Per-Slot 重新驗證與 Helper 租約保護
- 在 `.orchestrator/dispatch_engine.py` 中重構 `dispatch_ready_tasks`：
  - 改用 `while queued_for_agent < available_agent_slots and dispatches < max_dispatches_per_tick:` 迴圈，每派發一筆任務或每次 snapshot 變更後，皆基於最新 `status` snapshot 重新執行完整候選評估與排序。
  - 在 helper claim 路徑中，嚴格檢查 `existing_claim_live and existing_claimant != normalize_agent_id(target_agent)`，嚴禁覆寫任何其他 agent 的有效租約。
  - Helper lease commit 成功後，自動於下一輪 slot 迭代使用最新 snapshot 與更新之 `pending_task_ids`，徹底防禦 external sync 期間任務狀態轉為 blocked、新增依賴或改派租約的情境。

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
7. `test_diagnostic_cas_helper_candidate_revalidated_after_real_sync` (4 variants: unchanged, competing_lease, blocked, dependency):
   - 驗證 helper commit 刷新 canonical state 後，剩餘候選重新評估，且永不覆寫其他 agent 的有效租約，亦不派發 blocked 或 unsatisfied dependency 任務。
8. `test_diagnostic_cas_advisory_resync_failure_never_queues_superseded_reviewer` (3 variants: ok, sync_failure, reload_failure):
   - 驗證 advisory sync failure 與 reload failure 時壓抑過期派工，不對已被改派的舊 reviewer 派發 stale event。

### 4.2 測試執行收據（Test Execution Receipts Bound to Head SHA）

所有驗證命令均以獨立子程序執行，保留原始 terminal exit code 與持續時間，無背景等待迴圈或摘要 grep：

- **Tested Commit**: `98762e8fa584dfd20dbf002af465a6ecf80ee6c9`
- **Receipt 1 (Diff & Whitespace Check)**:
  - Command: `git diff --check`
  - Exit code: `0`
- **Receipt 2 (Full Dispatch Policy Test Suite - 190 passed)**:
  - Command: `uv run pytest -q .orchestrator/test_dispatch_policy.py`
  - Selection: 190 passed (includes candidate refresh, advisory resync, priority rank, and lease isolation tests)
  - Exit code: `0`
- **Receipt 3 (Supervisor Concurrency, Lease Escalation & Recovery Tests - 16 passed)**:
  - Command: `uv run pytest -q .orchestrator/test_supervisor.py::DispatchStatusSyncTests .orchestrator/test_supervisor.py::AutomaticRecoveryTests::test_ci_failure_requeue_fails_closed_on_stale_status_snapshot .orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_an_escalated_lease_block_is_reported_on_the_task_record .orchestrator/test_supervisor.py::PollWorkersRecoveryTests::test_dispatch_rollback_on_commit_failure`
  - Selection: 16 passed
  - Exit code: `0`
- **Receipt 4 (Codex2 External Mutation & Resync Failure Probes - 7 passed)**:
  - Command: `uv run pytest -q /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T032333Z-3db4e30a/test_review_resync_failure.py /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T032333Z-3db4e30a/test_review_helper_refresh.py`
  - Selection: 7 passed
  - Exit code: `0`

---

## 5. 上線與發布說明（Rollout Instructions）

1. **隔離 Worktree 限制**：
   - 任務執行與驗證僅在隔離之 task worktree 內進行，未直接修改 live runtime、state 或 supervisor config。
   - 不手動重啟 supervisor、不提高 quota、不觸動 product tests。
2. **協調者統一管理 Rollout**：
   - 本變更經獨立 PR 審查與 CI 通過並正式合併入 `dev` 後，由協調者（Coordinator）在維護窗口內統一重啟 Supervisor 載入最新控制平面程式碼。
3. **資料相容性**：
   - 無需資料庫或狀態遷移，完全向下相容既有 `ai-status.json` 結構。
