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

3. **Root Cause 3（Snapshot 重載與候選索引重建）**：
   在 CAS 寫入失敗或 snapshot 刷新時，若未自最新 snapshot 重建 `tasks` 與 `task_map` 索引，候選挑選迴圈可能繼續評估已脫鉤或已被外部 writer 修改的記憶體物件。

---

## 3. 修復方案與實作細節

### 3.1 `commit_canonical_task_transition` 狀態重載
- 在 `.orchestrator/status_transition.py` 與 `.orchestrator/supervisor.py` 中：
  - `commit_canonical_task_transition` 於 `sync_status_pipeline(config)` 成功後，透過 `load_status(config)` 重新載入磁碟上的最新 snapshot。
  - 當 `latest is not status and isinstance(latest, dict) and "tasks" in latest` 時，以 `status.clear(); status.update(latest)` 原地更新 `status`，確保記憶體內的 `_status_write_revision` 與磁碟保持一致。

### 3.2 `dispatch_ready_tasks` 診斷寫入容錯與動態重試
- 在 `.orchestrator/dispatch_engine.py` 中：
  - 對於非生命週期變更的 advisory diagnostic 寫入（`review_dispatch_suppressed`、`approved_head_missing`、`approved_head_unresolved`、`ci_status_unresolved`）：
    - 寫入失敗時不中斷 tick（不直接 `return changed`）。
    - 重新刷新 `tasks` 與 `task_map`，設定 `resynced = True` 並以 `break` 重啟該 agent 的候選評估。
    - 不將純 advisory 訊息標記為 `changed = True`，避免在無實質派工或狀態變更時誤報變更。
  - 將候選評估限制為動態有界的 `max_agent_eval_attempts = max(8, len(tasks) + 1)`，防止無限迴圈。

### 3.3 嚴格保留關鍵生命週期 Fail-Closed 語義
- 對於必須保證原子性的實質生命週期狀態變更（`requeue_task_for_ci_repair`、`re-review_required` 狀態轉移、`release_dead_helper_claims`）：
  - 嚴格保留 fail-closed 行為：若 CAS commit 失敗立即終止操作並返回，確保不可在過時狀態上派發 worker。

---

## 4. 驗證記錄（Test Receipts）

### 4.1 新增回歸測試
在 `.orchestrator/test_dispatch_policy.py` 新增以下回歸測試：
1. `test_diagnostic_cas_rejection_does_not_starve_subsequent_green_reviews`:
   - 模擬 2 個 pending CI 任務在 advisory 寫入時遭遇 CAS rejection，驗證後續 2 個 green CI 任務仍能正常派工，不發生飢餓。
2. `test_canonical_sync_advancing_disk_revision_reloads_status_for_subsequent_writes`:
   - 驗證第一次 commit 後 canonical sync 推進磁碟 revision，第二次 commit 能正確依據新 revision 成功寫入，不被 stale CAS 拒絕。
3. `test_diagnostic_cas_mismatch_resyncs_and_rebuilds_indices_from_disk`:
   - 驗證 external writer 推進磁碟狀態並加入新任務時，dispatcher 在 CAS 競爭後正確重載 snapshot 並派發新任務。

### 4.2 測試執行結果
```bash
# 1. 執行新加入與現有 dispatch policy 測試 (180/180 PASSED)
PYTHONPATH=.orchestrator:scripts:delivery_toolchain/git uv run --python 3.12 pytest -q .orchestrator/test_dispatch_policy.py
........................................................................ [ 40%]
........................................................................ [ 80%]
....................................                                     [100%]
180 passed in 23.45s

# 2. 執行 Supervisor Concurrency & Sync 測試 (17/17 PASSED)
PYTHONPATH=.orchestrator:scripts:delivery_toolchain/git uv run --python 3.12 pytest -q   .orchestrator/test_supervisor.py::DispatchStatusSyncTests   .orchestrator/test_supervisor.py::AutomaticRecoveryTests::test_ci_failure_requeue_fails_closed_on_stale_status_snapshot   .orchestrator/test_supervisor.py::StatusWriteConcurrencyTests
.................                                                        [100%]
17 passed in 3.12s
```

---

## 5. 上線與發布說明（Rollout Instructions）

1. 本修復屬於控制平面（Orchestrator Control Plane）派工排程核心修正。
2. 合併入 `dev` 後，Supervisor 重啟或下一輪派工週期會自動載入新版 `status_transition.py`、`dispatch_engine.py` 與 `supervisor.py`。
3. 無需進行資料庫 migration，向下相容既有 `ai-status.json` 格式。
