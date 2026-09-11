# ORCH-ACCOUNT-POOL-CANARY-RECOVERY-001：正常 worker 成功後 account pool 永久停留 recovering

- Task: ORCH-ACCOUNT-POOL-CANARY-RECOVERY-001
- Owner: Claude ／ Reviewer: Codex2
- Base commit（dev）: `00c03473`
- 交付 head（測量所在 commit）: `91cc0090063f96ef2a1d867a2befb58bff5d0211`
- 本文件不宣稱 live runtime 已恢復。修復僅存在於本 branch 的程式碼，live rollout 條件見第 5 節。

---

## 1. 缺陷與根因

`account_pool_runtime_state()`（`.orchestrator/supervisor.py`）把 quota 冷卻分成兩段：
`cooldown` 到期後轉為 `recovering`，把 pool 的 effective concurrency 壓到 1，並把「下一個
真正跑起來的任務」當成 authenticated canary。回到 configured capacity 的唯一出口是
`record_account_pool_canary_success()`（`.orchestrator/worker_failure_policy.py:792`），它要求
pool 目前必須是 `recovering`。

問題在於 `poll_workers()`（`.orchestrator/worker_lifecycle.py`）在修復前只有**一個**地方呼叫
這個 helper：discussion-planning worker 的退出分支（`worker_lifecycle.py:1790`）。一般 task
worker 的成功分支——也就是 owner `lifecycle_complete`、reviewer `review_decided`、
`incremental_progress` 三種 postcondition——完全沒有這個 hook。

結果是：一個被 fence 的帳號進入 `recovering` 之後，只要接下來派到它的是普通任務工作
（而不是剛好一個 discussion-planning 派工），無論那個 worker 多成功地完成生命週期，pool 都
會永遠停在 `recovering`、永遠只剩 1 個 slot。`probe_attempts` 不會再增加，因為 `cooldown →
recovering` 的轉換一輩子只會發生一次。這不是排程延遲，是缺少狀態轉換出口。

### 1.1 Live runtime 佐證（唯讀觀測，未做任何寫入）

於 2026-09-08 直接讀取 live runtime state（`$PANTHEON_STATUS_ROOT/.orchestrator/state.json`，
runtime 停在 `367fa6aa`）的 `account_pool_runtime`：

| pool | state | effective_concurrency | last_probe_at | probe_attempts |
| --- | --- | --- | --- | --- |
| `antigravity_main` | `recovering` | 1 | 2026-09-06T11:01:30Z | 1 |
| `codex_bjoe` | `recovering` | 1 | 2026-09-05T15:58:45Z | 12 |
| `claude_main` | `healthy` | 5 | — | — |
| `codex_lupin` | `healthy` | 2 | 2026-08-21T17:34:27Z | 1 |

兩個 pool 分別卡在 `recovering` 超過兩天與三天。同一份 state 的 worker 紀錄顯示，這段期間
它們各自都跑出過乾淨的成功退出：

| run_id | logical agent → pool | exit_code | runner_status | progress_outcome |
| --- | --- | --- | --- | --- |
| `codex-20260908T130600Z-1ae0bc84` | `codex` → `codex_bjoe` | 0 | `completed` | `review_decided` |
| `antigravity2-20260908T114333Z-a428b6fe` | `antigravity2` → `antigravity_main` | 0 | `completed` | `lifecycle_complete` |

兩筆都是零退出、無 signal、且 `progress_outcome` 正是修復後新增 hook 所涵蓋的兩種
outcome，但對應 pool 至今仍是 `recovering`。這直接對上第 1 節的程式碼分析。

---

## 2. 修復內容

`.orchestrator/worker_lifecycle.py`：在 `poll_workers()` 既有的成功分支中，於 postcondition
判定（`success_outcome in {"lifecycle_complete", "review_decided", "incremental_progress"}`）
與 owner handoff seal 都通過、worker 標記為 `completed` 並寫完 activity log 之後，呼叫既有的
`record_account_pool_canary_success(config, state, worker)`，再 finalize queue event。

設計上刻意維持最小面積：

- **沿用既有 helper，不新增恢復語意。** pool 解析、configured capacity 讀取、
  `recovering` 前置條件、activity log 事件（`account_pool_recovered`）全部由原本的
  `record_account_pool_canary_success()` 負責；它對非 `recovering` 的 pool 直接回傳 `False`。
- **Pool 隔離由 helper 自身保證。** pool 由該 worker 的 `logical_agent_id / agent_id /
  provider` 解析，只有跑出這次成功的那個帳號會恢復，其他 pool 不受影響。
- **不擴張 coordination semantics。** discussion-planning 與 coordination 兩個既有退出分支
  完全沒有更動；coordination worker 一如既往在自己的分支 `continue`，不會恢復 pool。
- **不建立假 planning task。** 沒有任何新的派工、任務或探測協定；canary 仍然是「真的被派到
  的那個真實任務」。

### 2.1 為什麼失敗路徑結構上不會誤恢復

新的呼叫點位於成功分支內部，而所有失敗路徑在抵達它之前都已經 `continue`：

| 情境 | 攔截點 | 結果 |
| --- | --- | --- |
| 非零退出 / runner 回報失敗 | `detect_worker_failure` → failure 分支 `continue` | 不恢復；quota 類另外走 `mark_account_pool_cooldown` 重新 fence |
| Signal termination | `worker_was_terminated()` 讓 `is_structured_successful_worker()` 為 False，`runner_succeeded` 為 False | 不恢復 |
| 零退出但無進展 | `success_outcome == "no_progress"` → 走 `redispatch_statuses` 失敗分支 | 不恢復 |
| Handoff seal rejected | seal 分支自己 `continue` | 不恢復 |
| 歷史 terminal run 重讀 | `TERMINAL_WORKER_STATUSES` 快速路徑 `continue`；唯一的 fall-through 需要 `_runner_reports_failure()` 為真，而該條件蘊含 `is_structured_successful_worker()` 為假 | 不恢復 |

---

## 3. 測試

`.orchestrator/test_supervisor.py` 新增 8 個回歸測試，全部走**真正的 `supervisor.poll_workers()`
路徑**，而不是直接呼叫恢復 helper。

`SuccessfulWorkerPostconditionTests`（config 來自 `config.example.json` fixture，
`account_pool_runtime` 以兩個同時 `recovering` 的 pool 起始，用來同時驗證恢復與隔離）：

- `test_poll_owner_lifecycle_completion_recovers_only_its_own_pool` — owner 完成生命週期
  （seal accepted）後 `antigravity_main` 回到 `healthy` 與 configured capacity 3，
  `codex_lupin` 仍為 `recovering`。
- `test_poll_owner_incremental_progress_recovers_recovering_pool` — `incremental_progress`
  同樣恢復，另一 pool 不受影響。
- `test_poll_reviewer_decision_recovers_the_reviewer_pool` — reviewer `review_decided`
  恢復 `codex_lupin`（capacity 2），且不觸發 owner handoff seal。
- `test_poll_zero_exit_without_progress_leaves_pool_recovering` — 零退出無進展維持 `recovering`。
- `test_poll_rejected_handoff_seal_leaves_pool_recovering` — seal 被拒維持 `recovering`。
- `test_poll_signal_terminated_worker_leaves_pool_recovering` — SIGTERM 維持 `recovering`。
- `test_poll_historical_terminal_run_never_recovers_pool` — 四種歷史 terminal status
  重讀皆維持 `recovering`。

`QuotaPlanningAndCoordinationPollOrderTests`：

- `test_quota_failure_on_owner_task_worker_refences_a_recovering_pool` — canary 本身撞到
  terminal quota 時，pool 從 `recovering` 被重新 fence 回 `cooldown`（effective concurrency 0），
  且 `record_account_pool_canary_success` 未被呼叫。

### 3.1 回歸測試確實會抓到這個缺陷

把 `worker_lifecycle.py` 的新呼叫移除後，於同一 selection 重跑，三個正向測試全部轉紅
（`AssertionError: 'recovering' != 'healthy'`），四個負向守門測試維持綠（它們本來就該綠）：

```
FAILED .orchestrator/test_supervisor.py::SuccessfulWorkerPostconditionTests::test_poll_owner_incremental_progress_recovers_recovering_pool
FAILED .orchestrator/test_supervisor.py::SuccessfulWorkerPostconditionTests::test_poll_owner_lifecycle_completion_recovers_only_its_own_pool
FAILED .orchestrator/test_supervisor.py::SuccessfulWorkerPostconditionTests::test_poll_reviewer_decision_recovers_the_reviewer_pool
```

修復復原後重跑同一 selection：全綠（見第 4 節收據）。

---

## 4. 驗證收據

所有命令皆在 `/tmp/pantheon-worker-worktrees/pantheon/orch-account-pool-canary-recovery-001`
執行，未經 pipe、未加 `|| true`、未 background，exit code 為工具本身的 terminal status。

- Head SHA：`91cc0090063f96ef2a1d867a2befb58bff5d0211`
- 日期：2026-09-08 (UTC)

| # | 命令 | exit | 時間 | 結果 |
| --- | --- | --- | --- | --- |
| 1 | `uv run --python 3.12 pytest -q .orchestrator/test_supervisor.py::SuccessfulWorkerPostconditionTests .orchestrator/test_supervisor.py::AccountPoolSchedulingTests .orchestrator/test_supervisor.py::QuotaPlanningAndCoordinationPollOrderTests` | 0 | 6s | 40 passed |
| 2 | `uv run --python 3.12 ruff check .orchestrator/worker_lifecycle.py .orchestrator/test_supervisor.py` | 0 | 1s | All checks passed |
| 3 | `git diff --check` | 0 | <1s | 無 whitespace 問題 |

Python 釘在 3.12（`uv run --python 3.12`）：cp314 缺 `pgserver` wheel，裸 `python3` 也沒有 pytest。

### 4.1 完整 `test_supervisor.py` 與 baseline 差集

額外跑了整份 `.orchestrator/test_supervisor.py`（非宣告 selection，僅作為附加檢查），
exit code 1，4 個失敗全部落在 `ReviewHeadFreezeTests`：

```
FAILED .orchestrator/test_supervisor.py::ReviewHeadFreezeTests::test_approve_fails_closed_when_approved_head_cannot_be_resolved
FAILED .orchestrator/test_supervisor.py::ReviewHeadFreezeTests::test_approve_refuses_to_overwrite_uncleared_approved_head
FAILED .orchestrator/test_supervisor.py::ReviewHeadFreezeTests::test_approve_saves_approved_head_and_rejects_same_owner_reviewer
FAILED .orchestrator/test_supervisor.py::ReviewHeadFreezeTests::test_restore_approved_refuses_without_a_durable_approved_head
```

失敗原因是 `ai_status.command_approve` 的 role/provider 審查政策
（`Claude ... is not permitted for role reviewer on unclassified work (allowed: codex)`），
與 account pool 無關。為了確認是既有 baseline 而非本次引入，在 base commit `00c03473`
另開一個 detached worktree 跑同一個 class：

```
uv run --python 3.12 pytest -q .orchestrator/test_supervisor.py::ReviewHeadFreezeTests
# exit 1，同樣這 4 筆失敗
```

差集為空：本 task 未新增任何測試失敗。該 baseline 失敗不屬於本 task 範圍，未在此修改。
（臨時 worktree 已於量測後移除。）

---

## 5. Live runtime rollout 的前後 readback 條件（尚待 review 與批准）

本 task **不執行** live rollout：沒有動 live state/config、沒有手動重設 quota、沒有重啟或啟用
runtime。以下是這個修復合併後、由有權限者執行 rollout 時應該量的條件，供 reviewer 檢核。

### 5.1 Rollout 前（前置條件）

1. 修復已合併進 `dev`，且 live runtime checkout 的 HEAD 已前進到含本 commit 的版本
   （rollout 前 runtime 停在 `367fa6aa`，不含本修復）。
2. 讀取 `$PANTHEON_STATUS_ROOT/.orchestrator/state.json` 的 `account_pool_runtime`，記下每個
   pool 的 `state`、`effective_concurrency`、`generation`、`probe_attempts`。預期至少
   `antigravity_main` 與 `codex_bjoe` 仍為 `recovering` / `effective_concurrency: 1`。
3. 確認 supervisor 重啟流程本身的既有競態注意事項（先更新工作樹再重啟；config 只在啟動時載入）。

### 5.2 Rollout 後（成功判準）

在 runtime 帶著修復實際跑完**一個** 派到該 pool 的普通任務、且該 worker 以
`exit_code: 0` 搭配 `progress_outcome ∈ {lifecycle_complete, review_decided,
incremental_progress}` 落地之後：

1. 該 pool 的 `state` 由 `recovering` 變為 `healthy`。
2. 該 pool 的 `effective_concurrency` 等於 config 中的 `max_concurrent`
   （`antigravity_main` → 3、`codex_bjoe` → 3，以當時 live config 為準）。
3. 該 pool 出現 `last_recovered_at` 與 `last_canary_run_id`，且 `last_canary_run_id` 等於那個
   成功 worker 的 `run_id`；`reason` 被清為 `null`。
4. activity log 出現一筆 `account_pool_recovered` 事件，`account_pool` 為該 pool。
5. **隔離檢查**：同一時間其他仍在 `recovering` 或 `cooldown` 的 pool 狀態不變
   （`state`、`effective_concurrency`、`generation` 皆不動）。

### 5.3 不應發生（負向判準）

- 沒有任何真實成功 worker 時，pool 不得自行變 `healthy`。
- 失敗、被 signal 中止、零退出無進展、handoff seal 被拒的 worker 之後，pool 必須維持
  `recovering`（quota 失敗則應被重新 fence 為 `cooldown`）。
- 不得為了觸發恢復而建立假的 planning task 或額外探測派工。

---

## 6. 邊界

- 只改 `.orchestrator/worker_lifecycle.py` 一處成功分支與 `.orchestrator/test_supervisor.py`
  的回歸測試，加上本 evidence 文件。
- 未修改 `config.example.json`、live config、live state，或任何 cooldown/probe 排程邏輯。
- 未觸碰 planning 行為與 coordination 分支。
