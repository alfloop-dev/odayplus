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
1. **`review_branch_for_task` 明確分支回退錯誤**:
   當任務明確指定 `branch` 或 `github.head_branch` 為帶後綴的分支（例如 `task/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001-RECOVERY-20260911`），因 `task_id_matches_branch` 回傳 `False`，明確指定的分支被忽略，進而錯誤回退到僅有 0 個 commit 的空預設分支 `task/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`。
2. **`ensure_auto_merge` 拒絕武裝**:
   在 `github_bus.py:1583` 的 `ensure_auto_merge` 中，`task_id_matches_branch(task_id, head)` 判定失敗，導致 PR 被標記為 `skipped_branch_mismatch`，auto-merge 永遠無法武裝。

### 3. Fallback 路徑與 Sidecar 守衛邊界 (Review Finding 回應)
在修復 `task_id_matches_branch` 支援 suffix match 後，必須嚴格區分「明確指定的分支 / PR head 驗證」與「無明確分支時的 fallback 探索」：
- **明確路徑 (Explicit path & auto-merge)**: `review_branch_for_task` 中的 `meta.get("head_branch") or task.get("branch")` 以及 `ensure_auto_merge` 中的 `head`，使用 suffix-tolerant 的 `task_id_matches_branch`（符合 A1, A2, A3）。
- **Fallback 路徑 (Agent branch & current branch)**: `review_branch_for_task` 中的 `agent_branch` 與 `current_branch` fallback，必須使用精確匹配 `task_id_exact_matches_branch`（僅接受完全一致或結尾為 `/{task_id}`）。
  - *原因*: Fleet 中常態存在 `<PARENT>-SIDECAR-<hex>`（如 `ODP-DEV-ROLLOUT-001-SIDECAR-26E14070`）或 sidecar 驗收分支。當父任務慣例分支不存在時，若 fallback 亦採用 suffix match，父任務會誤認領 sidecar 分支並錯誤推進 auto-merge。
  - *守衛還原*: `test_review_branch_for_task_returns_none_when_canonical_absent_and_only_sidecar_or_unrelated_branch_exists` 完全保留 `origin/dev` 原文斷言（驗證當 canonical 分支缺席且僅有 sidecar 分支時回傳 `None`）。

---

## 修復設計與實作

### 1. `.orchestrator/github_bus.py`
1. `task_id_matches_branch`（Suffix-tolerant，對齊 `supervisor.py:3975`）：
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
2. `task_id_exact_matches_branch`（Exact match，專用於 fallback 守衛）：
```python
def task_id_exact_matches_branch(task_id: str, branch: str) -> bool:
    if not task_id or not branch:
        return False
    task_ref = task_id.strip("/").lower().replace("_", "-")
    branch_ref = branch.strip("/").lower().replace("_", "-")
    return branch_ref == task_ref or branch_ref.endswith(f"/{task_ref}")
```
3. `review_branch_for_task` 中的路由區分：
- Explicit `head_branch` / `task.branch`: 使用 `task_id_matches_branch`。
- `agent_branch` fallback: 使用 `task_id_exact_matches_branch`。
- `current_branch()` fallback: 使用 `task_id_exact_matches_branch`。

### 2. `.orchestrator/test_github_bus.py`
- 新增 `test_task_id_matches_branch_accepts_canonical_and_suffixed_and_rejects_substrings`：涵蓋 A1 正例（`task/<id>`, `task/<id>-<suffix>`, `<id>`, `<id>-<suffix>`, 大小寫/底線變體, `origin/task/<id>`）與反例（`task/<id>0`, `task/<id>X`, `task/<id>0-RECOVERY`, 異任務 ID, 空字串）。
- 新增 `test_review_branch_for_task_prefers_explicit_suffixed_branch_over_canonical_coexisting`：涵蓋 A2 實況 fixture（`ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` 在 recovery 分支與 canonical 分支並存時，優先並正確回傳 recovery 分支）。
- 新增 `test_pr_from_suffixed_task_branch_is_armed`：涵蓋 A3 auto-merge 武裝情境，驗證帶後綴之 PR head 能成功啟用 auto-merge。
- 還原 `test_review_branch_for_task_returns_none_when_canonical_absent_and_only_sidecar_or_unrelated_branch_exists` 至 `origin/dev` 原文，確保 sidecar fallback 守衛依然嚴密。
- `test_review_branch_for_task_rejects_substring_agent_branch`：驗證子字串相異 ID 之明確指定分支會被拒絕並回退至 canonical。

---

## 驗收條件核對清單與 A1/A2 vs A4 取捨說明

- [x] **A1**: `github_bus.py:task_id_matches_branch(task_id, branch)` 接受 `task/<task_id>` 與 `task/<task_id>-<suffix>`（大小寫不敏感、底線與連字號等價），語意與 `supervisor.py` 約 3975 行一致；仍拒絕子字串相關但不同的 id（如 `task/ODP-FOO-0010`、`task/ODP-FOO-001X`）。
- [x] **A2**: `review_branch_for_task` 在 task 有明確 `branch` 欄位、該分支存在且名稱通過 A1 時，回傳該欄位，不因慣例名 `task/<id>` 亦存在而回退。以 `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` 為 fixture 通過測試。
- [x] **A3**: auto-merge 武裝路徑（`github_bus.py:1583`）對帶後綴且通過 A1 的分支不再拒絕武裝，成功標記 `enabled` 並調用 `gh pr merge --auto`。
- [x] **A4**: 新增測試涵蓋 A1 正反例、A2 雙分支並存、A3 武裝情境；.orchestrator/test_github_bus.py 既有 branch 相關測試維持通過，不得修改其斷言（*關於單一衝突測試之取捨見下方說明*）。
- [x] **A5**: 未修改 `supervisor.py`；未改動 canonical 看板；未 push 或 fast-forward 任何既有 task 分支。

### A1/A2 與 A4 既有斷言的矛盾與取捨揭露
> **說明**:
> A4 原文規範「`.orchestrator/test_github_bus.py` 既有 branch 相關測試維持通過，不得修改其斷言」。
> 然而在 `origin/dev` 中，既有測試 `test_review_branch_for_task_rejects_related_sidecar_agent_branch` 原文設定了 `task["github"]["head_branch"] = "task/ODP-FOO-001-SIDECAR-ACCEPTANCE"`，並斷言：
> 1. `assertEqual(found_branch, "task/ODP-FOO-001")`（斷言 explicit 的 suffixed 分支被忽略而回退到 canonical）
> 2. `assertFalse(task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-001-SIDECAR-ACCEPTANCE"))`
>
> 此二斷言與本任務之核心驗收條件 A1（要求 `task_id_matches_branch` 接受 `task/<id>-<suffix>`）以及 A2（要求 explicit 分支存在且符合 A1 時優先採用、不得回退至 canonical）在語意上**直接衝突且互斥**。
>
> 為落實 A1 與 A2 所要求的修正目標，同時維持「拒絕非相同 task 之子字串分支」的測試意圖，該測試調整為測試相異 ID 子字串（`task/ODP-FOO-0010`），確認非後綴的子字串分支會被拒絕。除此一因 A1/A2 修復目標而必須調整的衝突斷言外，其餘所有既有 branch 與 sidecar 測試（包括 `test_review_branch_for_task_returns_none_when_canonical_absent_and_only_sidecar_or_unrelated_branch_exists`）均原汁原味全數通過。

---

## 驗證執行結果 (Verification Receipts)

### 1. PyTest Suite
```bash
$ PATH="$HOME/.local/bin:$PATH" uv run pytest -q .orchestrator/test_github_bus.py -k "branch or matches or auto_merge"
.......................................                                  [100%]
39 passed, 150 deselected in 1.48s
```

全量 `test_github_bus.py`：
```bash
$ PATH="$HOME/.local/bin:$PATH" uv run pytest -q .orchestrator/test_github_bus.py
................................................................. [ 36%]
..................................................................... [ 74%]
.............................................          [100%]
189 passed in 4.12s
```

### 2. Ruff Linter
```bash
$ PATH="$HOME/.local/bin:$PATH" uv run ruff check .orchestrator/github_bus.py .orchestrator/test_github_bus.py
All checks passed!
```

### 3. Git Diff Check
```bash
$ git diff --check
(exit code 0, no trailing whitespace or formatting issues)
```
