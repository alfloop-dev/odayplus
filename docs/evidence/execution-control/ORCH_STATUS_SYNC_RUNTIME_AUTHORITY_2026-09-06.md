# Evidence Note: ORCH-STATUS-SYNC-RUNTIME-AUTHORITY-001

## 任務摘要
- **Task ID**: `ORCH-STATUS-SYNC-RUNTIME-AUTHORITY-001`
- **Owner**: `Claude2`
- **Reviewer**: `Codex`
- **Base**: `da4b77d1`（`origin/dev`，第 2 輪 base advance merge `27302f32`；分支點 `acdd357e` 已含依賴任務 `ORCH-CAPACITY-ARCHIVE-DEDUP-001` PR #1225）
- **審查輪次**: 第 1 輪 head `da5bc3b3` 由 Codex 退回（P1：只釘一個 status root 變數）；本 note 已含第 2 輪修正
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

### 1.5 第 2 輪根因：只釘一個 status root 會讓 writer authority 分裂

第 1 輪 head `da5bc3b3` 只把 `PANTHEON_STATUS_ROOT` 釘到 config 選定的 root。Codex review 指出這不足，實測確認屬實。

launcher 與它 exec 的 runtime writer 讀的是**不同**的變數優先序：

```bash
# scripts/ai-status.sh —— 只認 PANTHEON_STATUS_ROOT，且不碰 ORCH_STATUS_ROOT
export PANTHEON_STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$status_root}"
exec python3 /home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py "$@"
```

```python
# scripts/ai_status.py::resolve_status_root —— ORCH_STATUS_ROOT 優先
raw = str(source.get("ORCH_STATUS_ROOT") or source.get("PANTHEON_STATUS_ROOT") or "").strip()
```

因此當 supervisor 行程繼承了另一塊看板的 `ORCH_STATUS_ROOT=B`、而 config 選定 root 為 A 時：

| 層 | 讀到的 root | 結果 |
| --- | --- | --- |
| `_resolve_status_launcher` | A | 執行 `A/scripts/ai-status.sh` |
| launcher shim | A（已被釘住） | 原樣傳遞環境給 writer |
| `resolve_status_root`（實際寫入者） | **B**（繼承未被覆寫） | 狀態、CAS revision、archive 全落在 B |

也就是說：launcher 由 A 選出、寫入卻發生在 B。CAS 與 writer authority 分裂到兩塊看板，且不會有任何錯誤——`returncode` 仍為 0，caller 會把它記成同步成功。

**修正**：`_status_launcher_env` 以 `STATUS_ROOT_ENV_VARS = ("ORCH_STATUS_ROOT", "PANTHEON_STATUS_ROOT")` 把**兩個**名字都設為同一個 config 選定 root，使呼叫鏈每一層都解析到同一塊看板。未更動 `resolve_status_root` 的優先序，也未更動 launcher。

---

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

3. **釘住 status root（兩個變數），保留原有 status-root 語義**
   原本直接執行 `.py` 時，root 由 script 自身路徑決定（即 config 的 status root）。要完整保留這個語義，`_status_launcher_env` 把 `ORCH_STATUS_ROOT` 與 `PANTHEON_STATUS_ROOT` **兩者**都設成 `config_path(config, "status_file").parent`。
   兩個都要釘，是因為 launcher 只認 `PANTHEON_STATUS_ROOT`、而它 exec 的 runtime writer 以 `ORCH_STATUS_ROOT` 為優先（見 §1.5）；只釘其一時，繼承環境會讓 launcher 與 writer 落在不同看板。此處只覆寫子行程環境（`os.environ.copy()` 後改寫），未更動 `resolve_status_root` 的優先序、launcher 本身，或 supervisor 行程自己的環境。

4. **其餘語義完全不動**
   `cwd`、`timeout_seconds`（`supervisor.external_command_timeout_seconds`，預設 30）、`returncode` 判定、`AI_NAME=<target display name>`、成功時的 `task_dispatch_synced` 事件均維持原樣。
   `sync_status_pipeline` 開頭的外部委派守衛、`write_status_snapshot_if_current` 的 CAS／stale revision 防護、`sync_dispatched_task_status` 的 task owner／helper lease／status eligibility 檢查、以及「dispatch 之後才 sync」的時序，皆未更動。

5. **未觸碰 canonical 的 dirty 檔案**
   `scripts/ai_status.py`、`scripts/ai-status.sh`、`.orchestrator/common.py` 與 config schema 皆未修改。修正方式是改變呼叫路由，而非放寬 schema 去遷就舊 writer。

---

## 3. 測試 (Tests)

集中改寫 `.orchestrator/test_supervisor.py::DispatchStatusSyncTests`（原 fixture 只建立 `.py` 且僅以 mock 斷言字串），改為 **真實執行** fixture launcher：

- fixture 建立可執行的 `scripts/ai-status.sh`，把 `argv` / `cwd` / `AI_NAME` / `PANTHEON_STATUS_ROOT` / `ORCH_STATUS_ROOT` 記錄到檔案。
- fixture 同時建立**故意失敗**的 `scripts/ai_status.py`：一旦被執行就寫下 marker 並 `sys.exit(17)`。每個測試都以 `assert_legacy_writer_untouched()` 斷言該 marker 不存在。
- 正向路徑不 mock `subprocess.run`，實際執行 launcher 並驗證記錄內容。

13 個測試，兩條 caller 對稱覆蓋：

| 測試 | 覆蓋 |
| --- | --- |
| `test_sync_dispatched_task_status_starts_owned_todo_task_via_launcher` | 真實執行 launcher；argv=`start`/task/message、`AI_NAME=Copilot`、兩個 root 變數、`cwd`；`task_dispatch_synced` |
| `test_sync_status_pipeline_runs_launcher_sync` | 真實執行 launcher；argv=`["sync"]`、兩個 root 變數、cwd |
| `test_sync_dispatched_task_status_pins_runtime_root_over_inherited_root` | **衝突繼承環境**（見 §3.1）：AI_NAME/cwd/argv/`task_dispatch_synced` 全部保留 |
| `test_sync_status_pipeline_pins_runtime_root_over_inherited_root` | **衝突繼承環境**：argv=`["sync"]`、cwd 保留、無失敗事件 |
| `..._skips_review_dispatch` | 非同步 reason 不執行任何 writer |
| `..._fails_closed_when_launcher_missing` × 2 | 缺 launcher → False + 對應失敗事件 |
| `..._fails_closed_when_launcher_not_executable` × 2 | 不可執行 → False + 對應失敗事件 |
| `..._fails_closed_when_launcher_exits_non_zero` × 2 | exit≠0 → False，stderr 與 `target_agent`/`dispatch_reason` 保留 |
| `..._fails_closed_on_launcher_timeout` × 2 | 真實 timeout（0.5s vs `sleep 30`）→ False + timeout 事件 |

失敗情境測試都在 launcher 缺席／不可執行時仍保留那支故意失敗的 `.py`，因此若任何回退路徑存在，測試會抓到。

### 3.1 衝突繼承環境測試：真正調用 runtime resolver

兩條 caller 各有一個測試重現 §1.5 的情境，且**不是**只斷言環境字串：

1. `inherit_conflicting_status_root()` 以 `mock.patch.dict(os.environ, ...)` 把 `ORCH_STATUS_ROOT` 與 `PANTHEON_STATUS_ROOT` 都指向一個誘餌 root B（另一個 tmpdir），config 選定的仍是 root A。
2. fixture launcher 的 tail 在**真正 exec runtime writer 的那個位置**改為呼叫已出貨的 resolver，並把 marker 寫到它解析出來的 root：

```python
sys.path.insert(0, <repo>/scripts)
import ai_status
root = ai_status.resolve_status_root(os.environ)
(root / "runtime-writer-landed.txt").write_text("runtime writer landed here")
```

3. `assert_runtime_write_landed_on(A, B)` 斷言四件事：兩個 root 變數都等於 A；把 launcher 實收環境餵給 `ai_status.resolve_status_root()` 得到 A；marker 落在 A；**root B 目錄為空**（`sorted(entry.name for entry in decoy.iterdir()) == []`）。

因此「root 沒釘住」會表現為 marker 出現在錯的看板目錄下，而不是一句沒人查證的環境字串斷言。

### 3.2 變異驗證（測試確實會抓到這個缺陷）

**第 1 輪缺陷（直接執行舊 `.py`）**：把 `.orchestrator/status_transition.py` 還原成路由修正前版本後重跑：

```
11 tests → 10 FAILED, 1 passed   (PYTEST_EXIT=1)
AssertionError: True is not false : supervisor executed the in-checkout scripts/ai_status.py instead of the launcher
```

唯一通過的是 `..._skips_review_dispatch`（守衛測試，本就不受此修正影響）。

**第 2 輪缺陷（只釘 `PANTHEON_STATUS_ROOT`）**：只把 `_status_launcher_env` 的迴圈換回單一 `env["PANTHEON_STATUS_ROOT"] = status_root`，其餘不動後重跑：

```
uv run --frozen python -m pytest .orchestrator/test_supervisor.py -k DispatchStatusSync -p no:randomly
4 failed, 9 passed, 601 deselected in 6.81s   (PYTEST_EXIT=1)

FAILED ...::test_sync_dispatched_task_status_pins_runtime_root_over_inherited_root
FAILED ...::test_sync_dispatched_task_status_starts_owned_todo_task_via_launcher
FAILED ...::test_sync_status_pipeline_pins_runtime_root_over_inherited_root
FAILED ...::test_sync_status_pipeline_runs_launcher_sync

AssertionError: ... : ORCH_STATUS_ROOT was not pinned to the configured status root
AssertionError: '/tmp/pantheon-supervisor-tests-olvgkmdw' != '/tmp/tmp86u1plhs'
```

還原修正後同一組為 `13 passed`（`PYTEST_EXIT=0`）。

---

### 3.3 焦點回歸 (status-sync / dispatch-status / CAS)

環境需釘 Python 3.12：cp314 沒有 `pgserver` wheel，`uv run --frozen` 會在建立環境時就失敗。

```
uv run --frozen --python 3.12 python -m pytest \
  .orchestrator/test_supervisor.py \
  .orchestrator/test_dispatch_policy.py \
  scripts/orchestrator/test_rollout_supervisor_runtime.py -p no:randomly

4 failed, 734 passed, 6 skipped, 2 warnings, 230 subtests passed in 134.20s (0:02:14)
PYTEST_EXIT=1
```

（第 1 輪同一組為 `4 failed, 732 passed`；+2 即本輪新增的兩個衝突繼承環境測試。）

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

**Baseline 對照**：把兩個改動檔案 `git checkout origin/dev --` 還原成 base advance 後的未修改版本（`da4b77d1`），只跑同一組：

```
uv run --frozen --python 3.12 python -m pytest \
  .orchestrator/test_supervisor.py -k ReviewHeadFreeze -p no:randomly

4 failed, 29 passed, 570 deselected, 5 subtests passed in 21.57s
PYTEST_EXIT=1
```

失敗集合逐項相同，證實為既有環境相關失敗，非本次改動造成。回歸與 baseline 跑完後 `git status --short` 僅含本任務檔案，追蹤中的 `ai-status.json` 未被測試覆寫。

---

## 4. 未做事項 (Out of Scope)

- **未修改 canonical 目前 dirty 的 `scripts/ai_status.py` / `scripts/ai-status.sh` / `.orchestrator/common.py` 或 config schema**，未以放寬 schema 的方式繞開問題。
- **未觸碰 auth / quota / role / admission 門檻**，未新增或修改任何 launcher、writer、同步機制或 fallback。
- **未修改 private live config，未直接 restart 或 deploy runtime**。修正需待相關 code 合併後，沿既有單一 rollout（`scripts/orchestrator/rollout_supervisor_runtime.py`）集中啟用；在 rollout 之前，live supervisor 仍會沿用舊行為。
- **`github_bus.py:1772` 另有一處 `["python3", "scripts/ai_status.py", ...]`**，同樣繞過 launcher。本任務驗收範圍明確限定為 `status_transition` 的兩條同步 caller，故未更動，僅在此記錄供後續評估。
- **未更動 `resolve_status_root` 的 `ORCH_STATUS_ROOT` 優先序**，也未更動 `scripts/ai-status.sh` 只認 `PANTHEON_STATUS_ROOT` 的行為。修正只發生在 supervisor 傳給子行程的環境上，其他讀取這兩個變數的路徑（worker、CI、測試 harness）語義不變。
- **未釘 `ORCH_CONFIG_PATH` / `PANTHEON_CONFIG_PATH`**：canonical launcher 自己會匯出這兩個值，本任務驗收範圍限定在 status root 的 writer authority。
- 依驗收要求只跑相關 status-sync / dispatch-status / CAS 焦點回歸，未重跑產品 full suite。
