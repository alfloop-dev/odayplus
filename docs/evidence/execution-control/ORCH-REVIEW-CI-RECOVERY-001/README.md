# ORCH-REVIEW-CI-RECOVERY-001：讓 review CI 失敗可沿原任務回派 owner 修復

- Task: ORCH-REVIEW-CI-RECOVERY-001
- Owner: Antigravity ／ Reviewer: Codex2
- Base commit（dev）: `1260d977`
- 交付 branch: `task/ORCH-REVIEW-CI-RECOVERY-001`
- 本文件不宣稱 live runtime 已部署；修復在於本 branch 程式碼與回歸測試，等待 PR 審查與合併。

---

## 1. 缺陷與根因分析

在修復前，未核准的審查任務（`status == "review"`）在遇到 required CI 失敗時（`task_pr_ci_status` 回傳 `failure`），dispatcher 在審查派工迴圈（`dispatch_engine.py`）僅記錄：
```text
PR for task <task_id> has CI failure (failure); review dispatch suppressed until CI is repaired.
```
並寫入 `review_dispatch_suppressed` activity log 後直接 `continue`。

這造成了致命的排程停滯（stagnation gap）：
1. **Reviewer 不會被派工**：因為 required CI 必須為 `success` 才能進入 `review_ready_dispatch`。
2. **Owner 不會被回派**：任務狀態依然停留在 `status == "review"`，且 `waiting_for` 與 handoff 指向 reviewer，owner 派工迴圈無法取得該任務。
3. **實際案例**：`ODP-DATA-PLANE-DELETE-PROPAGATION-001` PR #1282 head `a964c83d66103444953e4ed2e8741b8e484a0f42`，因 GitHub run `34386285095` 之 `product-e2e-gate` 套件 index Hash Sum mismatch 導致 CI 退出，任務即陷入此停滯狀態。

---

## 2. 審查反饋修復（Codex2 PR #1288 審查要點）

根據 Codex2 在 PR #1288 審查提出的五大發現，本變更進行了嚴格而深入的加固：

### 2.1 消除 CI 解析分歧與強化 Check Rollup 驗證（Finding 1）
- **雙重驗證機制**：利用 `runtime_ai_status.latest_status_check_runs` 解析 GitHub `statusCheckRollup`，統一 CI 判斷標準。
- **嚴格驗證 Terminal Conclusion**：
  - 明確排除含有任何 pending / in-progress 狀態的檢查。
  - 排除 unknown conclusion 或非 dictionary 之畸形 rollup 項目。
  - 只有在所有檢查皆有確定結論且包含 failure 時才認定為 terminal CI failure。
- **豐富診斷資訊**：提取具體失敗 checks 之名稱、結論與 URL，寫入 `task["next"]` 與 activity log，使 Owner 能精確定位失敗原因。

### 2.2 CAS / 狀態發散防禦（Finding 2）
- 在 `recover_failed_ci_review_prs` 與 `recover_conflicted_review_prs` 中，當 `requeue_task_for_ci_repair` 或 canonical transition commit 因 CAS mismatch 失敗時：
  - 立即還原 in-memory marker。
  - 立即清空並自磁碟重新載入最新 `status`（`status.clear(); status.update(load_status(config))`）。
  - 中斷該 tick 之進一步 recovery 操作並安全退出，防止在過時 snapshot 上繼續操作。

### 2.3 派工候選狀態同步（Finding 3）
- 在 `dispatch_ready_tasks` 的 reconciliation 階段（包含 helper claims 釋放、churn 重派、mainline 正規化、衝突恢復與 CI 失敗恢復）完成後：
  - 在進入候選任務派工迴圈前，強制重新從磁碟載入最新 `status`、`tasks` 與 `task_map`。
  - 徹底杜絕因記憶體中物件狀態曾被暫時修改但未成功持久化，導致錯誤派發 `owned_in_progress_dispatch` 的風險。

### 2.4 Human Gate 與 Human Waiting 完整保護（Finding 4）
- 在 `requeue_task_for_ci_repair`、`recover_failed_ci_review_prs` 與 `recover_conflicted_review_prs` 中：
  - 除既有的 `task_is_human_gate` 與 `non_dispatchable` 檢查外，增加 `is_human_gate_agent(task.get("owner"))` 與 `is_human_gate_agent(task.get("waiting_for"))` 雙重防護。
  - 確保處於人工審查閘門或等待 Human/Ops 介入的任務絕不會被自動轉移或錯誤重開至 AI Owner。

### 2.5 測試名稱與覆蓋加固（Finding 5）
- 新增完整的 pending checks、unknown conclusion、畸形 rollup、human gate 防護、真實 CAS race 中斷、完整生命週期（CI failure -> recovery -> owner dispatch -> resubmission -> review dispatch）等回歸測試。
- 統一測試命名，確保 pytest `-k "ci_repair or ci_failure or conflicted_review"` 完整選中所有相關回歸測試。

---

## 3. 解決架構與設計

### 3.1 `status_transition.requeue_task_for_ci_repair`
- 新增 `allow_failed_ci_review: bool = False` 具名入口旗標。
- 當 `(allow_conflicted_review or allow_failed_ci_review)` 為 True 時，僅在 task 為未核准（`status == "review"` 且無 `approved_head` 與 `merge_route`）且 review submission 完整時放行進入 `in_progress`。
- 清理過時的 reviewer `waiting_for`，並將對應的 pending handoff 標記為 `done`（帶 `resolved_at`）。
- 嚴格守門 `is_human_gate_agent`。
- 使用 `REOPEN_CATEGORY_CONTROL_PLANE_RECOVERY` 與 `REOPEN_REASON_CONTROL_PLANE_RECOVERY`，確保不計入 review churn / review finding 計數。
- 記錄 activity log 事件 `ci_repair_requeued`，其中 `entry` 設為 `"review_ci_failure"`。

### 3.2 `dispatch_engine.recover_failed_ci_review_prs`
- 放置於 `dispatch_ready_tasks` 的 **reconciliation 階段**（與 `recover_conflicted_review_prs` 並列），不依賴 reviewer slot 或 agent loop 順序。
- **嚴格守門條件**：
  1. Task 處於 `review_statuses`（`"review"`）且非 busy（不在 `active_task_ids | pending_task_ids`）。
  2. 非 Human gate（非 `task_is_human_gate`、非 `is_human_gate_agent(owner)`、非 `is_human_gate_agent(waiting_for)`）且非 `non_dispatchable`。
  3. 未 approved（無 `approved_head`）且未 queued（無 `merge_route`）。
  4. 無活躍的 helper execution lease。
  5. 具有完整 verified review submission（`pr_number > 0` 與 `remote_sha`）。
  6. 遠端 HEAD 與 submitted SHA 一致（無 HEAD drift）。
  7. CI 經由 cached read 與 fresh read（`max_age_seconds=0`）皆確定為 `failure` 且 PR 為 `OPEN`。
  8. PR facts（state/mergeStateStatus/headRefOid）在 CI 探測前後一致（防 race）。
  9. GitHub `statusCheckRollup` 經 terminal 驗證確認無 pending/unknown，並取得具體 failed checks 清單。
- **持久化追蹤與重複 tick 冪等性**：
  - 定義 `REVIEW_CI_FAILURE_RECOVERY_HEAD_FIELD = "review_ci_failure_recovery_head"`。
  - 對同一 submitted head 僅執行一次 recovery，後續 tick 不會反覆 reopen。
  - 當 owner 修改程式碼並經由 `task_finalize.sh` 提交新 head 後，若新 head 再次失敗，則可再次安全觸發 recovery。
- **回派診斷訊息**：
  - `task["next"]` 明確指出 PR 編號、失敗的 submitted head 前 8 碼、失敗的 checks 清單，並引導 owner 修復 CI 後透過 `task_finalize.sh` 重新提交。

---

## 4. 驗證收據

所有命令皆在 `/tmp/pantheon-worker-worktrees/pantheon/orch-review-ci-recovery-001` 執行，未經 pipe、未加 `|| true`、未 background，保存工具本身真實 exit code。

- Task Branch：`task/ORCH-REVIEW-CI-RECOVERY-001`
- 執行環境：`PYTHONPATH=.orchestrator:scripts .venv/bin/python`

| # | 命令 | Exit Code | 結果摘要 |
|---|---|---|---|
| 1 | `git diff --check` | 0 | 無任何 whitespace 或 formatting 錯誤 |
| 2 | `PYTHONPATH=.orchestrator:scripts .venv/bin/python -m pytest -v .orchestrator/test_dispatch_policy.py -k "ci_repair or ci_failure or conflicted_review"` | 0 | 68 passed, 107 deselected in 2.23s |
| 3 | `PYTHONPATH=.orchestrator:scripts .venv/bin/python -m pytest -v .orchestrator/test_dispatch_policy.py` | 0 | 175 passed in 30.10s |
| 4 | `PYTHONPATH=.orchestrator:scripts .venv/bin/python -m pytest -v .orchestrator/ -k "ci_repair or review_resubmission or reopen_audit"` | 0 | 12 passed, 1928 deselected in 6.14s |

---

## 5. 邊界與規範

1. **窄邊界**：僅修改 `.orchestrator/status_transition.py`, `.orchestrator/dispatch_engine.py`, `.orchestrator/test_dispatch_policy.py` 與本 evidence 文件。
2. **無未授權修改**：未手動修改 live board、未動 runtime symlink、未動 Supervisor 運行中程序。
3. **交付路徑**：透過標準 `worker_commit.py` 與 `task_finalize.sh` 建立 PR，等待獨立 Reviewer（Codex2）審查與 CI 合併。
