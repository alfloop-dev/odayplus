# OPS-CAPACITY-RUNNABLE-TRUTH-001: 修正 Capacity Chair runnable 計數假陽性驗收紀錄

## 1. 問題概述與根因分析

### 問題背景
在既有實作中，`capacity_controller.py` 的 `capacity_snapshot` 使用靜態集合 `RUNNABLE_TASK_STATUSES = {"todo", "in_progress", "review", "review_approved"}` 來計算可執行任務數（`runnable_tasks`）。

這導致了嚴重的假陽性（false positive）計數：
1. **未完成依賴（Dependency Blocked）**：狀態為 `todo` 但其前置任務尚未完成（`depends_on` 未滿足）的任務被誤計為 runnable。
2. **人工守門（Human/Ops Gate）**：指派給 `Human/Ops` 或標記為 `human_gate` 的任務無法由 AI Supervisor 自動派發，卻被計為 runnable。
3. **專屬流程狀態（Review / Finalize）**：`review`（等待評審）與 `review_approved`（等待 PR 合併）屬於專屬生命週期狀態，非一般 owner 執行任務，卻被計入 runnable，導致誤觸發 `runnable_work_without_active_workers`。
4. **阻塞狀態（Blocked）與非派發（non_dispatchable）**：相關任務不應被視為可執行任務。
5. **架構層次與邊界處理問題**：
   - 早期改動在 `capacity_controller.py` 中透過 `_supervisor_module()` 動態反向 import `supervisor.py`，造成模組循環相依。
   - `capacity_controller.py` 自行維護一套 duplicate 判斷，且硬編碼 `id`/`owner`，未沿用 `schema.task_id_field` 與 `schema.assignee_field`，導致缺少 task ID 或未知 owner 的任務仍被誤計為 runnable。

### 實際影響
在現行看板只有 5 項活躍任務的環境下：
- `HUMAN-OSS-LEGAL-APPROVAL-001`（todo，Human/Ops，依賴未完成）
- `XR-SOURCE-APPROVAL-ACTIVATION-001`（todo，依賴 HUMAN-OSS-LEGAL-APPROVAL-001）
- `ODP-EPHEMERAL-STAGING-ROLLOUT-001`（blocked，waiting_for: Human/Ops）
- `ODP-PROD-BLUEGREEN-ROLLOUT-001`（todo，依賴 ODP-EPHEMERAL-STAGING-ROLLOUT-001）
- `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001`（todo，依賴 ODP-PROD-BLUEGREEN-ROLLOUT-001）

舊邏輯誤將其中 4 項 todo 任務全部計為 runnable（`runnable_tasks = 4`），造成：
- 在無 active workers 時誤判為 stall（觸發 `runnable_work_without_active_workers`）。
- 阻止 Sidecar wave 在容量閒置時依規則正常啟動（因誤判仍有 canonical runnable work）。

---

## 2. 修復架構與設計

沿用 Supervisor 現有 Dispatcher 與依賴真實來源（`dispatch_engine.dispatch_priority_for_task`、`dispatch_policy.py`、`task_archive.TaskResolver`、`worker_failure_policy.py`），實現單一權威來源（Single Source of Truth），徹底消除第二套規則與循環 import：

1. **沿用 Dispatcher 的 canonical owner-execution truth**：
   - `supervisor.canonical_dispatchable_task_ids` 呼叫既有 `dispatch_engine.dispatch_priority_for_task`，只投影 Dispatcher 的 owner `in_progress`/`todo` 優先級 2/3，不複製第三套 eligibility 規則。
   - 支援動態 schema 設定：`task_id_field = schema.get("task_id_field", "id")` 與 `owner_field = schema.get("assignee_field", "owner")`。
   - 在投影邊界排除無效或空白 task ID、`non_dispatchable`、Human/Ops gate、sidecar 與治理/阻塞狀態；owner eligibility 仍由既有 `agent_can_take_task` 與 Dispatcher 判斷。
   - 自訂 schema 的 active task lookup 先以 canonical task ID 建 map，再交給 `TaskResolver`；缺少 `taskId` 的 legacy `id` 不得滿足依賴。

2. **Capacity Chair 純消費模式（Zero Duplicate Rules / No Reverse Imports）**：
   - `capacity_controller.py` 徹底移除 `_supervisor_module()` 與任何對 `supervisor.py` 的反向匯入。
   - `capacity_controller.py` 徹底移除 `TaskResolver` 與任何對 `supervisor.py` 的反向匯入。
   - `capacity_snapshot`、`evaluate_chair`、`sidecar_candidates` 只接收 Supervisor 傳入的 runnable task count/ID set，不再維護獨立的可派發規則。
   - `supervisor.reconcile_capacity_controller` 先計算 canonical task ID set，再餵 Capacity Chair，確保 snapshot、sidecar decision 與 Dispatcher 判定一致。

---

## 3. 驗證與測試結果

### 3.1 測試套件執行
執行指令：
```bash
python3 -m pytest .orchestrator/test_capacity_controller.py -q
python3 -m pytest .orchestrator/test_supervisor.py -k capacity -q
```

### 3.2 測試項目清單
1. `test_chair_never_approves_sidecars_while_canonical_work_is_runnable`：確認當存在真正 canonical runnable work 時，絕不核准 sidecar wave。
2. `test_chair_approves_bounded_sidecars_after_sustained_idle_capacity`：確認在持續閒置且無 runnable work 時，正確核准有上限的 sidecar wave。
3. `test_sidecar_wave_requires_a_current_chair_decision`：確認 sidecar wave 需具備未過期的 Chair 決策。
4. `test_expired_helper_execution_leases_are_reported_without_changing_owner`：確認過期租約正常釋放且不影響 owner。
5. `test_capacity_snapshot_excludes_human_gate_non_dispatchable_review_and_blocked`：確認 human gate、non_dispatchable、review、review_approved、blocked 與未完成依賴皆被排除。
6. `test_current_active_tasks_regression_fixture_runnable_is_zero`：**Regression Fixture** 測試，重現目前看板 5 項任務，確認 `runnable_tasks` 從 4 降為 0，且 sidecar wave 可正常啟動。
7. `test_runnable_todo_task_approves_helper_wave_when_slots_available`：確認真正可執行的 todo 任務在有可用 slot 時仍正常核准 helper wave。
8. `test_capacity_snapshot_with_custom_schema_and_invalid_task_id_or_owner`：驗證自訂 schema 欄位、缺少 task ID、未知 owner、Human/Ops 等邊界情況皆正確歸零。
9. `test_capacity_and_supervisor_dispatch_share_identical_runnable_truth`：驗證 Capacity snapshot 與 Supervisor dispatcher 權威述詞判定完全一致。
10. `CapacityControllerReconciliationTests`：驗證 Supervisor 層級的 `reconcile_capacity_controller` 狀態與決策寫入一致性。
11. `test_task_is_runnable_respects_custom_schema_and_excludes_invalid_id_or_owner`：驗證 Supervisor 權威述詞在自訂 schema 與未知 owner 下的精確行為。
12. `test_task_is_runnable_excludes_non_execution_states_and_unsatisfied_dependencies`：驗證非執行狀態與未滿足依賴在 Supervisor Dispatcher-backed predicate 下的排除。
13. `test_custom_schema_dependency_requires_canonical_dependency_id`：驗證 custom `taskId` schema 下缺少 `taskId` 的 legacy dependency 不得滿足依賴。

### 3.3 測試輸出實錄
```text
.orchestrator/test_capacity_controller.py ..........                      [100%]
.orchestrator/test_supervisor.py ............                             [100%]

============================== 22 passed ==============================
```
全部 22 項相關測試 100% 通過；另已確認 `git diff --check` 通過，未修改 forbidden 的 Dispatcher 實作檔。
