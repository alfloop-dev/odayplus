---
evidence_id: ODP-DISPATCH-PREFERENCE-GUARDS-001
title: "修補 owner 偏好共用容量、immutable finalize 與 CI recovery 競態"
date: 2026-09-06
status: IMPLEMENTED
owner: Claude
reviewer: Codex
repository: alfloop-dev/odayplus
task: ODP-DISPATCH-PREFERENCE-GUARDS-001
base_ref: eed8d51b
source_docs_ref: eed8d51bb8a1
---

# 修補 owner 偏好共用容量、immutable finalize 與 CI recovery 競態

本任務是 ODP-IMPLEMENTATION-OWNER-PREFERENCE-001（PR #1209）與
ODP-REVIEW-CONFLICT-CI-RECOVERY-001（PR #1210）合併後的 follow-up。原任務已封存，
本次一次修補 root 與獨立審查確認的三項缺口，全部沿用既有 selector / recovery / CI reader，
未新建 scheduler、quota 系統、CI 判斷或部署管線。

## 0. 派工前置：`mutates_canonical` 誤設

task record 起初帶 `mutates_canonical: false`，與自身 acceptance（明文要求
`worker_commit` / `task_finalize` 同一 task PR）矛盾，supervisor 因此以 non-mutating
dispatch 喚醒 owner，禁止改碼與 commit，本任務結構上無法完成。owner 未執行 supersede
（那會把已驗證的缺口連同「工作從未開始」一起歸檔），改記 blocker 交回 root；root 於
2026-09-06T00:33:37Z 確認欄位設置錯誤並更正為 `true`，scope、驗收、owner／reviewer 均未更動。
此為派工 metadata 恢復，不計入 review churn。

---

## 1. 缺口一：共用 account pool 的容量沒有被計入

### 1.1 根因

`worker_failure_policy.agent_has_free_dispatch_slot` 原本只問 **單一 logical agent**：

```python
used = len(loads.get(display_name_for(config, agent_id), []) or [])
return used < agent_dispatch_capacity(config, agent_id)
```

兩邊都看不到同 pool 的其他 alias。live 配置中 `Antigravity`、`Antigravity2`、
`Antigravity3` 是三個 ownership role，共用 **一個** 真實帳號與一組 slot；當 5 個
slot 全被指向 `Antigravity2` 的 **未投遞 queue event** 佔滿時：

| 量測對象 | 讀到的值 | 結論 |
|---|---|---|
| `agent_dispatch_loads["Antigravity"]` | 不存在（0） | 「這條 lane 全空」 |
| `agent_dispatch_capacity("antigravity")` | 5 | 「還有 5 格」 |
| 實際 `agy_main` pool 可用量 | 0 | 起不了任何 worker |

於是 `Antigravity` / `Antigravity3` 取得 rank 0，壓過真的有空 slot 的候選人，
把 task 排進一個沒有人能開始的佇列前面。

**既有的 `agent_auto_dispatch_block_reason` 補不上這個洞**，原因有二：

1. 它只比對 pool 的 **active** worker（`active_quota_group_counts`），完全不算
   pending 的 queue event；
2. `if quota_limit and quota_group:` 在 `quota_limit` 為 falsy 時整段跳過。

### 1.2 修法

新增兩個 policy 函式，帳本完全複用 dispatcher 自己的：

| 函式 | 職責 |
|---|---|
| `dispatch_pool_usage(config, state)` | 每個真實 account pool 的 active + pending 佔用數，或 `None` |
| `account_pool_has_free_dispatch_slot(config, state, agent, pool_usage)` | 該 agent 背後的帳號還能不能再起一個 worker |

`agent_has_free_dispatch_slot` 改為兩道天花板取低者：logical agent 的 slot 數，
以及 account pool 的 effective concurrency。

```python
    if used >= agent_dispatch_capacity(config, agent_id):
        return False
    return account_pool_has_free_dispatch_slot(config, state, agent_id, pool_usage)
```

`owner_preference_ranks` 每次選擇只取一次 `dispatch_slot_loads` 與
`dispatch_pool_usage`，所以全部候選人是對同一個瞬間排名，共用 pool 也只被數一次。

### 1.3 四個必須守住的邊界

- **跨 alias**：`active_quota_group_counts` 讀 worker record 的 `quota_group`
  （dispatch 時由 `agent_quota_group_id` 寫入），`queued_quota_group_counts` 讀
  queue event 的 `target_agent` 再解析 `account_pool`；兩者都以 pool 為 key，
  因此 `Antigravity2` 的佔用會計入 `Antigravity` 的判斷。
- **不重複計算**：`queued_quota_group_counts` 本身會排除「已經有 active worker 的
  同一個 `queue_event_id`」。已投遞但仍留在 queue 檔中的 event 不會被算兩次，
  否則 pool 會提早一格被誤判為滿。
- **`effective_limit == 0` 不是「無上限」**：`account_pool_effective_concurrency`
  對 disabled／paused／exhausted／cooldown 的 pool 就是回 0。final dispatcher 的
  `if quota_limit and ...` 會把 0 當 falsy 略過，本 guard 明確 `<= 0 → False`。
- **量不到就保守**：`state` 不是 dict、event queue path 缺失或不可讀、計數不是整數，
  一律回 `None`；`owner_preference_ranks` 見 `None` 就讓所有候選人 rank 1，退回原行為。
  其中 event queue path **缺失** 這條是實作過程中由新測試抓出來的：
  `queued_quota_group_counts` 內部 `except KeyError: queued_events = []` 會把
  「讀不到佇列」靜默回報成「沒有 pending」，與「pool 是空的」無法區分，因此
  `dispatch_pool_usage` 先自行 `config_path(config, "event_queue")` 讓它 fail closed。
- **final dispatcher 的最後容量檢查完全保留**，本次沒有移除任何一道既有閘。

---

## 2. 缺口二：偏好不得介入 immutable closeout

### 2.1 根因

`owner_preference_applies_to_task` 原本只排除 role、human gate、`non_dispatchable`
與不在名單內的 `task_class`。`review_approved` 的 task 若 `task_class` 是
`implementation`／`remediation`／`documentation`，偏好照樣生效。

但進入 `review_approved` 會凍結 **確切的 PR head**，closeout 對該 branch 是唯讀的
（見 `.orchestrator/skills/task-closeout-finalization.md`）。此時「誰該當 owner」不是
一個關於「哪條 lane 該實作」的問題——答案早已由寫出那顆 commit 的人決定。讓 owner
偏好在這裡改派，等於把別人已審核的 head 交給一條沒寫過它、也不該碰它的 lane。

同樣的凍結語意也適用於已記錄 `approved_head`、或已進入 `merge_route` 的 task；
`dispatch_engine.recover_conflicted_review_prs` 早就用同一組欄位判斷凍結。

### 2.2 修法

新增 `task_closeout_owner_is_frozen(config, task)`，在
`owner_preference_applies_to_task` 內於 human gate 檢查之後短路回 False：

- `task.approved_head` 為真，或 `task.merge_route is not None` → 凍結；
- `task.status` 落在設定的 `ready_dispatcher.finalize_statuses` → 凍結；
- 無論該 fleet 怎麼拼自己的 finalize status，`review_approved` 一律凍結
  （凍結是 transition 的性質，不是拼字的性質）。

rank 回到全 1 後，`first_viable_agent` 的排序鍵與
`reassign_unavailable_reviewers` 的 stable sort 都會產生與偏好未配置時 **逐字相同**
的順序。

### 2.3 不凍結的部分

`todo`／`in_progress`／`blocked`／`review` 等一般 owned 狀態完全不受影響，正常的
owner fallback 沒有被凍住。reviewer 選擇、`runtime_release`、`rollout`、
`Human/*` 與 `non_dispatchable` 的既有規則本來就在偏好之外，本次未動。

### 2.4 測試 fixture 的修正

`test_dispatch_engine.py::PausedOwnerFailoverPreferenceTests._owned_task` 原本以
`review_approved` 當預設狀態，來證明偏好會改派 owner——那正是唯一不該被改派的狀態。
fixture 改為 `in_progress`（真正還沒寫、owner 仍是活問題的工作），並新增
`test_frozen_closeout_states_keep_the_existing_candidate_order` 作負向回歸：
`review_approved`、`approved_head`、`merge_route` 三種凍結狀態下，
「有偏好」與「無偏好」兩次執行必須得到 **相同** 的 owner。

---

## 3. 缺口三：CI `none → pending` 競態

### 3.1 根因

`recover_conflicted_review_prs` 的前提是「GitHub 從未在這顆 head 上跑過任何 check」。
原實作在 `dispatch_engine.py:754` 讀一次 CI，之後 `:774` 只重讀 **PR facts**
（state / mergeability / head）：

```python
pr_status, ci_status = runtime_ai_status.task_pr_ci_status(task_id)   # 可能來自快取
...
before = _review_pr_facts(slug, pr_number)
...
if _review_pr_facts(slug, pr_number) != before:   # 只確認 PR 沒動，沒再問 CI
```

`task_pr_ci_status` 預設 `max_age_seconds=10.0`，會從 `_CI_STATUS_CACHE` 回答。
若 owner 在這段時間內解掉衝突、GitHub 隨即開跑 checks，PR facts 的兩次讀取都還會
說「OPEN + CONFLICTING + 同一顆 head」（mergeability 的更新有延遲），而 CI 早已
`none → pending`。此時 recovery 會把一個 **正在跑 CI** 的 review 抽回 owner，
並寫下 marker，讓這顆 head 之後再也不會被重試。

### 3.2 修法

在轉態前，用同一個 canonical reader 以 `max_age_seconds=0` 再驗一次：

```
PR facts A  →  fresh canonical CI（cache bypass）→  PR facts B
```

順序刻意如此：第一次 cached 讀取留著當便宜的 disqualifier（避免每個 tick 為大量
不相干 task 付 `gh` 呼叫），真正授權轉態的是 fresh 讀取；而 PR facts B 仍排在最後，
所以「問 CI 的這段時間內 PR 有沒有動」依然被涵蓋。

`none` 以外的任何答案（`pending` / `success` / `failure` / `unknown`）、PR 不再是
OPEN、或 fresh 讀取本身丟例外，一律 **不轉態、不寫 marker**，等下一個 tick。

### 3.3 保留不動

Human gate、`non_dispatchable`、active／pending worker、helper lease、
`approved_head`／`merge_route`、queued 等既有 guard；canonical CAS 轉態；
同一顆 head 只恢復一次的 marker 語意；owner／churn／continuation 欄位的保存。

---

## 4. 驗證

全部指令在 `task/ODP-DISPATCH-PREFERENCE-GUARDS-001`（base `eed8d51b`，即當時的
`origin/dev` tip）上執行，Python 釘 3.12（cp314 缺 `pgserver` wheel）。

```bash
uv run --frozen --python 3.12 ruff check .orchestrator delivery_toolchain scripts
uv run --frozen --python 3.12 python delivery_toolchain/governance/check_orchestrator_config.py
uv run --frozen --python 3.12 python delivery_toolchain/governance/check_config_wiring.py
uv run --frozen --python 3.12 python delivery_toolchain/governance/check_code_boundaries.py
uv run --frozen --python 3.12 pytest -m "not requires_live_env" -q \
  .orchestrator/test_worker_failure_policy.py \
  .orchestrator/test_dispatch_engine.py \
  .orchestrator/test_dispatch_policy.py \
  .orchestrator/test_supervisor_scope_injection.py
```

| 檢查 | 結果 |
|---|---|
| `ruff check` | All checks passed! |
| `check_orchestrator_config.py` | Validated 2 config documents and their merged runtime views. |
| `check_config_wiring.py` | All 186 config keys are read by production code.（未新增設定鍵） |
| `check_code_boundaries.py` | Code boundary checks passed for 1126 files.（未新增 .py 檔，inventory 無異動） |
| 焦點 pytest | 191 passed（51 + 7 + 127 + 6），exit code 0 |

`test_supervisor_scope_injection.py` 一併執行：`worker_failure_policy` 與
`dispatch_engine` 靠 `_sync_supervisor_scope()` 注入名稱且整檔 `# ruff: noqa: F821`，
該測試靜態解析自由名稱並確認 supervisor 供得出來。本次新用到的
`active_quota_group_counts`、`queued_quota_group_counts`、`agent_quota_group_id`、
`account_pool_effective_concurrency`、`ready_dispatch_settings`、`config_path`
都在其涵蓋範圍內。

### 4.1 新增／修改的測試與其對應的失效情境

`.orchestrator/test_worker_failure_policy.py`

- `OwnerPreferenceSharedPoolCapacityTests`（新類別，7 例）
  - `test_pool_saturated_by_another_alias_pending_falls_back_to_a_free_lane`
    ——5 個指向 `Antigravity2` 的 pending event 佔滿共用 pool，`Antigravity` /
    `Antigravity3` 不得取得 rank 0；同時斷言 per-agent 帳仍顯示「有空位」，
    把兩道天花板的分歧寫進測試。
  - `test_one_free_slot_in_the_shared_pool_is_still_preferred`——guard 只在飽和時
    收手，不是一律關閉偏好。
  - `test_mixed_active_and_pending_never_charge_one_dispatch_twice`——3 個 active
    worker（其中一個帶 `queue_event_id`）+ 2 個 queue event = 4 筆而非 5 筆。
  - `test_the_lower_of_the_two_ceilings_decides`——pool 處於 `recovering`，
    5 個 slot 但 effective concurrency 只有 1。
  - `test_an_effective_limit_of_zero_is_a_limit_not_an_absence`
  - `test_a_pool_with_no_declared_ceiling_keeps_the_previous_behaviour`
  - `test_unmeasurable_pool_usage_never_claims_capacity`
- `OwnerProviderPreferenceTests` 新增 3 例：`test_frozen_closeout_ordering_is_completely_unchanged`、
  `test_frozen_closeout_detection_reads_the_configured_finalize_statuses`、
  `test_ordinary_owned_work_is_not_frozen_by_the_new_guard`。
- `_state()` fixture 補上 worker record 的 `quota_group` 欄位（live dispatch 會寫入），
  否則 fixture 會系統性低報所有共用 pool。

`.orchestrator/test_dispatch_engine.py`

- `_owned_task` 預設狀態 `review_approved` → `in_progress`（見 §2.4）。
- 新增 `test_frozen_closeout_states_keep_the_existing_candidate_order`（凍結負向回歸）
  與 `test_pending_delivery_on_the_shared_pool_also_saturates_the_lane`。

`.orchestrator/test_dispatch_policy.py`

- `_run_recovery` 的 `ci` / `ci_error` 改為可接受序列（最後一項重複），並新增
  `timeline` 參數記錄 `("gh", selector)` 與 `("ci", max_age_seconds)` 的呼叫順序。
- 新增 `test_the_authorising_ci_read_is_taken_fresh_and_after_the_first_pr_read`：
  直接斷言 timeline 為 `[("ci", None), ("gh", "1170"), ("ci", 0), ("gh", "1170")]`，
  把「PR facts A → fresh CI → PR facts B」的順序與 cache bypass 寫成契約。
- 新增 `test_ci_that_starts_after_the_cached_verdict_stops_the_recovery`（8 個
  參數化情境）：`pending` / `success` / `failure` / `unknown` / PR 不可讀 /
  PR closed / PR merged / fresh 讀取丟例外，全部必須不轉態、不寫 marker、不寫 log，
  且 timeline 停在 fresh 讀取（PR facts B 從未被取）。

### 4.2 突變驗證（確認新測試真的踩到缺陷路徑）

綠色測試不等於測到缺陷路徑。以下三處守衛被暫時停用後重跑同一組焦點測試：

1. `account_pool_has_free_dispatch_slot` 開頭插入 `return True`；
2. `owner_preference_applies_to_task` 內的 `task_closeout_owner_is_frozen` 檢查停用；
3. `dispatch_engine` 的 fresh CI 區塊停用。

三處同時停用後重跑，**16 個測試函式轉紅**（其中 8 個是 `subTest` 層級的
`SUBFAILED`），且轉紅的全部是本次針對缺陷新增的測試：

| 停用的守衛 | 轉紅的測試 |
|---|---|
| 共用 pool 容量 | `test_pool_saturated_by_another_alias_pending_falls_back_to_a_free_lane`（含 `Antigravity` / `Antigravity3` 兩個 subTest）、`test_mixed_active_and_pending_never_charge_one_dispatch_twice`、`test_the_lower_of_the_two_ceilings_decides`、`test_an_effective_limit_of_zero_is_a_limit_not_an_absence`、`test_unmeasurable_pool_usage_never_claims_capacity` |
| immutable closeout | `test_frozen_closeout_ordering_is_completely_unchanged`、`test_frozen_closeout_states_keep_the_existing_candidate_order`（各 3 個 subTest：`review_approved` / `approved_head` / `merge_route`；實際訊息為 `'Claude' != 'Codex'`，即偏好確實改派了凍結 task 的 owner） |
| CI fresh 讀取 | `test_the_authorising_ci_read_is_taken_fresh_and_after_the_first_pr_read` 與 `test_ci_that_starts_after_the_cached_verdict_stops_the_recovery` 的全部 8 個參數化情境 |

不變式測試在突變後 **維持綠色**，符合預期：
`test_one_free_slot_in_the_shared_pool_is_still_preferred` 與
`test_a_pool_with_no_declared_ceiling_keeps_the_previous_behaviour` 斷言的是
「偏好仍應生效」，把 pool 判斷改成永遠有空位並不會與它們衝突。

一個誠實的涵蓋範圍註記：`test_dispatch_engine.py::test_pending_delivery_on_the_shared_pool_also_saturates_the_lane`
在突變下 **沒有** 轉紅。該 fixture 的 `agy_b_pool` 只有 1 個 slot，logical agent 的
capacity 也是 1，因此 per-agent 那一半的檢查就已經擋下；它驗證的是「pending 的
queue event 也算佔用」，不是共用 pool 的跨 alias 加總。跨 alias 的部分由
`OwnerPreferenceSharedPoolCapacityTests` 涵蓋（該 fixture 是 3 個 alias 共用 5 個 slot）。

突變驗證後三處守衛均已還原，並以 `md5sum` 比對確認檔案回到突變前狀態。

### 4.3 原未配置行為的相容性

`preferred_providers` 未配置、`enabled: false`、`state` 缺失、event queue 不可讀
這四種情況的既有測試全部保留且維持綠色，選擇器行為與本次修改前逐字相同。

---

## 5. 未做（刻意不在本任務範圍）

- **未執行 live rollout**：未編修 `.orchestrator/config.json`、未重啟任何服務、
  未清除 quota、未動人類授權。載入偏好並驗證 Supervisor 實際派工屬
  ODP-SUPERVISOR-OWNER-PREFERENCE-LIVE-ROLLOUT-001（owner: Codex），
  且需本 PR 通過 root Codex 的 exact-head 整合審查並 merged 後才解鎖。
- **未改 schema 或既有 CI reader**：沒有新增設定鍵，`task_pr_ci_status` 只是以既有的
  `max_age_seconds` 參數多呼叫一次，reader 本身未動。
- **未改 immutable closeout 的 owner 選擇邏輯本身**：只讓偏好不要介入；closeout
  owner 規則、reviewer 選擇、`runtime_release`／`rollout`／`Human/*`
  ／`non_dispatchable` 的行為都沒有變。
- **未重跑完整 CI**：依驗收要求只跑受影響的焦點 suite 與配置契約，完整 CI 交由 PR 執行一次。

---

## 6. Rollback

三項修補彼此獨立，可個別回退：

| 缺口 | 回退方式 | 回退後行為 |
|---|---|---|
| 共用 pool 容量 | 將 `agent_has_free_dispatch_slot` 末行改回 `return used < agent_dispatch_capacity(...)`；`dispatch_pool_usage` / `account_pool_has_free_dispatch_slot` 可保留為無呼叫者 | 回到只看 logical agent 的容量判斷 |
| immutable closeout | 移除 `owner_preference_applies_to_task` 內的 `task_closeout_owner_is_frozen` 短路 | 偏好重新對 `review_approved` 生效 |
| CI 競態 | 移除 `dispatch_engine` 的 fresh CI 區塊 | 回到單次 cached CI 讀取 |

全域關閉：把 `.orchestrator/config.json` 的
`ready_dispatcher.owner_provider_preference.enabled` 設為 `false`，或清空
`preferred_providers`，整條偏好路徑（含本次新增的 pool 容量探測）即不會被觸發，
selector 回到偏好導入前的行為。缺口三的 CI 守衛不受該開關影響——它是一道只會讓
recovery 更保守的檢查，停用它只會讓 recovery 更容易誤動。

完整回退則 revert 本任務的單一 task PR 即可；本次未產生 migration、未寫入任何
runtime state、未變更任何持久化結構。
