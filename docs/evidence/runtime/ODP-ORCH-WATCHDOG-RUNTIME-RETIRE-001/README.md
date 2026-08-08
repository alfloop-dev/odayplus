# Watchdog Runtime Retirement

- Task: `ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001`
- Date: 2026-08-04
- Owner: Platform/Ops

## 背景

`pantheon-supervisor-watchdog.service` 原本從**第二份** runtime checkout
`oday-plus-supervisor-runtime-945a8366` 執行，與主 supervisor 的 checkout 不同。
兩者各自漂移：主 runtime 落後 `origin/dev` 126 個 commit，watchdog 的落後 381。

2026-08-04 把 watchdog 改指 `oday-plus-supervisor-runtime-current`
（與主 supervisor 共用同一份），確認後 `observe_only` / `supervisor_healthy`，
主 supervisor `NRestarts=0` 未被誤殺。自此**一次 rollout 同時更新兩個服務**。

## 為什麼從 freshness 檢查移除

`945a8366` 已無服務對象，但仍留在 `pantheon-runtime-freshness.service` 的監控清單，
每小時回報一次持續擴大的 FAIL（381 → 420），而**沒有任何動作能清除它**。

一個永遠不會轉綠、也不對應任何待辦的告警，只會讓人學會忽略整組告警。
移除後 service 回到 `success 0`，FAIL 重新代表「有事要處理」。

## 未提交變更的處置

該 checkout 帶有 9 個未提交檔案（`supervisor.py` +169/-75、`ai_status.py` +68/-4、
`supervisor_runtime_health.py` +28/-6、`wakeup.txt` +3/-3 等）。
完整 diff 保存於本目錄 `uncommitted-945a8366.diff`（604 行）。

逐項比對 `origin/dev` 後：

| 改動 | 在 dev? |
|---|---|
| `_git_operation_in_progress`、`REBASE_HEAD` 邏輯 | ✅ 已由 PR #602 正式合併 |
| `_refresh_reused_worker_worktree` | ✅ |
| `config_path_arg` | ✅ |
| `remote_branch_exists` | ❌ 但無消費端，行為為 no-op |
| `task_is_in_transition` / `timestamp_is_in_transition` | ❌ 待評估 |

多數是已正式合併工作的草稿版。`remote_branch_exists` 把「分支已推送但無 PR」
標為 `"missing"` 而非 `"unknown"`——診斷正確，但 `supervisor.py` 的
finalize catch-all 對兩者一視同仁，故今日無行為差異；
`scripts/orchestrator/finalize_lane_doctor.py` 已用更完整的五種分類實作同一件事。

`task_is_in_transition`（給 `in_progress` 120 秒寬限期避免誤報 truth mismatch）
是唯一可能仍有價值的，建議單獨評估是否補進 dev。

**目錄本身未刪除**，僅退出服務與監控。

## 這次記取的教訓

先前把保全的 diff 與 freshness 檢查腳本放進 `oday-plus-supervisor-live`，
兩者都被該 repo 的重置清掉。**稽核用的產物不能住在被稽核的系統裡**——
本次改放版控。
