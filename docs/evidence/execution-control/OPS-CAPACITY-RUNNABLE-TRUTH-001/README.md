# OPS-CAPACITY-RUNNABLE-TRUTH-001: 修正 Capacity Chair runnable 計數假陽性驗收紀錄

## 1. 問題概述與根因分析

### 問題背景
在既有實作中，`capacity_controller.py` 的 `capacity_snapshot` 使用靜態集合 `RUNNABLE_TASK_STATUSES = {"todo", "in_progress", "review", "review_approved"}` 來計算可執行任務數（`runnable_tasks`）。

這導致了嚴重的假陽性（false positive）計數：
1. **未完成依賴（Dependency Blocked）**：狀態為 `todo` 但其前置任務尚未完成（`depends_on` 未滿足）的任務被誤計為 runnable。
2. **人工守門（Human/Ops Gate）**：指派給 `Human/Ops` 或標記為 `human_gate` 的任務無法由 AI Supervisor 自動派發，卻被計為 runnable。
3. **專屬流程狀態（Review / Finalize）**：`review`（等待評審）與 `review_approved`（等待 PR 合併）屬於專屬生命週期狀態，非一般 owner 執行任務，卻被計入 runnable，導致誤觸發 `runnable_work_without_active_workers`。
4. **阻塞狀態（Blocked）與非派發（non_dispatchable）**：相關任務不應被視為可執行任務。

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

沿用 Supervisor 現有派發與依賴真實來源（`supervisor.py`、`dispatch_policy.py`、`task_archive.TaskResolver`、`worker_failure_policy.py`），不新增第二套排程器：

1. **`is_task_runnable` 述詞標準化**：
   - 排除 `non_dispatchable: True`。
   - 排除 Human Gate 任務（`task_is_human_gate(task)`、`is_human_gate_agent(owner)`、`is_human_gate_agent(waiting_for)`）。
   - 排除 Sidecar 輔助任務（`task_is_sidecar(task)`，確保只計入 canonical product work）。
   - 限制狀態必須在 owner 可執行狀態集（`owned_statuses`，預設 `{"todo", "in_progress"}`），排除 `review`、`review_approved`、`blocked` 等。
   - 透過 `TaskResolver` 與 `dependencies_satisfied` 驗證 `depends_on` 依賴是否皆已達 `done` 狀態。
   - 驗證 owner 是否為可派發代理人（`agent_can_take_task`）。

2. **`capacity_snapshot` 整合**：
   - 使用 `TaskResolver(tasks)` 批次解析依賴關聯，精確過濾出真正 runnable 的任務集合。

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
8. `CapacityControllerReconciliationTests`：驗證 Supervisor 層級的 `reconcile_capacity_controller` 狀態與決策寫入一致性。

### 3.3 測試輸出實錄
```text
.orchestrator/test_capacity_controller.py .......                        [100%]
.orchestrator/test_supervisor.py ..........                              [100%]

============================== 17 passed in 7.42s ===============================
```
全部 17 項相關測試 100% 通過。
