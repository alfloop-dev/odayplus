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
  - `.orchestrator/dispatch_engine.py`
  - `docs/evidence/execution-control/ODP-ORCH-MERGED-REVIEW-SUBMISSION-001/`

---

## 2. 背景與問題分析 (Background & Problem Analysis)

### 2.1 觀測到的真實案例 (`ODP-ORCH-QUOTA-RECOVERY-STATE-001` / PR #1305)
在真實運行環境中，`ODP-ORCH-QUOTA-RECOVERY-STATE-001` 的 PR #1305 於 2026-09-13T06:53:45Z 已成功合併至 `dev`（immutable source SHA 為 `2c50d5a6ff8d9f986221489d548072e9f8bfbde4`，merge commit 為 `cd9f45901ccd32b9234563db51d86cc4d82db290`）。

然而，canonical status 中該任務仍停留在 `in_progress`，`review_submission` 仍記錄為舊的 head `10e20f8a`，且沒有 `approved_head`。

### 2.2 工具鏈接續障礙與 Review Findings (R1–R5)
1. **R1 (Delivery Identity Base Range)**：`scripts/ai_status.py` 在驗證 merged delivery identity 時原先使用 `base=base_branch`（`origin/dev`），由於 source SHA 已合併至 `origin/dev`，導致 `origin/dev..source` 區間為空而被拒絕。修復為採用可證的 pre-merge base `merge_commit^1` 進行 delivery identity 檢驗。
2. **R2 (Dispatch Engine Merged State Integration)**：`.orchestrator/dispatch_engine.py` 原先在 `is_task_review_dispatch_eligible` 與 `dispatch_ready_tasks` 中要求 remote task branch 必須解析且 PR 狀態不可為 `MERGED`，導致 PR 合併後無論遠端分支是否刪除均無法派發給 Reviewer。修復為支援已驗證之 immutable merged review submission，允許分支被清理或保留，且不將 merge 誤判為 approval。
3. **R3 (Owner-Only Merged Recovery Authorization)**：保留 OPEN PR 允許持有有效 `helper_execution_lease` 之 helper 提交送審的既有行為，但對 `MERGED` PR 復原嚴格限制僅任務原 owner 可執行。
4. **R4 (Same-Submission Full Idempotency)**：修復重複執行 `submit_review`（如 `task_finalize.sh` retry）時的狀態覆寫問題；對於相同 repository/PR/head，完整保留原始 `verified_at`、`submitted_by` 審查憑證，不重複新增 pending handoff，且若任務已在 `review_approved` 則保留 `approved_head` 與審查通過狀態不降級。
5. **R5 (Review-Gate Separation in Pre-Review CI Checks)**：`normalized_green_pr_checks` 支援 `exclude_review_gate=True`，排除審查通過後才生成的 `task-review-gate`，避免事後送審因 review gate pending/failed 而無法進入 review 狀態，同時保留其餘 required CI checks 的 fail-closed 終態驗證。

---

## 3. 修復方案與設計 (Design & Implementation)

### 3.1 嚴格 Owner-Only Merged Review Submission (`scripts/ai_status.py`)
在 `review_submission_for_task` 與 `command_submit_review` 中實作：
- **Actor & Gate 檢查**：
  - 嚴格限定僅任務原 owner 可提交 `MERGED` PR recovery，拒絕 non-owner helper。
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
- **Pre-Merge Base Delivery Identity 驗證**：
  - 使用 `merge_commit^1` 作為 base ref 執行 `validate_delivery_identity`，真實檢驗交付 commit 之 `Task-ID` 等 trailers。
- **CI 終態成功驗證 (排除 Review Gate)**：
  - 透過 `normalized_green_pr_checks(pr, exclude_review_gate=True)` 驗證 required CI checks 終態為 SUCCESS/NEUTRAL/SKIPPED，排除 pre-approval 階段之 `task-review-gate`。
- **全冪等性 (Full Idempotence)**：
  - 針對相同 PR/head 重複提交時，保留原始 `verified_at` 與 `submitted_by`；若已處於 `review_approved` 則不抹除 `approved_head` 與通過狀態。

### 3.2 Dispatch Engine 支援 Merged Review Submission (`.orchestrator/dispatch_engine.py`)
- `is_task_review_dispatch_eligible`：
  - 當存在已驗證之 merged review submission 時，允許 remote branch 已刪除 (`current_head is None`) 或未 drift (`current_head == submitted_sha`)。
  - 接受 PR 狀態為 `MERGED`，並確認 CI 狀態為 `success`。
  - 保留 reviewer 派工獨立性，不視 merge 為 approval。
- `dispatch_ready_tasks`：
  - 對於 merged review submission，不發出「Cannot verify branch HEAD」之不當抑制訊息；若 branch 漂移或 CI 未過則維持抑制。

### 3.3 Task Finalize 接續已合併 PR (`delivery_toolchain/git/task_finalize.sh`)
- 當 `HEAD` 已是 `$BASE_REF` 祖先時，主動查詢 GitHub 上對應分支之 PR。
- 若發現存在已 `MERGED` 之 PR，且 `--no-status-submit` 未啟用，自動呼叫 `scripts/ai-status.sh submit_review "$TASK_ID" "$PR_NUMBER" "..."` 完成原子送審。

---

## 4. 驗證記錄 (Verification Receipts)

### 4.1 Pytest 回歸測試 (test_ai_status.py)
- **Command**: `uv run pytest -q scripts/test_ai_status.py -k "Review or Submission or Merged"`
- **Exit Code**: `0`
- **Output Summary**: `73 passed in 8.35s`

### 4.2 Pytest 回歸測試 (test_dispatch_policy.py)
- **Command**: `uv run pytest -q .orchestrator/test_dispatch_policy.py -k "merged or review"`
- **Exit Code**: `0`
- **Output Summary**: `77 passed in 12.14s`

### 4.3 Ruff 語法與代碼風格檢查
- **Command**: `uv run ruff check scripts/ai_status.py scripts/test_ai_status.py .orchestrator/dispatch_engine.py .orchestrator/test_dispatch_policy.py`
- **Exit Code**: `0`
- **Output**: `All checks passed!`

### 4.4 代碼邊界檢查 (Governance Boundary Check)
- **Command**: `uv run python delivery_toolchain/governance/check_code_boundaries.py`
- **Exit Code**: `0`
- **Output**: `Code boundary checks passed for 1163 files.`

### 4.5 Git Diff Whitespace 檢查
- **Command**: `git diff --check`
- **Exit Code**: `0`
- **Output**: Clean (no whitespace issues).

---

## 5. 驗收標準逐項對照 (Acceptance Criteria Mapping)

| Acceptance ID | 驗收規範描述 | 實作與驗證對照 | 狀態 |
|---|---|---|---|
| **A1** | 先讀 `MERGED-REVIEW-SUBMISSION.md` 與原 quota 真實案例；重用既有工具安全接續 | 已研讀真實案例 PR #1305 與 handoff 文檔，完全重用 `submit_review` 及 `task_finalize.sh` | **PASS** |
| **A2** | owner-only merged submission 接受精確 repo/PR/branch/base/head 與可證 merge ancestry；CI 終態 success，保留原 OPEN 規則 | 於 `scripts/ai_status.py` 完整實作 exact PR/branch/base/head、git object/merge ancestry 雙向檢驗、`merge_commit^1` delivery identity 檢驗、`exclude_review_gate` CI 終態驗證，嚴格限制 owner-only 並保留 OPEN 分支邏輯 (R1, R3, R5) | **PASS** |
| **A3** | 只記錄真實事後 review submission 與 provenance；需獨立 reviewer approve 及 owner done，不倒填審查、不清除 human/blocked gate | 記錄精確 `verified_at`、`merged_at`、`merge_commit`、`ci_status`；需獨立 reviewer approve 與 owner done；拒絕 blocked / human gate 任務 | **PASS** |
| **A4** | 已 merged `task_finalize` 及 review dispatch 沿原 task 接續，不建空 PR/重複 task，不手改 JSON，冪等防重派 | `task_finalize.sh` 遇 merged PR 自動接續 `submit_review`，`dispatch_engine.py` 整合 merged 審查派工 (R2)，並實作相同 head 提交之完全冪等性 (R4) | **PASS** |
| **A5** | 補有意義正反 CLI/Git ancestry/CI/actor regression，保存原始結果與 exact source，Ruff/boundary 通過 | `scripts/test_ai_status.py` 與 `.orchestrator/test_dispatch_policy.py` 增加真實 Git delivery identity、helper lease owner-only 鑑權、review-gate 隔離、冪等性及派工狀態測試，Ruff 與 Code Boundaries 100% 通過 | **PASS** |
| **A6** | 正常 per-task PR / required CI / 獨立 Codex2 審查 / owner 結案；不修改 live runtime/config/狀態或重啟 Supervisor | 循標準 `worker_commit.py` -> `task_finalize.sh` 提交流程，未改動 live runtime 與 supervisor 設定 | **PASS** |
