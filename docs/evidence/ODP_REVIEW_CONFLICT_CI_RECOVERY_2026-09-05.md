# ODP-REVIEW-CONFLICT-CI-RECOVERY-001 Evidence

- Owner: Claude2 · Reviewer: Antigravity · 日期: 2026-09-05
- Base: `origin/dev` @ `1d92e382`（已含 ODP-IMPLEMENTATION-OWNER-PREFERENCE-001 的 merge #1209）

## 1. 根因

`review` 狀態的 task 要被派給 reviewer，必須通過
`dispatch_engine.is_task_review_dispatch_eligible()`，而該述詞要求
「submitted head 上的必要 CI 已終局成功」：

```python
pr_status, ci_status = runtime_ai_status.task_pr_ci_status(task_id)
if ci_status != "success":
    return False
```

當 PR 與 base 衝突時，GitHub 算不出 merge commit，因此**不會**在該 head 上啟動
任何 workflow：check-runs、check-suites、Actions runs 全為零。
`scripts/ai_status.py::task_pr_ci_status()` 對空 rollup 回傳 `"none"`
（`valid_checks_count == 0` → `ci_status = "none"`）。

於是形成死結：

- CI 永遠不會出現 → `ci_status` 永遠是 `"none"` → reviewer 永遠不被派工；
- task 停在 `review`，`next` 不會更新，看板上看起來像「等 reviewer」；
- 只有 owner 能推進 base 解衝突，但 owner 不會被喚醒，因為 task 不在 `in_progress`。

#1170 就是這個形狀：exact head `847d498493343d0e8c1227f4eb3bf64a04448fa6`，
PR OPEN、`mergeStateStatus` 為衝突、同 head 零 check，review readiness 只回
`none` 而無限等待。

**既有機制不涵蓋**（已先驗）：唯一會因衝突而把 task 交回 owner 的路徑是
`dispatch_engine.route_approved_pr_to_merge()` 的 `_pr_merge_state(pr) == "DIRTY"`
→ `"ejected"` → `status_transition.requeue_task_for_ci_repair()`。該 transition 的
status guard 是：

```python
if str(task.get("status") or "").lower() != "review_approved":
    return False
```

也就是 queue-ejection repair 只接受**已核准**的 task。已核准流程不能當作本分支
已被覆蓋——`review` 早了一步，根本進不到 merge queue。
`scripts/ai_status.py` 中對 `DIRTY/CONFLICTING` 的判斷屬 cross-repo delivery
驗證（`add_blocker("conflicting", ...)`），是報告而非恢復路徑。

## 2. 單一既有入口（沒有新增第二套機制）

沒有新增 scanner、scheduler、queue，也沒有第二套 lease/capacity 計算；
Supervisor 不修改任何產品程式。改動只有三處接線：

1. **`.orchestrator/status_transition.py` — 既有 canonical transition 的具名受限入口**

   `requeue_task_for_ci_repair()` 新增 keyword-only 參數
   `allow_conflicted_review: bool = False`。status guard **沒有被全面放寬**：
   預設仍然只接受 `review_approved`；只有透過這個具名入口，且同時滿足

   - `task_status == "review"`
   - `review_submission_is_complete(config, task)`（已驗證的遠端 PR provenance）
   - 沒有 `approved_head`
   - 沒有 `merge_route`

   時才允許。也就是「已核准 / 已排入 merge queue 的 immutable head」永遠不會被
   這條路徑動到。

   走 review 入口時額外把 `submit_review` 建立的 owner→reviewer handoff 標為
   `done` 並清掉 `waiting_for`，否則 reviewer 會繼續握著已經回到 owner 的工作。
   activity log 的 `ci_repair_requeued` 事件新增 `entry` 欄位
   （`conflicted_review` / `review_approved`）以區分入口。

2. **`.orchestrator/dispatch_engine.py` — reconciliation 階段的掃描**

   新增 `recover_conflicted_review_prs()`，掛在 `dispatch_ready_tasks()` 的
   reconciliation 階段，緊接在既有的 `advance_approved_prs_to_merge()` 之後——
   也就是**在 per-agent dispatch loop 之前**。這是刻意的：這個 task 正是卡在
   reviewer 的 slot 上，如果恢復本身要佔 reviewer 剩餘 slot，等待就成了自己的成因。

   只在 GitHub 明確陳述的事實上動作：

   | 條件 | 來源 |
   | --- | --- |
   | task 狀態為 `review` | 本地 |
   | 非 human gate / 非 `non_dispatchable` | 本地 |
   | 無 `approved_head`、無 `merge_route` | 本地 |
   | 無 live helper lease（`helper_claim_is_live`） | 本地 |
   | 不在 active / pending worker 索引中 | `busy_task_ids` |
   | review submission provenance 已驗證 | `review_submission_is_complete` |
   | 該 head 尚未恢復過 | `review_conflict_recovery_head` |
   | 可解析 task repository slug | `_task_repository_slug` |
   | 同 head CI 為 `none` 且 PR 為 `OPEN` | `task_pr_ci_status`（canonical 讀取器） |
   | PR `state == OPEN` | `gh pr view --repo <slug>` |
   | `mergeStateStatus ∈ {DIRTY, CONFLICTING}` | 同上 |
   | `headRefOid == submission.remote_sha` | 同上 |

   查詢**綁定 task repository**（`--repo <slug>`）：拿 PR 號去問錯的 checkout 會得到
   一個無關 PR 的自信錯答；slug 無法解析時直接放棄，不猜測。

   **競態處理**：`state / mergeStateStatus / headRefOid` 由同一次 `gh pr view`
   取得（描述同一個瞬間），並在 CI 判定之後**再讀一次**，兩次必須完全一致才動作。
   head 被推進、PR 被關閉、衝突被解掉而 checks 開始跑，都會表現為 tuple 改變而
   改為繼續等待。`BLOCKED` / `BEHIND` / `UNKNOWN` 不算衝突——那些狀態會被後續事件
   自行解掉，不需要 owner 動分支。

   **只恢復一次**：`review_conflict_recovery_head` 由 transition 自己那一次
   canonical commit 一起寫入，因此重複 poll 與 supervisor 重啟都只會恢復同一個
   head 一次。transition 回傳 False（拒絕或 commit 沒落地）時會把 marker 回滾，
   否則這個 head 會被永久標成「已恢復」而再也救不回來。

   無法確認的讀數一律**靜默維持等待**：不改寫 `next`、不寫 activity log，避免把
   正常流轉中的 review task churn 掉。

3. **`.orchestrator/supervisor.py` — 轉發新的 keyword**

   `supervisor.requeue_task_for_ci_repair()` 是 `status_transition` 的委派
   wrapper，而 `dispatch_engine` 是透過 `_sync_supervisor_scope()` 從 supervisor
   命名空間解析這個名字的，所以 wrapper 必須一起轉發
   `allow_conflicted_review`，否則會 `TypeError`。這是結構上必要的 2 行接線，
   不是行為變更。（此檔不在 task 的 `owned_paths` 但也不在 `forbidden_paths`，
   在此明確揭露。）

## 3. 交回 owner 之後

沒有新增任何東西：task 回到 `in_progress`、owner 不變，由既有 owner dispatch
接手。`worker_workspace.py` **未修改**且不需要修改——`required_review_head` 只在
`REASON_REVIEW_READY` 時計算，因此 `owned_in_progress_dispatch` 走的是既有的
base-advance 路徑，`_refresh_reused_worker_worktree()` 回傳
`base_advance_rebase_required:` 時 worker 會收到既有的
「BASE ADVANCE REQUIRED BEFORE REVIEW OR MERGE」提示。之後照常正常（非 force）
push、`task_finalize.sh` 重新提交、拿到 exact-head CI 與獨立 review。

保留項目（皆由既有 transition 的行為保證，並有測試釘住）：
原 acceptance、owner、reviewer、`review_reopen_count` /
`review_churn_reassigned_at_count`、已消耗的 `human_continuation_approval_history`、
`review_submission`。reopen 記在 `control_plane_recovery` 類別下，而
`common.substantive_review_reopen_count()` 明確排除該類別，因此 review churn
計數不會被這條路徑推高。沒有重播 nonce、沒有清 cooldown、沒有重設 churn、
沒有偽造 Human/Ops。

## 4. 測試

新增 45 個 focused 正反例於 `.orchestrator/test_dispatch_policy.py`（1 + 20 + 1 + 12 + 1 + 1 + 1 + 1 + 6 + 1）：

- **正例**：`test_conflicted_review_with_no_ci_is_returned_to_its_owner`
  以 #1170 的 exact head `847d4984…` 重現 OPEN / CONFLICTING / 零 check，
  斷言交回原 owner、marker 寫入、handoff 收掉、`waiting_for` 清掉、
  acceptance / owner / churn 計數 / continuation history 全部保留、
  activity log 的 `entry == "conflicted_review"`、兩次查詢都綁 `--repo` 與 PR 號。
- **反例（不可確認就等待）**：`test_recovery_declines_every_unconfirmable_reading`
  20 個 case——CI pending / success / failure / unknown / 探測拋例外、
  PR CLOSED / MERGED、merge state CLEAN / BLOCKED / BEHIND / UNKNOWN、
  gh 離線 / JSON 壞掉 / 空 payload / 缺 head / 缺 mergeStateStatus / 缺 state、
  head drift。
- **競態**：`test_recovery_declines_when_the_facts_change_between_the_two_reads`
  （第二次讀到新 head / 已關閉 / 已解衝突 / 讀不到）。
- **閘門與凍結**：`test_recovery_never_acts_on_a_gated_frozen_or_busy_task`
  12 個 case——human gate 三種形狀、`non_dispatchable`、active/pending worker、
  live helper lease、`approved_head` 凍結、`merge_route` 已排隊、未提交 review、
  submission head 非 SHA、狀態非 review。這些一律在本地判定，**完全不呼叫 gh**。
- **repository 綁定**：`test_recovery_declines_when_the_task_repository_cannot_be_resolved`。
- **只恢復一次**：`test_repeated_polls_and_restarts_recover_one_head_exactly_once`
  （同一 tick 重複 poll、重啟後 owner 以同一 head 重送、換新 head 才會再恢復）。
- **不落地不留痕**：`test_a_recovery_that_does_not_persist_leaves_the_head_recoverable`。
- **既有 guard 不退化**：`test_canonical_ci_repair_transition_keeps_its_review_approved_guard`
  （不帶旗標時 review 仍被拒；queue-ejection 行為完全不變）與
  `test_named_entry_still_refuses_a_review_it_must_not_move`（6 個 case）。
- **接線**：`test_dispatch_ready_tasks_recovers_a_conflicted_review_without_a_reviewer_slot`
  以 `agent_ids_override=["antigravity7"]`（既非 owner 也非 reviewer）跑完整
  `dispatch_ready_tasks`，斷言沒有任何 dispatch event 被 queue、恢復仍然發生。

### 負向對照（證明測試不是空跑）

逐一注入缺陷並確認對應測試轉紅，之後還原：

| 注入的缺陷 | 結果 |
| --- | --- |
| 拿掉 `allow_conflicted_review` 具名入口條件（review 一律放行） | 1 failed |
| 拿掉第二次確認讀取 | 1 failed |
| 拿掉 head drift 檢查 | 1 failed |
| 拿掉 marker 回滾 | 1 failed |
| 拿掉「同 head 只恢復一次」 | 1 failed |
| 把 BLOCKED/BEHIND/UNKNOWN/CLEAN 也算成衝突 | 4 failed |
| 拿掉 busy / helper lease 檢查 | 2 failed |
| 拿掉「CI 必須為 none」 | 6 failed |
| 把 reconciliation 階段的呼叫拔掉（未接線） | 1 failed |

## 5. 未做事項

- **沒有**修改 `worker_workspace.py`（既有 base-advance 已足夠，且該檔在
  `forbidden_paths`）。
- **沒有**放寬 `is_task_review_dispatch_eligible()` 對 CI 終局成功的要求；
  reviewer 仍然只在 exact head CI 綠時才被派工。
- **沒有**自行 rerun 任何舊 CI、沒有 `gh run rerun`、沒有動 merge queue。
- **沒有**為「無法確認」的讀數改寫 `next` 或寫 activity log（避免對正常流轉中的
  review task 產生 churn）。這代表 API 長期不可讀時，看板上仍然只看得到原本的
  等待訊息——這是刻意的保守取捨，如果後續要補「report once」需另開 task。
- **沒有**處理「owner 以完全相同的衝突 head 重送 review」之後的第二次恢復：
  依 acceptance「同 head 僅恢復一次」，該情況會維持等待。
- **沒有** restart supervisor、**沒有**改任何 live runtime state 或
  `.orchestrator/config.json`。live runtime/config 由 root Codex 整合審核，
  合併後部署。注意 `.orchestrator/config.json` 是 gitignored 的顯式覆蓋，
  本 PR 只改程式預設路徑。
- **沒有**重跑完整產品 suite（acceptance 明示只跑相關 regression）。

## 6. Rollback

單一 revert 即可，無資料遷移、無 schema 變更：

```bash
git revert -m 1 <merge-commit-of-this-PR>
```

Revert 後行為完全回到現況（衝突且無 CI 的 review 會再度無限等待）。
唯一的殘留是已被恢復過的 task 上留下的 `review_conflict_recovery_head`
欄位，該欄位在 revert 後沒有任何讀取者，屬惰性資料，不需要清理。
不需要 restart supervisor 才能 rollback：所有邏輯都在 dispatch tick 內求值。

## 7. Exact-head receipt

`task_verification run` 產生的收據綁定本 task branch 的最終 head，寫在
`.orchestrator/evidence/`。**該目錄是 gitignored（`.gitignore:78`），所以收據不會
出現在本 PR 的 diff 裡**——它存在於執行 finalize 的主機上，並由
`task_finalize.sh` 的 `task_verification check` 針對同一個 exact head 驗證。
收據的 head、命令與 exit code 一併記在送審的 status note 與 PR 說明中，
commit 的 `Verified:` trailer 則記錄實際跑過的命令。

重跑方式（注意：本 repo 需要 Python 3.12，`cp314` 缺 `pgserver` wheel；
`.orchestrator` 的測試需要把 `scripts` 放進 `PYTHONPATH` 才 import 得到
`ai_status`）：

```bash
PYTHONPATH=scripts uv run --frozen --python 3.12 pytest -q -o addopts= \
  .orchestrator/test_dispatch_policy.py \
  .orchestrator/test_dispatch_engine.py \
  .orchestrator/test_supervisor.py \
  .orchestrator/test_supervisor_scope_injection.py \
  .orchestrator/test_fill_idle_slots.py \
  .orchestrator/test_task_reality.py \
  .orchestrator/test_worker_failure_policy.py
uv run --frozen --python 3.12 ruff check .orchestrator/
uv run --frozen --python 3.12 python delivery_toolchain/governance/check_code_boundaries.py
```
