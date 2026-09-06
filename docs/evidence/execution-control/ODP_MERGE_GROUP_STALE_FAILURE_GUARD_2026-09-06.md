# ODP-MERGE-GROUP-STALE-FAILURE-GUARD-001: 舊 merge-group 失敗誤撤銷核准單一路徑防護

- Task: `ODP-MERGE-GROUP-STALE-FAILURE-GUARD-001`
- Status: `ready_for_review`
- Owner: `Claude`
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

## 2. 第二輪 review 指出的四個缺口與本輪修正

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

因此新增 `fetch_commit_parent_shas()`（`GET /repos/{repo}/commits/{sha}`，GitHub 公開回傳 `parents[].sha`）與
`merge_group_incorporates_head()`：只有 `approved_head` 出現在候選 group head 的 **parents** 之中才算關聯成立。
為 B 建立的 group，其 parents 是 `[base, B]`，不含 A，因此 A→B 情境會被正確拒絕。

`merge_group_incorporates_head()` 為三態：`True` / `False` / `None`（GitHub 無法回答）。
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

## 3. 修正後的單一流程

`reconcile_merge_group_runs()` 在既有 CAS 之前只有一段查證，每個分支三選一：
**證明過期**（稽核 + 去重，零變更）、**證明仍為真實失敗**（落入原有 reviewer recovery）、
**問題未解**（保留、不 processed、下一輪再問）。

| 順序 | 查證 | 結果 |
| --- | --- | --- |
| 1 | `fetch_pr_queue_facts` 取得 PR state / head / 佇列身分 | 無法取得 → `unresolved: pr_facts_unavailable`（保留） |
| 2 | PR 已 merged | `stale: already_merged`（去重） |
| 3 | PR state 為 `CLOSED` | `stale: pr_state_closed`（去重） |
| 4 | `approved_head` 缺失 | `unresolved: approved_head_missing`（保留） |
| 5 | PR head ≠ `approved_head`（完整 SHA，禁止前綴比對） | `unresolved: pr_head_mismatch`（保留） |
| 6 | 取 fresh merge group runs 快照 | `None` → `unresolved: merge_group_runs_unavailable`（保留） |
| 7 | 找到更新且 parents 納入 reviewed head 的 group | `stale: pending_group` / `successful_group`（去重） |
| 8 | 有候選但關聯未解 | `unresolved: group_association_unknown`（保留） |
| 9 | 以上皆非 | 真實失敗：既有 CAS → `review`、reviewer recovery handoff、CAS 成功後重送 `task-review-gate` |

候選 group 另有四道硬性過濾，任何一道不過即不可作為接替證據：
同一 PR 編號、非同一個 group head（同 group 的 sibling run 或 re-run 不能接替自己）、
同 workflow 身分（`workflow_id` 或非空 `name`）、且時間或 run id 上確實較新。

其餘不變：CAS 拒絕時不寫 processed IDs、不送外部 review gate；handoff 去重與 replay 去重維持原狀；
未新增 scheduler、state store、恢復命令、平行 reader 或 CI 管線。

fresh runs 快照與 group commit 查詢在單次 reconcile 內各有快取，
批次中多筆失敗不會重複詢問 GitHub 同一個問題（`test_reconcile_merge_group_reads_each_api_once_per_batch`）。

---

## 4. 焦點驗證結果 (Verification Evidence)

測試 fixture 已全面改為以 `github_bus.gh_json` 為邊界的 GitHub 形狀 stub
（GraphQL PR node、`actions/runs` 列表、`repos/{repo}/commits/{sha}` 的 parents），
不再 patch 掉防護自身的 helper；因此真實失敗與 API 未知等負向案例是走完整 reader 堆疊得到結果。
所有 fixture 的 merge-group head SHA 與 PR head SHA 一律不同。

### A. `test_github_bus.py` (MergeGroupReconciliationTests)

```console
$ PYTHONPATH=.orchestrator python3 -m unittest test_github_bus.MergeGroupReconciliationTests
.................................................
----------------------------------------------------------------------
Ran 49 tests in 0.261s

OK
$ echo $?
0
```

### B. `test_supervisor.py` (ReviewHeadFreezeTests)

```console
$ PYTHONPATH=.orchestrator python3 -m unittest test_supervisor.ReviewHeadFreezeTests
----------------------------------------------------------------------
Ran 33 tests in 17.921s

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
| 關聯是父提交而非 ancestry（單元層） | `test_merge_group_incorporates_head_requires_direct_parenthood` |
| 不同 PR 的新 group | `test_reconcile_merge_group_different_pr_does_not_mask_genuine_failure` |
| 不同 workflow 的新 group | `test_reconcile_merge_group_different_workflow_does_not_mask_genuine_failure` |
| PR head 與 approved head 不符 | `test_reconcile_merge_group_different_pr_head_does_not_mask_genuine_failure` |
| 候選缺 head_sha / 缺 workflow 身分 | `test_reconcile_merge_group_newgroup_missing_head_sha_does_not_supersede_failure`、`test_reconcile_merge_group_missing_workflow_identity_does_not_match` |
| 同一 group 的 sibling run 不能接替自己 | `test_find_superseding_run_ignores_sibling_run_of_the_failing_group` |
| **enrolled 單獨不得證明 superseded** | `test_reconcile_merge_group_queue_enrollment_alone_does_not_mask_failure` |
| enrolled 且有可證新 group 才判 stale | `test_reconcile_merge_group_enrolled_pr_still_stale_when_new_group_proven` |
| **PR API 未知（含未知 state 字串、空 node）** | `test_reconcile_merge_group_pr_facts_unavailable_does_not_demote`、`test_reconcile_merge_group_empty_or_invalid_pr_node_does_not_demote` |
| **runs API 未知不得當成沒有新 group** | `test_reconcile_merge_group_runs_snapshot_unavailable_does_not_demote`、`test_fetch_merge_group_runs_distinguishes_empty_from_unanswered` |
| **group 關聯未解** | `test_reconcile_merge_group_association_unknown_does_not_demote` |
| 短 SHA 前綴比對必須拒絕 | `test_reconcile_merge_group_exact_sha_no_prefix_match` |
| 真正當前失敗仍走 reviewer recovery | `test_reconcile_merge_group_failure_creates_audit_log_and_recovery_handoff` |
| CAS 拒絕：不 processed、不送 gate | `test_reconcile_merge_group_failure_stale_snapshot_reject_does_not_mark_run_processed` |
| 重複事件嚴格冪等 | `test_reconcile_merge_group_duplicate_is_strictly_idempotent` |
| 單批次只問一次 runs API | `test_reconcile_merge_group_reads_each_api_once_per_batch` |

### E. 反向驗證（測試不是空的）

對實作注入六個缺陷並確認對應測試轉紅，全部為 `FAILED (failures=…)` 而非 import error；驗證後檔案已還原並與注入前逐位元組相同：

| 注入的缺陷 | 轉紅的測試 |
| --- | --- |
| `merge_group_incorporates_head` 改回 ancestry 式寬鬆接受 | `test_reconcile_merge_group_newer_group_for_advanced_head_does_not_mask_failure`、`test_merge_group_incorporates_head_requires_direct_parenthood` |
| 重新加回「enrolled 即 stale」 | `test_reconcile_merge_group_queue_enrollment_alone_does_not_mask_failure` |
| `fetch_merge_group_runs` 例外回 `[]` | `test_reconcile_merge_group_runs_snapshot_unavailable_does_not_demote`、`test_fetch_merge_group_runs_distinguishes_empty_from_unanswered` |
| 忽略 `association_unknown` | `test_reconcile_merge_group_association_unknown_does_not_demote` |
| `pr_state` 放行任意字串 | `test_reconcile_merge_group_empty_or_invalid_pr_node_does_not_demote`、`test_pr_state_accepts_only_github_states` |
| 允許同一 group 接替自己 | `test_find_superseding_run_ignores_sibling_run_of_the_failing_group` |

---

## 5. 範圍聲明

- 未重跑基線或完整產品測試套件；僅執行受影響的兩組焦點測試與變更檔案的 ruff lint。
- 未修改 live config、rollout、CI 管線、Supervisor 執行狀態，未觸碰 merge queue 或 Human gate。
- 未重開或重測已封存的原任務；本次僅修正 reconciler 單一恢復流程。
