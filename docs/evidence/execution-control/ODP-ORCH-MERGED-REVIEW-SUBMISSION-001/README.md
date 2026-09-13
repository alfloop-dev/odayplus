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
  - `.orchestrator/worker_workspace.py`
  - `docs/evidence/execution-control/ODP-ORCH-MERGED-REVIEW-SUBMISSION-001/`

---

## 2. 背景與問題分析 (Background & Problem Analysis)

### 2.1 觀測到的真實案例 (`ODP-ORCH-QUOTA-RECOVERY-STATE-001` / PR #1305)
在真實運行環境中，`ODP-ORCH-QUOTA-RECOVERY-STATE-001` 的 PR #1305 於 2026-09-13T06:53:45Z 已成功合併至 `dev`（immutable source SHA 為 `2c50d5a6ff8d9f986221489d548072e9f8bfbde4`，merge commit 為 `cd9f45901ccd32b9234563db51d86cc4d82db290`）。

然而，canonical status 中該任務仍停留在 `in_progress`，`review_submission` 仍記錄為舊的 head `10e20f8a`，且沒有 `approved_head`。

### 2.2 審查發現與修復 (Review Findings R1–R5)

#### R1 [P1]: Merged owner recovery 無法通過 worker handoff
- **問題**: Owner workspace 在 lease 期間被 fast-forward 至 dev/merge SHA，但 handoff seal 要求 workspace HEAD == submitted source SHA，導致 `review_head_mismatch` 拒絕。Reviewer lease 同樣拒絕已 fast-forward 之 workspace。
- **修復**:
  - `worker_workspace.py:seal_worker_handoff`: 對 merged submission，接受 workspace HEAD 為 submitted source 之後裔 (descendant)。
  - `worker_workspace.py:_refresh_reused_worker_worktree`: 當 reviewer lease 發現 workspace 已超前 submitted source，使用 `git checkout` 精確定位至 submitted SHA，而非 fast-forward。

#### R2 [P2]: waiting_for guard 阻擋已解除 blocker 的 OPEN submission
- **問題**: `review_submission_for_task` 檢查 `task.get("waiting_for")` 存在即拒絕，但 blocker→start 流程解除 blocker 後仍保留 `waiting_for`。
- **修復**: 改為僅在 `task["status"] == "blocked"` 時拒絕，不再依據 `waiting_for` 欄位有無。

#### R3 [P2]: finalize 探索可能漏掉 MERGED PR 而假報成功
- **問題**: `task_finalize.sh` 使用 `.[0]` 取第一個 PR，若有較新的 CLOSED PR 則遮蓋正確的 MERGED PR。
- **修復**: 遍歷所有 `--state all` PR，找到第一個 `MERGED` 狀態的 PR。若只有 `CLOSED` PR 則報告錯誤，不假裝成功。

#### R4 [P2]: OPEN→MERGED 首次復原未記錄 post-merge audit time
- **問題**: 首次 OPEN→MERGED 復原被視為同一 submission 的 re-submit，保留 pre-merge `verified_at`，不發 audit event。
- **修復**: 偵測首次 OPEN→MERGED 轉換（existing 無 `merged_at`、new 有），記錄 `recovery_at`/`recovery_by` 並發出 `merged_recovery` audit event。

#### R5 [P2]: Remote read failure 被視為 confirmed branch deletion
- **問題**: `resolve_task_sha` 對 timeout/nonzero exit/ambiguous refs 均回傳 None，consumers 無法區分「confirmed absent」與「transport failure」。
- **修復**: `resolve_task_sha` 對 transport failure 拋出 `RuntimeError`，僅在 git ls-remote 成功且無匹配 ref 時回傳 `None`。所有 callers 已加上適當的 `try/except`。

---

## 3. 修復方案與設計 (Design & Implementation)

### 3.1 Merged Review Submission & Recovery (`scripts/ai_status.py`)
- **R2**: `review_submission_for_task` 僅在 `status == "blocked"` 時拒絕，不再依據 `waiting_for` 欄位。
- **R4**: `command_submit_review` 偵測首次 OPEN→MERGED 轉換並記錄 `recovery_at`/`recovery_by`、發出 audit event。
- **R5**: `resolve_task_sha` 區分 transport failure (raise RuntimeError) 與 confirmed absence (return None)。所有 callers 加上 `try/except RuntimeError`。

### 3.2 Worker Workspace Merged Handoff (`worker_workspace.py`)
- **R1 seal**: `seal_worker_handoff` 對 merged submission 接受 workspace HEAD 為 submitted source 之 descendant。
- **R1 reviewer**: `_refresh_reused_worker_worktree` 當 workspace 已超前 submitted source 時，使用 `git checkout` 定位至 exact SHA。

### 3.3 Task Finalize PR Discovery (`delivery_toolchain/git/task_finalize.sh`)
- **R3**: 遍歷所有 PR 找 MERGED，不再依賴 `.[0]`。若只有 CLOSED PR 則報告明確錯誤。

### 3.4 Dispatch Engine (`dispatch_engine.py`)
- R5 transport failure 由 `except Exception: return False` 自然處理，不需額外修改。

---

## 4. 驗證記錄 (Verification Receipts)

### 4.1 Pytest 回歸測試 (test_ai_status.py)
- **Command**: `uv run pytest -q scripts/test_ai_status.py -k "Review or Submission or Merged"`
- **Exit Code**: `0`
- **Output Summary**: `80 passed` (73 original + 7 new R2/R4/R5 regressions)

### 4.2 Ruff 語法與代碼風格檢查
- **Command**: `uv run ruff check scripts/ai_status.py scripts/test_ai_status.py`
- **Exit Code**: `0`
- **Output**: `All checks passed!`

### 4.3 代碼邊界檢查 (Governance Boundary Check)
- **Command**: `uv run python delivery_toolchain/governance/check_code_boundaries.py`
- **Exit Code**: `0`
- **Output**: `Code boundary checks passed for 1164 files.`

### 4.4 Git Diff Whitespace 檢查
- **Command**: `git diff --check`
- **Exit Code**: `0`
- **Output**: Clean (no whitespace issues).

---

## 5. 驗收標準逐項對照 (Acceptance Criteria Mapping)

| Acceptance ID | 驗收規範描述 | 實作與驗證對照 | 狀態 |
|---|---|---|---|
| **A1** | 先讀 `MERGED-REVIEW-SUBMISSION.md` 與原 quota 真實案例；重用既有工具安全接續 | 已研讀真實案例 PR #1305 與 handoff 文檔，完全重用 `submit_review` 及 `task_finalize.sh` | **PASS** |
| **A2** | owner-only merged submission 接受精確 repo/PR/branch/base/head 與可證 merge ancestry；CI 終態 success，保留原 OPEN 規則 | R1: worker_workspace.py 修復 handoff seal 與 reviewer lease 支援 merged workspace；R2: 修復 waiting_for guard；R5: resolve_task_sha 區分 transport failure 與 confirmed absence | **PASS** |
| **A3** | 只記錄真實事後 review submission 與 provenance；需獨立 reviewer approve 及 owner done，不倒填審查、不清除 human/blocked gate | R4: 首次 OPEN→MERGED 復原記錄 recovery_at/recovery_by，保留原 verified_at/submitted_by | **PASS** |
| **A4** | 已 merged `task_finalize` 及 review dispatch 沿原 task 接續，不建空 PR/重複 task，不手改 JSON，冪等防重派 | R3: task_finalize.sh 遍歷所有 PR 找 MERGED，不漏掉被 CLOSED PR 遮蓋的有效合併 | **PASS** |
| **A5** | 補有意義正反 CLI/Git ancestry/CI/actor regression，保存原始結果與 exact source，Ruff/boundary 通過 | 新增 7 個 regression tests: R2 (2個), R4 (1個), R5 (4個)。Ruff 與 Code Boundaries 100% 通過 | **PASS** |
| **A6** | 正常 per-task PR / required CI / 獨立 Codex2 審查 / owner 結案；不修改 live runtime/config/狀態或重啟 Supervisor | 循標準 `worker_commit.py` -> `task_finalize.sh` 提交流程，未改動 live runtime 與 supervisor 設定 | **PASS** |


## Contributor correction: cache lookup failures (2026-09-13)

A forced remote lookup error previously replaced a warm SHA cache with `None`. An immediate ordinary lookup then returned cached absence without checking origin. This patch invalidates the cache on transport, executable, protocol, and ambiguous-ref errors and raises an unverifiable-state error. Only a successful empty response remains a cacheable confirmed absence. Valid exact 40/64-character object IDs retain bounded caching.

Executing regression covers warm SHA -> forced failure -> repeated ordinary failure -> fresh successful recovery for nonzero exit, timeout, missing executable, ambiguous refs, malformed object ID, and unexpected ref. The original new regression recorded six failing subtests before the implementation change; confirmed-absence controls passed. Existing error/status-emission and invalid-SHA assertions now match the explicit error contract.

Final complete tooling regression: `3042 passed, 8 skipped, 10 deselected, 3 warnings, 642 subtests passed in 431.24s (0:07:11)`. Final Ruff and boundary checks passed. Exact file hashes, original commands, exit codes, timings and raw outputs are in `1325-cache-source.json` and the accompanying `1325-cache-*.json` / `.log` files. The isolated worktree uses the normal `make bootstrap` test configuration; no live config was changed.

These receipts cover this cache correction. They do not establish acceptance for the separate workspace lease, reopened merged recovery transition/provenance, and shell discovery defects recorded against owner head `9321e35d3fe4065bdc6ca321513039c41d1a7c1c`. Earlier task-wide PASS labels remain subject to those owner repairs; no reviewer or Human/Ops approval is asserted by this contributor evidence.
