# Evidence Note: ORCH-STATUS-SYNC-RUNTIME-AUTHORITY-001

## 任務摘要
- **Task ID**: `ORCH-STATUS-SYNC-RUNTIME-AUTHORITY-001`
- **Owner**: `Claude2`
- **Reviewer**: `Codex2`
- **Base**: `acdd357e`（`origin/dev`，已含依賴任務 `ORCH-CAPACITY-ARCHIVE-DEDUP-001` PR #1225）
- **目標**: 修正 `.orchestrator/status_transition.py` 中兩條既有 supervisor 同步 caller。它們繞過 canonical launcher `scripts/ai-status.sh`，直接以 `python3` 執行 status root 內的 `scripts/ai_status.py`，因而在舊 schema 下拒絕現行 config 欄位並使同步失敗。改為沿用同一支既有 launcher，保留 CAS 與 fail-closed 語義，不新增任何同步入口。

---

## 1. 根因分析 (Root Cause)

### 1.1 兩條 caller 直接執行 checkout 內的 `ai_status.py`
在 base `acdd357e` 中，`status_transition.py` 的兩條 caller 都自行組出 script 路徑並用 `sys.executable` 直接執行：

```python
script = sv.config_path(config, "status_file").parent / "scripts" / "ai_status.py"
...
subprocess.run([sys.executable, str(script), "sync"], ...)                       # sync_status_pipeline
subprocess.run([sys.executable, str(script), command_name, task_id, message], ...)  # sync_dispatched_task_status
```

`<status_root>/scripts/ai_status.py` 只是「該 checkout 當下剛好持有的版本」，並非已選定的 runtime code authority。它在 import 時會連帶載入同一 checkout 的 `.orchestrator/common.py`（舊 schema）。

### 1.2 canonical launcher 才是 runtime authority
`scripts/ai-status.sh` 是既有且由 rollout 維護的間接層，指向已選定的 runtime：

```bash
#!/bin/bash
set -euo pipefail
status_root="$(cd "$(dirname "$0")/.." && pwd)"
export PANTHEON_STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$status_root}"
exec python3 /home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py "$@"
```

`scripts/orchestrator/test_rollout_supervisor_runtime.py::test_rollout_installs_launcher_for_stable_runtime_link` 已明文保證：rollout 會安裝這支 launcher、令其指向 runtime link 的 `scripts/ai_status.py`、匯出 `PANTHEON_STATUS_ROOT`，並確保 `os.access(launcher, os.X_OK)`。

### 1.3 同一份不信任判斷早已存在於 in-process 路徑
`supervisor.py` 對「行程內」的 `ai_status` import 早就釘死到 runtime 路徑（`EXPECTED_AI_STATUS_PATH`，`supervisor.py:32-43`），且 `RuntimeConfigTests::test_dashboard_refresh_does_not_prepend_status_root_scripts` 明確禁止把 status-root 的 `scripts/` 推進 `sys.path`。
換言之，「status root 內的 `ai_status.py` 不可信」是既有結論；**只有這兩條 subprocess caller 是漏網的缺口**。

### 1.4 真實事件（live activity log）
舊 writer 載入舊 `common.py` 後，以舊 schema 驗證現行 `config.json` 而拋出 `ConfigError`：

```
common.ConfigError: Invalid orchestrator config /home/lupin/odayplus/.orchestrator/config.json:
  providers.codex.codex: Additional properties are not allowed ('model_reasoning_effort' was unexpected);
  ready_dispatcher: Additional properties are not allowed ('owner_provider_preference', 'role_provider_policy' were unexpected)
```

traceback 路徑為
`/home/lupin/odayplus/scripts/ai_status.py` → `load_state` → `normalize_state_agents` → `active_agent_name` → `canonical_agent_name` → `registered_agent_names` → `configured_agent_names` → `merged_orchestrator_config` → `/home/lupin/odayplus/.orchestrator/common.py: validate_config`。

觀察到的兩次發生：

| 時間 (UTC) | 事件 | Task | 對象 |
| --- | --- | --- | --- |
| 2026-09-06T06:24:22Z | `worker_started`（antigravity, `owned_ready_dispatch`） | `ORCH-CAPACITY-ARCHIVE-DEDUP-001` | `antigravity_slot_1` |
| 2026-09-06T06:24:23Z | `task_dispatch_sync_failed`（舊 schema 拒絕三個欄位） | `ORCH-CAPACITY-ARCHIVE-DEDUP-001` | `Antigravity4` |
| 2026-09-06T06:25:19Z | `start`（owner 透過既有 launcher，正常成功） | `ORCH-CAPACITY-ARCHIVE-DEDUP-001` | `Antigravity4` |
| 2026-09-06T07:36:55Z | `worker_started`（claude_cli, `owned_ready_dispatch`） | **本任務** | `claude_slot_*` |
| 2026-09-06T07:36:56Z | `task_dispatch_sync_failed`（同一個 traceback） | **本任務** | `Claude2` |

本任務自身的 dispatch 即命中同一缺陷：worker 已啟動，但看板上該任務仍停在 `status=todo` / `next="Assignment created"`。

**症狀範圍界定**：這是**看板誤報**，不是 worker 沒工作。worker 已正常啟動並執行；失敗的只是 dispatch 後的狀態同步，因此看板顯示「無人接手」。舊 writer 是在 `load_state()` 階段就整支拒絕，屬 fail-closed，未寫入任何部分狀態。

---

## 2. 修正設計 (Fix & Mechanism Reuse)

僅改 `.orchestrator/status_transition.py` 兩條既有 caller，改沿同一支既有 launcher。

1. **重用既有 launcher，不新增入口**
   新增模組常數 `STATUS_LAUNCHER_NAME = "ai-status.sh"` 與兩個私有 helper（`_resolve_status_launcher`、`_status_launcher_env`）。這兩個 helper 只是把兩條 caller 原本各自重複的路徑解析與環境準備集中起來，**沒有新增 launcher、writer、同步機制，也沒有保留任何回退到舊 `ai_status.py` 的 fallback**。

2. **fail-closed 四種情況皆保留既有診斷**
   `_resolve_status_launcher` 回傳 `(path, error | None)`，讓兩條 caller 各自保有原本的 activity-log event type 與欄位：
   - launcher 不存在 → `launcher not found at <path>`
   - launcher 不可執行（`os.access(..., os.X_OK)`）→ `launcher at <path> is not executable`
   - exit 非 0 → 沿用原本的 `result.stderr / result.stdout` 傳遞
   - timeout → 沿用原本的 `subprocess.TimeoutExpired` 分支與訊息
   四者皆 `return False`，絕不把「未同步」當成功。event type 維持 `task_reassignment_sync_failed` 與 `task_dispatch_sync_failed`（含 `task_id` / `target_agent` / `dispatch_reason`）不變。
   「不可執行」採 fail-closed 而非退回 `bash <script>`：rollout 已保證 `os.access(launcher, os.X_OK)`，退回執行只會遮蔽壞掉的部署。

3. **釘住 status root，保留原有 status-root 語義**
   launcher 會優先採用繼承來的 `PANTHEON_STATUS_ROOT`，才回退到自身位置。原本直接執行 `.py` 時，root 是由 script 自身路徑決定（即 config 的 status root）。為完整保留這個語義，`_status_launcher_env` 明確把 `PANTHEON_STATUS_ROOT` 設為 `config_path(config, "status_file").parent`，使 supervisor 即使在其他 root 下啟動，寫入仍指向 config 選定的看板。

4. **其餘語義完全不動**
   `cwd`、`timeout_seconds`（`supervisor.external_command_timeout_seconds`，預設 30）、`returncode` 判定、`AI_NAME=<target display name>`、成功時的 `task_dispatch_synced` 事件均維持原樣。
   `sync_status_pipeline` 開頭的外部委派守衛、`write_status_snapshot_if_current` 的 CAS／stale revision 防護、`sync_dispatched_task_status` 的 task owner／helper lease／status eligibility 檢查、以及「dispatch 之後才 sync」的時序，皆未更動。

5. **未觸碰 canonical 的 dirty 檔案**
   `scripts/ai_status.py`、`scripts/ai-status.sh`、`.orchestrator/common.py` 與 config schema 皆未修改。修正方式是改變呼叫路由，而非放寬 schema 去遷就舊 writer。

---

## 3. 測試 (Tests)

集中改寫 `.orchestrator/test_supervisor.py::DispatchStatusSyncTests`（原 fixture 只建立 `.py` 且僅以 mock 斷言字串），改為 **真實執行** fixture launcher：

- fixture 建立可執行的 `scripts/ai-status.sh`，把 `argv` / `cwd` / `AI_NAME` / `PANTHEON_STATUS_ROOT` 記錄到檔案。
- fixture 同時建立**故意失敗**的 `scripts/ai_status.py`：一旦被執行就寫下 marker 並 `sys.exit(17)`。每個測試都以 `assert_legacy_writer_untouched()` 斷言該 marker 不存在。
- 正向路徑不 mock `subprocess.run`，實際執行 launcher 並驗證記錄內容。

11 個測試，兩條 caller 對稱覆蓋：

| 測試 | 覆蓋 |
| --- | --- |
| `test_sync_dispatched_task_status_starts_owned_todo_task_via_launcher` | 真實執行 launcher；argv=`start`/task/message、`AI_NAME=Copilot`、`PANTHEON_STATUS_ROOT`、`cwd`；`task_dispatch_synced` |
| `test_sync_status_pipeline_runs_launcher_sync` | 真實執行 launcher；argv=`["sync"]`、root、cwd |
| `..._skips_review_dispatch` | 非同步 reason 不執行任何 writer |
| `..._fails_closed_when_launcher_missing` × 2 | 缺 launcher → False + 對應失敗事件 |
| `..._fails_closed_when_launcher_not_executable` × 2 | 不可執行 → False + 對應失敗事件 |
| `..._fails_closed_when_launcher_exits_non_zero` × 2 | exit≠0 → False，stderr 與 `target_agent`/`dispatch_reason` 保留 |
| `..._fails_closed_on_launcher_timeout` × 2 | 真實 timeout（0.5s vs `sleep 30`）→ False + timeout 事件 |

失敗情境測試都在 launcher 缺席／不可執行時仍保留那支故意失敗的 `.py`，因此若任何回退路徑存在，測試會抓到。

### 3.1 變異驗證（測試確實會抓到這個缺陷）
把 `.orchestrator/status_transition.py` 還原成修正前版本、只保留新測試後重跑：

```
11 tests → 10 FAILED, 1 passed   (PYTEST_EXIT=1)
AssertionError: True is not false : supervisor executed the in-checkout scripts/ai_status.py instead of the launcher
```

唯一通過的是 `..._skips_review_dispatch`（守衛測試，本就不受此修正影響）。修正後同一組為 `11 passed`。

---

### 3.2 焦點回歸 (status-sync / dispatch-status / CAS)

環境需釘 Python 3.12：cp314 沒有 `pgserver` wheel，`uv run --frozen` 會在建立環境時就失敗。

```
uv run --frozen --python 3.12 python -m pytest \
  .orchestrator/test_supervisor.py \
  .orchestrator/test_dispatch_policy.py \
  scripts/orchestrator/test_rollout_supervisor_runtime.py -p no:randomly

4 failed, 732 passed, 6 skipped, 2 warnings, 230 subtests passed in 117.44s
PYTEST_EXIT=1
```

```
uv run --frozen --python 3.12 ruff check \
  .orchestrator/status_transition.py .orchestrator/test_supervisor.py
All checks passed!   (RUFF_EXIT=0)
```

**4 個失敗全部為先前既有、與本修正無關**，皆屬 `ReviewHeadFreezeTests`：

```
ReviewHeadFreezeTests::test_approve_fails_closed_when_approved_head_cannot_be_resolved
ReviewHeadFreezeTests::test_approve_refuses_to_overwrite_uncleared_approved_head
ReviewHeadFreezeTests::test_approve_saves_approved_head_and_rejects_same_owner_reviewer
ReviewHeadFreezeTests::test_restore_approved_refuses_without_a_durable_approved_head
```

失敗原因是 live role/provider 審查政策，與 status sync 無關：

```
SystemExit: Cannot approve task FREEZE-TEST-021A: reviewer Claude 不符合 role/provider 審查政策
（Claude provider claude, claude_cli is not permitted for role reviewer on unclassified work (allowed: codex)）
```

**Baseline 對照**：把兩個改動檔案 `git checkout --` 還原成未修改的 `acdd357e`，只跑同一組：

```
4 failed, 29 passed, 570 deselected, 5 subtests passed in 19.06s
PYTEST_EXIT=1
```

失敗集合逐項相同，證實為既有環境相關失敗，非本次改動造成。回歸跑完後 `git status --short` 僅含本任務三個檔案，追蹤中的 `ai-status.json` 未被測試覆寫。

---

## 4. 未做事項 (Out of Scope)

- **未修改 canonical 目前 dirty 的 `scripts/ai_status.py` / `scripts/ai-status.sh` / `.orchestrator/common.py` 或 config schema**，未以放寬 schema 的方式繞開問題。
- **未觸碰 auth / quota / role / admission 門檻**，未新增或修改任何 launcher、writer、同步機制或 fallback。
- **未修改 private live config，未直接 restart 或 deploy runtime**。修正需待相關 code 合併後，沿既有單一 rollout（`scripts/orchestrator/rollout_supervisor_runtime.py`）集中啟用；在 rollout 之前，live supervisor 仍會沿用舊行為。
- **`github_bus.py:1772` 另有一處 `["python3", "scripts/ai_status.py", ...]`**，同樣繞過 launcher。本任務驗收範圍明確限定為 `status_transition` 的兩條同步 caller，故未更動，僅在此記錄供後續評估。
- 依驗收要求只跑相關 status-sync / dispatch-status / CAS 焦點回歸，未重跑產品 full suite。
