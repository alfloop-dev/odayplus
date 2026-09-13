# ODP-ORCH-QUOTA-FENCE-HANDOFF-001

## 1. 任務背景與問題根因 (Incident Background & Root Cause)

- **任務 ID**: `ODP-ORCH-QUOTA-FENCE-HANDOFF-001`
- **任務標題**: 修復 quota 同帳號停派後的修改保全與續接 (Shared-Account Quota Fencing Dirty Worktree Preservation & Successor Handoff Recovery)
- **執行身分 (Owner)**: `Antigravity3`
- **指派審查者 (Reviewer)**: `Codex2`
- **交付檔案**:
  - `.orchestrator/worker_failure_policy.py`
  - `.orchestrator/test_worker_failure_policy.py`
  - `docs/evidence/execution-control/ODP-ORCH-QUOTA-FENCE-HANDOFF-001/`

### 根因分析 (Root Cause Analysis)
在共用帳號 pool（如 `antigravity_main` 含 `antigravity`, `antigravity2` 等多個 dispatch slots）發生 quota 耗盡時：
1. **未在改派前保全 Worktree**: `fence_account_pool_workers` 終止 sibling worker 後，立即調用 `maybe_reassign_task_after_worker_failure` 將任務 owner 改派為候選代理人（如 `Codex`）。此時 canonical `ai-status.json` 中的 owner 已經變更。若後續才執行 worktree 保全，`_dead_owner_continuation_eligible` 會比對 `task["owner"]`（新 owner `Codex`）與 `worker_agent`（原 owner `Antigravity2`），因不一致判定不可交接，導致未封裝 handoff seal。
2. **缺乏合法繼承者（Authorized Successor）交接語意**: 即使產生 handoff block，原機制的 handoff block 僅記錄原 owner（`Antigravity2`）。當新指派的合法繼承者（`Codex`）嘗試 lease 該 isolated worktree 時，`sealed_owner_continuation_allowed` 因比對 `target_agent == record["owner"]` 不符而判定 `not_same_owner`，導致 dirty worktree 永久無法被合法繼承者認領，造成任務停滯。

---

## 2. 修復設計與控制流程 (Architecture & Fix Design)

### (1) 先保全後改派 (Preserve Before Reassignment)
在 [`.orchestrator/worker_failure_policy.py`](file://.orchestrator/worker_failure_policy.py) 的 `fence_account_pool_workers` 中：
- 終止同 pool 的 sibling worker process。
- 確認 process 確已終止（`not pid_is_alive(pid)`，無 active writer）。
- **在改派責任前**，調用 `preserve_dead_worker_worktree(config, state, sibling, task=task_record, trigger="sibling_fenced")`，對 dirty worktree 進行檢查、建立備份並封裝 `handoff_blocks`。

### (2) 完整交接權屬與溯源轉移 (Handoff Provenance & Transfer)
在 [`.orchestrator/worker_failure_policy.py`](file://.orchestrator/worker_failure_policy.py) 的 `maybe_reassign_task_after_worker_failure` 中：
- 當任務成功改派給新 owner（`new_owner`，如 `Codex`）時，同步更新 `state["worker_worktrees"]["handoff_blocks"][task_id]`：
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

### (3) 合法繼承者驗證與續接 (Successor Lease Continuation)
- 合法繼承者（`Codex`）派工時，透過既有 `sealed_owner_continuation_allowed` 驗證：
  - `owner == target_agent == record["owner"]` 完全匹配。
  - Worktree 路徑、分支名稱、HEAD SHA、dirt fingerprint 均一致無漂移。
  - 允許合法繼承者認領並接續完成未提交之修改。

### (4) 嚴格負向防護與 Fail-Closed 保證
- **Active Writer Guard**: 若 sibling process 仍存活或未能終止，拒絕封裝 handoff block，防止並發寫入衝突。
- **Reviewer Guard**: `review_ready_dispatch` 審查者派工絕對禁止繼承未提交 dirty worktree，一律回傳 `not_owner_execution`。
- **Helper Guard**: `helper_claim` 協助者派工絕對禁止認領未提交 dirty worktree，一律回傳 `not_owner_execution`。
- **Foreign Agent Guard**: 非 authorized successor 的第三代理人派工一律拒絕（`not_same_owner`）。
- **HEAD Drift & Dirt Drift Guards**: 封裝後若 HEAD SHA 前移或 dirty 內容/檔案發生變更，一律拒絕認領（`head_changed` / `dirt_changed`）。
- **Branch Mismatch Guard**: 若派工分支與封裝分支不符，一律拒絕認領（`workspace_changed`）。
- **Same-Owner Cooldown Recovery**: 若 pool fencing 時無其他可用候選人（未改派），原 owner 保留 sealed block，冷卻後由原 owner 續派可合法認領。
- **Clean Worktree Handling**: 若 sibling worker 為 clean worktree，正常改派且不建立多餘的 handoff seal。

---

## 3. 端到端真實 Temporary Git 回歸測試 (Test Suite)

在 [`.orchestrator/test_worker_failure_policy.py`](file://.orchestrator/test_worker_failure_policy.py) 中新增 `QuotaSiblingFencingDirtyHandoffTests` 測試類別（共 10 支端到端 regression 測試）：

| 測試名稱 | 涵蓋面向 | 預期結果 |
|---|---|---|
| `test_sibling_quota_fence_preserves_dirty_worktree_and_authorizes_successor_lease_continuation` | E2E：同 pool sibling quota fencing 保全 dirty worktree、改派至 Codex、完整溯源轉移、Codex 合法 lease 續接並成功提交 | PASS |
| `test_sibling_quota_fence_refuses_preservation_while_writer_is_still_alive` | 負例：Process 存活（active writer）時拒絕封裝 handoff seal | PASS |
| `test_authorized_successor_refuses_foreign_agent_or_unauthorized_owner` | 負例：未授權第三代理人（Claude）無法認領 Codex 的 dirty worktree | PASS |
| `test_authorized_successor_refuses_reviewer_guard` | 負例：Reviewer（`review_ready_dispatch`）永遠禁止繼承 dirty worktree | PASS |
| `test_authorized_successor_refuses_helper_guard` | 負例：Helper（`helper_claim`）永遠禁止繼承 dirty worktree | PASS |
| `test_authorized_successor_refuses_when_head_sha_drifted` | 負例：封裝後 HEAD SHA 漂移拒絕 lease 續接 | PASS |
| `test_authorized_successor_refuses_when_dirt_fingerprint_drifted` | 負例：封裝後 dirty 內容變更拒絕 lease 續接 | PASS |
| `test_authorized_successor_refuses_when_branch_mismatched` | 負例：派工 branch 不符拒絕 lease 續接 | PASS |
| `test_sibling_quota_fence_without_reassignment_allows_same_owner_continuation` | 正例：未改派時原 owner 保留 seal，冷卻後可正常續接 | PASS |
| `test_sibling_quota_fence_clean_worktree_reassigns_without_handoff_seal` | 正例：Clean worktree 正常改派且不封裝 seal | PASS |

---

## 4. 驗證命令與執行收據 (Verification Commands & Receipts)

### (1) Focused Pytest Verification
- **Command**: `uv run pytest -q .orchestrator/test_worker_failure_policy.py -k "fence or handoff or quota"`
- **Exit Code**: `0`
- **Output**: `.......................... [100%]` (26 passed, 41 deselected)

### (2) Ruff Lint & Style Verification
- **Command**: `uv run ruff check .orchestrator/worker_failure_policy.py .orchestrator/worker_lifecycle.py .orchestrator/test_worker_failure_policy.py`
- **Exit Code**: `0`
- **Output**: `All checks passed!`

### (3) Git Diff & Whitespace Verification
- **Command**: `git diff --check`
- **Exit Code**: `0`
- **Output**: `""` (Clean, no trailing whitespace or conflict markers)

---

## 5. 治理邊界與不變量 (Governance & Boundary Invariants)

1. **不修改產品程式碼**: 變更嚴格局限於 `.orchestrator/worker_failure_policy.py`、`.orchestrator/test_worker_failure_policy.py` 及 `docs/evidence/`。
2. **不 Hot-patch 或重啟 Live Supervisor**: 未修改 live canonical runtime 或 supervisor 實體程序。
3. **隔離 PR #1325 審查邊界**: 避免修改 `worker_workspace.py`，保持與在途 PR 獨立不衝突。
4. **完整 Handoff Provenance 追溯性**: 嚴格保留 `original_owner`, `authorized_successor`, `transferred_from`, `transferred_to`, `transfer_reason`, `transferred_at`, `head_sha`, `dirt_fingerprint`。
