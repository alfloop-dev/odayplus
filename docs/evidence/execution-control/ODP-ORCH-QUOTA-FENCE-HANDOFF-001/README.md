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

### 審查根因分析與回饋修復 (Review Feedback & Root Cause Analysis)
在共用帳號 pool（如 `antigravity_main` 含 `antigravity`, `antigravity2` 等多個 dispatch slots）發生 quota 耗盡時：
1. **[P1] 來源溯源校驗與防範跨責任汙染 (Foreign/Stale Handoff Block Laundering Guard)**:
   - **問題**: `maybe_reassign_task_after_worker_failure` 原先無條件覆寫 `handoff_blocks` 中的 `owner` 為 `new_owner`。若先前存在其他 owner（如 Claude）留下的封裝 seal，而當前發生無工作區派工失敗（如 Antigravity2 dispatch failure），會將前任的未提交修改洗入新繼承者（Codex），破壞工作區隔離與安全界限。
   - **修復方案**: 在 `maybe_reassign_task_after_worker_failure` 中嚴格校驗來源責任、執行 ID（`run_id`）、工作區路徑（`workspace_path`）與分支（`workspace_branch`）。僅當當前失敗 worker 為該 handoff block 的真實來源且工作區匹配時才允許轉移 seal；無工作區或不匹配的失敗禁止改寫任何 seal。
2. **[P1] 遞迴程序樹、跨 Reparenting 寫入者追蹤與 Open FD 掃描 (Process Tree, Reparenting & Open FD Writer Shutdown)**:
   - **問題**: 原先 writer discovery 在父程序退出後因孫進程被 reparent 至 init/subreaper，且其 cwd 在工作區外部時，無法再透過父子鏈或 cwd 重新發現該進程，導致在孫進程仍存活且持有 open fd 時即過早保全與改派，繼承者租借後被殘留孫進程寫入導致 fingerprint 漂移。
   - **修復方案**:
     - 在 `worker_writer_pids` 中新增 durable tracking（`tracked_writer_pids`，持久化至 worker 記錄與 `pending_fence`），在程序樹終止前記錄所有發現的進程 PID，即便中間父進程退出、子進程 reparent，原 PID 仍持續被追蹤直到確認終亡。
     - 掃描 `/proc/<pid>/fd/*` 符號連結，識別所有持有工作區目錄或檔案 open file descriptor 的進程，即便 cwd 在外部亦能完整捕獲。
     - 於 `fence_account_pool_workers`、`poll_workers` 及 `_settle_fenced_sibling_worker` 中強制要求所有寫入進程完全消亡後方可執行保全與責任轉移；若寫入進程存活則 fail closed，維持 `pending_fence`。
3. **[P2] Boot Reconciliation、Terminal Fast Path 與 Retry 優先順序修復 (Boot Reconciliation & Pending-Fence Precedence)**:
   - **問題**:
     - 當 dirty worktree 備份發生暫態失敗（如 `backup_write_failed`）時，若 supervisor 重啟觸發 `reconcile_runtime_on_boot`，原實作將該 worker 視為一般 missing process 判定 terminal `failed` 並結案 queue event，導致重啟後 `poll_workers` 因 terminal fast path 跳過 pending fence，遺棄 dirty worktree 封裝。
     - `retry_due_workers` 與 orphaned queue cleanup 先於 pending fence 執行，可能繞過保全義務。
   - **修復方案**:
     - 在 `reconcile_runtime_on_boot` 開頭優先處理 `pending_fence`，若進程存活則發送信號，若已消亡則重試 `_settle_fenced_sibling_worker`；若保全仍受阻則維持 `pending_fence` 與 `status: running`，不提前標記 failed 或結案 queue。
     - 在 `poll_workers` 巡檢迴圈最頂端優先處理 `pending_fence`，使其高於 `TERMINAL_WORKER_STATUSES` fast path 與 orphaned queue cleanup。
     - 在 `retry_due_workers`、`prune_worker_records` 與 `compact_worker_history` 中增加 `pending_fence` 防護，確保受 fence 影響之 worker 在封裝結案前不被提早重試或壓縮丟棄。
     - 暫態檔案系統問題排除後，後續 `poll_workers` 成功建立 seal 並完成合法改派與租借。

---

## 2. 修復設計與控制流程 (Architecture & Fix Design)

### (1) 來源溯源與 Handoff Seal 轉移校驗
在 [`.orchestrator/worker_failure_policy.py`](file://.orchestrator/worker_failure_policy.py) 的 `maybe_reassign_task_after_worker_failure` 中：
```python
handoff_block = ((state.get("worker_worktrees") or {}).get("handoff_blocks") or {}).get(task_id)
worker_run_id = str(worker.get("run_id") or "")
worker_workspace_path = str(worker.get("workspace_path") or "")
worker_workspace_branch = str(worker.get("workspace_branch") or "")
if (
    isinstance(handoff_block, dict)
    and worker_run_id
    and worker_workspace_path
    and normalize_agent_id(str(handoff_block.get("owner") or "")) == normalize_agent_id(owner)
    and str(handoff_block.get("source_run_id") or "") == worker_run_id
    and str(handoff_block.get("workspace_path") or "") == worker_workspace_path
    and (not worker_workspace_branch or str(handoff_block.get("workspace_branch") or "") == worker_workspace_branch)
):
    handoff_block["original_owner"] = handoff_block.get("original_owner") or handoff_block.get("owner") or owner
    handoff_block["owner"] = new_owner
    handoff_block["authorized_successor"] = new_owner
    handoff_block["transferred_from"] = owner
    handoff_block["transferred_to"] = new_owner
    handoff_block["transfer_reason"] = reason
    handoff_block["transferred_at"] = utc_now()
    handoff_block["transfer_source_run_id"] = worker_run_id
```

### (2) 程序樹掃描、跨 Reparenting 追蹤與完整消亡保證
- 提供 `worker_writer_pids`、`worker_writers_are_alive` 與 `terminate_worker_writers`：
  - 整合 wrapper PID、child PID、`tracked_writer_pids` 以及 `/proc` 遞迴子程序樹。
  - 掃描 `/proc/<pid>/cwd` 及 `/proc/<pid>/fd/*` 符號連結，識別所有指向工作區路徑的進程。
  - 發現的存活 PID 自動回寫至 `worker["tracked_writer_pids"]` 與 `pending_fence["tracked_writer_pids"]`，跨 reparenting 保持持久追蹤。
- 在 `fence_account_pool_workers` 與 `worker_lifecycle.py` 中：
  - 若 `worker_writers_are_alive(worker)` 為 True，向程序樹發送終止信號，維持 `pending_fence`，不提早改派。
  - 在 `_settle_fenced_sibling_worker` 開頭雙重防護，確保寫入者完全停止。

### (3) 可重試保全結案與暫態修復 (Retryable Settle on Transient Backup Failure)
- 若 `preserve_dead_worker_worktree` 回傳失敗且非 clean：
  - 保留 `sibling["pending_fence"]` 並記錄 `preservation_failed: True`。
  - 不調用 `maybe_reassign_task_after_worker_failure`，不結案 queue event。
  - `_settle_fenced_sibling_worker` 回傳 `False`。
  - 下次 `poll_workers` 巡檢自動重試，修復後成功建立 seal 並改派。

---

## 3. 端到端真實 Temporary Git 回歸測試 (Test Suite)

在 [`.orchestrator/test_worker_failure_policy.py`](file://.orchestrator/test_worker_failure_policy.py) 中 `QuotaSiblingFencingDirtyHandoffTests` 測試類別包含 15 支端到端 regression 測試：

| 測試名稱 | 涵蓋面向 | 預期結果 |
|---|---|---|
| `test_sibling_quota_fence_preserves_dirty_worktree_and_authorizes_successor_lease_continuation` | E2E：包含 staged、unstaged、untracked 變更，驗證備份目錄物理檔案、manifest、patch、checksums，改派至 Codex，並透過真實 `prepare_worker_workspace` 成功租借續接與提交 | PASS |
| `test_sibling_quota_fence_alive_terminating_dead_ordering` | 順序性：Alive (SIGTERM) -> Terminating (pending fence, 狀態與 owner 不變) -> Dead (poll_workers 觸發保全、改派與結案) | PASS |
| `test_sibling_quota_fence_failed_termination_preserves_active_state_without_reassign` | 負例：終止失敗（process 持續存活）時保留 active 與原 owner，禁止提早改派 | PASS |
| `test_sibling_quota_fence_backup_failure_refuses_reassignment_and_leaves_owner_unchanged` | 負例：Dirty worktree 備份失敗（`backup_write_failed`）時拒絕改派 owner，保留 retryable pending_fence，修復後成功保全與續接 | PASS |
| `test_sibling_quota_fence_refuses_preservation_while_writer_is_still_alive` | 負例：Active writer 存活時拒絕封裝 handoff seal | PASS |
| `test_unrelated_or_noworkspace_failure_does_not_transfer_foreign_handoff_block` | 負例：無工作區或不匹配的失敗禁止改寫他人 handoff seal，防範 foreign dirt 汙染 | PASS |
| `test_sibling_quota_fence_writer_process_tree_descendants_prevent_premature_settlement` | 程序樹與孫進程：真實 runner -> CLI -> grandchild writer（cwd 於工作區外、持 open fd、忽略 SIGTERM），驗證跨 reparenting 追蹤、阻止繼承者提前租借、進程消亡後成功封裝續接與保留寫入 bytes | PASS |
| `test_sibling_quota_fence_boot_reconciliation_pending_preservation_recovery` | Boot 復原：備份目錄受阻時發生 fence，經歷 JSON 序列化/反序列化與 `reconcile_runtime_on_boot`，驗證 pending_fence 優先權不被 boot 破壞，障礙排除後 `poll_workers` 成功修復與續派 | PASS |
| `test_authorized_successor_refuses_foreign_agent_or_unauthorized_owner` | 負例：未授權第三代理人（Claude）無法透過 `prepare_worker_workspace` 認領 dirty worktree | PASS |
| `test_authorized_successor_refuses_reviewer_guard` | 負例：Reviewer（`review_ready_dispatch`）永遠禁止繼承 dirty worktree | PASS |
| `test_authorized_successor_refuses_helper_guard` | 負例：Helper（`helper_claim`）永遠禁止繼承 dirty worktree | PASS |
| `test_authorized_successor_refuses_when_head_sha_drifted` | 負例：封裝後 HEAD SHA 漂移拒絕 lease 續接 | PASS |
| `test_authorized_successor_refuses_when_dirt_fingerprint_drifted` | 負例：封裝後 dirty 內容變更拒絕 lease 續接 | PASS |
| `test_authorized_successor_refuses_when_branch_mismatched` | 負例：派工 branch 不符拒絕 lease 續接 | PASS |
| `test_sibling_quota_fence_without_reassignment_allows_same_owner_continuation` | 正例：未改派時原 owner 保留 seal，冷卻後由原 owner 透過 `prepare_worker_workspace` 成功續接 | PASS |
| `test_sibling_quota_fence_clean_worktree_reassigns_without_handoff_seal` | 正例：Clean worktree 正常改派且不封裝 seal，繼承者透過 `prepare_worker_workspace` 成功租借 | PASS |

---

## 4. 治理邊界與不變量 (Governance & Boundary Invariants)

1. **不修改產品程式碼**: 變更嚴格局限於 `.orchestrator/` 及 `docs/evidence/`。
2. **不 Hot-patch 或重啟 Live Supervisor**: 未修改 live canonical runtime 或 supervisor 實體程序。
3. **隔離 PR #1325 審查邊界**: 避免修改 `worker_workspace.py`，保持與在途 PR 獨立不衝突。
4. **完整 Handoff Provenance 追溯性**: 嚴格保留 `original_owner`, `authorized_successor`, `transferred_from`, `transferred_to`, `transfer_reason`, `transferred_at`, `head_sha`, `dirt_fingerprint`。
