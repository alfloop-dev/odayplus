# ODP-ORCH-QUOTA-FENCE-HANDOFF-001

## 1. 任務背景與審查根因 (Incident Background & Review Findings)

- **任務 ID**: `ODP-ORCH-QUOTA-FENCE-HANDOFF-001`
- **任務標題**: 修復 quota 同帳號停派後的修改保全與續接 (Shared-Account Quota Fencing Dirty Worktree Preservation & Successor Handoff Recovery)
- **執行身分 (Owner)**: `Antigravity3`
- **指派審查者 (Reviewer)**: `Codex2`
- **交付檔案**:
  - `.orchestrator/worker_failure_policy.py`
  - `.orchestrator/worker_lifecycle.py`
  - `.orchestrator/supervisor.py`
  - `.orchestrator/test_worker_failure_policy.py`
  - `docs/evidence/execution-control/ODP-ORCH-QUOTA-FENCE-HANDOFF-001/`

### 審查根因分析 (Review Feedback & Root Cause Analysis)
在共用帳號 pool（如 `antigravity_main` 含 `antigravity`, `antigravity2` 等多個 dispatch slots）發生 quota 耗盡時：
1. **[P1] SIGTERM 延遲退出與提早改派 (Delayed Shutdown & Premature Actor Change)**:
   - `terminate_worker_pid` 僅發送 SIGTERM 信號。若 sibling process 仍在關閉退出中，立即檢查 `pid_is_alive` 會回傳 True。
   - 原實作者略過 worktree 保全，但仍提早調用 `maybe_reassign_task_after_worker_failure` 將 owner 改派為 `Codex`，並將 worker 狀態標記為終止態（`reassigned`/`failed`），且結案 queue event。
   - 後續 `poll_workers` 巡檢時命中 `TERMINAL_WORKER_STATUSES` fast path，略過後續保全，導致 dirty worktree 無法封裝 seal，繼承者面臨 `owner_dirty` 派工停滯。
   - **修復方案**: 在確定程序消亡前，禁止變更 task owner 與 worker/queue 終止態；設置 retryable `pending_fence`，於 `poll_workers` 偵測程序確已死亡後才執行保全與改派。
2. **[P1] 忽略 QuarantineOutcome 失敗導致孤立 (QuarantineOutcome Failure Handling)**:
   - 原實作忽略 `preserve_dead_worker_worktree` 回傳的 `QuarantineOutcome`。當 dirty worktree 備份發生 `backup_write_failed` 或 `preserve_raised` 時，仍盲目將 task 改派至 `Codex`。
   - 由於未能建立有效 seal，繼承者（`Codex`）派工時將永遠無法認領該 dirty worktree，造成任務永久停滯。
   - **修復方案**: 區分 clean 與 dirty 狀態；若 dirty worktree 保全失敗，**嚴格禁止改派 task 責任**，保留原 owner 的 retryable/blocker recovery，避免繼承者被孤立。
3. **驗證邊界與真實 Git 租借差距 (Acceptance & Lease Gaps)**:
   - 補充驗證物理備份目錄（`manifest.json`、`staged.patch`、`unstaged.patch`、`untracked/` 檔案與 `backup_checksums.sha256`）。
   - 測試透過真實 `prepare_worker_workspace` 租借入口驗證 `sealed_owner_dirt` 權限。
   - 補齊 alive -> terminating -> dead 順序、failed termination 負例、以及 real backup failure 負例。

---

## 2. 修復設計與控制流程 (Architecture & Fix Design)

### (1) 延遲改派與 Pending Fence 機制 (Deferred Reassignment & Pending Fence)
在 [`.orchestrator/worker_failure_policy.py`](file://.orchestrator/worker_failure_policy.py) 的 `fence_account_pool_workers` 中：
- 向同 pool 的 sibling worker process 發送 SIGTERM (`terminate_worker_pid(pid)`)。
- 若 process 仍在存活中（`pid_is_alive(pid)` 為 True）：
  - 記錄 `sibling["pending_fence"] = {"pool_id": pool_id, "reason": reason, "fenced_at": utc_now()}`。
  - **不修改 task owner**（保留原 owner）。
  - **不修改 worker 狀態為終止態**（保留 active `running` 狀態）。
  - **不結案 queue event**（保留 `started` 狀態）。
- 在 [`.orchestrator/worker_lifecycle.py`](file://.orchestrator/worker_lifecycle.py) 的 `poll_workers` 中：
  - 巡檢時若 worker 包含 `pending_fence`，再次嘗試 `terminate_worker_pid(pid)`。
  - 當確認 `not pid_is_alive(pid)` 時，調用 `_settle_fenced_sibling_worker` 進行保全與改派。

### (2) 先保全後改派與 QuarantineOutcome 檢查 (QuarantineOutcome Guard & Settle)
在 `_settle_fenced_sibling_worker` 中：
- 調用 `preserve_dead_worker_worktree(config, state, sibling, task=task_record, trigger="sibling_fenced")` 獲取 `QuarantineOutcome`。
- **正向路徑（Preservation Succeeded 或 Clean Worktree）**:
  - 若 `outcome.preserved` 為 True，或 `outcome.reason in {"worktree_clean", "nothing_to_preserve", "worker_had_no_workspace", "no_worktree_path"}`：
  - 調用 `maybe_reassign_task_after_worker_failure` 改派 task owner 至 `Codex`。
  - 若有 unsealed handoff block，同步轉移完整溯源：
    ```python
    handoff_block["original_owner"] = handoff_block.get("original_owner") or handoff_block.get("owner") or owner
    handoff_block["owner"] = new_owner
    handoff_block["authorized_successor"] = new_owner
    handoff_block["transferred_from"] = owner
    handoff_block["transferred_to"] = new_owner
    handoff_block["transfer_reason"] = reason
    handoff_block["transferred_at"] = utc_now()
    handoff_block["transfer_source_run_id"] = str(worker.get("run_id") or "")
    ```
  - 更新 sibling 狀態為 `reassigned`，結案 queue event 為 `completed`。
- **負向防護路徑（Preservation Failed on Dirty Worktree）**:
  - 若 `not outcome.preserved` 且非 clean 狀態（如 `backup_write_failed`, `preserve_raised` 等）：
  - **不調用 `maybe_reassign_task_after_worker_failure`**（Task owner 保留原 owner `Antigravity2`）。
  - Sibling 狀態設為 `failed`（`reassigned_to = None`）。
  - Queue event 標記為 `failed`。
  - 記錄 activity log 說明保全失敗與原因，保留原 owner 恢復與重試途徑。

### (3) 端到端 prepare_worker_workspace 租借續接 (Successor Lease Entry)
- 合法繼承者（`Codex`）透過 `prepare_worker_workspace` 請求 isolated worktree 租借：
  - `_refresh_reused_worker_worktree` 發現 `skipped_dirty_worktree:owner_dirty`。
  - 調用 `sealed_owner_continuation_allowed` 驗證：
    - `owner == target_agent == record["owner"]`（Codex）。
    - Worktree 路徑、分支名稱、HEAD SHA、dirt fingerprint 均一致。
  - `prepare_worker_workspace` 成功返回 `(True, None)` 並注入 `worktree_continuation: "sealed_owner_dirt"`。

---

## 3. 端到端真實 Temporary Git 回歸測試 (Test Suite)

在 [`.orchestrator/test_worker_failure_policy.py`](file://.orchestrator/test_worker_failure_policy.py) 中 `QuotaSiblingFencingDirtyHandoffTests` 測試類別包含 12 支端到端 regression 測試：

| 測試名稱 | 涵蓋面向 | 預期結果 |
|---|---|---|
| `test_sibling_quota_fence_preserves_dirty_worktree_and_authorizes_successor_lease_continuation` | E2E：包含 staged、unstaged、untracked 變更，驗證備份目錄物理檔案、manifest、patch、checksums，改派至 Codex，並透過真實 `prepare_worker_workspace` 成功租借續接與提交 | PASS |
| `test_sibling_quota_fence_alive_terminating_dead_ordering` | 順序性：Alive (SIGTERM) -> Terminating (pending fence, 狀態與 owner 不變) -> Dead (poll_workers 觸發保全、改派與結案) | PASS |
| `test_sibling_quota_fence_failed_termination_preserves_active_state_without_reassign` | 負例：終止失敗（process 持續存活）時保留 active 與原 owner，禁止提早改派 | PASS |
| `test_sibling_quota_fence_backup_failure_refuses_reassignment_and_leaves_owner_unchanged` | 負例：Dirty worktree 備份失敗（`backup_write_failed`）時拒絕改派 owner，後續繼承者 `prepare_worker_workspace` 被拒絕以防孤立 | PASS |
| `test_sibling_quota_fence_refuses_preservation_while_writer_is_still_alive` | 負例：Active writer 存活時拒絕封裝 handoff seal | PASS |
| `test_authorized_successor_refuses_foreign_agent_or_unauthorized_owner` | 負例：未授權第三代理人（Claude）無法透過 `prepare_worker_workspace` 認領 dirty worktree | PASS |
| `test_authorized_successor_refuses_reviewer_guard` | 負例：Reviewer（`review_ready_dispatch`）永遠禁止繼承 dirty worktree | PASS |
| `test_authorized_successor_refuses_helper_guard` | 負例：Helper（`helper_claim`）永遠禁止繼承 dirty worktree | PASS |
| `test_authorized_successor_refuses_when_head_sha_drifted` | 負例：封裝後 HEAD SHA 漂移拒絕 lease 續接 | PASS |
| `test_authorized_successor_refuses_when_dirt_fingerprint_drifted` | 負例：封裝後 dirty 內容變更拒絕 lease 續接 | PASS |
| `test_authorized_successor_refuses_when_branch_mismatched` | 負例：派工 branch 不符拒絕 lease 續接 | PASS |
| `test_sibling_quota_fence_without_reassignment_allows_same_owner_continuation` | 正例：未改派時原 owner 保留 seal，冷卻後由原 owner 透過 `prepare_worker_workspace` 成功續接 | PASS |
| `test_sibling_quota_fence_clean_worktree_reassigns_without_handoff_seal` | 正例：Clean worktree 正常改派且不封裝 seal，繼承者透過 `prepare_worker_workspace` 成功租借 | PASS |

---

## 4. 驗證命令與執行收據 (Verification Commands & Receipts)

### (1) Focused Pytest Verification
- **Command**: `uv run pytest -q .orchestrator/test_worker_failure_policy.py -k "fence or handoff or quota"`
- **Exit Code**: `0`
- **Output**: `............................ [100%]` (28 passed, 41 deselected)
- **Duration**: ~8.8s

### (2) Ruff Lint & Style Verification
- **Command**: `uv run ruff check .orchestrator/worker_failure_policy.py .orchestrator/worker_lifecycle.py .orchestrator/test_worker_failure_policy.py .orchestrator/supervisor.py`
- **Exit Code**: `0`
- **Output**: `All checks passed!`
- **Duration**: ~0.08s

### (3) Git Diff & Whitespace Verification
- **Command**: `git diff --check`
- **Exit Code**: `0`
- **Output**: `""` (Clean, no trailing whitespace or conflict markers)
- **Duration**: ~0.04s

---

## 5. 治理邊界與不變量 (Governance & Boundary Invariants)

1. **不修改產品程式碼**: 變更嚴格局限於 `.orchestrator/` 及 `docs/evidence/`。
2. **不 Hot-patch 或重啟 Live Supervisor**: 未修改 live canonical runtime 或 supervisor 實體程序。
3. **隔離 PR #1325 審查邊界**: 避免修改 `worker_workspace.py`，保持與在途 PR 獨立不衝突。
4. **完整 Handoff Provenance 追溯性**: 嚴格保留 `original_owner`, `authorized_successor`, `transferred_from`, `transferred_to`, `transfer_reason`, `transferred_at`, `head_sha`, `dirt_fingerprint`。
