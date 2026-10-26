# ODP-ORCH-WORKTREE-ACTIVITY-STALL-GUARD-001 — worktree activity stall guard

- **Task ID**: `ODP-ORCH-WORKTREE-ACTIVITY-STALL-GUARD-001`
- **Owner**: Codex2
- **Reviewer**: Claude
- **記錄日期**: 2026-09-01

## 修正內容

`poll_workers` 現在把 trusted isolated task worktree 的 HEAD 變更與
task-owned dirty/untracked path 的有效 mtime 納入既有 stall 判斷。worktree
不存在、Git 查詢失敗、路徑無法驗證或無法讀取 mtime 時，不會被當成健康活動。

worktree activity state 只保留驗證狀態、reason code、HEAD SHA、dirty path
數量與時間戳，不保存路徑名稱或檔案內容。刪除中的 tracked path 會跳過，
讓同一份 status sample 中其他有效 dirty path 繼續提供證據；未來 mtime 則
直接丟棄，避免 clock skew 在每次 poll 被錯誤轉成新活動。

本修正延伸既有 `poll_workers` stall window，沒有新增 scheduler、retry 或
claim protocol。

## 驗證

Focused regression:

```text
uv run pytest .orchestrator/test_supervisor.py -k WorkerWorktreeActivityTests -q
```

Additional checks:

```text
python3 -m py_compile .orchestrator/worker_lifecycle.py .orchestrator/worker_workspace.py .orchestrator/supervisor.py .orchestrator/test_supervisor.py .orchestrator/test_worker_settlement_paths.py
git diff --check
```

測試涵蓋：log/process 靜默時 dirty worktree 不被標 stalled、無新活動後
仍會 stalled、M+D 混合狀態保留有效 mtime，以及 future mtime 不得讓 silent
worker 永遠保持健康。
