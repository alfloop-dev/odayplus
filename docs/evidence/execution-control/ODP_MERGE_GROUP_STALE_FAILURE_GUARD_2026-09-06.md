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

在 `.orchestrator/github_reconciliation.py` 的單一恢復流程中，於發動 CAS 狀態轉移與降級前，增加了即時查證與防護邏輯：

1. **即時 PR 事實查證 (`fetch_pr_facts`)**：
   - 透過 `gh_json` 查詢 PR 的即時 `state`、`headRefOid`、`mergedAt` 等資訊。
   - 若 PR 已處於 `MERGED` 狀態，判定該失敗為過期事件（stale），記錄 `merge_group_failure_stale` (reason: `already_merged`)，寫入 processed ID 去重，不變更任務狀態與審查閘門。
   - 若 PR 已非 `OPEN` 狀態（如 closed），同樣記錄 stale 並去重，不觸發降級。

2. **Reviewed PR Head 一致性驗證**：
   - 核對 PR 的 `headRefOid` 是否與 task 的 `approved_head` 一致。
   - 若 PR head 不一致或無法解析，判定關聯不明確，記錄 `merge_group_failure_unresolved`，**不降級任務且不永久標記為 processed**，保留至下一輪重新查證。

3. **同 PR、同 Workflow 新 Group 接替判定**：
   - 掃描候選 runs，尋找同 PR 且同 workflow（`workflow_id` 或 `name` 相同）且建立時間／Run ID 更具時效性（newer）的 run。
   - 若存在接替 run 且其狀態為 active（`in_progress`, `queued`, `pending` 等且非 failure）或 `success`（`SUCCESS_CONCLUSIONS`）：
     - 判定舊 run 為過期事件（stale），記錄 `merge_group_failure_stale`（reason: `pending_group` 或 `successful_group`），將舊 run 標記為 processed 去重。
     - 保持任務 `review_approved` 與 `approved_head` 完整，不修改 handoffs 與 GitHub review gate。

4. **安全防界與真實失敗處理 (Fail-Safe Boundary)**：
   - **嚴格區分 PR head 與 merge-group SHA**：不要求 PR head 與 merge-group commit SHA 相等。
   - **防遮掩真實失敗**：不同 PR、不同 PR head、不同 workflow 的 run 絕對不可用於覆蓋真正失敗。
   - **真實當前失敗恢復**：若經查證無新 run 接替且 PR 為 OPEN 且 head 一致，則維持既有 Reviewer Recovery 流程，執行 CAS 轉移至 `review`，派發 reviewer recovery handoff，並於 CAS 成功後重送 `task-review-gate` 待審狀態。
   - **API 不明與 CAS 失敗防護**：若 API 查詢失敗或 CAS 轉移拒絕，不永久 processed、不發送不一致 external gate。

---

## 3. 焦點驗證結果 (Verification Evidence)

執行受影響之單元測試套件：

### A. `test_github_bus.py` (MergeGroupReconciliationTests)
執行命令：
```bash
python3 -m unittest discover -s .orchestrator -p 'test_github_bus.py'
```
輸出結果：
```text
----------------------------------------------------------------------
Ran 137 tests in 2.049s

OK
```
涵蓋之核心測試項目：
1. `test_reconcile_merge_group_superseded_by_pending_run_is_stale_and_non_mutating`：新 group 先啟動 pending、舊 group 後 cancelled 之亂序情境，確認不降級、標記 stale 去重。
2. `test_reconcile_merge_group_superseded_by_success_run_is_stale_and_non_mutating`：新 group 已 success，舊 failed run 不降級。
3. `test_reconcile_merge_group_pr_already_merged_is_stale_and_non_mutating`：PR 已 merged，舊 cancelled run 記 stale 並去重。
4. `test_reconcile_merge_group_different_pr_does_not_mask_genuine_failure`：不同 PR 的成功 run 不得遮掩當前 PR 的真實失敗。
5. `test_reconcile_merge_group_different_workflow_does_not_mask_genuine_failure`：不同 workflow 的成功 run 不得遮掩當前 workflow 的真實失敗。
6. `test_reconcile_merge_group_different_pr_head_does_not_mask_genuine_failure`：PR head 變更時不誤判為同 head 接替，保留下一輪查證。
7. `test_reconcile_merge_group_api_unresolved_does_not_demote_and_does_not_mark_processed`：API 無法解析時 fail-safe 保留。
8. `test_reconcile_merge_group_failure_creates_audit_log_and_recovery_handoff`：真正當前失敗正常觸發 Reviewer Recovery。
9. `test_reconcile_merge_group_failure_stale_snapshot_reject_does_not_mark_run_processed`：CAS 拒絕時不標記 processed、不發送 review gate。
10. `test_reconcile_merge_group_duplicate_is_strictly_idempotent`：重複事件嚴格冪等。

### B. `test_supervisor.py` (ReviewHeadFreezeTests)
執行命令：
```bash
python3 -m unittest discover -s .orchestrator -p 'test_supervisor.py' -k ReviewHeadFreezeTests
```
輸出結果：
```text
----------------------------------------------------------------------
Ran 33 tests in 16.252s

OK
```
確認 Supervisor 整合介面與審查 head 凍結邏輯均維持綠燈通過。
