# ODP-ORCH-RECORDED-BRANCH-DELIVERY-001 驗收與證據紀錄

## 任務資訊
- **任務 ID**: ODP-ORCH-RECORDED-BRANCH-DELIVERY-001
- **標題**: 修正已 retarget 分支的交付 checkout 解析並保留 legacy fallback
- **負責人**: Antigravity5 (Canonical Owner)
- **評審人**: Codex2
- **狀態**: Review Ready

---

## 問題分析與草稿差異

### 1. 現行 Live Runtime 草稿觀察 (`runtime-recorded-branch-observed.patch`)
在 live runtime 快照中，修改了 `resolve_task_delivery_checkout`、`task_delivery_checkout` 與 `collect_done_delivery_metadata` 傳入 `recorded_branch = task_branch_name(task, task_id)`。
然而，`task_branch_name(task, task_id)` 在 `task.get("branch")` 為空或未設定時，會無條件回退至 `task/{task_id}` 衍生預設名稱。
若直接將此字串視為「明示 recorded_branch」，會導致 `resolve_task_delivery_checkout` 與 `task_delivery_checkout` 中的候選分支只剩下 `["task/" + task_id]`，進而**遺失對 `task-<task_id>` 舊有命名慣例的 fallback 搜尋相容性**。

### 2. 正式修復設計
- **`task_explicit_branch(task, branch=None)` 輔助函式**：
  - 精確解析任務中明示設定之分支名稱，重用 `task_branch_name` 對 git ref 不合法字元（空白、`~`、`^`、`:`、`?`、`*`、`[`、`\`、`..`）的校驗規則。
  - 當無明示分支或名稱不合法時回傳 `None`，不預設衍生 `task/<id>`。
- **`task_branch_name` 重構**：重用 `task_explicit_branch`，若有明示分支則回傳明示分支，否則回退至 `task/{resolved_id}`。
- **`resolve_task_delivery_checkout` 與 `task_delivery_checkout`**：
  - 接受可選之 `recorded_branch` 參數。
  - 透過 `task_explicit_branch(branch=recorded_branch)` 解析：若有有效明示分支，候選名單為 `[explicit_branch]`；若無，保留 `[f"task/{task_id}", f"task-{task_id}"]` 搜尋相容性。
- **`collect_done_delivery_metadata`**：
  - 傳入 `recorded_branch = task_explicit_branch(task)`。
  - 具有明示分支（例如透過 `retarget_branch` 建立的 recovery 分支）時，僅解析該明示分支之工作樹，舊分支即使持有 `approved_head` 亦不得冒充交付。
  - 無明示分支時，維持對 `task/<ID>` 與 `task-<ID>` 的完整搜尋與相容性。
- **`resolve_task_sha` 同步健全化**：
  - 採用 `task_explicit_branch(task)`，有明示分支時僅查詢該分支 remote ref；無明示分支時同時查詢 `task/<ID>` 與 `task-<ID>`，避免因衍生預設而遺漏 legacy 分支或隱蔽歧義。
- **維持所有現行安全防護與恢復路徑**：
  - 保持 exact approved head、實際 PR branch/base、merged provenance、wrong repository、dirty/moved/diverged checkout、兩個可能 worktree 歧義判定。
  - 保留 cleaned-up checkout（無本機工作樹時依賴 merged PR provenance）與 stale checkout（落後工作樹安全讀取）路徑。

---

## 變更檔案清單
1. `scripts/ai_status.py`:
   - 新增 `task_explicit_branch` 函式。
   - 重構 `task_branch_name` 使用 `task_explicit_branch`。
   - 更新 `resolve_task_delivery_checkout` 與 `task_delivery_checkout` 支援 `recorded_branch` 與 legacy fallback。
   - 更新 `collect_done_delivery_metadata` 傳遞 `task_explicit_branch(task)`。
   - 更新 `resolve_task_sha` 採用 `task_explicit_branch(task)`。
2. `scripts/test_ai_status.py`:
   - 新增 `TaskExplicitBranchTests` 測試類別（明示分支解析、空白與無效字元過濾）。
   - 新增 `RecordedBranchDeliveryCheckoutTests` 測試類別（明示 retarget 分支解析、舊分支不得冒充、無分支 legacy hyphen 回退、無效分支回退、錯誤 PR / 歧義 checkout 拒絕、`resolve_task_sha` 候選測試）。
3. `docs/evidence/execution-control/ODP-ORCH-RECORDED-BRANCH-DELIVERY-001/README.md`:
   - 驗收證據、測試收據與交付紀錄。

---

## 驗證收據 (Verification Receipts)

### 1. `git diff --check`
- **Command**: `git diff --check`
- **Exit Code**: `0`
- **Output**: clean (no whitespace errors)

### 2. Focused `test_ai_status.py`
- **Command**: `uv run --python 3.12 pytest -q scripts/test_ai_status.py -k 'checkout or done or branch or retarget or provenance or closemerge'`
- **Exit Code**: `0`
- **Result**: 94 passed in 8.32s

### 3. Full `test_ai_status.py` Suite
- **Command**: `uv run --python 3.12 pytest -q scripts/test_ai_status.py`
- **Exit Code**: `0`
- **Result**: 264 passed in 8.51s

### 4. Cross-Repo Terminal Gate
- **Command**: `uv run --python 3.12 pytest -q .orchestrator/test_cross_repo_terminal_gate.py`
- **Exit Code**: `0`
- **Result**: 6 passed in 2.91s

---

## 驗收條件對照表 (Acceptance Checklist)

| 驗收項目 | 狀態 | 說明 |
| :--- | :---: | :--- |
| **隔離 task worktree PR 交付** | 通過 | 在 per-task worktree (`task/ODP-ORCH-RECORDED-BRANCH-DELIVERY-001`) 實作，不直接修改 live runtime 或清除告警。 |
| **有效明示 canonical branch 交付** | 通過 | `resolve_task_delivery_checkout` 與 `collect_done_delivery_metadata` 使用 `task_explicit_branch`，支援合法 `retarget_branch` recovery 分支。 |
| **舊分支不得冒充明示分支** | 通過 | 測試證明當有明示分支時，既有 conventional 分支即使持有 `approved_head` 亦不會被選中。 |
| **無明示或無效 branch 保留 legacy fallback** | 通過 | 無明示分支或包含無效字元時，完整保留 `task/<ID>` 與 `task-<ID>` 搜尋相容性。 |
| **安全路徑與 Terminal Gates 保持完整** | 通過 | 保持 exact approved head、PR branch/base、merged provenance、wrong repo、dirty/diverged/ambiguous checkouts 等防護；保留 cleaned-up 與 stale checkout 恢復能力。 |
| **新增針對性完整測試套件** | 通過 | 於 `scripts/test_ai_status.py` 新增 `TaskExplicitBranchTests` 與 `RecordedBranchDeliveryCheckoutTests` 共 10+ 項新增測試，全數通過。 |
| **PR #1244 一致性** | 通過 | 重用既有 branch 驗證規則，同步健全化 `resolve_task_sha`。 |
| **Runtime 更新交接** | 通過 | 交付程式經 PR 審查與 CI 驗證後，由協調者在正式合併後更新 live runtime，worker 不自行重啟 supervisor 或覆寫 canonical state。 |
