# Evidence Note: ORCH-PREEMPTION-READINESS-001

## 任務摘要
- **Task ID**: `ORCH-PREEMPTION-READINESS-001`
- **Owner**: `Claude`
- **Reviewer**: `Codex`
- **目標**: 修正 `higher_priority_ready_task_exists` 的 review 快路。該快路只看 `status` / `reviewer` / lifecycle priority 就把候選記為 priority 0，未核對該候選是否真的可被 `dispatch_ready_tasks` 派發，導致「無法執行的高優先候選」反覆搶占一個正在執行的 review worker。修既有 preemption / readiness 路徑，不新增 scheduler、不新增第二套快取或輪詢。

---

## 1. 事故證據（唯讀量測）

量測時間 `READ_AT=2026-09-07T04:24:00Z`，來源為 live canonical `"$PANTHEON_STATUS_ROOT"/ai-activity-log.jsonl` 與 `ai-status.json`，全程只讀。

### 1.1 受害 worker

```
$ grep "ORCH-STATUS-SYNC-RUNTIME-AUTHORITY-001" ai-activity-log.jsonl | grep -c '"worker_superseded"'
286
first_ts 2026-09-06T11:01:43Z
last_ts  2026-09-07T04:03:17Z
```

286 筆全部是同一則訊息：

```
"Worker superseded to prioritize higher-priority review/finalize work."
```

該訊息只由 `worker_lifecycle.py` 的 priority-escalation 分支產生，其判定條件即 `higher_priority_ready_task_exists(config, worker, task_map, state)`。同一 task 的伴生事件量級一致（`worker_started` 286、`task_preempt_sync_failed` 286、`worker_worktree_reused` 287、`wake_queued` 286），即「派發 → 搶占 → 再派發」的完整迴圈，約每 3.5 分鐘一輪、持續 17 小時。

被殺的 worker：`target_agent=codex_lupin_slot_1`、`logical agent = Codex2`、`reason=review_ready_dispatch`、task `ORCH-STATUS-SYNC-RUNTIME-AUTHORITY-001`（P1，PR #1227）。

### 1.2 搶占者：三個永遠派不出去的 P0 review

`ai-status.json` 中 reviewer 為 `Codex2` 且 status 為 `review` 的任務，量測當下只有三筆，全部 P0：

| Task | priority | non_dispatchable | 板上 `next` |
| --- | --- | --- | --- |
| `ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001` | P0 | **true** | 等待人工指定 reviewer 核對收據 |
| `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` | P0 | false | `... has CI failure (failure); review dispatch suppressed until CI is repaired.` |
| `ODP-CODEX-ULTRA-DRIFT-REPAIR-001` | P0 | false | `... has CI failure (failure); review dispatch suppressed until CI is repaired.` |

三者的 task rank（P0）都優於受害 task（P1），因此 `(rank, lane)` 比較必然成立；而三者都不可能被 `dispatch_ready_tasks` 派發——一個被 `non_dispatchable` 擋掉，兩個卡在 CI failure。搶占騰出的 slot 沒有任何候選能接手，於是 supervisor 立刻把同一個 review 再派回同一個 slot，下一輪再殺一次。

---

## 2. 根因 (Root Cause)

`.orchestrator/dispatch_engine.py` 的 `higher_priority_ready_task_exists` 對候選分兩路計分：

- **review 快路**：`task_status in review_statuses and reviewer == agent_name` → 直接 `candidate_priority = 0`。
- **其他**：呼叫 `dispatch_priority_for_task`（內含 `is_task_review_dispatch_eligible`：exact submitted head、CI terminal success、merge_route、approved_head、owner/reviewer 獨立性）。

快路完全繞過唯一的 ready eligibility 判斷。此外兩路都沒有經過 `agent_can_take_task`——那是 `dispatch_ready_tasks` 在 `reason` 決定後必過的第二道閘（`dispatch_engine.py:2591`），負責 `non_dispatchable` / human gate / role-provider policy / disabled agent / sidecar-only。於是：

1. `non_dispatchable` 的 review 可以成為搶占理由（快路與 `dispatch_priority_for_task` 都不看它）。
2. CI failure / pending / unknown、submitted head 漂移、已 approved、已排入 merge queue 的 review 也都可以成為搶占理由（只有快路看不到）。
3. role/provider policy 排除該 lane 的候選同樣可以成為搶占理由（兩路都看不到）。

`dispatch_ready_tasks` 拒絕派發這些候選，`higher_priority_ready_task_exists` 卻認為它們是「更高優先的 ready 工作」。這個不一致就是 livelock 本身。

---

## 3. 修正 (Fix)

### 3.1 沿用唯一的 ready eligibility 判斷

移除 review 快路，改為所有候選一律經 `dispatch_priority_for_task`（其 review 分支即 `is_task_review_dispatch_eligible`），並在其後補上 `dispatch_ready_tasks` 自己用的第二道閘 `agent_can_take_task(config, agent_name, task, role=...)`。role 由 `dispatch_reason_role(dispatch_priority_reason(priority))` 導出。

`dispatch_policy.dispatch_priority_reason` 是本次新增的唯一公開 helper：`DISPATCH_REASON_PRIORITIES` 的**推導**反轉（非另寫一份對照表），讓持有 lane priority 的呼叫端能問 role policy，而不必自己複製 lane→role 的映射。

### 3.2 rank 先剪枝，讓 readiness 讀取有界

`candidate_priority` 下界為 0，因此：

- `candidate_task_rank > current_task_rank` → 不可能勝出，直接跳過；
- rank 相同且 `current_priority <= 0`（正在跑 review）→ 不可能勝出，直接跳過。

這兩個判斷完全不讀取任何外部狀態。接著再以 `task_is_human_gate(task) or task.get("non_dispatchable")` 做一次零成本拒絕。**只有**通過以上剪枝、真的可能終結一個 worker 的候選，才會付出 exact-head readiness 讀取。

修正前，`dispatch_priority_for_task` 是對 `task_map` 中**每一個**任務呼叫的（含 `review_approved` 分支的 `resolve_task_checkout_sha(force_refresh=True)` + `task_pr_ci_status`）。改動後每個 poll 的 GitHub 讀取量嚴格下降。

### 3.3 重用既有有時效 / 精確 SHA 的 readiness 來源

`is_task_review_dispatch_eligible` 與 `dispatch_priority_for_task` 新增 keyword-only 參數 `readiness_force_refresh: bool = True`（預設即現行行為，所有既有呼叫端語意不變）。preemption 路徑傳 `False`，改由 `ai_status.resolve_task_sha` / `resolve_task_checkout_sha` 既有的短時效快取服務，而非每次提問都強制一次 `git ls-remote`。

- 沒有新增任何快取、輪詢或排程機制；用的是這兩個 reader 原本就有的 `max_age_seconds`。
- CI 讀取本來就走 `task_pr_ci_status` 的既有 10 秒快取，兩條路徑一致。
- 兩個 reader 在讀不到答案時都回 `None` / `unknown`，`is_task_review_dispatch_eligible` 對任何例外一律 `return False`：**無證據即 fail-closed**，快取 miss 或讀取失敗都是拒絕搶占，不是假設就緒。

### 3.4 保留的既有語意

- 容量 / 空閒 slot 優先：`occupied_count` / `free_slots` / `served_higher_priority_task_ids` 與 `len(unserved) > free_slots` 完全未動。
- 同 task 排除、sidecar 對稱性排除、`is_sidecar_review_of_current_parent` 排除：未動。
- 同 rank 的 lifecycle 順序（review > finalize > in_progress > todo > helper）：未動。
- `urgent_priority_cutoff` 的同 rank 保護：未動。
- healthy owner 保護（`worker_can_be_preempted`）：未動。
- 真正可執行的 P0 搶占：保留（見 §4 的正向測試）。

---

## 4. 測試

新增測試落在 `.orchestrator/test_dispatch_policy.py` 既有 scope（未新增測試檔），所有 root 均指向記憶體內 fixture 或 `mock.patch.object`，未觸碰真實 canonical 狀態，未跑產品全套件。

| 測試 | 斷言 |
| --- | --- |
| `test_undispatchable_p0_reviews_do_not_preempt_a_running_p1_review` | 179/286-loop 的縮小 fixture：`non_dispatchable`、CI failure、CI pending、CI unknown、head 漂移、已 approved 六種 P0 review 候選皆**不**搶占 P1 review worker；且 `non_dispatchable` 候選從未被 exact-head 讀取詢問 |
| `test_dispatchable_p0_review_still_preempts_when_no_slot_is_free` | 真的可派發的 P0 review 在無空 slot 時**仍**搶占 |
| `test_a_free_slot_is_used_before_a_running_worker_is_killed` | 同一個 P0 候選，有空 slot 時**不**殺既有 worker |
| `test_role_policy_that_excludes_the_reviewer_lane_is_not_a_preemption_reason` | role/provider policy 排除該 lane 時，就緒的 P0 review 不成搶占理由 |
| `test_p0_owned_work_preempts_only_once_its_dependencies_are_done` | 依賴未滿的 P0 owned work 不搶占；依賴 done 後恢復搶占（正向控制） |
| `test_dispatch_priority_reason_round_trips_every_lane` | 新 helper 對五個 lane 往返一致；`None` / 未知值 / bool 皆回 `None` |

**未修正碼上的反證**（把新測試與新版 `dispatch_policy.py` 放到 base `da4b77d1` 的乾淨 worktree、保留舊 `dispatch_engine.py`）：

```
FAILED test_undispatchable_p0_reviews_do_not_preempt_a_running_p1_review
FAILED test_role_policy_that_excludes_the_reviewer_lane_is_not_a_preemption_reason
2 failed, 4 passed
```

兩個描述缺陷的測試在舊碼上確實紅，四個描述「必須保留」語意的測試在新舊碼上都綠——新測試不是空轉的綠燈。

### 4.1 既有 orchestrator 套件（同機、同命令、與 base 對照）

第一輪（head `40bba61c`、base `da4b77d1`）的完整 `.orchestrator` 量測：

```
$ uv run --frozen --python 3.12 pytest .orchestrator -p no:cacheprovider -m 'not requires_live_env'

base da4b77d1（乾淨 worktree）      : 20 failed, 1806 passed, 6 skipped, 440 subtests passed  EXIT=1
第一輪（含 test_supervisor fixture）: 20 failed, 1812 passed, 6 skipped, 440 subtests passed  EXIT=1
第二輪（test_supervisor 已還原）    : 23 failed, 1809 passed, 6 skipped, 440 subtests passed  EXIT=1
```

base advance merge 至 `origin/dev` = `9094b4ac`（含 PR #1227）後，改以受影響的兩個測試檔對照量測。base 側用 `origin/dev` 的獨立 detached worktree，兩側同機、同 interpreter、同命令：

```
$ uv run --frozen python -m pytest \
    .orchestrator/test_supervisor.py .orchestrator/test_dispatch_policy.py \
    -p no:randomly --tb=line -rf

base origin/dev 9094b4ac : 4 failed, 731 passed, 6 skipped, 230 subtests passed  EXIT=1
本 PR（fixture 已補齊）   : 4 failed, 737 passed, 6 skipped, 230 subtests passed  EXIT=1
```

兩側 FAILED 名單逐項比對後**完全相同**，皆為本機環境既有的紅：

```
ReviewHeadFreezeTests::test_approve_fails_closed_when_approved_head_cannot_be_resolved
ReviewHeadFreezeTests::test_approve_refuses_to_overwrite_uncleared_approved_head
ReviewHeadFreezeTests::test_approve_saves_approved_head_and_rejects_same_owner_reviewer
ReviewHeadFreezeTests::test_restore_approved_refuses_without_a_durable_approved_head
```

相對 base **新增的紅：0**；`+6 passed` 即 §4 新增的六個 `test_dispatch_policy` 測試。本機那 4 項紅在 CI 上是綠的（見 §5.2 的 CI 收據：同一 head 上 orchestrator job 僅有三項紅，且不含 `ReviewHeadFreezeTests`），屬環境差異，非本次改動造成。

Lint：

```
$ uv run --frozen --python 3.12 ruff check .orchestrator delivery_toolchain scripts
All checks passed!
```

執行測試後 worktree 僅剩本任務的改動檔（`git status --porcelain` 只有 `M .orchestrator/test_supervisor.py` 一項未提交），`ai-status.json` 未被測試覆寫。

---

## 5. `test_supervisor.py` 的三個既有測試：依 reviewer 裁決在本 PR 補齊 fixture

### 5.1 兩次 reopen 與最終裁決

| # | 時間 | 裁決 |
| --- | --- | --- |
| 1 | `2026-09-07T04:29:43Z` | head `40bba61c` 一併修改 `.orchestrator/test_supervisor.py` 違反 single-owner scope（PR #1227 正在改同一檔）；請移除，另開 follow-up 或待 #1227 併入後由同一 owner 處理 |
| 2 | `2026-09-07T04:44:38Z` | 「PR1227 已合併，原先共檔衝突前提消失；請由 worker 依 reviewer scope 裁決補齊必要 fixture／測試相容性（**可在同一 PR 觸及 test_supervisor.py**），確保不恢復 review fast path、不新增第二套機制，CI 必須綠後再送 Codex 審查」 |

第一次裁決已照辦（head `b33dec9a` 將該檔還原至 base）。第二次裁決即 §5.3 舊文中的**選項 B**，本輪據此執行：在本 PR 補上該三處 fixture，`.orchestrator/test_supervisor.py` 重新進入本 task 的改動範圍。

### 5.2 修補前的紅燈（已量測，含 CI 收據）

PR #1233 exact head `1700951a` 的 CI `orchestrator` job：

```
$ gh run view 34083946293 --log-failed
3 failed, 2621 passed, 6 skipped, 10 deselected, 3 warnings, 567 subtests passed in 166.95s
FAILED WorkerPreemptionSafeBoundaryTests::test_clean_finalize_worker_can_be_preempted_and_preserves_review_approved
FAILED WorkerPreemptionSafeBoundaryTests::test_dirty_worktree_fails_closed_and_preserves_receipt_on_forced_preemption
FAILED WorkerPreemptionSafeBoundaryTests::test_same_task_priority_candidates_follow_existing_lifecycle_preemption_rules
```

同三項在本機亦可重現，且在 `origin/dev` = `9094b4ac` 的乾淨 detached worktree 上為全綠——確認是本 PR 的 engine 改動造成，不是既有環境紅：

```
$ uv run --frozen python -m pytest .orchestrator/test_supervisor.py \
    -k WorkerPreemptionSafeBoundaryTests -p no:randomly

base origin/dev 9094b4ac : 14 passed              EXIT=0
本 PR（修補前）           : 3 failed, 11 passed    EXIT=1
```

三項的失敗原因相同：它們的 review 候選是「只有 `status` / `owner` / `reviewer`、沒有 `review_submission`、沒有 CI mock」的空殼。它們之所以在 base 上綠，正是因為舊的 review 快路不去看它們缺了什麼——舊 fixture 把本次要修掉的缺陷語意寫進了前提。

### 5.3 修補內容：只補事實，不動任何斷言

三處各補上其情境本就隱含的事實，與同一批測試對 finalize 候選既有的做法（`approved_head` + 兩個 `ai_status` mock）完全一致：

- 候選 task 加一筆 `"review_submission": {"remote_sha": DISPATCHABLE_REVIEW_HEAD}`；
- 對應 `with` 區塊加兩個既有 mock：`ai_status.resolve_task_sha` 回同一 head、`ai_status.task_pr_ci_status` 回 `("OPEN", "success")`。

`DISPATCHABLE_REVIEW_HEAD` 是本次新增的唯一模組層常數，只為了讓「這個 review 候選是刻意要可派發的」在三處讀起來一致。

**未新增、未刪除、未改寫任何一行 `assert`**；三個測試原本斷言的安全邊界（dirty worktree fail-closed 並留存收據、clean finalize 可搶占且保留 `review_approved`、同 rank 依 lifecycle 順序）逐字不變。

```
$ git diff origin/dev -- .orchestrator/test_supervisor.py --stat
 .orchestrator/test_supervisor.py | 21 +++++++++++++++++----
```

### 5.4 修補後的量測

```
$ uv run --frozen python -m pytest .orchestrator/test_supervisor.py \
    -k WorkerPreemptionSafeBoundaryTests -p no:randomly
14 passed   EXIT=0

$ uv run --frozen python -m pytest .orchestrator/test_supervisor.py \
    -k "higher_priority or preempt or preemption" -p no:randomly
22 passed, 592 deselected   EXIT=0
```

### 5.5 反證：這個 fixture 修補本身不含修正，也不恢復 fast path

把同一份 fixture patch 套到 `origin/dev` = `9094b4ac` 的乾淨 detached worktree（**保留未修正的 `dispatch_engine.py`**）：

```
$ git apply test_supervisor_fixture.patch
$ uv run --frozen python -m pytest .orchestrator/test_supervisor.py \
    -k "higher_priority or preempt or preemption" -p no:randomly
22 passed, 592 deselected   EXIT=0
```

新舊 engine 上皆綠，代表這份修補只是把場景本就隱含的事實寫出來，沒有替「沒有 review submission 的 review」開任何特例，也沒有把缺陷語意搬到別處。真正描述缺陷的斷言仍然只在 `.orchestrator/test_dispatch_policy.py`（§4：兩項在舊 engine 上紅）。

`higher_priority_ready_task_exists` 的 review fast path **未恢復**：所有候選一律經 `dispatch_priority_for_task` + `agent_can_take_task`，未新增第二套 eligibility、快取、輪詢或排程機制。

本 PR 仍**完全未觸碰** `.orchestrator/status_transition.py`。

---

## 6. 未做事項 (Out of Scope / Non-Goals)

- 未新增任何 scheduler、快取、輪詢機制或第二個 Supervisor。
- 未關閉 priority preemption；真正可執行的 P0 搶占、容量優先、同 rank lifecycle 順序、healthy owner 保護全部保留。
- 未修改 live config（`.orchestrator/config.json`）、未重啟 runtime、未手寫看板、未變更 quota/auth/role 指派。
- 未觸碰 `.orchestrator/status_transition.py`（PR1227 範圍；#1227 已併入 `dev`）。
- 未新增或改寫 `.orchestrator/test_supervisor.py` 的任何斷言；該檔的改動僅為 §5.3 的三處 fixture 事實補齊。
- 未修復事故中的三個 P0 review 本身（`non_dispatchable` 待人工裁決、兩個 CI failure 屬各自 owner）；本任務只修「它們不該成為搶占理由」。
- 合併後的既有 rollout 由 Codex 統一處理。
