# ODP-MERGE-GROUP-STALE-FAILURE-GUARD-001: 舊 merge-group 失敗誤撤銷核准單一路徑防護

- Task: `ODP-MERGE-GROUP-STALE-FAILURE-GUARD-001`
- Status: `ready_for_review`
- Owner: `Antigravity`
- Reviewer: `Codex`
- Date: `2026-09-06`

---

## 1. 問題背景與根因分析 (Incident & Root Cause)

在 PR #1212 合併過程中，發現了如下異常路徑：
1. **事實情境**：PR #1212 的審查核准 head 為 `a9e7853f9ed910303eb6b7fa1d81c1e1b46c5787`，PR head 完全未變更。
2. **舊 Run 結算干擾**：舊 merge group run `34003742537`（merge group head `908e99fa`）於 `01:33:23` 結算為 `cancelled`。
3. **誤撤銷核准**：Reconciler 於 `01:34` 輪詢處理到該舊 run 的 cancelled 狀態，在未核實 PR 最新狀態及新 run 的情況下，立即將任務由 `review_approved` 降級至 `review` 並清空 `approved_head`。
4. **新 Run 實質成功**：事實上，新 merge group run `34004041183`（merge group head `17393dd`）已於 `01:29:54` 啟動為 pending，並於 `01:54:17` 成功，PR 亦於 `01:54:20` 完成合併。

### 根因歸納
- GitHub Actions 的 `merge_group` workflow run SHA 為 Merge Queue 自動生成的臨時 merge commit SHA，而非 PR 本身的 branch head SHA。
- 既有 `reconcile_merge_group_runs()` 在遇到 failure/cancelled conclusion 時，缺乏對 PR 即時狀態（是否已 merged、PR head 是否仍與 approved_head 一致）以及是否有更新的同 PR、同 workflow merge group run 接替（pending 或 success）的驗證，導致亂序或延遲回傳的舊失敗覆蓋了正常進行中的佇列流程。

---

## 2. 防護設計與實作 (Guard Design)

在 `.orchestrator/github_reconciliation.py` 的單一恢復流程中，於發動 CAS 狀態轉移與降級前，增加了全套即時查證、血統驗證與防護邏輯：

1. **嚴格即時 PR 事實查證 (`fetch_pr_facts` 與 `is_valid_pr_facts`)**：
   - 透過 `gh_json` 查詢 PR 的即時 `state`、`headRefOid`、`mergedAt` 等資訊。
   - 若 `pr_facts` 為空字典 `{}`、`None` 或缺失 `state`/`headRefOid` 欄位，嚴格判定為 `unresolved`，**不降級且不標記為 processed**，保留至下一輪。
   - 若 PR 已處於 `MERGED` 狀態，判定該失敗為過期事件（stale），記錄 `merge_group_failure_stale` (reason: `already_merged`)，寫入 processed ID 去重，不變更任務狀態與審查閘門。
   - 若 PR 已非 `OPEN` 狀態（如 closed），同樣記錄 stale 並去重，不觸發降級。

2. **Reviewed PR Head 完整 Exact SHA 一致性驗證**：
   - 核對 PR 的 `headRefOid` 是否與 task 的 `approved_head` 完整相等（`pr_head_sha.lower() == approved_head.lower()`）。
   - 禁止使用 `startswith` 短 SHA 前綴比對。
   - 若 PR head 不一致或無法解析，判定關聯不明確，記錄 `merge_group_failure_unresolved`，**不降級任務且不永久標記為 processed**，保留至下一輪重新查證。

3. **嚴格 Workflow 身份與血統包含驗證 (`match_workflow_identity` 與 `verify_group_contains_head`)**：
   - **Workflow 身份核對**：候選 run 與目標 run 必須具備相同的 `workflow_id` 或非空 `name`；若任一缺少 workflow 身份則不可匹配。
   - **候選 Run 完整 Head SHA 檢查**：候選 run 必須具備有效完整的 `head_sha`，缺 head_sha 者不可作為接替候選。
   - **Merge Group 包含 Approved PR Head 驗證**：透過本地 `git merge-base --is-ancestor` 或 GitHub compare API 證明該 merge group commit 包含完整的 `approved_head`。
   - 若符合上述條件且狀態為 active（`in_progress`, `queued`, `pending` 等且非 failure）或 `success`：
     - 判定舊 run 為過期事件（stale），記錄 `merge_group_failure_stale`（reason: `pending_group` 或 `successful_group`），將舊 run 標記為 processed 去重。
     - 保持任務 `review_approved` 與 `approved_head` 完整，不修改 handoffs 與 GitHub review gate。

4. **Pre-CAS / Pre-Demote 即時二次查證 (Live Re-verification)**：
   - **Facts B 二次查證**：在發動降級前再次讀取 PR facts B，確認 PR 仍為 OPEN 且 PR head 未發生漂移（drift）。
   - **即時 Merge Queue 佇列查驗**：透過 `fetch_pr_merge_queue_status` / `is_in_merge_queue` 查驗 PR 當前是否已在 merge queue 內，若在佇列中則視為 stale 接替，不降級。
   - **最新 Runs 快照查驗**：重新取得最新 merge group runs 快照，防止輪詢窗口間隙內已啟動新 group 的競態。

5. **安全防界與真實失敗處理 (Fail-Safe Boundary)**：
   - **防遮掩真實失敗**：不同 PR、不同 PR head、不同 workflow、不同 head 包含關係的 run 絕對不可用於覆蓋真正失敗。
   - **真實當前失敗恢復**：若經查證無新 run 接替且 PR 為 OPEN 且 head 一致，則維持既有 Reviewer Recovery 流程，執行 CAS 轉移至 `review`，派發 reviewer recovery handoff，並於 CAS 成功後重送 `task-review-gate` 待審狀態。
   - **CAS 失敗防護**：若 CAS 狀態轉移拒絕，不標記 processed、不發送外部 review gate。

---

## 3. 焦點驗證結果 (Verification Evidence)

### A. `test_github_bus.py` (MergeGroupReconciliationTests)
執行命令：
```bash
PYTHONPATH=.orchestrator python3 -m unittest test_github_bus.MergeGroupReconciliationTests
```
輸出結果：
```text
....................................
----------------------------------------------------------------------
Ran 36 tests in 10.977s

OK
```
涵蓋之核心與負向測試項目：
1. `test_parse_merge_group_pr_number`：驗證標準 merge group ref 格式解析。
2. `test_parse_merge_group_rejects_bare_pr_ref`：非標準 bare ref 拒絕。
3. `test_save_bus_state_retains_merge_group_failure_audit_record`：審計記錄持久化。
4. `test_correlate_merge_group_task`：任務關聯（pr_number, submission, route, bus_state）。
5. `test_is_valid_pr_facts`：PR facts 結構完整性驗證。
6. `test_match_workflow_identity`：Workflow 身份匹配與缺失拒絕。
7. `test_verify_group_contains_head`：本地 git merge-base 與 GitHub compare API 血統驗證。
8. `test_reconcile_merge_group_superseded_by_pending_run_is_stale_and_non_mutating`：新 group pending 接替，舊 run 判定 stale 去重不降級。
9. `test_reconcile_merge_group_superseded_by_success_run_is_stale_and_non_mutating`：新 group success 接替，舊 run 判定 stale 去重不降級。
10. `test_reconcile_merge_group_pr_already_merged_is_stale_and_non_mutating`：PR 已 merged，舊 run 判定 stale 去重不降級。
11. `test_reconcile_merge_group_different_pr_does_not_mask_genuine_failure`：不同 PR 的 run 不得遮掩真失敗。
12. `test_reconcile_merge_group_different_workflow_does_not_mask_genuine_failure`：不同 workflow 的 run 不得遮掩真失敗。
13. `test_reconcile_merge_group_different_pr_head_does_not_mask_genuine_failure`：PR head 不一致不得遮掩真失敗。
14. `test_reconcile_merge_group_newgroup_different_head_does_not_supersede_failure`：候選 group 不包含 approved head 時不得接替。
15. `test_reconcile_merge_group_newgroup_missing_head_sha_does_not_supersede_failure`：候選 group 缺失 head_sha 時不得接替。
16. `test_reconcile_merge_group_missing_workflow_identity_does_not_match`：缺失 workflow identity 時不得匹配。
17. `test_reconcile_merge_group_fresh_queue_enrollment_supersedes_failure`：Pre-CAS 發現 PR 在 queue 中時判定 stale 不降級。
18. `test_reconcile_merge_group_pr_head_drift_at_facts_b_does_not_demote`：Facts B 發現 head 漂移時 fail-safe 保留不降級。
19. `test_reconcile_merge_group_exact_sha_no_prefix_match`：短 SHA 前綴比對拒絕。
20. `test_reconcile_merge_group_empty_or_missing_fields_facts_does_not_demote_and_does_not_mark_processed`：空 facts `{}` / 缺欄位時 fail-safe 保留不永久 processed。
21. `test_reconcile_merge_group_api_unresolved_does_not_demote_and_does_not_mark_processed`：API 回傳 None 時 fail-safe 保留。
22. `test_reconcile_merge_group_failure_creates_audit_log_and_recovery_handoff`：真正當前失敗正常觸發 Reviewer Recovery。
23. `test_reconcile_merge_group_failure_stale_snapshot_reject_does_not_mark_run_processed`：CAS 拒絕時不標記 processed、不發送 review gate。
24. `test_reconcile_merge_group_duplicate_is_strictly_idempotent`：重複事件嚴格冪等。

### B. `test_supervisor.py` (ReviewHeadFreezeTests)
執行命令：
```bash
PYTHONPATH=.orchestrator python3 -m unittest test_supervisor.ReviewHeadFreezeTests
```
輸出結果：
```text
Ran 33 tests in 16.470s

OK
```
確認 Supervisor 審查 head 凍結與呼叫契約完整通過。
