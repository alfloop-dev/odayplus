# Evidence Note: ORCH-CAPACITY-ARCHIVE-DEDUP-001

## 任務摘要
- **Task ID**: `ORCH-CAPACITY-ARCHIVE-DEDUP-001`
- **Owner**: `Antigravity4`
- **Reviewer**: `Codex`
- **目標**: 修正既有 Capacity Sidecar 生成器對已封存（`done`/`superseded`）任務的去重缺口，重用既有 `task_archive.load_archived_task` 與 canonical CAS，防止已完成診斷 sidecars（如 `ODP-EPHEMERAL-STAGING-ROLLOUT-0-SIDECAR-C1E25549`、`ODP-DEV-LIVE-ROLLOUT-REMEDIATIO-SIDECAR-7CC5581A`、`ODP-STAGING-FOUNDATION-IAC-REME-SIDECAR-D7ED3693`）因 active-only 去重而在 parent 處於 blocked 時重複重生。

---

## 1. 根因分析 (Root Cause)
在 `dev` base（commit `64f3b239`）中：
1. `capacity_controller.sidecar_candidates` 僅檢查 active 任務清單中的 sidecar 簽名（`existing_signatures = {f"{task.get('helper_parent')}:{task.get('helper_kind')}" for task in existing}`）。
2. `supervisor.reconcile_capacity_controller` 僅在 CAS 寫入前比對 active 任務 ID（`known = {str(task.get(task_id_field) or task.get("id") or "") for task in tasks}`）。
3. 當診斷 sidecar 完成並封存至 `ai-task-archive/tasks/<task_id>.json` 後，會從 `ai-status.json` 的 active 任務清單中移除。若其 parent 任務仍處於 `blocked` 狀態，下一個 Capacity Chair 評估週期會再度生成相同的 deterministic sidecar ID，並佔用 wave budget。
4. `reconcile_capacity_controller` 隨後將重複的 sidecar 寫入 `ai-status.json`，導致已合併（PR #1066、#1111、#1067）的任務被重新指派或覆寫舊驗證收據。

---

## 2. 既有機制復用與修正設計 (Fix & Mechanism Reuse)
1. **重用 Canonical Task Archive 讀取**:
   - 直接調用 `task_archive.load_archived_task(task_id)` 進行 O(1) 的 canonical archive 查找。
   - 不使用 active-first 的 `TaskResolver.get/snapshot`，避免 active 狀態遮蔽 archive。
   - 不新增任何黑名單（blacklist）、額外設定檔、state store 或第二個 scheduler。
2. **生成器端去重與 Wave Budget 保護 (`capacity_controller.sidecar_candidates`)**:
   - 在計算出 deterministic sidecar ID（`build_sidecar_task_id(parent_id, kind)`）後，立即檢查 `if sidecar_id in existing_ids or load_archived_task(sidecar_id) is not None:`。
   - 若為 active 或已封存任務（無論是 `completed` 還是 `superseded`），直接跳過（`continue`），不加入 `candidates`，且**不消耗 wave budget**。
   - 確保混合 archived 與 fresh 候選時，合法 fresh 任務仍能獲得完整的 wave budget。
3. **CAS 提交端雙重過濾與 Log 防護 (`supervisor.reconcile_capacity_controller`)**:
   - 在 CAS 提交路徑（`commit_canonical_task_transition`）前，再度以 `load_archived_task` 過濾 `additions`，防禦候選生成後到寫入期間的競態（race condition）。
   - 被排除的任務不得提交至板上，亦不得記錄 `capacity_sidecar_created` activity log。

---

## 3. 未做事項 (Out of Scope / Non-Goals)
- 未修改既有已封存任務的 JSON 內容或 sidecar markdown 產物。
- 未修改 deterministic sidecar ID 生成演算法（長度上限 48 字元、SHA-256 digest 格式保持不變）。
- 未更動 private live config、不直接重啟或部署 Supervisor（由 runtime rollout 統一管理）。

---

## 4. 驗證與回歸測試 (Verification & Regression Coverage)
執行測試指令：
`uv run --python 3.12 pytest .orchestrator/test_capacity_controller.py .orchestrator/test_supervisor.py -k capacity`

通過的測試案例包含：
1. `test_sidecar_candidates_excludes_archived_three_exact_ids_across_multiple_rounds`:
   - 重現 Acceptance criteria 明列之 3 個 exact IDs：
     - `ODP-EPHEMERAL-STAGING-ROLLOUT-0-SIDECAR-C1E25549`
     - `ODP-DEV-LIVE-ROLLOUT-REMEDIATIO-SIDECAR-7CC5581A`
     - `ODP-STAGING-FOUNDATION-IAC-REME-SIDECAR-D7ED3693`
   - 驗證在 parent 處於 blocked 且 active 清單無 sidecar 時，連續 5 輪均不重建。
2. `test_sidecar_candidates_excludes_superseded_archived_sidecars`:
   - 驗證 `terminal_outcome: "superseded"` 封存 sidecar 同樣被排除。
3. `test_mixed_archived_and_fresh_candidates_preserves_full_wave_budget_for_fresh`:
   - 驗證 2 個封存 parent + 2 個 fresh parent 在 wave budget 為 2 時，2 個 fresh 任務取得全部 budget。
4. `test_reconcile_capacity_controller_excludes_archived_sidecars_and_writes_no_logs`:
   - 驗證 `reconcile_capacity_controller` 排除封存任務，不更新 status 且不寫入 `capacity_sidecar_created` log。
5. `test_reconcile_capacity_controller_filters_sidecar_archived_before_cas_commit`:
   - 驗證候選生成後、寫板前出現封存時的防禦與 log 靜默。

---

## 5. 風險與回滾計畫 (Risk & Rollback)
- **風險評估**: 
  - `load_archived_task` 依賴檔案系統直接路徑查找（`ARCHIVE_TASKS_DIR / f"{slug}.json"`），時間複雜度為 O(1)，不會隨封存數量增長而退化。
  - 不影響現有 human gate、依賴滿足、role policy 或 active worker 追蹤機制。
- **回滾計畫**:
  - 若需回滾，只需 revert 此 task branch 之 commit，即可回到 active-only 去重邏輯，不影響其他控制面狀態或核心契約。
