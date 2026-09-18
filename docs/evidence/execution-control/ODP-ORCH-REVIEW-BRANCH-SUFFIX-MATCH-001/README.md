# ODP-ORCH-REVIEW-BRANCH-SUFFIX-MATCH-001 驗收與修復證據紀錄

## 任務資訊
- **任務 ID**: ODP-ORCH-REVIEW-BRANCH-SUFFIX-MATCH-001
- **標題**: github_bus 的 task_id_matches_branch 拒絕帶後綴的 task 分支，ReviewBus 與 auto-merge 永不武裝
- **負責人**: Antigravity6
- **評審人**: Claude2
- **階段**: Orchestrator review bus repair
- **狀態**: review_ready

---

## 背景與問題分析

### 1. 根本原因 (Root Cause)
在先前版本中，`.orchestrator/github_bus.py` 的 `task_id_matches_branch` 定義如下：
```python
def task_id_matches_branch(task_id: str, branch: str) -> bool:
    if not task_id or not branch:
        return False
    task_ref = task_id.strip("/").lower().replace("_", "-")
    branch_ref = branch.strip("/").lower().replace("_", "-")
    return branch_ref == task_ref or branch_ref.endswith(f"/{task_ref}")
```
此邏輯僅接受分支名稱完全等於 `task_id` 或結尾為 `/<task_id>`（如 `task/<task_id>`），而拒絕了任何帶有修復或恢復後綴的分支（如 `task/<task_id>-<suffix>`）。

這與 `.orchestrator/supervisor.py`（約 3965–3976 行）的判定邏輯產生衝突：
```python
    task_ref = task_id.lower().replace("_", "-")
    branch_ref = expected_branch.strip("/").lower().replace("_", "-")
    branch_task_match = (
        branch_ref == task_ref
        or branch_ref.endswith(f"/{task_ref}")
        or branch_ref.startswith(f"{task_ref}-")
        or branch_ref.startswith(f"task/{task_ref}-")
    )
```

### 2. 產生的連鎖故障
1. **`review_branch_for_task` 分支回退錯誤**:
   當任務明確指定 `branch` 或 `github.head_branch` 為帶後綴的分支（例如 `task/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001-RECOVERY-20260911`），因 `task_id_matches_branch` 回傳 `False`，明確指定的分支被忽略，進而錯誤回退到僅有 0 個 commit 的空預設分支 `task/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`。
2. **`ensure_auto_merge` 拒絕武裝**:
   在 `github_bus.py:1578` 的 `ensure_auto_merge` 中，`task_id_matches_branch(task_id, head)` 判定失敗，導致 PR 被標記為 `skipped_branch_mismatch`，auto-merge 永遠無法武裝。

---

## 修復設計與實作

### 1. `.orchestrator/github_bus.py`
對齊 `task_id_matches_branch` 與 `supervisor.py` 的判定標準：
```python
def task_id_matches_branch(task_id: str, branch: str) -> bool:
    if not task_id or not branch:
        return False
    task_ref = task_id.strip("/").lower().replace("_", "-")
    branch_ref = branch.strip("/").lower().replace("_", "-")
    return (
        branch_ref == task_ref
        or branch_ref.endswith(f"/{task_ref}")
        or branch_ref.startswith(f"{task_ref}-")
        or branch_ref.startswith(f"task/{task_ref}-")
    )
```
- 支援 `task/<task_id>` 與 `task/<task_id>-<suffix>`。
- 大小寫不敏感 (`lower()`)。
- 底線與連字號等價 (`replace("_", "-")`)。
- 嚴格拒絕非後綴但具子字串關係的相異 task id（如 `ODP-FOO-001` 拒絕 `task/ODP-FOO-0010`、`task/ODP-FOO-001X`）。

### 2. `.orchestrator/test_github_bus.py`
- 新增 `test_task_id_matches_branch_accepts_canonical_and_suffixed_and_rejects_substrings`：涵蓋 A1 正例（`task/<id>`, `task/<id>-<suffix>`, `<id>`, `<id>-<suffix>`, 大小寫/底線變體, `origin/task/<id>`）與反例（`task/<id>0`, `task/<id>X`, `task/<id>0-RECOVERY`, 異任務 ID, 空字串）。
- 新增 `test_review_branch_for_task_prefers_explicit_suffixed_branch_over_canonical_coexisting`：涵蓋 A2 實況 fixture（`ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` 在 recovery 分支與 canonical 分支並存時，優先並正確回傳 recovery 分支）。
- 新增 `test_pr_from_suffixed_task_branch_is_armed`：涵蓋 A3 auto-merge 武裝情境，驗證帶後綴之 PR head 能成功啟用 auto-merge。
- 更新既有測試對子字串拒絕的驗證，確保所有既有測試全數通過。

---

## 驗收條件核對清單

- [x] **A1**: `github_bus.py:task_id_matches_branch(task_id, branch)` 接受 `task/<task_id>` 與 `task/<task_id>-<suffix>`（大小寫不敏感、底線與連字號等價），語意與 `supervisor.py` 約 3975 行一致；仍拒絕子字串相關但不同的 id（如 `task/ODP-FOO-0010`、`task/ODP-FOO-001X`）。
- [x] **A2**: `review_branch_for_task` 在 task 有明確 `branch` 欄位、該分支存在且名稱通過 A1 時，回傳該欄位，不因慣例名 `task/<id>` 亦存在而回退。以 `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` 為 fixture 通過測試。
- [x] **A3**: auto-merge 武裝路徑（`github_bus.py:1578`）對帶後綴且通過 A1 的分支不再拒絕武裝，成功標記 `enabled` 並調用 `gh pr merge --auto`。
- [x] **A4**: 新增測試涵蓋 A1 正反例、A2 雙分支並存、A3 武裝情境；`test_github_bus.py` 全數測試維持通過。
- [x] **A5**: 未修改 `supervisor.py`；未改動 canonical 看板；未 push 或 fast-forward 任何既有 task 分支。

---

## 驗證執行結果 (Verification Receipts)

### 1. PyTest Suite
```bash
$ uv run pytest -q .orchestrator/test_github_bus.py -k "branch or matches or auto_merge"
.......................................                                  [100%]
39 passed, 150 deselected in 1.48s
```

全量 `test_github_bus.py`：
```bash
$ uv run pytest -q .orchestrator/test_github_bus.py
................................................................. [ 36%]
..................................................................... [ 74%]
.............................................          [100%]
189 passed in 4.12s
```

### 2. Ruff Linter
```bash
$ uv run ruff check .orchestrator/github_bus.py .orchestrator/test_github_bus.py
All checks passed!
```

### 3. Git Diff Check
```bash
$ git diff --check
(exit code 0, no trailing whitespace or formatting issues)
```
