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

### 4.1 完整 orchestrator 套件（同機、同命令、與 base 對照）

```
$ uv run --frozen --python 3.12 pytest .orchestrator -p no:cacheprovider -m 'not requires_live_env'

base da4b77d1（乾淨 worktree）  : 20 failed, 1806 passed, 6 skipped, 440 subtests passed  EXIT=1
本 branch                       : 20 failed, 1812 passed, 6 skipped, 440 subtests passed  EXIT=1
```

兩側 FAILED 名單逐項比對後**完全相同**（`comm -13` / `comm -23` 皆為空），差異只有本次新增的 6 個測試。這 20 項是本機環境既有的紅（`ReviewHeadFreezeTests` 4 項、`test_worker_hard_inactivity` 5 項、`test_worker_settlement_paths` 11 項），非本次改動造成；CI 上的實際結果以 PR 的 exact head run 為準。

Lint：

```
$ uv run --python 3.12 ruff check .orchestrator delivery_toolchain scripts
All checks passed!
```

執行測試後 worktree 僅剩本任務的 5 個改動檔與 1 個新增 evidence 檔，`ai-status.json` 未被測試覆寫。

---

## 5. 對 `test_supervisor.py` 的三處 fixture 修補（本輪例外，請 reviewer 裁決）

Task brief 要求「不接觸 PR1227 正在修的 `status_transition` / `test_supervisor`」。本輪**無法**完全遵守，理由與證據如下，先在此揭露：

`.orchestrator/test_supervisor.py::WorkerPreemptionSafeBoundaryTests` 有三個既有測試，其 review 候選是「只有 status/owner/reviewer、沒有 `review_submission`、沒有 CI mock」的空殼：

- `test_dirty_worktree_fails_closed_and_preserves_receipt_on_forced_preemption`
- `test_clean_finalize_worker_can_be_preempted_and_preserves_review_approved`
- `test_same_task_priority_candidates_follow_existing_lifecycle_preemption_rules`（case 1）

這三個 fixture 之所以能通過，正是因為舊的 review 快路不去看它們缺了什麼——它們把本次要修掉的缺陷語意寫成了斷言。基線量測：

```
base da4b77d1（乾淨 worktree）：22 passed
只改 dispatch_engine/dispatch_policy：3 failed, 19 passed
```

**無法在 scope 內規避**：任何讓這三個 fixture 維持綠燈的寫法，都等於為「沒有 review submission 的 review」開一個特例，也就是再寫一份比 `is_task_review_dispatch_eligible` 更弱的第二套 eligibility——這與 brief 的「沿用唯一 ready eligibility 判斷」「無證據 fail-closed」兩條直接衝突。

**採取的最小修補**：只為這三個候選補上其情境本就隱含的事實——一筆 `review_submission`（含 `remote_sha`）與兩個 `ai_status` mock（`resolve_task_sha` 回同一 head、`task_pr_ci_status` 回 `("OPEN", "success")`），與同一批測試對 finalize 候選既有的做法一致。未改任何斷言、未改任何 production 接線。

**與 PR1227 的重疊量測**：

```
$ gh pr view 1227 --json files
.orchestrator/status_transition.py, .orchestrator/test_supervisor.py, docs/evidence/...

$ gh pr diff 1227  # test_supervisor.py 的 hunk 位置
@@ -4611,12 +4611,41 @@
@@ -4652,24 +4681,55 @@
@@ -4679,11 +4739,114 @@
```

PR1227 在 `test_supervisor.py` 的三個 hunk 落在第 4611–4790 行；本次修補落在第 11103–11530 行，相距逾 6000 行，無文字重疊、無同一 gate 的接線重疊。本次**完全未觸碰** `.orchestrator/status_transition.py`。

若 reviewer 判定仍不應在本 PR 觸碰 `test_supervisor.py`，可退回並改以獨立 task 承接該三處 fixture；但在那之前 engine 修正無法取得綠燈。

---

## 6. 未做事項 (Out of Scope / Non-Goals)

- 未新增任何 scheduler、快取、輪詢機制或第二個 Supervisor。
- 未關閉 priority preemption；真正可執行的 P0 搶占、容量優先、同 rank lifecycle 順序、healthy owner 保護全部保留。
- 未修改 live config（`.orchestrator/config.json`）、未重啟 runtime、未手寫看板、未變更 quota/auth/role 指派。
- 未觸碰 `.orchestrator/status_transition.py`（PR1227 範圍）。
- 未修復事故中的三個 P0 review 本身（`non_dispatchable` 待人工裁決、兩個 CI failure 屬各自 owner）；本任務只修「它們不該成為搶占理由」。
- 合併後的既有 rollout 由 Codex 統一處理。
