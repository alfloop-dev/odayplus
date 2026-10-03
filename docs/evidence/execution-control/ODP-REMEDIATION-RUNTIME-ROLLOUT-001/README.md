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

本文件記錄 `ODP-REMEDIATION-RUNTIME-ROLLOUT-001` 控制面修復套用到 live Supervisor runtime 之受控採用程序與驗證收據。

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

## 3. Rollout 預檢與執行規劃 (Preflight & Execution Plan)

### 3.1 執行前環境狀態 (Pre-Rollout State)
- **Canonical Status Root**: `/home/lupin/odayplus`
- **Live Config Path**: `/home/lupin/odayplus/.orchestrator/config.json`
- **Live Config SHA256**: `11d346bd1579209220a18433b30af4d5e17983955d2a966f1c9d09826ed2eec5`
- **Stable Runtime Symlink**: `/home/lupin/oday-plus-supervisor-runtime-current`
- **前一版本 Runtime SHA**: `6140d0ef633cbf94522c171d9927103ab200257f`
- **前一版本 Supervisor PID**: `884078`
- **回滾目標 (Rollback Target)**: `/home/lupin/oday-plus-supervisor-runtime-6140d0ef633c`

### 3.2 治理靜態檢查 (Governance & Wiring Pre-Checks)
於部署前執行設定檔 contract 與 wiring 驗證：
```bash
python3 delivery_toolchain/governance/check_orchestrator_config.py --config /home/lupin/odayplus/.orchestrator/config.json
# 輸出: Validated 3 config documents and their merged runtime views. (exit 0)

python3 delivery_toolchain/governance/check_config_wiring.py
# 輸出: All 190 config keys are read by production code. (exit 0)
```

---

## 4. Runtime 採用執行 (Runtime Rollout Execution)

遵循 [`docs/runbooks/supervisor-runtime-rollout.md`](file:///tmp/pantheon-worker-worktrees/pantheon/odp-remediation-runtime-rollout-001/docs/runbooks/supervisor-runtime-rollout.md) 規範，使用既有唯一部署原語 `scripts/orchestrator/rollout_supervisor_runtime.py` 進行原子切換：

### 4.1 部署指令
```bash
python3 /home/lupin/oday-plus-supervisor-deploy-source-20260919/scripts/orchestrator/rollout_supervisor_runtime.py \
  --source-root /home/lupin/oday-plus-supervisor-deploy-source-20260919 \
  --runtime-link /home/lupin/oday-plus-supervisor-runtime-current \
  --runtime-parent /home/lupin \
  --status-root /home/lupin/odayplus \
  --watchdog-pid-file /home/lupin/odayplus/.orchestrator/supervisor.pid
```

### 4.2 部署結果
```text
target=/home/lupin/oday-plus-supervisor-runtime-66665090a9a3 sha=66665090a9a329877e1a88d80883a8eca1818f09 branch=runtime-live-66665090a9a3
previous=/home/lupin/oday-plus-supervisor-runtime-6140d0ef633c
status_launcher=/home/lupin/odayplus/scripts/ai-status.sh writer=/home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py
watchdog decision=restart_supervisor reason=missing_pid pid=None new_pid=1466298
rollout complete: /home/lupin/oday-plus-supervisor-runtime-current -> /home/lupin/oday-plus-supervisor-runtime-66665090a9a3
```
- **Exit Code**: `0`
- **Rollback Invoked**: `false`

---

## 5. 採用後健康讀回與驗證 (Post-Rollout Health Readback & Verification)

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

### 5.3 執行期健康檢查 (Runtime Health Probe)
執行 `supervisor_runtime_health.py` 讀回全部檢查項目為 `OK` / `healthy: true`：
- `supervisor_process_alive`: `ok: true`, PID `1466298`, lock held
- `supervisor_heartbeat_present`: `ok: true`, heartbeat timestamp `2026-10-03T14:49:56Z`
- `supervisor_heartbeat_fresh`: `ok: true` (age < 10s)
- `supervisor_not_degraded`: `ok: true`, lifecycle `running`
- `dashboard_bundle_present`: `ok: true`
- `dashboard_bundle_valid`: `ok: true`
- `dashboard_bundle_fresh`: `ok: true`
- `dashboard_supervisor_pid_matches`: `ok: true`
- `runtime_git_not_detached`: `ok: true`
- `runtime_git_not_behind`: `ok: true` (commits_behind: 0, upstream: origin/dev)

---

## 6. Focused 回歸測試驗收 (Focused Regression Verification)

為證明本輪合併之控制面修正已於 source 完整生效，執行兩項 PR 關聯之 focused selection 測試：
```bash
uv run --frozen --python 3.12 pytest -q \
  .orchestrator/test_verification_evidence.py \
  .orchestrator/test_blocker_recovery_authority.py \
  .orchestrator/test_task_dependency_gate.py \
  .orchestrator/test_supervisor.py::AutomaticRecoveryTests
```
- **Exit Code**: `0`
- **Result**: `100% passed`
- **涵蓋範圍**:
  - `test_verification_evidence.py`: 驗證指令指紋精確性與去重避免無謂重跑。
  - `test_blocker_recovery_authority.py`: 驗證 canonical open blocker 於普通 note 下不會被誤清除。
  - `test_task_dependency_gate.py`: 驗證依賴閘門判斷正確性。
  - `test_supervisor.py::AutomaticRecoveryTests`: 驗證合法 routing 故障恢復依然保留。

---

## 7. 驗證收據摘要 (Verification Receipt Summary)

| 指令 | Exit Code | 結果 | 說明 |
|---|---|---|---|
| `git diff --check` | 0 | 通過 | 工作樹無格式或空格瑕疵 |
| `check_orchestrator_config.py` | 0 | 通過 | 3 份 config 文件與 runtime 合併視圖驗證通過 |
| `check_config_wiring.py` | 0 | 通過 | 190 個 config keys 均有 production code 讀取 |
| `rollout_supervisor_runtime.py` | 0 | 通過 | 原子替換 runtime symlink 與 status launcher |
| `supervisor_runtime_health.py` | 0 | 通過 | healthy: true, PID 1466298, heartbeat fresh |
| Focused `pytest` | 0 | 通過 | 覆蓋 PR #1398 與 PR #1402 之全部聚焦回歸測試 |

---

## 8. 結論 (Conclusion)

本輪修正（`ODP-ORCH-VERIFICATION-COMMAND-IDENTITY-001` 與 `ODP-ORCH-BLOCKER-RECOVERY-AUTHORITY-001`）已依受控程序完整採用至 Live Supervisor Runtime（commit SHA `66665090a9a329877e1a88d80883a8eca1818f09`）。新版 Supervisor（PID `1466298`）已成功啟動並回傳 fresh heartbeat，健康檢查全數通過，所有 non_dispatchable holds 與現場 canonical 狀態完整保全。
