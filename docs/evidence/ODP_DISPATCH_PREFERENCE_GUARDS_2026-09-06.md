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

## 0.1 第一次實質退回：容量守衛的兩個洞沒被補到

commit `55975071`／PR #1213 送審後，reviewer Codex 於 2026-09-06T01:04:31Z 對 exact head
`55975071` 提出正式 review finding 退回。這是本任務**第一次實質退回**，不是 control-plane
recovery，也不是 metadata 修正——第一版把缺口一宣告為已修補，但守衛本身仍有兩處會在
真實配置下回到缺陷行為：

| # | 缺陷 | 第一版的錯誤前提 |
|---|---|---|
| 1 | `dispatch_pool_usage` 只呼叫 `config_path(config, "event_queue")` | 「路徑解析得出來」＝「佇列讀得到」。路徑已設定但檔案不存在時，`load_jsonl` 回 `[]`，pending 佔用被算成 0，共用 pool 仍取得 rank 0 |
| 2 | `account_pool_has_free_dispatch_slot` 在 `effective_limit is None` 時直接回 `True`，有 limit 時只比對 limit | 「配額＝容量」。沒寫 `max_concurrent` 不代表沒有 pool；`max_concurrent: 10` 也不會把 5 個 slot 變成 10 個。5 個實體 slot 被 `Antigravity2` 的 pending 佔滿時，`Antigravity` / `Antigravity3` 仍取得 rank 0 |

兩項都在本次同一個 task PR 內修完，缺口二（immutable closeout）與缺口三
（fresh CI `A → CI → B` 順序）經 reviewer 確認正確，未重寫。以下第 1 節描述的是
**修正後**的最終實作。

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

新增三個 policy 函式，帳本完全複用 dispatcher 自己的：

| 函式 | 職責 |
|---|---|
| `dispatch_pool_usage(config, state)` | 每個真實 account pool 的 active + pending 佔用數，或 `None` |
| `account_pool_physical_capacity(config, agent)` | 該帳號背後**實際有幾個 process**（去重後的 slot 身分數） |
| `account_pool_has_free_dispatch_slot(config, state, agent, pool_usage)` | 該 agent 背後的帳號還能不能再起一個 worker |

`account_pool_physical_capacity` 是退回後補上的關鍵一環。`agent_dispatch_capacity`
對同一個 pool 的每個 alias 都回答 5，因為 `logical_worker_slot_ids` 會把每個 alias
都解析到**同一組** `dispatch_slot_for_pool` slot 上；把三個 5 相加會得到 15，而實際
只有 5 個 process。因此這裡改為對同 pool 的 logical agent 取 slot 身分的**聯集**，
數出唯一的實體上限：

```python
    for member in members:
        slots.update(logical_worker_slot_ids(config, member) or [member])
    return len(slots)
```

沒有宣告 slot 的 logical agent 貢獻它自己（正是 `agent_dispatch_capacity` 給它的
那 1 個 process），所以真正 unpooled 的配置得到「每個身分 1 格」，pool 上限恰等於
各 per-agent 上限之和，**永遠不會比原本那道檢查更緊**；只有真的共用 slot 時才會咬住。

`account_pool_has_free_dispatch_slot` 於是對兩道獨立天花板取低者——帳號**有幾個
process**（physical capacity）與帳號**現在被允許用幾個**（effective concurrency）：

```python
    if effective_limit is not None and effective_limit <= 0:
        return False
    ceiling = account_pool_physical_capacity(config, agent_id)
    if ceiling <= 0:
        return False
    if effective_limit is not None:
        ceiling = min(ceiling, effective_limit)
    return pool_usage.get(quota_group, 0) < ceiling
```

`effective_limit is None` 不再直接回 `True`：沒有寫下配額不代表沒有 pool，此時由
slot 數獨自定界。`agent_has_free_dispatch_slot` 仍先問 logical agent 的 slot 數，
再問 account pool。

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
- **配額不是容量**：`effective_limit` 是「現在被允許用幾個」，不是「有幾個」。
  `max_concurrent: 10` 配上 5 個 slot，實際上限仍是 5；沒有 `max_concurrent`
  則由 slot 數獨自定界，而不是視為無限。兩道天花板取低者，見 1.2。
- **量不到就保守**：`state` 不是 dict、event queue **讀不到**、計數不是整數，
  一律回 `None`；`owner_preference_ranks` 見 `None` 就讓所有候選人 rank 1，退回原行為。
  「讀不到」的判定是本次退回修正的重點：`queued_quota_group_counts` 內部
  `except KeyError: queued_events = []`，而 `load_jsonl` 對**不存在的檔案**直接回
  `[]`，兩者都會把「讀不到佇列」靜默回報成「沒有 pending」，與「pool 是空的」無法區分。
  第一版只做 `config_path(config, "event_queue")`，那只證明「有人把路徑寫下來」，
  路徑指向不存在的檔案時照樣回報零佔用。改為實際開啟佇列：

  ```python
        with config_path(config, "event_queue").open("rb"):
            pass
  ```

  缺檔、無權限、路徑不是檔案都會拋 `OSError` 而落入 `None`。這裡只做**存在性與可讀性
  探測**，實際讀取仍是 `load_event_queue` 的職責，沒有新增第二個計數 reader。
  代價是誠實的：從未寫入過 event queue 檔的全新 fleet 會被判為「量不到」而不啟用偏好，
  退化成本次修改前的排序，而不是做出錯誤的偏好。
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
# 注意：不要再自行加 -q，pyproject 的 addopts 已經有一個（見 4.0）
uv run --frozen --python 3.12 pytest -m "not requires_live_env" \
  --junit-xml=/tmp/focused.xml \
  .orchestrator/test_worker_failure_policy.py \
  .orchestrator/test_dispatch_engine.py \
  .orchestrator/test_dispatch_policy.py \
  .orchestrator/test_supervisor_scope_injection.py
```

| 檢查 | 結果 |
|---|---|
| `ruff check` | All checks passed!（exit 0） |
| `check_orchestrator_config.py` | Validated 2 config documents and their merged runtime views.（exit 0） |
| `check_config_wiring.py` | All 186 config keys are read by production code.（exit 0；未新增設定鍵） |
| `check_code_boundaries.py` | Code boundary checks passed for 1126 files.（exit 0；未新增 .py 檔，inventory 無異動） |
| 焦點 pytest | **exit code 0**；JUnit XML 197 個 testcase，`failures=0` `errors=0` `skipped=0` |

焦點 pytest 的 197 = 退回前的 191 加上本次新增的 6 例。這個數字取自
`--junit-xml` 的 testcase 元素，不是從終端文字數出來的——原因見 4.0。

### 4.0 wait-loop 事故：`-q` 疊成 `-qq`，「N passed」永遠不會出現

實作過程中發生過一次 wait loop：以 `grep passed` 輪詢 pytest 輸出等待完成，但那行
永遠不會印出來，迴圈因此無限等待一個不存在的字串。根因已定位並可穩定重現：

`pyproject.toml` 的 `[tool.pytest.ini_options]` 已經帶 `addopts = "-q"`，命令列再加
一個 `-q` 就變成 `-qq`，而 pytest 在第二級 quiet 會**整段拿掉 summary line**：

```
$ pytest .orchestrator/test_supervisor_scope_injection.py        # addopts 供 -q
......                                                       [100%]
6 passed, 12 subtests passed in 2.19s

$ pytest -q .orchestrator/test_supervisor_scope_injection.py     # 實際是 -qq
......                                                       [100%]
```

兩次都是成功、exit code 都是 0，差別只在有沒有那行字。四檔焦點 suite 的
`/tmp/focused2.log` 全檔只有 3 行、`grep -cE "passed|failed"` 得到 **0**，
而同一次執行的 exit code 是 0、197 個 dot 裡 `F`／`E` 各 0 個。

**結論寫進作業規則**：完成與否的權威是 CLI 的 exit code（背景執行時是
TaskOutput 回報的 exit code），不是 stdout 裡的文字。需要筆數就用
`--junit-xml` 這種機器可讀的產物。本次所有結論都據此取得；順帶一提，用
`while kill -0 ...; do sleep 5; done` 這種前景阻塞等待也不可靠——它自己被 SIGTERM
砍掉並回報 exit 144，那是量測被中斷，與被等待的工作無關（該背景工作實際 exit 0）。

`test_supervisor_scope_injection.py` 一併執行：`worker_failure_policy` 與
`dispatch_engine` 靠 `_sync_supervisor_scope()` 注入名稱且整檔 `# ruff: noqa: F821`，
該測試靜態解析自由名稱並確認 supervisor 供得出來。本次新用到的
`active_quota_group_counts`、`queued_quota_group_counts`、`agent_quota_group_id`、
`account_pool_effective_concurrency`、`ready_dispatch_settings`、`config_path`，
以及退回後新用到的 `logical_worker_slot_ids`、`agent_is_dispatch_slot`、
`agent_dispatch_capacity`，都在其涵蓋範圍內。

### 4.1 新增／修改的測試與其對應的失效情境

`.orchestrator/test_worker_failure_policy.py`

- `OwnerPreferenceSharedPoolCapacityTests`（新類別，13 例，其中 6 例為本次退回後新增）

  退回後新增的 6 例（前 3 例對應 0.1 的兩個缺陷，後 3 例是把既有正確行為鎖住的
  regression lock）：

  | 測試 | 情境 | 突變下 |
  |---|---|---|
  | `test_a_configured_queue_path_pointing_at_nothing_is_not_an_empty_queue` | 路徑已設定、檔案被 unlink；先斷言 `queued_quota_group_counts` 確實回報 `{}`，再要求 `dispatch_pool_usage` 回 `None` | **紅** |
  | `test_a_pool_without_a_quota_ceiling_is_still_bounded_by_its_slots` | 無 `max_concurrent`（`effective_limit is None`），5 個實體 slot 被 pending 佔滿 | **紅** |
  | `test_a_quota_ceiling_above_the_slot_count_does_not_create_slots` | `max_concurrent: 10`、實體 5 格全滿；同時斷言三個 alias 的 `agent_dispatch_capacity` 都回 5（相加會得 15） | **紅** |
  | `test_a_queue_that_cannot_be_read_is_not_an_empty_queue` | 路徑是目錄（所有 uid 皆確定）＋真實 `PermissionError`（只在本行程確實會被拒時斷言，否則不做裝飾性斷言） | 綠（此路徑原本就已 fail closed） |
  | `test_room_under_both_ceilings_keeps_the_preference` | 10 格配額、5 格實體、佔 3 格 → 偏好仍生效 | 綠（正向對照） |
  | `test_a_configured_ceiling_of_zero_is_a_ceiling` | 設定層 `max_concurrent: 0`，不得被 5 格實體救回 | 綠（既有行為的 lock） |

  退回前既有的 7 例：
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

**退回後的第二次突變驗證**（針對 0.1 的兩個缺陷，把守衛精確還原成第一版的樣子，
而不是插入 `return True` 這種粗暴停用）：

1. `dispatch_pool_usage` 的 `with config_path(...).open("rb"): pass` 還原成
   `config_path(config, "event_queue")`；
2. `account_pool_has_free_dispatch_slot` 還原成 `if effective_limit is None:
   return True` ＋ 只比對 `effective_limit`（不取 physical capacity）。

兩處同時還原後執行 `pytest .orchestrator/test_worker_failure_policy.py -k
OwnerPreferenceSharedPoolCapacityTests`：**exit code 1，5 failed / 10 passed**
（3 個測試函式加 2 個 `SUBFAILED`），轉紅的正是 4.1 表中標「紅」的三例：

```
FAILED ...::test_a_configured_queue_path_pointing_at_nothing_is_not_an_empty_queue
FAILED ...::test_a_pool_without_a_quota_ceiling_is_still_bounded_by_its_slots
FAILED ...::test_a_quota_ceiling_above_the_slot_count_does_not_create_slots
SUBFAILED(alias='Antigravity')  ...::test_a_pool_without_a_quota_ceiling_...
SUBFAILED(alias='Antigravity3') ...::test_a_pool_without_a_quota_ceiling_...
```

還原後 `md5sum` 比對確認 `worker_failure_policy.py` 回到突變前狀態
（`a48df3f397adb336e69aceda3b28adc7`），同一組測試重跑 13 passed。

值得誠實記下的一點：`test_a_queue_that_cannot_be_read_is_not_an_empty_queue`
在突變下**沒有**轉紅。原因是「不可讀」這條路徑第一版就已經 fail closed
（`load_jsonl` 讀目錄或無權限檔案都會拋 `OSError` 而被既有 `except` 接住），
真正的缺陷只在「檔案不存在」——`load_jsonl` 對它是靜默回 `[]`。該測試因此是
regression lock 而非缺陷捕捉，不宜宣稱成後者。

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

退回後新增的 `account_pool_physical_capacity` 不影響真正 unpooled 的配置：沒有宣告
slot 的 logical agent 貢獻它自己 1 格，pool 上限因此等於各 per-agent 上限之和，
永遠不會比 `used < agent_dispatch_capacity(...)` 那道既有檢查更緊。唯一會被它咬住的
是真的共用 slot 的配置——那正是本次要修的缺陷。

一個必須誠實揭露的行為差異：單一 unpooled agent 若自身 0 個 active worker 但有 1 個
**pending queue event**，第一版（`effective_limit is None → True`）會給它 rank 0，
修正後會判定無空位。這不是相容性破壞而是同一類缺陷的一部分——那個 pending event
一旦投遞就會吃掉它唯一的 process——但它確實改變了「未設定 `max_concurrent`」配置下
的排序，故在此列明，由 `test_room_under_both_ceilings_keeps_the_preference` 與
`test_a_pool_without_a_quota_ceiling_is_still_bounded_by_its_slots` 兩例分別鎖住
有空位與無空位兩側。

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
| 共用 pool 容量 | 將 `agent_has_free_dispatch_slot` 末行改回 `return used < agent_dispatch_capacity(...)`；`dispatch_pool_usage` / `account_pool_physical_capacity` / `account_pool_has_free_dispatch_slot` 可保留為無呼叫者 | 回到只看 logical agent 的容量判斷 |
| immutable closeout | 移除 `owner_preference_applies_to_task` 內的 `task_closeout_owner_is_frozen` 短路 | 偏好重新對 `review_approved` 生效 |
| CI 競態 | 移除 `dispatch_engine` 的 fresh CI 區塊 | 回到單次 cached CI 讀取 |

全域關閉：把 `.orchestrator/config.json` 的
`ready_dispatcher.owner_provider_preference.enabled` 設為 `false`，或清空
`preferred_providers`，整條偏好路徑（含本次新增的 pool 容量探測）即不會被觸發，
selector 回到偏好導入前的行為。缺口三的 CI 守衛不受該開關影響——它是一道只會讓
recovery 更保守的檢查，停用它只會讓 recovery 更容易誤動。

完整回退則 revert 本任務的單一 task PR 即可；本次未產生 migration、未寫入任何
runtime state、未變更任何持久化結構。
