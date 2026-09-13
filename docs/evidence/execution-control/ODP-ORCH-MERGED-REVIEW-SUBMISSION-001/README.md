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

### 2.2 Codex2 審查發現與修復 (Review Findings & Fixes)

#### R1 [P1]: Workspace Lease 週期與 Detached HEAD 重新掛載
- **問題**: Reviewer lease 使用 `git checkout` 定位至 submitted source SHA 時會進入 detached HEAD 狀態。在 reviewer 完成審查（reopen 或 approve）後，後續 owner lease 因 `_existing_worktree_for_branch` 未能匹配 detached 工作區而嘗試新建工作區並失敗，或因 detached HEAD 判定為 `wrong_branch`。
- **修復**:
  - `worker_workspace.py:_existing_worktree_for_branch`: 支援 `expected_path` 參數，若工作區路徑存在且屬於該 repo，即便處於 detached HEAD 亦能正確辨識並重用。
  - `worker_workspace.py:_refresh_worker_worktree`:
    - Reviewer lease (`required_head` 存在): 精確定位至 submitted source SHA（保留 pinned 狀態）。
    - Owner / Finalize lease (`required_head` 為 None): 若處於 detached HEAD 狀態，安全執行 `git checkout <expected_branch>` 重新掛載回任務分支，並保持 dirty worktree 檢查優先阻擋。
  - 新增真實 Git 整合測試 `test_r1_workspace_lease_lifecycle_merged_review_reopen_and_owner_reconnect`，完整涵蓋 owner 建立/修改 -> 合併 -> reviewer lease pin SHA -> reviewer reopen -> owner lease 重新掛載 -> dirty 工作區拒絕。

#### R2 [P1]: Reopen 後的首次 Merged Recovery 送審漏掉狀態轉換
- **問題**: `scripts/ai_status.py:command_submit_review` 在偵測到首次 OPEN-to-MERGED 轉換時直接 `return`，導致被 reviewer reopen 為 `in_progress` 的任務無法轉換至 `review` 狀態，且未建立給審查者的 pending handoff。
- **修復**: 首次 OPEN-to-MERGED 復原記錄 `recovery_at`/`recovery_by` 與 audit event 後，不再提早 return，繼續執行狀態轉換至 `review` 並建立審查者 handoff。
- 新增測試 `test_r2_merged_recovery_after_reopen_transitions_to_review_and_creates_reviewer_handoff`。

#### R3 [P2]: Merged Recovery 重試遺失 recovery_at / recovery_by 審查源數據
- **問題**: 重複呼叫 `command_submit_review` 時，既有邏輯僅保留 `verified_at` 與 `submitted_by`，導致後續重試抹除 `recovery_at` 與 `recovery_by`。
- **修復**: 完整保留 `existing_sub` 中的 `recovery_at` 與 `recovery_by`，確保在 `review` 與 `review_approved` 狀態下的重入冪等性。
- 新增測試 `test_r3_merged_recovery_retries_preserve_full_provenance`。

#### R4 [P2]: GitHub CLI 傳輸錯誤導致 task_finalize 假報成功
- **問題**: `delivery_toolchain/git/task_finalize.sh` 使用 `2>/dev/null || true` 吞噬了 `gh` 錯誤，當 GitHub API 回傳 HTTP 503 等錯誤時，誤判為「無 PR，工作已合併」並以 exit 0 退出，未提交送審。此外，在無 GitHub remote 的測試/本地環境中，`gh` 回傳非 GitHub host 提示時應正確處理而不誤拋 transport failure。
- **修復**: 嚴格檢查 `gh pr list` 與 `gh pr view` 之 exit code，遇傳輸或 API 錯誤時輸出錯誤訊息並 exit 1；針對非 GitHub git remotes 提示安全處理。
- 新增真實 shell 執行回歸測試 `test_r4_task_finalize_shell_gh_transport_failure_and_discovery`，並通過 `tests/tooling/test_git_task_scripts.py` 完整測試套件。

#### R5 [P2]: Remote read failure 區分
- **修復**: `resolve_task_sha` 區分 transport failure (拋出 `RuntimeError`) 與 confirmed absence (回傳 `None`)。

---

## 3. 修復方案與設計 (Design & Implementation)

### 3.1 Merged Review Submission & Recovery (`scripts/ai_status.py`)
- `command_submit_review`:
  - 完整保留 `verified_at`、`submitted_by`、`recovery_at`、`recovery_by`。
  - 偵測首次 OPEN-to-MERGED 轉換並記錄 `recovery_at`/`recovery_by` 與發布 audit log。
  - 確保 reopen 後（`status == "in_progress"`）能正確推進至 `status == "review"` 並建立 pending reviewer handoff。
  - 對於 `review` 與 `review_approved` 保持嚴格冪等，不抹除核准狀態與 approved_head。

### 3.2 Worker Workspace Merged Handoff (`worker_workspace.py`)
- `_existing_worktree_for_branch`: 支援透過 `expected_path` 發現 detached HEAD 工作區。
- `_refresh_worker_worktree`:
  - Reviewer: 驗證工作區乾淨後使用 `git checkout` 精確定位 submitted SHA。
  - Owner / Finalize: 驗證工作區乾淨後若為 detached HEAD 則重新掛載回任務分支。
  - Dirty worktree 檢查在任何 HEAD 移動前嚴格 fail-closed。

### 3.3 Task Finalize PR Discovery (`delivery_toolchain/git/task_finalize.sh`)
- 嚴格捕獲 `gh` 命令 returncode，遇 API / 網路故障時 fail-closed (exit 1)。
- 遍歷所有 PR 尋找 `MERGED` 狀態，若僅有 `CLOSED` 則報錯，不假裝已合併。
- 正確解析非 GitHub remote 提示，相容本機與 CI 工具鏈測試。

---

## 4. 驗證記錄 (Verification Receipts)

### 4.1 Pytest 回歸測試 (test_ai_status.py & test_git_task_scripts.py)
- **Command**: `uv run pytest scripts/test_ai_status.py -k "Review or Submission or Merged"`
- **Exit Code**: `0`
- **Output Summary**: `84 passed, 198 deselected, 11 subtests passed in 2.69s`
- **Tooling Command**: `uv run pytest tests/tooling/test_git_task_scripts.py`
- **Exit Code**: `0`
- **Output Summary**: `58 passed in 9.79s`
- **Full Tooling Suite**: `uv run pytest tests/tooling`
- **Exit Code**: `0`
- **Output Summary**: `245 passed in 68.35s`

### 4.2 Ruff 語法與代碼風格檢查
- **Command**: `uv run ruff check scripts/ai_status.py scripts/test_ai_status.py .orchestrator/worker_workspace.py delivery_toolchain/git/task_finalize.sh`
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
| **A2** | owner-only merged submission 接受精確 repo/PR/branch/base/head 與可證 merge ancestry；CI 終態 success，保留原 OPEN 規則 | R1: worker_workspace.py 支援 detached reviewer pinning 與 owner re-attachment；R2: waiting_for guard 修復；R5: resolve_task_sha 區分 transport failure 與 confirmed absence | **PASS** |
| **A3** | 只記錄真實事後 review submission 與 provenance；需獨立 reviewer approve 及 owner done，不倒填審查、不清除 human/blocked gate | R2/R4: 首次 OPEN→MERGED 復原記錄 recovery_at/recovery_by，保留原 verified_at/submitted_by，推進至 review 狀態；R3: 重複提交保持 provenance 冪等 | **PASS** |
| **A4** | 已 merged `task_finalize` 及 review dispatch 沿原 task 接續，不建空 PR/重複 task，不手改 JSON，冪等防重派 | R4: task_finalize.sh 遍歷所有 PR 找 MERGED，不漏掉被 CLOSED PR 遮蓋的有效合併，遇 gh 錯誤 fail-closed | **PASS** |
| **A5** | 補有意義正反 CLI/Git ancestry/CI/actor regression，保存原始結果與 exact source，Ruff/boundary 通過 | 新增/完善多項真實 Git / Shell / Status 回歸測試（包括 R1-R5），84 passed, Ruff 與 Code Boundaries 100% 通過 | **PASS** |
| **A6** | 正常 per-task PR / required CI / 獨立 Codex2 審查 / owner 結案；不修改 live runtime/config/狀態或重啟 Supervisor | 循標準 `worker_commit.py` -> `task_finalize.sh` 提交流程，未改動 live runtime 與 supervisor 設定 | **PASS** |
