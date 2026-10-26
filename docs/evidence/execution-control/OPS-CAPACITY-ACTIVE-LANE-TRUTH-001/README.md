# OPS-CAPACITY-ACTIVE-LANE-TRUTH-001: 修正 Capacity Chair active lane 計數真相驗收紀錄

## 1. 問題概述與根因分析

### 問題背景
在先前的實作中，`capacity_controller.py` 的 `evaluate_chair` 在評估是否核准 Helper wave（`approve_helper_wave`）時，直接比較 canonical 可執行任務數（`runnable_tasks`）與全部活躍 worker 總數（`snapshot["active_workers"]`）：

```python
"approve_helper_wave": bool(
    snapshot["runnable_tasks"] > snapshot["active_workers"]
    and snapshot["available_slots"] > 0
)
```

### 根因與實際影響
這造成了活躍 worker lane accounting 的語義混淆：
1. **Reviewer / Finalizer 誤抵銷 Owner Runnable 任務**：
   - 當系統中有 1 個活躍 Reviewer 正在審查 PR（例如審查 `TASK-REVIEW-001`，其狀態為 `review`），同時有另 1 個不相關的 Owner 可執行任務（`TASK-RUNNABLE-002`，狀態為 `todo`）等待派工。
   - 此時 `runnable_tasks = 1`，而 `active_workers = 1`（即該 Reviewer）。
   - 舊邏輯計算 `runnable_tasks (1) > active_workers (1)` 結果為 `False`，導致 Helper wave 被錯誤拒絕。
   - 事實上，沒有任何 worker 正在執行 `TASK-RUNNABLE-002`，Reviewer / Finalizer 的存在不應抵銷另一個待執行的 Owner Runnable 任務。
2. **容量使用率與 Helper Backlog 需求混為一談**：
   - `available_slots`（可用槽位）與 `utilization_ratio`（容量使用率）確實必須計算全體活躍 worker（包含 Reviewer、Finalizer 等），以避免超過總併發上限。
   - 但 Helper backlog 決策僅關心「當前 canonical runnable tasks 是否已有足夠的 active execution workers 在執行」，兩者屬於不同層次的 accounting 真相。

---

## 2. 修復架構與設計

為徹底解決上述問題並符合單一權威來源原則，本修復落實了嚴格的 Active Lane Accounting 分離：

1. **Capacity Snapshot 區分全體活躍 Worker 與 Runnable 活躍 Worker**：
   - `active_workers`：繼續統計所有狀態處於 `ACTIVE_WORKER_STATUSES`（`running`, `waiting_approval`, `suspended_approval`, `retry_backoff`, `manual_pending`, `stalled`）的 worker。
   - `available_slots` 與 `utilization_ratio`：繼續依據全體 `active_workers` 計算，確保硬性容量槽位限制與閒置率監控維持真實。
   - `active_runnable_workers`：僅統計其指派 `task_id` 存在於 Supervisor 傳入的 canonical runnable task set 之活躍 worker。
2. **Helper Backlog 精確比較**：
   - `approve_helper_wave` 修正為：
     ```python
     "approve_helper_wave": bool(
         snapshot["runnable_tasks"] > snapshot.get("active_runnable_workers", snapshot["active_workers"])
         and snapshot["available_slots"] > 0
     )
     ```
   - 當只有 Reviewer 或 Finalizer 處於 active 狀態時，其 `task_id` 不在 canonical runnable set 中，`active_runnable_workers` 為 0，因此 `runnable_tasks (1) > active_runnable_workers (0)` 成立，在尚有可用 slot 時正確核准 Helper wave。
3. **零新增 Task Eligibility 判斷（純消費模式）**：
   - `capacity_controller.py` 嚴格僅從 Supervisor 傳入的 `runnable_tasks` 集合提取 canonical task IDs，不自行新增任何 task status、依賴或 owner eligibility 判定邏輯。
4. **Int-only Legacy 輸入保守 Fallback**：
   - 當 `runnable_tasks` 傳入整數計數（無法得知確切 task IDs 集合）時，採取保守 fallback：將 `active_runnable_workers` 設為全體 `active_workers` 總數，確保不會因缺失集合資訊而過度派工。
5. **支援動態 Schema 與多種 Worker Task ID 結構**：
   - 提取 worker 指派任務 ID 時，支援 `worker["task_id"]`、`worker[task_id_field]`、`request_snapshot.metadata.task` 以及 `metadata.task_id`，確保在自訂 schema 或不同運行模式下皆能正確比對。

---

## 3. 驗證與測試結果

### 3.1 測試套件執行
使用專案 Python 環境（`.venv`）執行完整測試：
```bash
/home/lupin/odayplus-devbase/.venv/bin/pytest .orchestrator/test_capacity_controller.py -q
/home/lupin/odayplus-devbase/.venv/bin/pytest .orchestrator/test_supervisor.py -k capacity -q
/home/lupin/odayplus-devbase/.venv/bin/python3 delivery_toolchain/governance/check_code_boundaries.py
/home/lupin/odayplus-devbase/.venv/bin/ruff check .orchestrator/capacity_controller.py .orchestrator/test_capacity_controller.py
git diff --check origin/dev
```

### 3.2 測試項目清單
1. `test_reviewer_active_does_not_offset_unrelated_owner_runnable_task`：**核心 Regression 測試**，驗證 1 Reviewer active + 1 無關 Owner runnable 任務時，`active_runnable_workers` 為 0，`approve_helper_wave` 正確回傳 `True`。
2. `test_finalizer_active_does_not_offset_unrelated_owner_runnable_task`：驗證 1 Finalizer active on `review_approved` + 1 無關 Owner runnable 任務時，不抵銷 runnable 任務，正確核准 Helper wave。
3. `test_active_worker_on_runnable_task_suppresses_helper_wave`：驗證已有 active worker 正在執行該 runnable 任務時，`active_runnable_workers` 為 1，正確抑制 Helper wave。
4. `test_int_only_legacy_runnable_tasks_conservative_fallback`：驗證傳入 int-only legacy runnable 計數時，保守 fallback 為全體 active worker，防止過度派工。
5. `test_custom_schema_active_worker_task_id_matching`：驗證自訂 `taskId` schema 下，worker task ID 正確解析並比對 runnable 集合。
6. `test_chair_never_approves_sidecars_while_canonical_work_is_runnable`：確認存在 runnable work 時絕不核准 Sidecar。
7. `test_chair_approves_bounded_sidecars_after_sustained_idle_capacity`：確認在持續閒置且無 runnable work 時正確核准有上限的 Sidecar wave。
8. `test_sidecar_wave_requires_a_current_chair_decision`：確認 Sidecar wave 需具備當前有效 Chair 決策。
9. `test_expired_helper_execution_leases_are_reported_without_changing_owner`：確認過期租約正常釋放。
10. `test_capacity_snapshot_excludes_human_gate_non_dispatchable_review_and_blocked`：確認治理與阻塞狀態被排除於 runnable 之外。
11. `test_current_active_tasks_regression_fixture_runnable_is_zero`：確認實體看板 fixture 的 runnable 計數為 0。
12. `test_runnable_todo_task_approves_helper_wave_when_slots_available`：確認標準可執行 todo 任務在有可用 slot 時核准 Helper wave。
13. `test_capacity_snapshot_with_custom_schema_and_invalid_task_id_or_owner`：自訂 schema 邊界測試。
14. `test_capacity_and_supervisor_dispatch_share_identical_runnable_truth`：驗證 Capacity 與 Dispatcher 判定一致。
15. `test_custom_schema_dependency_requires_canonical_dependency_id`：自訂 schema 依賴測試。
16. `test_custom_schema_sidecar_parent_requires_canonical_task_id`：自訂 schema Sidecar 父任務測試。
17. `test_sidecar_candidates_fail_closed_when_runnable_work_appears_during_valid_approval`：Sidecar fail-closed 回歸測試。
18. `CapacityControllerReconciliationTests`（`test_supervisor.py` 中 13 項相關測試）：包含 supervisor 層級 reconcile 在各情境下的正確性。

### 3.3 測試輸出實錄
```text
.orchestrator/test_capacity_controller.py .................              [100%]
.orchestrator/test_supervisor.py .............                            [100%]
Code boundary checks passed for 982 files.
All checks passed! (ruff)
git diff --check origin/dev (clean, exit 0)
```
全部 30 項測試 100% 通過；程式邊界檢查（982 個檔案）、ruff linter 與 git diff check 全數通過。
