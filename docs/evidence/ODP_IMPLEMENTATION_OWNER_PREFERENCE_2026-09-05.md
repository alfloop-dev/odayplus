---
evidence_id: ODP-IMPLEMENTATION-OWNER-PREFERENCE-001
title: "在既有 owner 選擇器落實 agy／Claude 優先並保留安全平行容量"
date: 2026-09-05
status: IMPLEMENTED
owner: Claude
reviewer: Codex
repository: alfloop-dev/odayplus
task: ODP-IMPLEMENTATION-OWNER-PREFERENCE-001
base_ref: f28f4241
source_docs_ref: 6c4a8be856a5
---

# 在既有 owner 選擇器落實 agy／Claude 優先並保留安全平行容量

## 1. 根因：負載平衡回答不了「誰該實作」

owner 的選擇目前由兩條既有路徑決定，兩條都無法表達「實作交給 agy／Claude、整合部署交給 GPT」這個意圖。

### 1.1 `worker_failure_policy.first_viable_agent`

排序鍵原本是 `(open_task_count, caller_order)`：

```python
counts = agent_open_task_counts(config, status, role=role)
return min(viable, key=lambda name: (counts.get(normalize_agent_id(name), 0), viable.index(name)))
```

`agent_open_task_counts` 統計的是看板上 `owner` 欄位仍為 open 的 task 數量，**不是實際可派工容量**。live 配置中 `antigravity_main` 有 3 個 worker slot、`claude_main` 有 2 個、`codex_bjoe` 有 3 個；一條持有 9 個 open task 但 slot 全空的 agy lane，排序時永遠輸給只持有 1 個 open task 的 Codex lane。

同時「只重排 `worker_reassignment.owner_fallbacks`」也修不掉：`viable.index(name)` 是排序鍵的**第二**個元素，只有在 open task 數相同時才會被讀到。負載一有差距，清單順序完全不影響結果。

### 1.2 `dispatch_engine.reassign_unavailable_reviewers` 的 paused-owner 路徑

該函式在 `claimed_role == "owner"` 時不呼叫 `first_viable_agent`，而是走 `dispatch_loop_agent_ids(config)` 的順序取第一個通過過濾的候選人。那個順序來自 `supervisor.agent_dispatch_preference_rank`，它讀的是 provider **名稱字串**：

```python
if provider.startswith("antigravity"): return 0
if provider.startswith("claude"):      return 1
if provider.startswith("codex"):       return 2
return 99
```

因此這條路徑握有第二套、不可設定、且靠拼字判斷 LLM 的 owner 偏好。任何以 `delivery_mode: antigravity` 交付但 provider key 不叫 `antigravity*` 的 lane 會被排到最後（rank 99，排在 Codex 之後）。此外候選人是 **logical agent**，而 `reserved_agents` 記的是 **dispatch slot id**，所以一條 slot 已全滿的 logical agent 不會被 `reserved_agents` 濾掉，可能被指派到沒有空 slot 的 lane。

## 2. 修法：一條 policy，兩個既有呼叫點

### 2.1 新增可設定的 owner provider preference group

`.orchestrator/worker_failure_policy.py` 新增下列 policy 函式（放在既有 policy 層，未新建模組、未新建 scheduler）：

| 函式 | 職責 |
|---|---|
| `owner_provider_preference_settings(config)` | 讀 `ready_dispatcher.owner_provider_preference` 並套預設值 |
| `preferred_owner_provider_ids(config)` | 正規化後的偏好 provider 集合；未配置時為空集合 |
| `agent_provider_identity_ids(config, agent)` | 用 provider/adapter 解析 agent 背後的模型身分 |
| `agent_is_preferred_owner_provider(config, agent)` | 身分集合與偏好集合是否相交 |
| `owner_preference_applies_to_task(config, task, role)` | 是否允許偏好介入這次選擇 |
| `dispatch_slot_loads(config, state)` | 呼叫既有 `agent_dispatch_loads`，取得 active + 未投遞 queue event 的負載 |
| `agent_has_free_dispatch_slot(config, agent, loads)` | 既有 `agent_dispatch_capacity` 減去上述負載 |
| `owner_preference_ranks(config, names, ...)` | 唯一排名函式：0 = 偏好組且現在有空 slot，1 = 其他 |

### 2.2 兩個呼叫點共用 `owner_preference_ranks`

`first_viable_agent` 的排序鍵改為 `(rank, open_task_count, caller_order)`——rank 只是**前置**鍵，其後兩項完全沿用原邏輯，所以偏好組內部、以及非偏好組內部的負載平衡與 fallback 順序都沒有變。

`reassign_unavailable_reviewers` 的 owner 分支在既有 for 迴圈前，用同一個 `owner_preference_ranks` 對 `candidate_agent_ids` 做 **stable sort**；同 rank 內維持 `dispatch_loop_agent_ids` 的原順序。reviewer 分支完全未動。

### 2.3 不靠名稱猜 LLM（含 antigravity2 alias）

`agent_provider_identity_ids` 收集的身分來自設定，而非拼字：

- agent 的 `provider` key 與 `provider_config_entry` 解析出的 canonical key
- agent 的 `adapter`
- provider entry 的 `delivery_mode`、`adapter`／`type`

以 shipped `config.example.json` 實測：

| Agent | 解析出的 provider 身分 | 是否屬偏好組 |
|---|---|---|
| Antigravity | `{antigravity}` | 是 |
| Antigravity2 | `{antigravity, antigravity2}` | 是（alias 經 `delivery_mode` 解析） |
| Claude | `{claude, claude_cli}` | 是 |
| Codex | `{codex}` | 否 |
| Codex2 | `{codex}` | 否 |

### 2.4 邊界：偏好只作用在 owner 實作 lane

`owner_preference_applies_to_task` 在下列任一情況直接回 False，選擇器行為與修改前逐字相同：

- `role != "owner"`（reviewer 獨立性與 reviewer failover 不受影響）
- `preferred_providers` 未配置或 `enabled: false`
- 沒有 task 物件可讀（無法確認 `task_class`，不臆測）
- `task_is_human_gate(task)` 或 `task.non_dispatchable`
- `task_class` 不在 `implementation` / `remediation` / `documentation`（`runtime_release`、`rollout`、`sidecar`、`human_gate` 都在外）

### 2.5 容量：用實際 slot，不用 open task count

偏好只有在該 lane **現在真的能起 worker** 時才生效。判斷完全複用 ready dispatcher 自己的帳：

```
free = agent_dispatch_capacity(config, agent)          # logical_worker_slot_ids，沒有 slot 則為 1
     - len(agent_dispatch_loads(...)[display_name])    # active worker + 未投遞的 queue event
```

quota group、dispatch pause、account pool、auth／能力、互斥、不同 account_pool、已失敗 owner 這些排除條件**都在偏好之前**由既有的 `agent_auto_dispatch_block_reason` / `exclude` / `exclude_pools` / `agent_can_take_task` 完成，本次沒有重算第二套資格判斷。

若 `state` 為 None，或 event queue path 缺失／不可讀，`dispatch_slot_loads` 回傳 None，所有候選人一律 rank 1——**量不到的容量不算容量**，退回原行為而非假設 lane 是空的。

## 3. 配置

`config.schema.json` 於 `ready_dispatcher` 下新增 `owner_provider_preference`（`additionalProperties: false`），`config.example.json` 同步寫入意圖：

```json
"owner_provider_preference": {
  "enabled": true,
  "preferred_providers": ["antigravity", "claude"],
  "task_classes": ["implementation", "remediation", "documentation"]
}
```

未配置時 `preferred_providers` 預設為空清單，整條偏好路徑（含容量探測）不會被觸發。

## 4. 驗證

所有指令在 `task/ODP-IMPLEMENTATION-OWNER-PREFERENCE-001`（base `f28f4241`）上執行，Python 釘 3.12（cp314 缺 `pgserver` wheel）。

```bash
uv run --frozen --python 3.12 ruff check .orchestrator delivery_toolchain scripts
uv run --frozen --python 3.12 python delivery_toolchain/governance/check_orchestrator_config.py
uv run --frozen --python 3.12 python delivery_toolchain/governance/check_config_wiring.py
uv run --frozen --python 3.12 python delivery_toolchain/governance/check_code_boundaries.py
uv run --frozen --python 3.12 pytest -m "not requires_live_env" -q .orchestrator delivery_toolchain
```

| 檢查 | 結果 |
|---|---|
| `ruff check` | All checks passed! |
| `check_orchestrator_config.py` | Validated 2 config documents and their merged runtime views. |
| `check_config_wiring.py` | All 186 config keys are read by production code. |
| `check_code_boundaries.py` | 通過（新增 test 檔後已 `--write-inventory` 重產 inventory，無重複列） |
| `pytest .orchestrator delivery_toolchain` | 見 §4.2 |

### 4.1 新增測試與其對應的失效情境

`.orchestrator/test_worker_failure_policy.py::OwnerProviderPreferenceTests`（19 例）與
`.orchestrator/test_dispatch_engine.py::PausedOwnerFailoverPreferenceTests`（5 例）。

正例（在未修正的選擇器上會紅）：

- `test_busier_preferred_owner_with_a_free_slot_beats_idle_codex`
- `test_partially_loaded_preferred_lane_is_still_preferred`
- `test_provider_alias_is_resolved_through_config_not_agent_names`
- `test_paused_owner_fails_over_to_the_preferred_provider`

以暫時把 rank 全部固定為 1（等同移除偏好）重跑，確認上述前三例轉紅、其餘保持綠；第四例由
`test_candidate_order_without_the_preference_puts_codex_first` 對照，未配置偏好時選出的是 `Codex2`。

負例／不變式（兩種實作下都必須綠）：

- reviewer role、reviewer failover 分支順序不變
- `runtime_release` / `rollout` / `sidecar` / 無 `task_class` / `human_gate` / `non_dispatchable` 不受影響
- 偏好組 slot 全滿 → fallback 回 Codex
- 偏好未配置、`enabled: false`、`state` 缺失、event queue 不可讀 → 原行為
- paused 的偏好 agent 先被既有 block reason 排除
- `exclude` 與 `exclude_pools` 仍優先於偏好
- 單一候選人與 `balance_load=False` 路徑不讀看板、不探容量
- 偏好組內部仍照 open task count 做負載平衡

### 4.2 完整 tooling suite

收集 1781 例，17 例 failed，其餘通過。這 17 例全部與本次修改無關，且在 base `f28f4241`（未含本任務任何 diff）上以同一個直譯器重跑，**失敗清單逐項相同、差集為 0**：

```bash
git worktree add --detach /tmp/odp-owner-pref-baseline origin/dev
cd /tmp/odp-owner-pref-baseline && <本 worktree 的 .venv>/bin/python -m pytest -m "not requires_live_env" -q \
  .orchestrator/test_worker_hard_inactivity.py \
  .orchestrator/test_worker_settlement_paths.py \
  .orchestrator/test_source_document_router.py
```

| 檔案 | 例數 | 失敗原因 | 與本次 diff 的關係 |
|---|---|---|---|
| `test_worker_hard_inactivity.py` | 5 | `supervisor.load_config(".orchestrator/config.json")` → `ConfigError: Orchestrator config does not exist` | 無。`.orchestrator/config.json` 由 `.gitignore:68` 排除、不在任何 commit 內，只存在於主 checkout；所有隔離 worktree 都缺這個檔 |
| `test_worker_settlement_paths.py` | 11 | 同上 | 同上 |
| `test_source_document_router.py` | 1 | `ValueError: Archived-task ambiguity for task DPF-GOV-001` — 需要 live 多repo local-path 環境 | 無 |

與本任務直接相關的檔案全綠：

```bash
uv run --frozen --python 3.12 pytest -q \
  .orchestrator/test_worker_failure_policy.py \
  .orchestrator/test_dispatch_engine.py \
  .orchestrator/test_dispatch_policy.py \
  .orchestrator/test_supervisor.py \
  .orchestrator/test_supervisor_scope_injection.py \
  .orchestrator/test_fill_idle_slots.py
```

`test_supervisor_scope_injection.py` 特別重要：`worker_failure_policy` 與 `dispatch_engine` 靠 `_sync_supervisor_scope()` 注入名稱且整檔 `# ruff: noqa: F821`，該測試靜態解析自由名稱並確認 supervisor 供得出來——本次新用到的 `agent_dispatch_loads`、`agent_dispatch_capacity`、`active_worker_statuses`、`display_name_for`、`task_is_human_gate` 都在其涵蓋範圍內。

## 5. 明確未做的事

1. **未改動 live config**：`.orchestrator/config.json` 完全未動，也未重啟 Supervisor。空配置不需要重啟；偏好要生效必須由 GPT 在獨立 review／合併後，沿用既有 immutable runtime rollout 載入設定，並核對 `loaded_config_digest` 與實際派工結果。
2. **未改初次派工**：本次只影響 owner 的**選擇／重派**。task 建立時已指定的 owner 不會被搶走，健康的既定 Codex owner 也不會因為偏好而被換掉。
3. **未動 helper / sidecar 機制**：helper claim、worker lease、`capacity_controller` 的 sidecar 全部沿用原機制；Codex 仍可執行 helper／sidecar 而不改變 canonical owner。
4. **未主張所有 executor 都是 agy**：偏好是一個可設定的 provider 群組，不是身分宣告。
5. **未改 cooldown 與 Human continuation**：沒有清除任何 cooldown，`review_churn` 的 8 次 Human continuation 邊界未動。
6. **未動 reviewer 獨立性、`review_approved` immutable finalize、`runtime_release` 與 Human/Ops gate**。
7. **未建立第二套 scheduler／容量計算**：容量沿用 `agent_dispatch_capacity` + `agent_dispatch_loads`，資格沿用 `agent_auto_dispatch_block_reason` 等既有述詞。
8. **未跑完整 product suite**：本任務範圍在 `.orchestrator` 與 `delivery_toolchain`，不需要 GCP、lease 或部署。

## 6. Rollback

風險與回復成本都很低，三個層級任選其一：

1. **不改碼、只改設定（最快）**：把 live config 的
   `ready_dispatcher.owner_provider_preference.enabled` 設為 `false`，或把
   `preferred_providers` 清成 `[]`。兩者都會讓 `owner_preference_ranks` 對所有候選人回傳 1，
   選擇器逐字回到 `(open_task_count, caller_order)`，且不再觸發任何容量探測。
   此路徑不需要改碼、不需要新 PR。
2. **縮小適用範圍**：從 `task_classes` 移除特定 class（例如只保留 `implementation`）。
3. **完整回滾**：`git revert` 本 task PR。被還原的檔案是
   `.orchestrator/worker_failure_policy.py`、`.orchestrator/dispatch_engine.py`、
   `.orchestrator/config.schema.json`、`.orchestrator/config.example.json`、
   三個測試檔與 `docs/audits/code-boundary-inventory.csv`（新增 test 檔導致的必然重產）。
   還原後若 live config 仍留有 `owner_provider_preference`，schema 會因
   `additionalProperties: false` 拒絕該設定，因此**還原碼之前必須先移除 live config 的該區塊**。
