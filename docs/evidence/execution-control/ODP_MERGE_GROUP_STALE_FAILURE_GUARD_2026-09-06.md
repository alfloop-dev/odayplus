# ODP-MERGE-GROUP-STALE-FAILURE-GUARD-001: 舊 merge-group 失敗誤撤銷核准單一路徑防護

- Task: `ODP-MERGE-GROUP-STALE-FAILURE-GUARD-001`
- Status: `ready_for_review`
- Owner: `Antigravity2`
- Reviewer: `Codex`
- Date: `2026-09-06`

---

## 1. 問題背景與根因分析 (Incident & Root Cause)

在 PR #1212 合併過程中，發現了如下異常路徑：

1. **事實情境**：PR #1212 的審查核准 head 為 `a9e7853f9ed910303eb6b7fa1d81c1e1b46c5787`，PR head 完全未變更。
2. **舊 Run 結算干擾**：舊 merge group run `34003742537`（merge group head `908e99fa`）於 `01:33:23` 結算為 `cancelled`。
3. **誤撤銷核准**：Reconciler 於 `01:34` 輪詢處理到該舊 run 的 cancelled 狀態，在未核實 PR 最新狀態及新 run 的情況下，立即將任務由 `review_approved` 降級至 `review` 並清空 `approved_head`。
4. **新 Run 實質成功**：事實上，新 merge group run `34004041183`（merge group head `17393dd4`）已於 `01:29:54` 啟動為 pending，並於 `01:54:17` 成功，PR 亦於 `01:54:20` 完成合併。

### 根因歸納

- GitHub Actions 的 `merge_group` workflow run SHA 是 Merge Queue 自動生成的臨時 merge commit SHA，**不是** PR 本身的 branch head SHA。
- 既有 `reconcile_merge_group_runs()` 在遇到 failure/cancelled conclusion 時，未核實 PR 即時狀態，也未確認是否已有更新的同 PR merge group 接替，導致亂序或延遲回傳的舊失敗覆蓋了正常進行中的佇列流程。

---

## 2. 第二輪 review 指出的四個缺口與修正

### 2.1 `verify_group_contains_head` 只證 ancestry，不證精確 head

**問題**：ancestry 無法區分 A→B。若 PR head 由 A 前進到 B，A 必然是為 B 建立之 group 的祖先，ancestry 檢查會把「B 的 group」誤判為「涵蓋 A」，讓不相干的 run 蓋掉針對 A 的真實失敗。

**修正**：改用 GitHub 真實可取得的**父提交關聯**，不再用 ancestry，也不再用本地 `git merge-base`。

Merge queue 的 group head 是 GitHub 為該筆佇列建立的臨時 merge commit，其 parents 就是「佇列基底」加上「本次納入的精確 PR head」。以本次事故的真實資料驗證：

```console
$ git cat-file -p 17393dd4                      # 新 group head
tree   1760ff19544920a51d3be316408cbca8ad2cea0c
parent a297b2a0245e1dfdfade38d982cac75bb842137a   # 佇列基底
parent a9e7853f9ed910303eb6b7fa1d81c1e1b46c5787   # PR #1212 的核准 head
```

因此新增 `fetch_commit_parent_shas()`（`GET /repos/{repo}/commits/{sha}`，GitHub 公開回傳 `parents[].sha`）與三態的
`merge_group_incorporates_head()`（`True` / `False` / `None`）。

**第一個 parent 是 base，其餘才是本次納入的 PR head**（見第 3 輪 §2.6）。
為 B 建立的 group，其 parents 是 `[base, B]`，不含 A，因此 A→B 情境會被正確拒絕。
另外，group head 等於 PR head 一律回 `False`——GitHub 不會產生這種形狀，接受它等於允許 fixture 自行捏造關聯。

### 2.2 fresh queue `enrolled` 分支會吞掉真實失敗

**問題**：舊實作只要 PR 在 merge queue 內就判定 stale 並永久 processed。但 GitHub 只回報「PR 在某個 group 裡」，不回報「在哪個 group」；正在失敗的那個 group 本身就可能是 PR 當前的佇列身分，於是真實失敗被永久吞掉。

**修正**：移除「enrolled 即 stale」分支。`isInMergeQueue` 只作為稽核欄位 `in_merge_queue` 記錄於 stale 事件，
不再單獨構成 supersede 證據；唯一能壓過失敗的證據是「可指名的、更新的 group，且其 group commit 確實納入本次 reviewed head」。
測試 `test_reconcile_merge_group_queue_enrollment_alone_does_not_mask_failure` 覆蓋此反向情境。

### 2.3 queue/runs API 的 `None`/exception 被當成「沒有新 group」

**問題**：`fetch_merge_group_runs()` 舊實作把所有例外吞成 `[]`，於是「API 壞掉」與「GitHub 回報沒有 run」變成同一個答案，接著繼續降級。

**修正**：`fetch_merge_group_runs()` 改為 `list | None`。`[]` = GitHub 回報沒有 run；`None` = 問題未被回答。
reconcile 在 `None` 時記 `merge_group_failure_unresolved`（reason `merge_group_runs_unavailable`），
**不降級、不寫入 processed IDs**，保留下一輪查證。
同理，`find_superseding_merge_group_run()` 回傳第三個值 `unknown`：只要有候選 group 的關聯無法證實也無法否證，
即回報 `group_association_unknown` 並保留，不得在未回答的問題上撤銷核准。

### 2.4 PR state 未知字串仍被當 closed-stale

**問題**：舊實作 `pr_state != "OPEN"` 即視為 closed 並永久 processed，任何非預期字串（截斷的 payload、錯誤形狀）都會被讀成「已關閉，所以失敗過期」。

**修正**：新增 `pr_state()`，只接受 GitHub 實際定義的 `OPEN` / `CLOSED` / `MERGED`，其餘一律回 `""`（未知）。
`is_valid_pr_facts()` 因此對未知 state 回 `False`，走 `pr_facts_unavailable` 保留路徑，
永遠不會落到 closed-stale 分支。

### 2.5 收斂 reader：移除重複的 `fetch_pr_facts`

`fetch_pr_facts()`（`gh pr view --json`）已刪除。PR 事實改由既有的單一 reader
`github_bus.fetch_pr_merge_queue_status()` 提供：其 GraphQL query 擴充為同時回傳
`number / state / merged / mergedAt / headRefOid / isInMergeQueue / mergeQueueEntry`。

`gh pr view --json` 結構上取不到 `isInMergeQueue`，所以第二個 CLI reader 只會用較弱的來源回答同一個問題並與 GraphQL 漂移。
`github_reconciliation.fetch_pr_queue_facts()` 是這個 reader 的薄封裝，負責驗證與「未知即 `None`」的語意。

---

## 2bis. 第三輪 review 指出的四個缺口與修正

### 2.6 缺最後的 facts B（freshness 未包住 runs／parents API）

**問題**：第二輪把重複的 `fetch_pr_facts` 收斂掉時，連帶把「轉態前的第二次 PR 查證」一起移除了。
流程變成「讀 PR → 查 runs／parents → 直接 CAS」，而 runs 與 commit parents 這幾次 API 呼叫需要實際時間，
PR 可能在這段期間 merge、close 或前進 head。

**修正**：以**同一個 reader** 再讀一次（facts B），位置在所有 runs／parents 呼叫**之後**、
任何 mutation 與任何 processed 標記**之前**。兩次查證共用 `check_pr_facts()` 這一段邏輯，
輸出三態 `open` / `stale` / `unresolved`：

- facts B 顯示 merged 或 closed → `stale`（去重，不變更任務）
- facts B 的 head 與 `approved_head` 漂移 → `unresolved: pr_head_drift`（保留，不 processed）
- facts B 無法取得 → `unresolved: pr_facts_B_unavailable`（保留，不 processed）

facts A 的對應 reason 一併改名為 `pr_facts_A_unavailable`，讓稽核可分辨是哪一次查證失敗。

### 2.7 target run 自身的新 attempt 被跳過

**問題**：target 來自舊的 runs 快照。GitHub 會用**同一個 run id** 重跑 merge group workflow，
所以快照裡讀到 `failure` 的那筆，此刻可能已經是 `in_progress` 或 `success`。
候選掃描結構上看不到這件事——它會跳過與 target 相同的 run id。

**修正**：新增 `classify_fresh_target_run()`，先在 fresh 快照中以 id 找回 target；
找不到時再用同一族既有 reader 精確查 `GET /repos/{repo}/actions/runs/{run_id}`（`fetch_workflow_run()`）。
四種結果：

| 現況 | 處置 |
| --- | --- |
| 仍為 completed failure | 繼續既有流程 |
| 同 id 新 attempt 仍在跑（無 conclusion） | `unresolved: target_run_retrying`，保留、不 processed |
| 同 id 已 success | `stale: target_run_succeeded_on_retry`，去重、不降級 |
| 讀不到或 conclusion 不可辨識 | `unresolved: target_run_unresolved`，保留、不 processed |

只有「目前仍是 completed failure」才會走到撤核准。

### 2.8 同 PR/workflow 候選欄位不全被壓成明確的「無替代者」

**問題**：候選缺 `head_sha`、workflow 身分不明、順序無法比較、或 parents payload 壞掉時，
舊碼一律 `continue`。這些 skip 累積起來會被讀成「查過了，沒有任何新 group」，接著走 recovery——
把未回答的問題當成否定答案。

**修正**：這幾種情況改為 `unknown`，不降級也不 processed：

- `match_workflow_identity()` 改為三態，兩邊都沒有可比對身分時回 `None`（不是 `False`）。
- 新增三態的 `compare_run_recency()`：run id 與時間戳都無法比較時回 `None`。
- 候選缺 `head_sha` → unknown。
- `fetch_commit_parent_shas()` 對 `parents=[{}]`、`parents=[]`、非 list 一律回 `None`；
  `[{}]` 是壞掉的 payload，不是「這個 commit 沒有 parent」。

順序上先做確定性的排除（不同 PR、非 pending/success、可證較舊、同一個 group head），
再對真正「有機會成為替代者」的候選要求可回答的欄位，避免把不相干的 run 誤標成 unknown。

原本期待 demote 的 `missing-head` 測試 oracle 是錯的，已改為期待保留；
`missing workflow identity` 同理一併改正。

### 2.9 `approved_sha in parents` 會命中 base parent

**問題**：merge commit 的第一個 parent 是 base。若 `approved_head` 剛好等於某個新 group 的 base
（也就是該 PR 的 head 已經合併成為後續 group 的基底），`in parents` 會誤判成「這個 group 納入了本 head」，
讓**下一個 PR** 的 group 蓋掉本 PR 的真實失敗。

真實資料佐證這個結構：

```console
$ git cat-file -p 62dfc845                        # PR #1213 的 group head
parent 17393dd447e9b25978a83641cabf7cd77951f24a   # base = 前一個 group 的 merge（PR #1212）
parent a4000bf2426f30f3b52cc33864dd21fc0e6cf7a3   # PR #1213 的 head
```

**修正**：只比對 `parents[1:]`（非 base parent）。
parents 少於兩個時（不是 merge commit 形狀）視為結構不明，回 `None` 保留重試。

---

## 2ter. 第四輪 review 指出的三個缺口與修正

### 2.10 `classify_fresh_target_run` 嚴格校驗 status 與 conclusion

**問題**：舊實作僅根據 `conclusion` 判定，若 `status` 缺失或為 `in_progress` 但 payload 帶 `failure`（矛盾資料），仍被誤判為 `current_failure`；若 `status` 缺失但 `conclusion` 為 `success`，仍永久 ack。

**修正**：
- 只有合法的 `status == "completed"` 且 `conclusion in FAILURE_CONCLUSIONS` 才判定為 `current_failure`；
- 只有合法的 `status == "completed"` 且 `conclusion in SUCCESS_CONCLUSIONS` 才判定為 `retry_succeeded`；
- `status in PENDING_RUN_STATUSES` 且無 conclusion 時判定為 `retrying`；
- 缺欄位、未識別 status、或矛盾 payload（如 `in_progress` 帶 conclusion、`completed` 無 conclusion 等）一律回 `unknown`，不 demote 亦不 processed，保持下輪 retry。

### 2.11 原 failed target group 自身非 base parent 嚴格綁定 `approved_head`

**問題**：PR 從 A 前進並經審查核准 B 後，若先前針對 A 的 merge group 延遲回傳 failure，此時 fresh PR 為 B 且無更新 candidate，舊邏輯因未驗證 target group 自身的 PR parent，仍會錯誤撤銷 B 的核准。

**修正**：
- 重用 `merge_group_incorporates_head()` 驗證 target group 的非 base parent 是否精確包含當前 `approved_head`；
- 確證不屬於當前 `approved_head`（例如延遲抵達的 A group failure）：判定為 `stale`（reason: `target_not_for_approved_head`），記錄審計並加入 `processed_merge_group_run_ids` 去重，零變更；
- target head SHA 缺失或 API 回傳 parents 未知：判定為 `unknown`（reason: `target_identity_incomplete` / `target_association_unknown`），保留不 processed；
- current failure 測試 fixture 全面補足 target 真實 parent 數據收據。

### 2.12 facts A 與 facts B 隊列 generation / enrollment 漂移防護

**問題**：facts A 與 B 原先只比對 state 與 head SHA，完全忽略佇列入隊狀態及 `enqueuedAt` 變化。若同一 PR head 在 API 查證期間重新入隊（新的 queue generation），B 已觀察到 generation 不同卻仍可能 demote。

**修正**：
- 新增 `pr_queue_identity(pr_facts)` 提取 `(is_in_merge_queue, enqueuedAt)` 作為佇列身分代；
- facts A 與 facts B 之間若觀察到佇列身分漂移（例如從未入隊到入隊、或 re-enqueue 產生新的 `enqueuedAt`），立即判定為 `unresolved: pr_queue_drift`，保留下輪重讀；
- 佇列中的位置正常前進（`position` 正常移動，例如 3 -> 1，`enqueuedAt` 未變）不視為 generation 漂移，避免誤阻斷正常流程。

---

## 3. 修正後的單一流程

`reconcile_merge_group_runs()` 在既有 CAS 之前只有一段查證，每個分支三選一：
**證明過期**（稽核 + 去重，零變更）、**證明仍為真實失敗**（落入原有 reviewer recovery）、
**問題未解**（保留、不 processed、下一輪再問）。

先取證，再判斷；所有外部查詢都在任何 mutation 與任何 processed 標記之前完成。

| 順序 | 查證 | 結果 |
| --- | --- | --- |
| 1 | task 是否帶 `approved_head` | 無 → `unresolved: approved_head_missing`（保留） |
| 2 | **facts A**：`fetch_pr_queue_facts` 取得 PR state / head / 佇列身分 | 無法取得 → `unresolved: pr_facts_A_unavailable`（保留） |
| 3 | PR 已 merged | `stale: already_merged`（去重） |
| 4 | PR state 為 `CLOSED` | `stale: pr_state_closed`（去重） |
| 5 | PR head ≠ `approved_head`（完整 SHA，禁止前綴比對） | `unresolved: pr_head_mismatch`（保留） |
| 6 | 取 fresh merge group runs 快照 | `None` → `unresolved: merge_group_runs_unavailable`（保留） |
| 7 | **重讀 target run 自身**（合法 completed 狀態檢驗） | 缺欄位/矛盾/讀不到 → `unresolved`（保留）；成功 → `stale`（去重）；retrying → `unresolved`（保留） |
| 8 | **target run group head 驗證**（非 base parent 包含 reviewed head） | 不含 → `stale: target_not_for_approved_head`（去重）；API 未知/缺失 → `unresolved`（保留） |
| 9 | 找更新且 parents（非 base）納入 reviewed head 的 superseding group | 找到 → `stale: pending_group` / `successful_group`（去重）；關聯未解 → `unresolved: group_association_unknown`（保留） |
| 10 | **facts B**：同一 reader 於 runs／parents 查詢後重讀 PR | merged/closed → `stale`（去重）；漂移或讀不到 → `unresolved`（保留） |
| 11 | **facts A vs B 隊列 generation 漂移比對** | `enqueuedAt` 或入隊狀態變更 → `unresolved: pr_queue_drift`（保留） |
| 12 | 以上皆非 | 真實失敗：既有 CAS → `review`、reviewer recovery handoff、CAS 成功後重送 `task-review-gate` |

候選 group 的確定性排除條件（不產生 unknown）：不同 PR 編號、既非 pending 也非 success、
可證較舊、與 target 同一個 group head（同 group 的 sibling run 或 re-run 不能接替自己）、
或 workflow 身分明確不同。其餘欄位不可回答者一律 unknown。

其餘不變：CAS 拒絕時不寫 processed IDs、不送外部 review gate；handoff 去重與 replay 去重維持原狀；
未新增 scheduler、state store、恢復命令、平行 reader 或 CI 管線。

---

## 4. 焦點驗證結果 (Verification Evidence)

測試 fixture 已全面改為以 `github_bus.gh_json` 為邊界的 GitHub 形狀 stub
（GraphQL PR node、`actions/runs` 列表、`repos/{repo}/commits/{sha}` 的 parents），
不再 patch 掉防護自身的 helper；因此真實失敗與 API 未知等負向案例是走完整 reader 堆疊得到結果。
所有 fixture 的 merge-group head SHA 與 PR head SHA 一律不同。

### A. `test_github_bus.py` (MergeGroupReconciliationTests)

```console
$ PYTHONPATH=.orchestrator python3 -m unittest test_github_bus.MergeGroupReconciliationTests
.................................................................
----------------------------------------------------------------------
Ran 65 tests in 0.910s

OK
$ echo $?
0
```

### B. `test_supervisor.py` (ReviewHeadFreezeTests)

```console
$ PYTHONPATH=.orchestrator python3 -m unittest test_supervisor.ReviewHeadFreezeTests
----------------------------------------------------------------------
Ran 33 tests in 16.694s

OK
$ echo $?
0
```

### C. Lint

```console
$ uv run --frozen --python 3.12 ruff check .orchestrator/github_reconciliation.py \
    .orchestrator/github_bus.py .orchestrator/test_github_bus.py .orchestrator/test_supervisor.py
All checks passed!
```

### D. 覆蓋的情境

| 情境 | 測試 |
| --- | --- |
| 新 group 先開始、舊 cancelled 後處理（本次事故，兩種批次順序） | `test_reconcile_merge_group_superseded_by_pending_run_is_stale_and_non_mutating` |
| 新 group 已 success | `test_reconcile_merge_group_superseded_by_success_run_is_stale_and_non_mutating` |
| PR 已 merged | `test_reconcile_merge_group_pr_already_merged_is_stale_and_non_mutating` |
| PR 已 closed 未 merged | `test_reconcile_merge_group_pr_closed_unmerged_is_stale_and_non_mutating` |
| **A→B：新 group 納入的是後續 head，不得遮掩真失敗** | `test_reconcile_merge_group_newer_group_for_advanced_head_does_not_mask_failure` |
| **A→B：延遲到達的舊 head A failure 不得撤銷核准 B** | `test_reconcile_merge_group_delayed_failure_for_previous_head_is_stale_and_non_mutating` |
| **target parent API 未知不得 demote** | `test_reconcile_merge_group_target_parents_api_unknown_does_not_demote` |
| **target head SHA 缺失不得 demote** | `test_reconcile_merge_group_target_missing_head_sha_is_unknown_does_not_demote` |
| **target status/conclusion 所有排列組合校驗** | `test_classify_fresh_target_run_status_and_conclusion_permutations` |
| **facts B 佇列 generation 漂移（re-enqueue）不得 demote** | `test_reconcile_merge_group_facts_b_queue_generation_drift_does_not_demote` |
| **facts B 新入隊漂移不得 demote** | `test_reconcile_merge_group_facts_b_newly_enrolled_drift_does_not_demote` |
| **facts B 佇列 position 正常移動不視為漂移** | `test_reconcile_merge_group_facts_b_position_movement_alone_is_not_drift` |
| **佇列身分輔助函式單元測試** | `test_pr_queue_identity_helper` |
| 關聯是父提交而非 ancestry（單元層） | `test_merge_group_incorporates_head_requires_direct_parenthood` |
| 不同 PR 的新 group | `test_reconcile_merge_group_different_pr_does_not_mask_genuine_failure` |
| 不同 workflow 的新 group | `test_reconcile_merge_group_different_workflow_does_not_mask_genuine_failure` |
| PR head 與 approved head 不符 | `test_reconcile_merge_group_different_pr_head_does_not_mask_genuine_failure` |
| 候選缺 head_sha／workflow 不明／順序不明／parents 壞掉一律 unknown | `test_reconcile_merge_group_candidate_missing_fields_is_unknown_not_demote` |
| reviewed head 只是候選 group 的 base parent，不算納入 | `test_reconcile_merge_group_base_parent_match_does_not_supersede_failure`、`test_merge_group_incorporates_head_requires_direct_parenthood` |
| facts B：PR 在查證期間 merge | `test_reconcile_merge_group_facts_b_merged_is_stale_and_non_mutating` |
| facts B：head 漂移／讀不到 | `test_reconcile_merge_group_facts_b_head_drift_does_not_demote`、`test_reconcile_merge_group_facts_b_unavailable_does_not_demote` |
| 同 run id 新 attempt 仍在跑／已成功／讀不到 | `test_reconcile_merge_group_target_rerun_pending_does_not_demote`、`test_reconcile_merge_group_target_rerun_succeeded_is_stale`、`test_reconcile_merge_group_target_unreadable_does_not_demote` |
| 快照輪替掉 target，改以單筆 run 查詢確認仍為失敗 | `test_reconcile_merge_group_target_resolved_by_single_run_read` |
| workflow 身分／run 新舊皆為三態 | `test_match_workflow_identity_is_tri_state`、`test_compare_run_recency_is_tri_state` |
| 壞掉的 parents payload（`[{}]`／`[]`／非 list） | `test_fetch_commit_parent_shas_tri_state` |
| 同一 group 的 sibling run 不能接替自己 | `test_find_superseding_run_ignores_sibling_run_of_the_failing_group` |
| enrolled 單獨不得證明 superseded | `test_reconcile_merge_group_queue_enrollment_alone_does_not_mask_failure` |
| enrolled 且有可證新 group 才判 stale | `test_reconcile_merge_group_enrolled_pr_still_stale_when_new_group_proven` |
| PR API 未知（含未知 state 字串、空 node） | `test_reconcile_merge_group_pr_facts_unavailable_does_not_demote`、`test_reconcile_merge_group_empty_or_invalid_pr_node_does_not_demote` |
| runs API 未知不得當成沒有新 group | `test_reconcile_merge_group_runs_snapshot_unavailable_does_not_demote`、`test_fetch_merge_group_runs_distinguishes_empty_from_unanswered` |
| group 關聯未解 | `test_reconcile_merge_group_association_unknown_does_not_demote` |
| 短 SHA 前綴比對必須拒絕 | `test_reconcile_merge_group_exact_sha_no_prefix_match` |
| 真正當前失敗仍走 reviewer recovery | `test_reconcile_merge_group_failure_creates_audit_log_and_recovery_handoff` |
| CAS 拒絕：不 processed、不送 gate | `test_reconcile_merge_group_failure_stale_snapshot_reject_does_not_mark_run_processed` |
| 重複事件嚴格冪等 | `test_reconcile_merge_group_duplicate_is_strictly_idempotent` |
| 單批次只問一次 runs API | `test_reconcile_merge_group_reads_each_api_once_per_batch` |

### E. 反向驗證（測試不是空的）

對實作注入十五個缺陷並確認對應測試轉紅，全部為 `FAILED (failures=…)` 而非 import error；驗證後檔案已還原並與注入前逐位元組相同：

| 注入的缺陷 | 轉紅的測試 |
| --- | --- |
| `merge_group_incorporates_head` 改回 ancestry 式寬鬆接受 | `test_reconcile_merge_group_newer_group_for_advanced_head_does_not_mask_failure`、`test_merge_group_incorporates_head_requires_direct_parenthood` |
| 重新加回「enrolled 即 stale」 | `test_reconcile_merge_group_queue_enrollment_alone_does_not_mask_failure` |
| `fetch_merge_group_runs` 例外回 `[]` | `test_reconcile_merge_group_runs_snapshot_unavailable_does_not_demote`、`test_fetch_merge_group_runs_distinguishes_empty_from_unanswered` |
| 忽略 `association_unknown` | `test_reconcile_merge_group_association_unknown_does_not_demote` |
| `pr_state` 放行任意字串 | `test_reconcile_merge_group_empty_or_invalid_pr_node_does_not_demote`、`test_pr_state_accepts_only_github_states` |
| 允許同一 group 接替自己 | `test_find_superseding_run_ignores_sibling_run_of_the_failing_group` |
| 移除 facts B | 三個 `..._facts_b_...` 測試 |
| 不重讀 target、直接信任舊快照 | 三個 `..._target_rerun_*` / `..._target_unreadable_...` 測試 |
| 候選欄位不全改回安靜 skip | `test_reconcile_merge_group_candidate_missing_fields_is_unknown_not_demote`、`test_reconcile_merge_group_association_unknown_does_not_demote` |
| `approved_sha in parents` 含 base parent | `test_reconcile_merge_group_base_parent_match_does_not_supersede_failure`、`test_merge_group_incorporates_head_requires_direct_parenthood` |
| parents 壞掉的元素被忽略而非視為 unknown | `test_fetch_commit_parent_shas_tri_state` |
| workflow 身分 unknown 壓成 mismatch | `test_match_workflow_identity_is_tri_state`、`test_reconcile_merge_group_candidate_missing_fields_is_unknown_not_demote` |
| **`classify_fresh_target_run` 放行缺少 status 的 payload** | `test_classify_fresh_target_run_status_and_conclusion_permutations` |
| **未驗證 target run 的 parents 與 `approved_head` 關聯** | `test_reconcile_merge_group_delayed_failure_for_previous_head_is_stale_and_non_mutating`、`test_reconcile_merge_group_target_parents_api_unknown_does_not_demote` |
| **忽略 facts A 與 B 之間的佇列 generation 漂移** | `test_reconcile_merge_group_facts_b_queue_generation_drift_does_not_demote` |

---

## 5. 範圍聲明

- 未重跑基線或完整產品測試套件；僅執行受影響的兩組焦點測試與變更檔案的 ruff lint。
- 未修改 live config、rollout、CI 管線、Supervisor 執行狀態，未觸碰 merge queue 或 Human gate。
- 未重開或重測已封存的原任務；本次僅修正 reconciler 單一恢復流程。
