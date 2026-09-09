# ORCH-REVIEW-CI-RECOVERY-001：讓 review CI 失敗可沿原任務回派 owner 修復

- Task: ORCH-REVIEW-CI-RECOVERY-001
- Owner: Antigravity ／ Reviewer: Codex2
- Base commit（dev）: `1260d977`
- 交付 branch: `task/ORCH-REVIEW-CI-RECOVERY-001`
- 本文件不宣稱 live runtime 已部署；修復在於本 branch 程式碼與回歸測試，等待 PR 審查與合併。

---

## 1. 缺陷與根因分析

在修復前，未核准的審查任務（`status == "review"`）在遇到 required CI 失敗時（`task_pr_ci_status` 回傳 `failure`），dispatcher 在審查派工迴圈（`dispatch_engine.py:2437`）僅記錄：
```text
PR for task <task_id> has CI failure (failure); review dispatch suppressed until CI is repaired.
```
並寫入 `review_dispatch_suppressed` activity log 後直接 `continue`。

這造成了致命的排程停滯（stagnation gap）：
1. **Reviewer 不會被派工**：因為 required CI 必須為 `success` 才能進入 `review_ready_dispatch`。
2. **Owner 不會被回派**：任務狀態依然停留在 `status == "review"`，且 `waiting_for` 與 handoff 指向 reviewer，owner 派工迴圈無法取得該任務。
3. **實際案例**：`ODP-DATA-PLANE-DELETE-PROPAGATION-001` PR #1282 head `a964c83d66103444953e4ed2e8741b8e484a0f42`，因 GitHub run `34386285095` 之 `product-e2e-gate` 套件 index Hash Sum mismatch 導致 CI 退出，任務即陷入此停滯狀態。

---

## 2. 解決架構與設計

為徹底修復此缺口，我們沿用 `dispatch_engine.py` 與 `status_transition.py` 的唯一 canonical transition 體系，建立了明確且窄的 review CI-failure recovery：

### 2.1 `status_transition.requeue_task_for_ci_repair`
- 新增 `allow_failed_ci_review: bool = False` 具名入口旗標。
- 當 `(allow_conflicted_review or allow_failed_ci_review)` 為 True 時，僅在 task 為未核准（`status == "review"` 且無 `approved_head` 與 `merge_route`）且 review submission 完整時放行進入 `in_progress`。
- 清理過時的 reviewer `waiting_for`，並將對應的 pending handoff 標記為 `done`（帶 `resolved_at`）。
- 使用 `REOPEN_CATEGORY_CONTROL_PLANE_RECOVERY` 與 `REOPEN_REASON_CONTROL_PLANE_RECOVERY`，確保不計入 review churn / review finding 計數。
- 記錄 activity log 事件 `ci_repair_requeued`，其中 `entry` 設為 `"review_ci_failure"`。

### 2.2 `dispatch_engine.recover_failed_ci_review_prs`
- 放置於 `dispatch_ready_tasks` 的 **reconciliation 階段**（與 `recover_conflicted_review_prs` 並列），不依賴 reviewer slot 或 agent loop 順序。
- **嚴格守門條件**：
  1. Task 處於 `review_statuses`（`"review"`）且非 busy（不在 `active_task_ids | pending_task_ids`）。
  2. 非 Human gate 且非 `non_dispatchable`。
  3. 未 approved（無 `approved_head`）且未 queued（無 `merge_route`）。
  4. 無活躍的 helper execution lease。
  5. 具有完整 verified review submission（`pr_number > 0` 與 `remote_sha`）。
  6. 遠端 HEAD 與 submitted SHA 一致（無 HEAD drift）。
  7. CI 經由 cached read 與 fresh read（`max_age_seconds=0`）皆確定為 `failure` 且 PR 為 `OPEN`。
  8. PR facts（state/mergeStateStatus/headRefOid）在 CI 探測前後一致（防 race）。
- **持久化追蹤與重複 tick 冪等性**：
  - 定義 `REVIEW_CI_FAILURE_RECOVERY_HEAD_FIELD = "review_ci_failure_recovery_head"`。
  - 對同一 submitted head 僅執行一次 recovery，後續 tick 不會反覆 reopen 或新增 worker。
  - 當 owner 修改程式碼並經由 `task_finalize.sh` 提交新 head 後，若新 head 再次失敗，則可再次安全觸發 recovery。
- **回派診斷訊息**：
  - `task["next"]` 明確指出 PR 編號、失敗的 submitted head 前 8 碼，並引導 owner 修復 CI 後透過 `task_finalize.sh` 重新提交。

### 2.3 `supervisor.py`
- 在 `supervisor.requeue_task_for_ci_repair` 中轉發 `allow_failed_ci_review` 參數。

---

## 3. 回歸測試覆蓋

在 `.orchestrator/test_dispatch_policy.py` 補齊完整的 dispatcher 與 transition 回歸測試：

1. `test_failed_ci_review_is_returned_to_its_owner`:
   - 驗證未核准 review 任務在 CI failure 下成功回到 `in_progress`。
   - 驗證 owner、reviewer、acceptance、depends_on、review_submission 完整保留。
   - 驗證 waiting_for 清除、handoff 標記為 done。
   - 驗證 `REVIEW_CI_FAILURE_RECOVERY_HEAD_FIELD` 記錄 exact head。
   - 驗證 `ci_repair_requeued`（`entry: review_ci_failure`）與 `review_ci_failure_recovered` 事件。
   - 驗證 `gh pr view` 雙重讀取綁定 repo 與 PR。
2. `test_failed_ci_recovery_declines_every_unconfirmable_reading`:
   - 覆蓋 `ci_pending`, `ci_success`, `ci_none`, `ci_unknown`, `ci_unreadable_pr`, `ci_probe_raises`, `pr_closed_by_ci_probe`, `pr_closed`, `pr_merged`, `gh_offline`, `gh_malformed_json`, `gh_empty_payload`, `missing_head`, `missing_state`, `head_drift` 共 15 種不可確認或不合規情形，全部正確拒絕變更。
3. `test_repeated_tick_on_same_failed_ci_head_is_idempotent`:
   - 驗證對相同 head 不重複觸發 recovery。
4. `test_resubmitted_new_head_with_ci_failure_can_recover_again`:
   - 驗證 owner 提交新 head 後若再失敗可正常二次 recovery。
5. `test_canonical_ci_repair_transition_allows_failed_ci_review_and_keeps_guard`:
   - 驗證 transition 的 `allow_failed_ci_review` 門檻，以及對 `frozen_approved_head`, `queued_merge_route`, `unsubmitted_review`, `human_gate`, `non_dispatchable`, `wrong_status` 等不合法狀態的防禦。
6. `test_dispatch_ready_tasks_recovers_failed_ci_review_without_reviewer_slot`:
   - 驗證 `dispatch_ready_tasks` 在無需 reviewer slot 的情況下於 reconciliation 階段成功執行 recovery。
7. `test_failed_ci_review_recovery_then_success_reaches_review_dispatch`:
   - 驗證 owner 修復並 resubmit、CI success 後，reviewer 正確獲得 `review_ready_dispatch` 資格。

---

## 4. 驗證收據

所有命令皆在 `/tmp/pantheon-worker-worktrees/pantheon/orch-review-ci-recovery-001` 執行，未經 pipe、未加 `|| true`、未 background，保存工具本身真實 exit code。

- Task Branch：`task/ORCH-REVIEW-CI-RECOVERY-001`
- 執行環境：`uv run --python 3.12` / `PYTHONPATH=.orchestrator:scripts`

| # | 命令 | Exit Code | 結果摘要 |
|---|---|---|---|
| 1 | `git diff --check` | 0 | 無任何 whitespace 或 formatting 錯誤 |
| 2 | `PYTHONPATH=.orchestrator:scripts .venv/bin/pytest -q .orchestrator/test_dispatch_policy.py -k "ci_repair or ci_failure or conflicted_review"` | 0 | 7 passed in 3.19s |
| 3 | `PYTHONPATH=.orchestrator:scripts .venv/bin/pytest -q .orchestrator/test_dispatch_policy.py` | 0 | 160 passed in 17.58s |
| 4 | `PYTHONPATH=.orchestrator:scripts .venv/bin/pytest -q .orchestrator/test_supervisor.py -k "ci_repair or ci_failure"` | 0 | 5 passed in 6.43s |

---

## 5. 邊界與規範

1. **窄邊界**：僅修改 `.orchestrator/status_transition.py`, `.orchestrator/supervisor.py`, `.orchestrator/dispatch_engine.py`, `.orchestrator/test_dispatch_policy.py` 與本 evidence 文件。
2. **無未授權修改**：未手動修改 live board、未動 runtime symlink、未動 Supervisor 運行中程序。
3. **交付路徑**：透過標準 `worker_commit.py` 與 `task_finalize.sh` 建立 PR，等待獨立 Reviewer（Codex2）審查與 CI 合併。
