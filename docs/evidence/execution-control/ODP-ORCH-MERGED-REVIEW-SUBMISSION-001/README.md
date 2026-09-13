# ODP-ORCH-MERGED-REVIEW-SUBMISSION-001: 修復已合併 PR 的精確版本送審接續

## 1. 任務基本資訊 (Task Metadata)

- **Task ID**: `ODP-ORCH-MERGED-REVIEW-SUBMISSION-001`
- **Title**: 修復已合併PR的精確版本送審接續
- **Owner**: `Antigravity2`
- **Reviewer**: `Codex2`
- **Branch**: `task/ODP-ORCH-MERGED-REVIEW-SUBMISSION-001`
- **Target Branch**: `dev`
- **Phase**: ODP execution control recovery
- **Artifacts**:
  - `scripts/ai_status.py`
  - `delivery_toolchain/git/task_finalize.sh`
  - `.orchestrator/dispatch_engine.py`
  - `.orchestrator/worker_workspace.py`
  - `docs/evidence/execution-control/ODP-ORCH-MERGED-REVIEW-SUBMISSION-001/`

---

## 2. 背景與問題分析 (Background & Problem Analysis)

### 2.1 觀測到的真實案例 (`ODP-ORCH-QUOTA-RECOVERY-STATE-001` / PR #1305)
在真實運行環境中，`ODP-ORCH-QUOTA-RECOVERY-STATE-001` 的 PR #1305 於 2026-09-13T06:53:45Z 已成功合併至 `dev`（immutable source SHA 為 `2c50d5a6ff8d9f986221489d548072e9f8bfbde4`，merge commit 為 `cd9f45901ccd32b9234563db51d86cc4d82db290`）。

然而，canonical status 中該任務仍停留在 `in_progress`，`review_submission` 仍記錄為舊的 head `10e20f8a`，且沒有 `approved_head`。既有工具要求 PR 必須處於 OPEN 狀態，且 `task_finalize.sh` 對已合併 ancestor 只報告成功而不執行 status 送審，造成 owner 循環重派而無法完成正式獨立審查。

### 2.2 審查發現與修復歷程 (Review Findings & Fixes)

#### Round 1 審查修復 (R1-R5 Baseline)
- **R1 (Merged Lease)**: Reviewer lease 支援 detached HEAD 狀態的 exact source pinning，Owner / Finalize lease 在無 required_head 時安全 re-attach 回任務分支。
- **R2 (Reopen Merged Recovery Transition)**: `command_submit_review` 在首次 OPEN-to-MERGED 轉換後不再提早 return，繼續推進至 `review` 狀態並建立 reviewer handoff。
- **R3 (Provenance Preservation)**: 重複呼叫 `command_submit_review` 時完整保留 `recovery_at` 與 `recovery_by`。
- **R4 (Task Finalize Merged PR Discovery)**: 遍歷所有 PR 匹配 `MERGED` 狀態，避免被 CLOSED PR 遮蔽。
- **R5 (Transport vs Absence)**: `resolve_task_sha` 區分 transport failure (拋出 `RuntimeError`) 與 confirmed absence (回傳 `None`)。

#### Round 2 審查發現與修復 (R1 & R2 Reopen Findings)
- **R1 [P1]: OPEN Reviewer-to-Owner Lease 保持 Submitted Checkout**
  - **問題**: `.orchestrator/worker_workspace.py` 在 `local_head` 為 `expected_head` 之 ancestor 時使用 detached `git checkout`，導致 `refs/heads/task/<id>` 留在舊 commit A。當 Reviewer reopen 後，Owner lease 重新掛載到該 stale ref，使 checkout 丟失已提交的修改。
  - **修復**: 在 `worker_workspace.py:_refresh_reused_worker_worktree` 中，於檢查 `required_head` 前確保 worktree 掛載於 `expected_branch`；當 `local_head` 為 `expected_head` 的 ancestor（OPEN PR 送審路徑）時，執行 safe fast-forward (`git merge --ff-only <expected_head>`)，使本機 task branch ref 與 HEAD 一併前進至提交 commit H；若為 MERGED PR 路徑（`expected_head` 為 `local_head` 之 ancestor），則保留 detached checkout 以精確鎖定 submitted source。Dirty worktree 檢查在任何操作前持續 fail-closed。
  - **測試**: 新增真實 Git 生命週期整合測試 `test_r1_open_review_reopen_and_owner_reconnect_preserves_submitted_work`，驗證 reviewer lease fast-forward -> reopen -> owner lease 重新連接完整保留提交內容。
- **R2 [P2]: Task Finalize 認證與傳輸錯誤 Fail-Closed**
  - **問題**: `delivery_toolchain/git/task_finalize.sh` 曾將 `GH_TOKEN` / authentication / credential 錯誤誤當作 empty discovery，導致在未配置憑證或 401 錯誤時誤判為「工作已合併、無需 PR」並以 exit 0 退出，未建立 review submission。
  - **修復**: 僅在 `--dry-run` 且屬於 local repository 無 GitHub remote 時寬容處理；在正常（非 dry-run）發布路徑下，凡遇 `HTTP 401: Requires authentication`、`GH_TOKEN` 缺失、網路 503 等錯誤一律以 nonzero (exit 1) 報錯退出；Fallback `gh pr view $BRANCH` 僅在 gh 確認 `no pull requests found` 時視為無 PR，其餘錯誤亦嚴格 fail-closed。
  - **測試**: 在 `test_r4_task_finalize_shell_gh_transport_failure_and_discovery` 中新增 Scenario D (non-dry-run HTTP 401 list failure)、Scenario E (non-dry-run missing GH_TOKEN list failure)、Scenario F (non-dry-run HTTP 401 view failure)、Scenario G (non-dry-run confirmed absence exit 0)。

#### Round 3 審查發現與修復 (R1 & R2 Reopen Findings)
- **R1 [P2]: Merged Reviewer Startup Branch Contract & Verifier Alignment**
  - **問題**: `worker_workspace.py` 在 merged PR 審查租約中將 exact submitted source H 簽出為 detached HEAD，但 `watch_events.py` 與 `AI_COLLABORATION_GUIDE.md` 仍要求審查者在 `task/<id>` 分支並指示執行 `task_start.sh`，而 `task_start.sh` 對 detached checkout 一律回傳 exit 1。此外，worker activity observer 將合法的 detached review lease 誤判為 `wrong_branch`。
  - **修復**:
    1. `delivery_toolchain/git/task_start.sh`: 當 `CURRENT == "HEAD"` (detached) 時，檢查該 HEAD 是否屬於當前 `$TASK_ID`（commit subject/message 或 `ai-status.json`）且與 `$BRANCH` / `origin/$BRANCH` / `dev` / `origin/dev` 具備有效 ancestry；驗證通過則回傳 exit 0（`already at verified immutable review checkout`），非該 task 之任意 detached HEAD 持續 fail-closed (exit 1)。
    2. `.orchestrator/worker_workspace.py`: 更新 activity observer，在 detached HEAD 時若 HEAD 等於 `review_head` / `approved_head` / `review_submission.remote_sha` 則認可為合法 review lease，不誤報 `wrong_branch`；更新 `AI_COLLABORATION_GUIDE.md` 範本。
    3. `.orchestrator/watch_events.py`: 為 `review_ready_dispatch` / `status:review` 提供專屬 `branch_work_guardrails`，明確指出 merged PR 審查工作區為 exact submitted source 鎖定狀態，指導使用 `task_start.sh` 核對。
  - **測試**: 在 `tests/tooling/test_git_task_scripts.py` 新增 `test_task_start_accepts_verified_immutable_review_checkout_detached_head` 與 `test_task_start_refuses_detached_head_on_unrelated_commit`；在 `scripts/test_ai_status.py` 的 `test_r1_workspace_lease_lifecycle_merged_review_reopen_and_owner_reconnect` 中整合實際 `task_start.sh` 執行驗證；在 `.orchestrator/test_watch_events.py` 中驗證審查提示詞生成。
- **R2 [P2]: Task Finalize 缺失 gh 在已合併乾跑路徑的未初始化變數修復**
  - **問題**: `delivery_toolchain/git/task_finalize.sh` 中的 `FOUND_CLOSED` 僅在 gh 可用分支中初始化，當 gh 缺失且執行 `--dry-run` 時，`set -u` 展開未定義的 `$FOUND_CLOSED` 造成腳本異常中斷；且 `GH="${GH:-gh}"` 使空 `GH` 檢查失效。
  - **修復**:
    1. 在進入 gh 分支前先行初始化 `FOUND_CLOSED=0`。
    2. `resolve_gh` 支援環境變數 `GH` 覆寫與有效性檢查。
    3. 前置檢查改以 `if ( [ -z "$GH" ] || ! command -v "$GH" >/dev/null 2>&1 ) && [ "$DRY_RUN" -eq 0 ]; then` 嚴格確保在需要時 gh 必須存在且可執行。
  - **測試**: 在 `tests/tooling/test_git_task_scripts.py` 新增 `test_task_finalize_dry_run_with_missing_gh_on_already_merged_branch` 與 `test_task_finalize_refuses_when_gh_missing_in_non_dry_run_on_merged_branch`。

#### Round 4 審查發現與修復 (R1-R4 Final Hardening)
- **R1 [P1]: 已刪除遠端分支之 Merged Recovery 送審／核准／狀態檢查發布**
  - **問題**: 當 PR 合併後遠端 `origin/task/<id>` 分支被 GitHub 自動刪除時，`command_approve` 與 `task_review_status_payload` 需透過 `resolve_immutable_merged_task_sha` 自 `review_submission` 與 merge ancestry 解析 exact source commit，並正確發布 `task-review-gate=success` 狀態檢查。
  - **修復**: 在 `scripts/ai_status.py` 抽出 `resolve_immutable_merged_task_sha`，統一用於 `command_approve` 與 `task_review_status_payload`；支援 `enforce_delivery_merged_gate` 於分支刪除情境下之交付校驗。
  - **測試**: 在 `scripts/test_ai_status.py` 新增 `test_r1_deleted_branch_merged_recovery_submit_approve_emit_status_and_owner_done`。
- **R2 [P1]: Task Start Authoritative Head 驗證與過期 Anchor 拒絕**
  - **問題**: `delivery_toolchain/git/task_start.sh` 曾以 commit subject / message 比對 Task ID 作為 detached HEAD 判定依據，導致 checkout 於舊的 intermediate anchor commit 時被誤判為合法工作區。
  - **修復**: `task_start.sh` 改為嚴格比對 `ai-status.json` 中的 authoritative head（`approved_head` // `review_submission.remote_sha`），並驗證 HEAD 為目標分支之 ancestor；任何過期 anchor 或未授權 detached commit 均 fail-closed (exit 1)。
  - **測試**: 在 `tests/tooling/test_git_task_scripts.py` 新增 `test_task_start_refuses_same_task_stale_anchor_commit`。
- **R3 [P2]: Worktree Lease 重新掛載之 Detached Drift 保護**
  - **問題**: 當 worktree 曾處於 detached HEAD 並在此狀態下產生新的 committed work (drift D) 時，若未經檢查直接 re-attach 回 task branch，將導致 drift D 丟失或孤立。
  - **修復**: 在 `worker_workspace.py:_refresh_reused_worker_worktree` 中，於 detached HEAD 重新掛載前檢查 `local_head` 是否被 `expected_head` 包含；若存在未合入的 extra commits，則以 `wrong_branch` 拒絕並保留現場，防止工作遺失。
  - **測試**: 在 `scripts/test_ai_status.py` 新增 `test_r3_workspace_reattachment_rejects_clean_detached_drift_and_preserves_work`。
- **R4 [P2]: Submit Review 前置獨立 Gate 檢查**
  - **問題**: `command_submit_review` 需在變更狀態或解決 blocker 之前，先行拒絕包含獨立 human gate、credential gate、deployment gate 或 production gate 之任務。
  - **修復**: 在 `scripts/ai_status.py` 新增 `task_unresolved_human_or_independent_gate_reason`，於 `command_submit_review` 進入點前置攔截，確保不破壞 human signoff 與 independent gates。
  - **測試**: 在 `scripts/test_ai_status.py` 新增 `test_r4_submit_review_rejects_independent_and_human_gates_before_mutation`。

---

## 3. 修復方案與設計 (Design & Implementation)

### 3.1 Merged Review Submission & Recovery (`scripts/ai_status.py`)
- `command_submit_review`:
  - 前置阻擋未解決之 human approval、credentials_gate、deployment_gate 等獨立 gates。
  - 完整保留 `verified_at`、`submitted_by`、`recovery_at`、`recovery_by`。
  - 偵測首次 OPEN-to-MERGED 轉換並記錄 `recovery_at`/`recovery_by` 與發布 audit log。
  - 確保 reopen 後（`status == "in_progress"`）能正確推進至 `status == "review"` 並建立 pending reviewer handoff。
  - 對於 `review` 與 `review_approved` 保持嚴格冪等，不抹除核准狀態與 approved_head。
- `resolve_immutable_merged_task_sha`:
  - 統一封裝已合併 PR 之 exact submitted SHA 解析與 target ancestry 驗證。

### 3.2 Worker Workspace Handoff & Branch Advancement (`.orchestrator/worker_workspace.py` & `watch_events.py`)
- `_existing_worktree_for_branch`: 支援透過 `expected_path` 發現 detached HEAD 工作區。
- `_refresh_reused_worker_worktree`:
  - 先行確保 worktree 掛載於 `expected_branch`。
  - Reviewer:
    - OPEN PR (`local_head` 為 ancestor): `git merge --ff-only expected_head` 前進任務分支，保持 HEAD attached。
    - MERGED PR (`expected_head` 為 ancestor): `git checkout expected_head` 精確鎖定 submitted source (detached)。
  - Owner / Finalize: 若先前處於 detached HEAD 則重新掛載回任務分支，並根據 base_sha 執行必要驗證；若存在 detached drift 則 fail-closed 保護。
  - Dirty worktree 檢查在任何 HEAD 移動前嚴格 fail-closed。
- Activity observer: 支援驗證合法的 `review_head` / `approved_head` detached 租約。
- Prompt rendering: 審查提示詞明確宣告 merged exact source 鎖定合約。

### 3.3 Task Finalize & Task Start Integration (`delivery_toolchain/git/`)
- `task_start.sh`: 依據 `ai-status.json` authoritative head 驗證 detached HEAD 之 task 屬性與 ancestry，安全相容 merged immutable review checkout 並拒絕 stale anchor commits。
- `task_finalize.sh`: 嚴格捕獲 `gh` 命令 returncode，遇 API / 認證 / 網路故障時 fail-closed (exit 1)；安全初始化 `FOUND_CLOSED=0` 並強化 missing-CLI preflight。

---

## 4. 驗證記錄 (Verification Receipts)

### 4.1 Pytest 回歸測試 (test_ai_status.py & test_git_task_scripts.py & test_watch_events.py)
- **Command**: `uv run pytest -q scripts/test_ai_status.py -k "Review or Submission or Merged"`
- **Exit Code**: `0`
- **Output Summary**: `88 passed, 198 deselected in 3.42s`
- **Tooling Command**: `uv run pytest tests/tooling/test_git_task_scripts.py`
- **Exit Code**: `0`
- **Output Summary**: `63 passed in 11.30s`
- **Watch Events Command**: `uv run pytest .orchestrator/test_watch_events.py`
- **Exit Code**: `0`
- **Output Summary**: `4 passed, 8 subtests passed in 0.21s`

### 4.2 Ruff 語法與代碼風格檢查
- **Command**: `uv run ruff check scripts/ai_status.py scripts/test_ai_status.py .orchestrator/worker_workspace.py tests/tooling/test_git_task_scripts.py .orchestrator/watch_events.py .orchestrator/test_watch_events.py`
- **Exit Code**: `0`
- **Output**: `All checks passed!`

### 4.3 代碼邊界檢查 (Governance Boundary Check)
- **Command**: `uv run python delivery_toolchain/governance/check_code_boundaries.py`
- **Exit Code**: `0`
- **Output**: `Code boundary checks passed for 1165 files.`

### 4.4 Git Diff Whitespace 檢查
- **Command**: `git diff --check`
- **Exit Code**: `0`
- **Output**: Clean (no whitespace issues).

---

## 5. 驗收標準逐項對照 (Acceptance Criteria Mapping)

| Acceptance ID | 驗收規範描述 | 實作與驗證對照 | 狀態 |
|---|---|---|---|
| **A1** | 先讀 `MERGED-REVIEW-SUBMISSION.md` 與原 quota 真實案例；重用既有工具安全接續 | 已研讀真實案例 PR #1305 與 handoff 文檔，完全重用 `submit_review` 及 `task_finalize.sh` | **PASS** |
| **A2** | owner-only merged submission 接受精確 repo/PR/branch/base/head 與可證 merge ancestry；CI 終態 success，保留原 OPEN 規則 | R1: worker_workspace.py 支援 OPEN task-ref fast-forward 與 MERGED exact source pinning；R2: waiting_for guard 修復；R5: resolve_task_sha 區分 transport failure 與 confirmed absence | **PASS** |
| **A3** | 只記錄真實事後 review submission 與 provenance；需獨立 reviewer approve 及 owner done，不倒填審查、不清除 human/blocked gate | R2/R4: 首次 OPEN→MERGED 復原記錄 recovery_at/recovery_by，保留原 verified_at/submitted_by，推進至 review 狀態；R3: 重複提交保持 provenance 冪等 | **PASS** |
| **A4** | 已 merged `task_finalize` 及 review dispatch 沿原 task 接續，不建空 PR/重複 task，不手改 JSON，冪等防重派 | R4/R2/R3: task_finalize.sh 遍歷所有 PR 找 MERGED，遇認證/網路錯誤 fail-closed，初始化 FOUND_CLOSED，不假報已合併 | **PASS** |
| **A5** | 補有意義正反 CLI/Git ancestry/CI/actor regression，保存原始結果與 exact source，Ruff/boundary 通過 | 新增真實 Git OPEN lease lifecycle、task_start detached verifier、Shell missing-CLI 回歸測試（85 passed + 62 passed），Ruff 與 Code Boundaries 100% 通過 | **PASS** |
| **A6** | 正常 per-task PR / required CI / 獨立 Codex2 審查 / owner 結案；不修改 live runtime/config/狀態或重啟 Supervisor | 循標準 `worker_commit.py` -> `task_finalize.sh` 提交流程，未改動 live runtime 與 supervisor 設定 | **PASS** |
