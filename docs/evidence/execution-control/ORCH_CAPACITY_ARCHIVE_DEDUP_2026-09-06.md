# Evidence Note: ORCH-CAPACITY-ARCHIVE-DEDUP-001

## 任務摘要
- **Task ID**: `ORCH-CAPACITY-ARCHIVE-DEDUP-001`
- **Owner**: `Claude`（於 2 次 reviewer reopen 後自 `Antigravity4` 接手；production 去重由前任完成並經 review 保留）
- **Reviewer**: `Codex`
- **目標**: 修正既有 Capacity Sidecar 生成器對已封存（`done`/`superseded`）任務的去重缺口，重用既有 `task_archive.load_archived_task` 與 canonical CAS，防止已完成診斷 sidecars（如 `ODP-EPHEMERAL-STAGING-ROLLOUT-0-SIDECAR-C1E25549`、`ODP-DEV-LIVE-ROLLOUT-REMEDIATIO-SIDECAR-7CC5581A`、`ODP-STAGING-FOUNDATION-IAC-REME-SIDECAR-D7ED3693`）因 active-only 去重而在 parent 處於 blocked 時重複重生。

---

## 1. 根因分析 (Root Cause)
在 `dev` base（commit `64f3b239`）中：
1. `capacity_controller.sidecar_candidates` 僅檢查 active 任務清單中的 sidecar 簽名（`existing_signatures = {f"{task.get('helper_parent')}:{task.get('helper_kind')}" for task in existing}`）。
2. `supervisor.reconcile_capacity_controller` 僅在 CAS 寫入前比對 active 任務 ID（`known = {str(task.get(task_id_field) or task.get("id") or "") for task in tasks}`）。
3. 當診斷 sidecar 完成並封存至 `ai-task-archive/tasks/<task_id>.json` 後，會從 `ai-status.json` 的 active 任務清單中移除。若其 parent 任務仍處於 `blocked` 狀態，下一個 Capacity Chair 評估週期會再度生成相同的 deterministic sidecar ID，並佔用 wave budget。
4. `reconcile_capacity_controller` 隨後將重複的 sidecar 寫入 `ai-status.json`。在實際運行觀察中，此行為會造成已封存任務在 active 清單中重生並持續佔用 wave budget；而在後續 dispatch 階段，雖被既有 archive ambiguity gate 阻擋拒絕 dispatch，未觀察到實際 worker 執行或覆寫歷史收據，但持續重生會污染 active 清單，且存在重新指派已合併任務（PR #1066、#1111、#1067）或覆寫舊收據之潛在風險。

---

## 1.5 第二個根因：測試會刪除它所指向的 canonical archive（Codex P1）

前一輪 review（reopen #2）指出的缺陷不在 production code，而在本任務新增的測試：

1. `task_archive` 於 **import 時**一次性解析 `STATUS_ROOT` / `ARCHIVE_DIR` / `ARCHIVE_TASKS_DIR` / `ARCHIVE_INDEX_FILE`（`task_archive.py:28-31`），來源是當下環境變數。
2. pytest 依參數順序先載入 `test_capacity_controller.py:6` → `capacity_controller.py:9` → `task_archive`，此時綁定的是 **worker 當下的 live coordination root**。
3. `test_supervisor.py:33-34` 隨後才改寫 env，但 `task_archive` 已在 `sys.modules` 中，globals **不會**重算；而 capacity 套件的 scoped fixture 在退場時又把 globals 還原成那個 live root。
4. 於是 `CapacityControllerReconciliationTests.setUp/tearDown` 對 `task_archive.ARCHIVE_TASKS_DIR` 直接 `shutil.rmtree`、對 `ARCHIVE_INDEX_FILE` 直接 `unlink`，刪掉的是**真實的 canonical archive**。

「archive globals 指向哪個 root」是一個 **import order 屬性**，不是單一 module 內可觀察的性質，因此該子集自己全綠也看不出這個破壞。

### 獨立重現（本輪，使用拋棄式 fake canonical root，全程未觸碰 live archive）

於 `PANTHEON_STATUS_ROOT` / `ORCH_STATUS_ROOT` 指向一個預置了 sentinel 封存任務與 index 的 fake root，依序執行
`test_capacity_controller.py::test_sidecar_candidates_excludes_archived_three_exact_ids_across_multiple_rounds`
與
`test_supervisor.py::CapacityControllerReconciliationTests::test_reconcile_capacity_controller_excludes_archived_sidecars_and_writes_no_logs`：

```
BEFORE: SENTINEL-DO-NOT-DELETE-001.json  index=present
2 passed in 0.93s
PYTEST_EXIT=0
AFTER: tasks_dir=MISSING sentinel=DELETED index=DELETED
```

測試全綠、exit 0，但預置的 archive 連同 tasks 目錄與 index 一併消失。

### 修正
`CapacityControllerReconciliationTests.setUp` 改為自建 `TemporaryDirectory`，以 `mock.patch.object` 局部覆寫上述四個 `task_archive` global，並以 `addCleanup` 還原 globals、只清理自建目錄。**移除**對既有 archive globals 的 `rmtree` / `unlink`：這些測試現在最多只能刪掉自己的暫存目錄。

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
`uv run --python 3.12 pytest .orchestrator/test_capacity_controller.py .orchestrator/test_supervisor.py .orchestrator/test_role_provider_policy.py -k "capacity or role_provider"`

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
   - 在有效 chair 與正 wave budget（8 slots × 0.25 = 2）條件下，驗證 `reconcile_capacity_controller` 排除封存任務，不更新 status 且不寫入 `capacity_sidecar_created` log。
5. `test_reconcile_capacity_controller_generates_and_commits_sidecar_when_not_archived`:
   - 對照測試：在相同有效 chair 與正 wave budget 條件下，當任務未封存時，確認 sidecar 正常生成、CAS 提交並記錄 activity log。
6. `test_reconcile_capacity_controller_filters_sidecar_archived_before_cas_commit`:
   - 驗證候選生成後、寫板前出現封存時的防禦與 log 靜默（race condition 防禦）。
7. `CanonicalArchiveTestIsolationTests::test_capacity_then_supervisor_reconcile_leaves_a_preexisting_archive_intact`（本輪新增）:
   - 在獨立 interpreter 中，以指向拋棄式 canonical root 的 env 依「先 capacity 後 supervisor」的真實載入順序執行兩個既有 case，並斷言預置的 sentinel 封存任務與 index 於事後仍**逐位元組相同**。
   - 這是一個 import-order 屬性，單一 module 內的斷言無法觀察，因此以 subprocess 形式釘住。
8. 跨 Module 隔離回歸：
   - `test_capacity_controller.py` 使用 scoped fixture，不污染環境，同 process 連跑 `test_role_provider_policy.py` 通過。

### 本輪執行收據

| 驗證 | 命令 / 條件 | 結果 |
| --- | --- | --- |
| 重現（修正前） | fake canonical root + 兩個 case | `2 passed`、`PYTEST_EXIT=0`，但 `sentinel=DELETED index=DELETED` |
| 新回歸（修正後） | `pytest .orchestrator/test_supervisor.py::CanonicalArchiveTestIsolationTests` | `1 passed in 3.58s`、exit 0 |
| 新回歸（負向對照） | 暫時還原具破壞性的 `setUp` 後重跑同一測試 | `1 failed`、exit 1，訊息為 `the capacity suites deleted the archive tasks directory of the root they were pointed at`；內層子集本身仍 exit 0 |
| 焦點子集 | `uv run --python 3.12 pytest .orchestrator/test_capacity_controller.py .orchestrator/test_supervisor.py .orchestrator/test_role_provider_policy.py -k "capacity or role_provider"` | `165 passed, 584 deselected in 14.11s`、exit 0 |
| live archive 完整性 | 上列焦點子集在**繼承 live root** 的環境執行，前後比對 `sha256sum` | 4 個檔案雜湊完全一致（`LIVE_ARCHIVE_UNCHANGED`） |
| Lint | `uv run --python 3.12 ruff check`（4 個受影響檔案） | `All checks passed!`、exit 0 |

負向對照是這輪的關鍵證據：**內層子集在有缺陷的程式碼上依然 exit 0**，只有新回歸會轉紅，證明它擋的是原本無法被觀察到的破壞。

---

## 5. 風險與回滾計畫 (Risk & Rollback)
- **風險評估**: 
  - `load_archived_task` 依賴檔案系統直接路徑查找（`ARCHIVE_TASKS_DIR / f"{slug}.json"`），時間複雜度為 O(1)，不會隨封存數量增長而退化。
  - 不影響現有 human gate、依賴滿足、role policy 或 active worker 追蹤機制。
  - 新增的隔離回歸會啟動一個子 interpreter 執行兩個 case（實測約 3.6 秒），成本可忽略；若未來 case 名稱變更，該測試會以明確的 node id 失敗而非靜默跳過。
  - 本輪未改動 production code，`capacity_controller` 與 `supervisor` 的兩處去重維持前一輪 review 已認可的樣態。
- **回滾計畫**:
  - 若需回滾，只需 revert 此 task branch 之 commit，即可回到 active-only 去重邏輯，不影響其他控制面狀態或核心契約。
