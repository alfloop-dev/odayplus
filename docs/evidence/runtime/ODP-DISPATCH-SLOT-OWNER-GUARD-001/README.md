# 執行佐證：防止實體 dispatch slot 被當成任務 owner 而永久無法派工 (ODP-DISPATCH-SLOT-OWNER-GUARD-001)

- 任務 ID: `ODP-DISPATCH-SLOT-OWNER-GUARD-001`
- 執行身分: `Antigravity3`
- 審查者: `Codex`
- 日期: 2026-09-02

---

## 1. 問題根因分析 (Root Cause Analysis)

在 Supervisor 架構中：
- 實體 dispatch slot（例如 `antigravity_slot_1`、`claude_slot_1`、`codex_bjoe_slot_1`）屬於進程容量資源（Process Capacity Lease），負責提供執行槽位。
- 邏輯 worker（例如 `Antigravity`、`Claude`、`Codex`）才是可被指派並具備派工邏輯的 Actor。
- Supervisor 的 Dispatch Engine 巡覽清單 `dispatch_loop_agent_ids(config)` 會明確排除 `agent_is_dispatch_slot(agent)`，只巡覽邏輯 worker。
- 若任務的 `owner` 或 `reviewer` 被設定為實體 dispatch slot，Supervisor 的巡覽迴圈將永遠無法比對到該任務，導致任務永久卡在 `todo` 狀態。

---

## 2. 修正方案與架構保證 (Remediation Design)

### 2.1 新指派 Fail-Fast 與可行動錯誤提示
在 `scripts/ai_status.py` 中：
1. `is_dispatch_slot_config` 與 `resolve_dispatch_slot_info`：正確識別具有 `dispatch_slot_for` 或 `dispatch_slot_for_pool` 的 slot 配置。
2. `configured_agent_names`：將實體 dispatch slot 從合法可指派 worker 清單排除。
3. `resolve_actor_reference`：在接收 caller 輸入（如 `assign`、`AI_NAME`、`handoff`、`blocked`）時，若輸入為 dispatch slot，立即以 `SystemExit` 中斷，輸出可行動錯誤，提示對應的邏輯 worker（如 `Specify logical worker 'Antigravity' instead.`）或帳號池（如 `Specify a logical worker from account pool 'claude_main' instead.`），防止寫入不一致資料。

### 2.2 既有 Open Task 完整性自動收斂
在 `.orchestrator/supervisor.py` 與 `.orchestrator/worker_failure_policy.py` 中：
1. `task_actor_assignment_block_reason`：若 actor 為 dispatch slot，判定為 `actor <name> is a dispatch slot`。
2. `task_assignment_integrity_issues`：將 slot owner / reviewer / waiting_for 標記為 `owner_unavailable` / `reviewer_unavailable` / `waiting_for_unavailable`。
3. `get_agent_reassignment_candidates`：針對 dispatch slot，優先提取其 `dispatch_slot_for` 所屬邏輯 worker 或 `dispatch_slot_for_pool` 所屬帳號池的健康 logical agents。
4. `normalize_task_assignment_integrity`：透過既有的完整性修復管線，自動將 slot 轉移為健康的邏輯 worker，並確保 reviewer 屬於獨立帳號池，寫入 audit activity log，無需新增第二套派工或 retry 迴圈。
5. `known_agent_display_names` 與 `agent_can_take_task`：過濾掉 dispatch slot，確保候選人與執行端皆為邏輯 worker。

---

## 3. 測試與驗證結果 (Verification & Regression Proof)

### 3.1 測試套件執行結果
1. `python3 -m unittest scripts/test_ai_status.py`
   - 執行 215 項測試，全數通過 (OK)。
   - 包含新增之 `test_dispatch_slots_are_excluded_from_configured_agents_and_rejected_on_assignment`，驗證 CLI/assign fail-fast 與錯誤提示。

2. `PYTHONPATH=.orchestrator python3 -m unittest discover -s .orchestrator -p "test_supervisor.py"`
   - 執行 549 項測試，全數通過 (OK)。
   - 包含新增之 `test_assignment_integrity_flags_and_reassigns_dispatch_slot_owner_and_reviewer` 與 `test_assignment_integrity_does_not_create_dispatch_loop_for_slot_owner`，驗證 slot owner/reviewer 完整性審計、收斂至邏輯 worker、帳號池獨立性及派工不卡 todo。

### 3.2 驗證指令紀錄
```bash
python3 -m unittest scripts/test_ai_status.py
PYTHONPATH=.orchestrator python3 -m unittest discover -s .orchestrator -p "test_supervisor.py"
```
測試結果：全部通過，無失敗或錯誤。
