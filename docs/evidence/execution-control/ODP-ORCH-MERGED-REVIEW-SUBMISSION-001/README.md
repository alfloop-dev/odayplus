# ODP-ORCH-MERGED-REVIEW-SUBMISSION-001: 修復已合併 PR 的精確版本送審接續

## 1. 任務基本資訊 (Task Metadata)

- **Task ID**: `ODP-ORCH-MERGED-REVIEW-SUBMISSION-001`
- **Title**: 修復已合併PR的精確版本送審接續
- **Owner**: `Antigravity3`
- **Reviewer**: `Codex2`
- **Branch**: `task/ODP-ORCH-MERGED-REVIEW-SUBMISSION-001`
- **Target Branch**: `dev`
- **Phase**: ODP execution control recovery
- **Artifacts**:
  - `scripts/ai_status.py`
  - `delivery_toolchain/git/task_finalize.sh`
  - `docs/evidence/execution-control/ODP-ORCH-MERGED-REVIEW-SUBMISSION-001/`

---

## 2. 背景與問題分析 (Background & Problem Analysis)

### 2.1 觀測到的真實案例 (`ODP-ORCH-QUOTA-RECOVERY-STATE-001` / PR #1305)
在真實運行環境中，`ODP-ORCH-QUOTA-RECOVERY-STATE-001` 的 PR #1305 於 2026-09-13T06:53:45Z 已成功合併至 `dev`（immutable source SHA 為 `2c50d5a6ff8d9f986221489d548072e9f8bfbde4`，merge commit 為 `cd9f45901ccd32b9234563db51d86cc4d82db290`）。

然而，canonical status 中該任務仍停留在 `in_progress`，`review_submission` 仍記錄為舊的 head `10e20f8a`，且沒有 `approved_head`。

### 2.2 工具鏈接續障礙
1. `delivery_toolchain/git/task_finalize.sh`：當檢測到 `HEAD` 已是 `$BASE_REF`（`origin/dev`）的祖先時，直接印出「HEAD is already an ancestor of dev -- the work has landed. no PR needed. Close the task out with done」並 exit 0，未向 canonical status 提交 review 請求。
2. `scripts/ai_status.py`（`review_submission_for_task`）：舊有邏輯嚴格要求 `pr.state == "OPEN"`，直接拒絕 `MERGED` 狀態的 PR。
3. `command_approve`：當 PR 合併後 GitHub 自動刪除 remote `origin/task/<id>` 分支時，`resolve_task_sha` 返回 `None`，造成即使進入 review 也無法完成審查凍結（freeze）。
4. 導致 Supervisor 不斷重新派工給 owner（`owned_ready_dispatch`），owner 執行 `task_finalize.sh` 無法推進 review，形成無進度循環重派（dispatch loop starvation）。

---

## 3. 修復方案與設計 (Design & Implementation)

### 3.1 嚴格 Owner-Only Merged Review Submission (`scripts/ai_status.py`)
在 `review_submission_for_task` 中新增對 `MERGED` PR 的嚴格事後送審驗證：
- **Actor & Gate 檢查**：
  - 僅允許任務 owner 執行。
  - 拒絕處於 `blocked` 狀態或等待 `Human/Ops` 的任務，不清除獨立 human gate / blocked gate。
- **PR 屬性精確核對**：
  - 驗證 PR 狀態為 `MERGED` 且非 draft。
  - 驗證 PR `headRefName` 精確匹配任務分支名稱（`task/<TASK-ID>`）。
  - 驗證 PR `baseRefName` 精確匹配目標基準分支（`dev`）。
  - 驗證 PR `mergedAt` 時間戳與 `mergeCommit` SHA 存在且有效。
- **Git 祖先與物件存在性驗證 (Ancestry Verification)**：
  - 驗證 immutable source SHA (`headRefOid`) 與 `mergeCommit` 為本機 repository 中存在的有效 commit 物件。
  - 驗證 `headRefOid` 是 `mergeCommit` 的合法祖先（`git merge-base --is-ancestor`）。
  - 驗證 `mergeCommit` 可從 target ref (`origin/dev` / `dev`) 歷史中追溯（`git merge-base --is-ancestor`）。
- **CI 終態成功驗證**：
  - 透過 `normalized_green_pr_checks(pr)` 驗證 `statusCheckRollup` 中所有 checks 均已完成且為 terminal SUCCESS（允許 NEUTRAL / SKIPPED），拒絕 pending / failed / malformed / missing checks。
- **Delivery Identity 驗證**：
  - 執行 `validate_delivery_identity` 確保 commit trailers（`Task-ID` 等）無洩漏。
- **審查證據記錄**：
  - 記錄真實事後送審時間戳（`verified_at` = `iso_now()`）、`merged_at`、`merge_commit`、`ci_status: "success"`、`ci_checks`。
  - 任務進入 `review` 狀態並生成指派給獨立 reviewer（如 `Codex2`）的 pending handoff。
  - 不倒填先前審查結果、不預先設置 `approved_head`。
  - 相同已記錄 head 之重複提交具備完全冪等性（idempotent）。

### 3.2 遠端分支刪除後的審查凍結修復 (`command_approve`)
當 PR 合併後 remote 分支已被 GitHub 清理（`resolve_task_sha` 回傳 `None`）時，若任務持有合法驗證之 merged `review_submission`，`command_approve` 會重新核實 `remote_sha` 與 `merge_commit` 在 target ref 歷史中的可追溯性，並將 `approved_sha` 凍結於 `review_submission` 的 immutable source SHA。

### 3.3 Task Finalize 接續已合併 PR (`delivery_toolchain/git/task_finalize.sh`)
- 當 `HEAD` 已是 `$BASE_REF` 祖先時，主動查詢 GitHub 上對應分支之 PR。
- 若發現存在已 `MERGED` 之 PR，且 `--no-status-submit` 未啟用：
  - 自動呼叫 `scripts/ai-status.sh submit_review "$TASK_ID" "$PR_NUMBER" "..."` 完成原子送審。
  - 提示等待 reviewer approval，不建立重複或空 PR。

---

## 4. 驗證記錄 (Verification Receipts)

### 4.1 Pytest 回歸測試 (Review, Submission, Merged, Real Git Ancestry, Idempotency)
- **Command**: `uv run pytest -q scripts/test_ai_status.py -k "Review or Submission or Merged or test_merged"`
- **Exit Code**: `0`
- **Output Summary**: `70 passed in 7.82s`

### 4.2 Ruff 語法與代碼風格檢查
- **Command**: `uv run ruff check scripts/ai_status.py scripts/test_ai_status.py`
- **Exit Code**: `0`
- **Output**: `All checks passed!`

### 4.3 代碼邊界檢查 (Governance Boundary Check)
- **Command**: `uv run python delivery_toolchain/governance/check_code_boundaries.py`
- **Exit Code**: `0`
- **Output**: `Code boundary checks passed for 1161 files.`

### 4.4 Git Diff Whitespace 檢查
- **Command**: `git diff --check`
- **Exit Code**: `0`
- **Output**: Clean (no whitespace issues).

---

## 5. 驗收標準逐項對照 (Acceptance Criteria Mapping)

| Acceptance ID | 驗收規範描述 | 實作與驗證對照 | 狀態 |
|---|---|---|---|
| **A1** | 先讀 `MERGED-REVIEW-SUBMISSION.md` 與原 quota 真實案例；重用既有工具安全接續 | 已研讀真實案例 PR #1305 與 handoff 文檔，完全重用 `submit_review` 及 `task_finalize.sh` | **PASS** |
| **A2** | owner-only merged submission 接受精確 repo/PR/branch/base/head 與可證 merge ancestry；CI 終態 success，保留原 OPEN 規則 | 於 `scripts/ai_status.py` 完整實作 exact PR/branch/base/head、git object/merge ancestry 雙向檢驗、`normalized_green_pr_checks` 終態成功驗證，保留原 OPEN 分支邏輯 | **PASS** |
| **A3** | 只記錄真實事後 review submission 與 provenance；需獨立 reviewer approve 及 owner done，不倒填審查、不清除 human/blocked gate | 記錄精確 `verified_at`、`merged_at`、`merge_commit`、`ci_status`；需獨立 reviewer approve 與 owner done；拒絕 blocked / human gate 任務 | **PASS** |
| **A4** | 已 merged `task_finalize` 及 review dispatch 沿原 task 接續，不建空 PR/重複 task，不手改 JSON，冪等防重派 | `task_finalize.sh` 遇 merged PR 自動接續 `submit_review`，支援冪等重複呼叫 | **PASS** |
| **A5** | 補有意義正反 CLI/Git ancestry/CI/actor regression，保存原始結果與 exact source，Ruff/boundary 通過 | `scripts/test_ai_status.py` 增加 10 項完整正反與真實 Git ancestry 測試（70 passed），Ruff 與 Code Boundaries 100% 通過 | **PASS** |
| **A6** | 正常 per-task PR / required CI / 獨立 Codex2 審查 / owner 結案；不修改 live runtime/config/狀態或重啟 Supervisor | 循標準 `worker_commit.py` -> `task_finalize.sh` 提交流程，未改動 live runtime 與 supervisor 設定 | **PASS** |
