# ODP-ORCH-RECORDED-BRANCH-DELIVERY-001 驗收與證據紀錄

## 任務資訊
- **任務 ID**: ODP-ORCH-RECORDED-BRANCH-DELIVERY-001
- **標題**: 修正已 retarget 分支的交付 checkout 解析並保留 legacy fallback
- **負責人**: Antigravity5 (Canonical Owner)
- **評審人**: Codex2
- **狀態**: Review Ready (Resubmission after addressing review findings)

---

## 問題分析、草稿差異與審查修復

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

### 3. Codex2 評審發現修復 (Review Finding Resolution)
- **問題一 (P2 - Recovery Branch Post-Merge Advance)**: 在 `enforce_delivery_merged_gate`（`scripts/ai_status.py:3057-3059`）中，當 task checkout 在 PR 合併後正常 advance 時，第二道 `is_approved_head_satisfied` 檢查重組 task dict 時遺失了 `branch` 欄位。導致明示 recovery 分支（如 `recovery/ODP-...`）在 `is_approved_head_satisfied` 內部透過 `task_branch_name` 錯誤衍生回 `task/recovery/ODP-...`，GitHub PR 查詢失敗並造成合法 post-merge checkout advance 結案被拒絕（`task-owned checkout HEAD differs from reviewer-approved head`）。
  - **修復內容**:
    1. `enforce_delivery_merged_gate` 增加 `task: dict[str, Any] | None = None` 參數，並在重組傳入 `is_approved_head_satisfied` 的 task 字典時完整保留 `branch`、`id`、`approved_head` 與 `repository`。
    2. `collect_done_delivery_metadata` 調用 `enforce_delivery_merged_gate` 時傳遞原始 `task=task`。
    3. `tests/ops/test_delivery_toolchain.py` 同步更新 mock 預期字典包含 `branch`。
    4. 新增整合回歸測試 `test_done_finalizes_from_merged_pr_despite_post_merge_checkout_advance_with_explicit_recovery_branch` 於 `scripts/test_ai_status.py`，完整覆蓋 explicit recovery branch 在 post-merge advanced checkout 之結案路徑。

- **問題二 (P2 - Coordinator Runtime Rollout & Health Handoff)**: 原 README 之 post-merge handoff 敘述為在 `/home/lupin/odayplus` pull dev 並執行 `ai-status.sh check`。然而 canonical status launcher 執行的是 `/home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py`，僅更新 status workspace 並不會提升 supervisor 與 canonical writer 執行的程式碼；且 `ai_status.py` 並無 `check` 子命令。
  - **修復內容**:
    1. 依循 [`docs/runbooks/supervisor-runtime-rollout.md`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-orch-recorded-branch-delivery-001/docs/runbooks/supervisor-runtime-rollout.md) 第 6 節規範，改採 `scripts/orchestrator/rollout_supervisor_runtime.py` 協調者流程。
    2. 明確指定 canonical status root (`/home/lupin/odayplus`)、config path (`/home/lupin/odayplus/.orchestrator/config.json`)、乾淨 `origin/dev` source worktree 與 stable runtime link (`/home/lupin/oday-plus-supervisor-runtime-current`)。
    3. 加入真實 runtime link 與 HEAD SHA 驗證指令。
    4. 使用支援之 [`scripts/supervisor_runtime_health.py`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-orch-recorded-branch-delivery-001/scripts/supervisor_runtime_health.py) 作為健康與 Git freshness probe。
    5. 狀態指令使用 canonical `PANTHEON_STATUS_ROOT` 之 launcher 及支援之子命令（如 `summary`）。
    6. 明確界定職責：Worker 僅於隔離工作區修復交接文件，由 Coordinator 於 PR 合併後執行 rollout，保全現場 live draft 快照與其他 worker。

---

## 變更檔案清單
1. `scripts/ai_status.py`:
   - 新增 `task_explicit_branch` 函式。
   - 重構 `task_branch_name` 使用 `task_explicit_branch`。
   - 更新 `resolve_task_delivery_checkout` 與 `task_delivery_checkout` 支援 `recorded_branch` 與 legacy fallback。
   - 更新 `collect_done_delivery_metadata` 傳遞 `task_explicit_branch(task)`。
   - 更新 `resolve_task_sha` 採用 `task_explicit_branch(task)`。
   - 修復 `enforce_delivery_merged_gate` 在 post-merge advance 判定時保留明示 `branch`。
2. `scripts/test_ai_status.py`:
   - 新增 `TaskExplicitBranchTests` 測試類別（明示分支解析、空白與無效字元過濾）。
   - 新增 `RecordedBranchDeliveryCheckoutTests` 測試類別（明示 retarget 分支解析、舊分支不得冒充、無分支 legacy hyphen 回退、無效分支回退、錯誤 PR / 歧義 checkout 拒絕、`resolve_task_sha` 候選測試）。
   - 新增 `test_done_finalizes_from_merged_pr_despite_post_merge_checkout_advance_with_explicit_recovery_branch` 回歸測試。
3. `tests/ops/test_delivery_toolchain.py`:
   - 更新 `test_delivery_merged_gate_same_repo_post_merge_checkout_advance` 與 `test_delivery_merged_gate_cross_repo_repository_slug_propagation` 之 mock 參數驗證。
4. `docs/evidence/execution-control/ODP-ORCH-RECORDED-BRANCH-DELIVERY-001/README.md`:
   - 驗收證據、測試收據、審查發現修復說明與遵循 Runbook §6 之 Runtime 更新交接指引。

---

## 驗證收據 (Verification Receipts)

所有驗證指令均在 task branch 上以獨立指令執行並記錄真實 exit code、耗時與 SHA 綁定：

### 1. `git diff --check`
- **Command**: `git diff --check`
- **Exit Code**: `0`
- **Duration**: `0.025s`
- **Tested Head**: `452e8855b66de607666cbda4aee6e7c779eb42b2` (after merging origin/dev 50d932207bd5 base advance)
- **Receipt ID**: `5b32bf32a7fbed79`
- **Output**: clean (no whitespace or format errors)

### 2. Focused `test_ai_status.py` Selection
- **Command**: `uv run pytest -q scripts/test_ai_status.py -k 'checkout or done or branch or retarget or provenance or closemerge'`
- **Exit Code**: `0`
- **Duration**: `3.793s`
- **Selection**: `'checkout or done or branch or retarget or provenance or closemerge'`
- **Tested Head**: `452e8855b66de607666cbda4aee6e7c779eb42b2`
- **Receipt ID**: `7ccf76ff2eed2e1d`
- **Result**: 95 passed in 3.79s (含新增之 explicit recovery branch post-merge advance 回歸測試)

### 3. Reviewer Reproduction Verification (`review_post_merge_recovery.py`)
- **Command**: `uv run python /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260910T235854Z-c9361bbd/review_post_merge_recovery.py`
- **Exit Code**: `0`
- **Duration**: `1.72s`
- **Result**: PR branch lookup 均正確解析為 `recovery/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`，成功結案。

### 4. Cross-Repo Terminal Gate
- **Command**: `uv run pytest -q .orchestrator/test_cross_repo_terminal_gate.py`
- **Exit Code**: `0`
- **Duration**: `2.373s`
- **Selection**: `.orchestrator/test_cross_repo_terminal_gate.py`
- **Tested Head**: `452e8855b66de607666cbda4aee6e7c779eb42b2`
- **Receipt ID**: `b699a1e35d7bd87d`
- **Result**: 6 passed in 2.37s

### 5. Delivery Toolchain Gate Suite
- **Command**: `uv run pytest -q tests/ops/test_delivery_toolchain.py`
- **Exit Code**: `0`
- **Duration**: `0.32s`
- **Result**: 7 passed in 0.32s

---

## 協調者 Runtime 更新與健康驗證交接 (Coordinator Runtime Update & Health Handoff)

> [!IMPORTANT]
> 此任務由 worker 在隔離 worktree 開發並透過 PR 交付。程式交付完成不代表 live runtime 已更新。
> Worker 依規範**僅修復交接文件，不直接執行 rollout 或重啟 supervisor、不中斷其他執行中 worker、不手動清除 canonical dirty alarm，並完整保全現場之 live draft 快照與 worker 狀態**。正式合併後由協調者（Coordinator）依標準 Rollout Runbook 執行發布。

### 正式合併後 Live Runtime 更新流程 (Post-Merge SOP)
在 GitHub PR #1300 合併進入 `dev` 之後，協調者（Coordinator / Supervisor）依據 [`docs/runbooks/supervisor-runtime-rollout.md`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-orch-recorded-branch-delivery-001/docs/runbooks/supervisor-runtime-rollout.md) 第 6 節（136–184 行）標準流程將更新同步至 canonical live runtime：

1. **準備乾淨之 `origin/dev` Source Worktree**：
   確認 source checkout 乾淨且 HEAD 已推進至包含 PR #1300 的 `origin/dev` tip：
   ```bash
   git -C /path/to/clean-origin-dev-worktree fetch origin dev
   git -C /path/to/clean-origin-dev-worktree checkout origin/dev
   ```

2. **執行原子 Supervisor Runtime Rollout**：
   使用 [`scripts/orchestrator/rollout_supervisor_runtime.py`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-orch-recorded-branch-delivery-001/scripts/orchestrator/rollout_supervisor_runtime.py) 建立乾淨具名分支 worktree，替換 `runtime-current` symlink 並原子更新 canonical status launcher：
   ```bash
   # 若現場使用 watchdog process manager（cron / supervisor.pid）：
   python3 scripts/orchestrator/rollout_supervisor_runtime.py \
     --source-root /path/to/clean-origin-dev-worktree \
     --runtime-link /home/lupin/oday-plus-supervisor-runtime-current \
     --runtime-parent /home/lupin \
     --status-root /home/lupin/odayplus \
     --config-path /home/lupin/odayplus/.orchestrator/config.json \
     --watchdog-pid-file /home/lupin/odayplus/.orchestrator/supervisor.pid

   # 或若使用 systemd user service：
   python3 scripts/orchestrator/rollout_supervisor_runtime.py \
     --source-root /path/to/clean-origin-dev-worktree \
     --runtime-link /home/lupin/oday-plus-supervisor-runtime-current \
     --runtime-parent /home/lupin \
     --status-root /home/lupin/odayplus \
     --config-path /home/lupin/odayplus/.orchestrator/config.json \
     --service pantheon-supervisor.service
   ```

3. **驗證 Runtime Link 與 HEAD SHA**：
   確認 stable symlink 指向新版 runtime 目錄且 HEAD SHA 與已合併之 PR #1300 commit 一致：
   ```bash
   readlink -f /home/lupin/oday-plus-supervisor-runtime-current
   git -C /home/lupin/oday-plus-supervisor-runtime-current rev-parse HEAD
   ```

4. **執行 Supported Supervisor 健康檢查 Probe**：
   使用 [`scripts/supervisor_runtime_health.py`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-orch-recorded-branch-delivery-001/scripts/supervisor_runtime_health.py) 檢查執行中 supervisor 程序、heartbeat 與 Git freshness：
   ```bash
   python3 /home/lupin/odayplus/scripts/supervisor_runtime_health.py \
     --repo /home/lupin/oday-plus-supervisor-runtime-current \
     --config-path /home/lupin/odayplus/.orchestrator/config.json \
     --check-git-freshness
   ```

5. **以 Canonical Status Launcher 與授權身份驗證狀態系統**：
   使用 canonical `PANTHEON_STATUS_ROOT` 之 launcher 執行支援之狀態指令（例如 `summary` 或 `status`）：
   ```bash
   AI_NAME=Supervisor /home/lupin/odayplus/scripts/ai-status.sh summary
   ```
   - 驗證 `ai-status.json` 正確解析且各欄位與 schema 一致。
   - 驗證 Dashboard 與 current-work 正常呈現。
   - 驗證後續具有 `recovery/...` 或明示分支之任務於 `done` finalization 時能正常通過 delivery checkout 與 merged PR provenance 檢驗。

---

## 驗收條件對照表 (Acceptance Checklist)

| 驗收項目 | 狀態 | 說明 |
| :--- | :---: | :--- |
| **隔離 task worktree PR 交付** | 通過 | 在 per-task worktree (`task/ODP-ORCH-RECORDED-BRANCH-DELIVERY-001`) 實作，不直接修改 live runtime 或清除告警。 |
| **有效明示 canonical branch 交付** | 通過 | `resolve_task_delivery_checkout` 與 `collect_done_delivery_metadata` 使用 `task_explicit_branch`，支援合法 `retarget_branch` recovery 分支。 |
| **舊分支不得冒充明示分支** | 通過 | 測試證明當有明示分支時，既有 conventional 分支即使持有 `approved_head` 亦不會被選中。 |
| **無明示或無效 branch 保留 legacy fallback** | 通過 | 無明示分支或包含無效字元時，完整保留 `task/<ID>` 與 `task-<ID>` 搜尋相容性。 |
| **安全路徑與 Terminal Gates 保持完整** | 通過 | 保持 exact approved head、PR branch/base、merged provenance、wrong repo、dirty/diverged/ambiguous checkouts 等防護；保留 cleaned-up 與 stale checkout 恢復能力。 |
| **新增針對性完整測試套件** | 通過 | 於 `scripts/test_ai_status.py` 新增 `TaskExplicitBranchTests`、`RecordedBranchDeliveryCheckoutTests` 及 recovery branch post-merge advance 回歸測試，全數通過。 |
| **PR #1244 一致性** | 通過 | 重用既有 branch 驗證規則，同步健全化 `resolve_task_sha`。 |
| **Runtime 更新與健康交接** | 通過 | 依據 Runbook §6 與 `rollout_supervisor_runtime.py` / `supervisor_runtime_health.py` 提供詳細之 Post-Merge SOP 與 Coordinator 健康驗證交接指引，worker 恪守職責不侵入 live runtime。 |
