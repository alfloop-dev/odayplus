---
evidence_id: ODP-REMEDIATION-RUNTIME-ROLLOUT-001
title: "將已審核的去重與blocker保護修正套用到live supervisor"
date: 2026-10-03
status: IMPLEMENTED
owner: Antigravity
reviewer: Codex2
repository: alfloop-dev/odayplus
task: ODP-REMEDIATION-RUNTIME-ROLLOUT-001
base_ref: 66665090a9a329877e1a88d80883a8eca1818f09
---

# 將已審核的去重與blocker保護修正套用到live supervisor

## 1. 概述與前置依賴確認 (Task Overview & Dependency Prerequisite Verification)

本文件記錄 `ODP-REMEDIATION-RUNTIME-ROLLOUT-001` 控制面修復套用到 live Supervisor runtime 之受控採用程序、真實探針 (probe) 執行收據與驗證結果。

### 1.1 前置依賴完成查證
本輪兩項控制面修正均已完成獨立審核、測試通過並合併至 `origin/dev`：

1. **`ODP-ORCH-VERIFICATION-COMMAND-IDENTITY-001`**:
   - 修正驗證命令誤判重複，避免整組測試重跑。
   - PR [#1398](https://github.com/alfloop-dev/odayplus/pull/1398) 已於 2026-10-03T00:17:31Z 合併至 dev，Commit SHA 為 `9a5ef53de6955aef2b2c772b8e5f83c78222c675`，狀態為 `done`。
2. **`ODP-ORCH-BLOCKER-RECOVERY-AUTHORITY-001`**:
   - 修正一般 note 覆蓋造成真實 blocker 被自動清除的准入缺陷。
   - PR [#1402](https://github.com/alfloop-dev/odayplus/pull/1402) 經 Codex2 獨立 review 核准，於 2026-10-03T14:41:35Z 合併至 dev，Merge Commit SHA 為 `66665090a9a329877e1a88d80883a8eca1818f09`，狀態為 `done`。

---

## 2. 授權邊界與不變式保留 (Authority Boundaries & Invariant Protections)

- **僅限本地 Supervisor/Runtime 受控採用**：本次操作僅執行本機 Supervisor runtime 之 worktree 部署、symlink 切換與 watchdog 重啟。
- **嚴格禁止非授權操作**：未授權亦未執行任何 GCP 產品部署、資料庫 migration、模型 promotion、第三方來源啟用、IAM 變更、signed lease 簽發或人工作業繞過。
- **保留真實 non_dispatchable holds**：完全保留各項未解除之外部資料／人工作業 hold（例如 `XR-EXT-OSS-FINAL-AUDIT-001` 之資料閘門 hold），不得因 runtime 升級而擅自清除。
- **保留現行 Provider／Model／Account-Pool 配置**：維持目前 live config（SHA256: `11d346bd1579209220a18433b30af4d5e17983955d2a966f1c9d09826ed2eec5`）及 Pi provider 配置原樣不變。
- **不遺棄 canonical 狀態**：canonical status root 內所有未提交狀態、queue、task worktree 與 review 狀態完全保留，未覆寫過期 snapshot。

---

## 3. Rollout 預檢與治理檢查 (Preflight & Governance Pre-Checks)

### 3.1 執行前環境狀態 (Pre-Rollout State)
- **Canonical Status Root**: `/home/lupin/odayplus`
- **Live Config Path**: `/home/lupin/odayplus/.orchestrator/config.json`
- **Live Config SHA256**: `11d346bd1579209220a18433b30af4d5e17983955d2a966f1c9d09826ed2eec5`
- **Stable Runtime Symlink**: `/home/lupin/oday-plus-supervisor-runtime-current`
- **前一版本 Runtime SHA**: `6140d0ef633cbf94522c171d9927103ab200257f`
- **前一版本 Supervisor PID**: `884078`
- **回滾目標 (Rollback Target)**: `/home/lupin/oday-plus-supervisor-runtime-6140d0ef633c`

### 3.2 治理靜態檢查 (Governance Wiring Checks - Step 90)
於部署前執行設定檔 contract 與 wiring 驗證：
```bash
# Step 90 (CWD: /tmp/pantheon-worker-worktrees/pantheon/odp-remediation-runtime-rollout-001)
python3 delivery_toolchain/governance/check_orchestrator_config.py --config /home/lupin/odayplus/.orchestrator/config.json && python3 delivery_toolchain/governance/check_config_wiring.py
```
- **Exit Code**: `0`
- **Duration**: `2.144192971s`
- **Output**:
  ```text
  Validated 3 config documents and their merged runtime views.
  All 190 config keys are read by production code.
  ```

---

## 4. Runtime 採用執行 (Runtime Rollout Execution)

遵循 [`docs/runbooks/supervisor-runtime-rollout.md`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-remediation-runtime-rollout-001/docs/runbooks/supervisor-runtime-rollout.md) 規範，使用既有唯一部署原語 `scripts/orchestrator/rollout_supervisor_runtime.py` 進行原子切換：

### 4.1 部署前 Dry-Run 預檢 (Dry-Run Preflight - Step 86)
```bash
# Step 86 (CWD: /tmp/pantheon-worker-worktrees/pantheon/odp-remediation-runtime-rollout-001)
python3 /home/lupin/oday-plus-supervisor-deploy-source-20260919/scripts/orchestrator/rollout_supervisor_runtime.py \
  --source-root /home/lupin/oday-plus-supervisor-deploy-source-20260919 \
  --runtime-link /home/lupin/oday-plus-supervisor-runtime-current \
  --runtime-parent /home/lupin \
  --status-root /home/lupin/odayplus \
  --watchdog-pid-file /home/lupin/odayplus/.orchestrator/supervisor.pid \
  --dry-run
```
- **Exit Code**: `0`
- **Duration**: `0.843644176s`
- **Output**:
  ```text
  target=/home/lupin/oday-plus-supervisor-runtime-66665090a9a3 sha=66665090a9a329877e1a88d80883a8eca1818f09 branch=runtime-live-66665090a9a3
  previous=/home/lupin/oday-plus-supervisor-runtime-6140d0ef633c
  status_launcher=/home/lupin/odayplus/scripts/ai-status.sh writer=/home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py
  ```

### 4.2 部署實際執行 (Rollout Execution - Step 94)
```bash
# Step 94 (CWD: /tmp/pantheon-worker-worktrees/pantheon/odp-remediation-runtime-rollout-001)
python3 /home/lupin/oday-plus-supervisor-deploy-source-20260919/scripts/orchestrator/rollout_supervisor_runtime.py \
  --source-root /home/lupin/oday-plus-supervisor-deploy-source-20260919 \
  --runtime-link /home/lupin/oday-plus-supervisor-runtime-current \
  --runtime-parent /home/lupin \
  --status-root /home/lupin/odayplus \
  --watchdog-pid-file /home/lupin/odayplus/.orchestrator/supervisor.pid
```
- **Exit Code**: `0`
- **Duration**: `3.811725635s`
- **Rollback Invoked**: `false`
- **Output**:
  ```text
  target=/home/lupin/oday-plus-supervisor-runtime-66665090a9a3 sha=66665090a9a329877e1a88d80883a8eca1818f09 branch=runtime-live-66665090a9a3
  previous=/home/lupin/oday-plus-supervisor-runtime-6140d0ef633c
  status_launcher=/home/lupin/odayplus/scripts/ai-status.sh writer=/home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py
  watchdog decision=restart_supervisor reason=missing_pid pid=None new_pid=1466298
  rollout complete: /home/lupin/oday-plus-supervisor-runtime-current -> /home/lupin/oday-plus-supervisor-runtime-66665090a9a3
  ```

---

## 5. 採用後健康讀回與診斷探針 (Post-Rollout Health Readback & Probes)

### 5.1 程序與軟連結讀回 (Process & Symlink Verification)
- **Stable Link 解析**: `/home/lupin/oday-plus-supervisor-runtime-current` -> `/home/lupin/oday-plus-supervisor-runtime-66665090a9a3`
- **Runtime Branch**: `runtime-live-66665090a9a3`
- **Runtime Commit SHA**: `66665090a9a329877e1a88d80883a8eca1818f09` (完全等於 `origin/dev`)
- **新 Supervisor PID**: `1466298`
- **Supervisor 程序 CWD**: `/home/lupin/oday-plus-supervisor-runtime-66665090a9a3`
- **Supervisor 命令行**: `python3 -u .orchestrator/supervisor.py --verbose`

### 5.2 狀態啟動器讀回 (Status Launcher Verification)
`/home/lupin/odayplus/scripts/ai-status.sh` 已原子更新，指向 runtime-current 之 `ai_status.py`：
```bash
#!/bin/bash
set -euo pipefail
status_root="$(cd "$(dirname "$0")/.." && pwd)"
export PANTHEON_STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$status_root}"
exec python3 /home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py "$@"
```
- **Launcher SHA256**: `5bc351efdc74813a0dc45d1e3bb2877a92094dee788afd05a4b295b07b32cbe8`
- 透過 `AI_NAME=Antigravity "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" show ...` 驗證通訊與 canonical writer 解析正常。

### 5.3 現場健康檢查探針 (Canonical Live Health Probe - Step 104)
針對 Canonical Status Root (`/home/lupin/odayplus`) 執行完整 Live 狀態健康檢查：
```bash
# Step 104 (CWD: /tmp/pantheon-worker-worktrees/pantheon/odp-remediation-runtime-rollout-001)
python3 /home/lupin/oday-plus-supervisor-runtime-current/scripts/supervisor_runtime_health.py \
  --repo /home/lupin/odayplus \
  --config-path /home/lupin/odayplus/.orchestrator/config.json \
  --json
```
- **Timestamp**: `2026-10-03T14:50:02.776870Z`
- **Exit Code**: `0`
- **Duration**: `0.222389977s`
- **Healthy**: `true`
- **Git Freshness**: `null` (此指令未帶 `--check-git-freshness`)
- **檢查項目結果**:
  - `supervisor_process_alive`: `ok: true` (PID 1466298, lock_held: true)
  - `supervisor_heartbeat_present`: `ok: true` (last_heartbeat_at: 2026-10-03T14:49:56Z)
  - `supervisor_heartbeat_fresh`: `ok: true` (age: 6.78s, max: 900.0s)
  - `supervisor_not_degraded`: `ok: true` (lifecycle: running)
  - `dashboard_bundle_present`: `ok: true` (path: `/home/lupin/odayplus/dashboard-bundle.json`)
  - `dashboard_bundle_valid`: `ok: true`
  - `dashboard_bundle_fresh`: `ok: true` (age: 6.78s)
  - `dashboard_supervisor_pid_matches`: `ok: true` (PID 1466298)

### 5.4 Runtime 工作目錄 Git 鮮度探針 (Runtime Git Freshness Probe - Step 106)
針對 Runtime 目錄 (`/home/lupin/oday-plus-supervisor-runtime-current`) 執行 Git 鮮度檢查：
```bash
# Step 106 (CWD: /tmp/pantheon-worker-worktrees/pantheon/odp-remediation-runtime-rollout-001)
python3 /home/lupin/oday-plus-supervisor-runtime-current/scripts/supervisor_runtime_health.py \
  --repo /home/lupin/oday-plus-supervisor-runtime-current \
  --config-path /home/lupin/odayplus/.orchestrator/config.json \
  --check-git-freshness \
  --git-upstream origin/dev \
  --json
```
- **Timestamp**: `2026-10-03T14:50:05.804979Z`
- **Exit Code**: `1`
- **Duration**: `0.120189254s`
- **Healthy**: `false`
- **Git Freshness 檢查結果 (通過)**:
  - `runtime_git_not_detached`: `ok: true` (head_ref: `refs/heads/runtime-live-66665090a9a3`)
  - `runtime_git_not_behind`: `ok: true` (commits_behind: `0`, upstream: `origin/dev`)
- **非零回傳值 (Exit 1) 與 Healthy=false 原因分析**:
  - `supervisor_runtime_health.py` 預設將相對狀態路徑（`.orchestrator/state.json` 與 `dashboard-bundle.json`）錨定於 `--repo` 參數目錄。
  - 當 `--repo` 指向 Runtime 工作樹 (`/home/lupin/oday-plus-supervisor-runtime-current` -> `/home/lupin/oday-plus-supervisor-runtime-66665090a9a3`) 時，檢查腳本在該 code checkout 內尋找狀態檔案，導致 process/heartbeat/dashboard 檢查判定為 false。
  - 此非零結果為診斷指令在不同 repo 目錄下的路徑解析特性，**並非** live supervisor 不健康。Step 104 對 Canonical Status Root 的檢查已證明 Supervisor 服務健康運行中。

### 5.5 衍生綜合健康摘要 (Derived Combined Health Summary)
> [!NOTE]
> 本節為 **衍生合成視圖 (Derived Synthesis)**，由 Step 104（現場程序/狀態健康）與 Step 106（Runtime Git 鮮度）兩項真實探針合成：

| 檢查項目 | 結果 | 來源依據 | 實測值 / 狀態 |
|---|---|---|---|
| `supervisor_process_alive` | OK | Step 104 (exit 0) | PID 1466298, lock held |
| `supervisor_heartbeat_present` | OK | Step 104 (exit 0) | `2026-10-03T14:49:56Z` |
| `supervisor_heartbeat_fresh` | OK | Step 104 (exit 0) | age: 6.78s < 900.0s |
| `supervisor_not_degraded` | OK | Step 104 (exit 0) | lifecycle: `running` |
| `dashboard_bundle_present` | OK | Step 104 (exit 0) | `/home/lupin/odayplus/dashboard-bundle.json` |
| `dashboard_bundle_valid` | OK | Step 104 (exit 0) | `valid: true` |
| `dashboard_bundle_fresh` | OK | Step 104 (exit 0) | age: 6.78s |
| `dashboard_supervisor_pid_matches`| OK | Step 104 (exit 0) | PID 1466298 |
| `runtime_git_not_detached` | OK | Step 106 (exit 1 probe) | `refs/heads/runtime-live-66665090a9a3` |
| `runtime_git_not_behind` | OK | Step 106 (exit 1 probe) | 0 commits behind `origin/dev` |

---

## 6. Focused 回歸測試驗收 (Focused Regression Verification - Step 108)

為證明本輪合併之控制面修正已於 source 完整生效，於 task checkout 執行兩項 PR 關聯之 focused selection 測試：
```bash
# Step 108 (CWD: /tmp/pantheon-worker-worktrees/pantheon/odp-remediation-runtime-rollout-001)
uv run --frozen --python 3.12 pytest -q \
  .orchestrator/test_verification_evidence.py \
  .orchestrator/test_blocker_recovery_authority.py \
  .orchestrator/test_task_dependency_gate.py \
  .orchestrator/test_supervisor.py::AutomaticRecoveryTests
```
- **Executed Source SHA**: `66665090a9a329877e1a88d80883a8eca1818f09`
- **CWD**: `/tmp/pantheon-worker-worktrees/pantheon/odp-remediation-runtime-rollout-001`
- **Exit Code**: `0`
- **Duration**: `12.168618779s`
- **Result**: `100% passed`
- **涵蓋範圍**:
  - `test_verification_evidence.py`: 驗證指令指紋精確性與去重避免無謂重跑 (PR #1398)。
  - `test_blocker_recovery_authority.py`: 驗證 canonical open blocker 於普通 note 下不會被誤清除 (PR #1402)。
  - `test_task_dependency_gate.py`: 驗證依賴閘門判斷正確性。
  - `test_supervisor.py::AutomaticRecoveryTests`: 驗證合法 routing 故障恢復依然保留。

---

## 7. 驗證收據摘要 (Verification Receipt Summary)

| 步驟 / 檔案 | 指令 | Exit Code | 耗時 | 結果 | 說明 |
|---|---|---|---|---|---|
| Step 86 | `rollout_supervisor_runtime.py ... --dry-run` | 0 | 0.844s | 通過 | 部署前 dry-run 預檢 target worktree 與 launcher |
| Step 90 | `check_orchestrator_config.py ... && check_config_wiring.py` | 0 | 2.144s | 通過 | 3 份 config 文件與 190 個 config keys 讀取驗證通過 |
| Step 94 | `rollout_supervisor_runtime.py ...` | 0 | 3.812s | 通過 | 原子建立 `runtime-live-66665090a9a3`、切換 symlink 並由 watchdog 重啟 supervisor |
| Step 104 | `supervisor_runtime_health.py --repo /home/lupin/odayplus ...` | 0 | 0.222s | 通過 | Canonical live supervisor 健康讀回 (PID 1466298, heartbeat fresh) |
| Step 106 | `supervisor_runtime_health.py --repo /home/lupin/...-current --check-git-freshness ...` | 1 | 0.120s | 探針完成 | Git 鮮度檢查通過（not detached / 0 behind）；因相對狀態路徑解析至 code checkout 致整體 probe exit 1 |
| Step 108 | Focused `pytest` | 0 | 12.169s | 通過 | 覆蓋 PR #1398 與 PR #1402 之全部聚焦回歸測試 (SHA 66665090a9a3) |
| Verification | `git diff --check` | 0 | 0.015s | 通過 | 工作樹無格式或空格瑕疵 |

---

## 8. 結論 (Conclusion)

本輪修正（`ODP-ORCH-VERIFICATION-COMMAND-IDENTITY-001` 與 `ODP-ORCH-BLOCKER-RECOVERY-AUTHORITY-001`）已依受控程序完整採用至 Live Supervisor Runtime（commit SHA `66665090a9a329877e1a88d80883a8eca1818f09`）。新版 Supervisor（PID `1466298`）已成功啟動並回傳 fresh heartbeat，現場健康檢查全數通過，所有 non_dispatchable holds 與現場 canonical 狀態完整保全。本文件與關聯收據已完整修正探針與測試之命令、目錄、時間戳、執行期及真實 exit code 紀錄。
