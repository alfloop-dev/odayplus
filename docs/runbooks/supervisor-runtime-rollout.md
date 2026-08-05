---
doc_id: ODP-RUNBOOK-SUPERVISOR-RUNTIME-ROLLOUT
title: Supervisor Runtime Rollout
status: executed
date: 2026-08-04
language: zh-TW
owner: "Platform/Ops"
---

# Supervisor Runtime Rollout

## 1. 為什麼需要這份 runbook

合併進 `dev` **不等於生效**。實際執行 fleet 的 supervisor 是另一份 checkout，
而它已經停在 126 個 commit 之前。過去數日合併的 orchestrator 修復
（#602、#610、#611、#613、#614）**一個都沒有在生產環境生效**。

三份 repo 的實況（2026-08-04 觀測）：

| 路徑 | 角色 | 版本 | 落後 `origin/dev` |
|---|---|---|---:|
| `oday-plus` | 開發工作區 | 隨 dev | 0 |
| `oday-plus-supervisor-live` | 只存放 `ai-status.json` / `ai-task-archive` | 分支 `dev` | **188** |
| `oday-plus-supervisor-runtime-d9c4b474` | **實際執行的 process** | **detached HEAD** `d9c4b474` | **126** |

執行中的 process：

```
python3 /home/lupin/oday-plus-supervisor-runtime-d9c4b474/.orchestrator/supervisor.py \
  --config /home/lupin/.config/pantheon/supervisor-runtime.json --verbose
```

它的 `status_file` 指向 `oday-plus-supervisor-live/ai-status.json`，所以狀態與程式碼
分屬不同 checkout，兩者各自落後不同的距離。

## 2. 不 rollout 的代價

現行 runtime 有三個已知缺陷正在生產環境生效：

### 2.1 detached HEAD 造成孤兒 task

runtime repo 處於 detached HEAD，`current_branch()`
（`git rev-parse --abbrev-ref HEAD`）回傳字串 `"HEAD"`，被當成分支名往下傳。
`review_branch_for_task()` 三道檢查全部放行，ReviewBus 拿假分支去建 PR，
判定「分支未發布」就跳過。

實證：`.orchestrator/github-bus-state.json` 中
`ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001` 與
`ODP-ORCH-HELPER-CLAIM-ASSIGNMENT-PRESERVATION-001` 皆為
`branch="HEAD"` / `state="skipped_unpublished_branch"`。

修復在 PR #616。

### 2.2 task PR 以 `main` 為 base，繞過 dev CI

現行 `github_bus.py` 缺 `task_pr_base_branch()`，fallback 到 `default_branch()`。
新版註解逐字說明：

> basing task PRs on `default_branch()` would route them straight at main and
> bypass that promotion entirely.

工作流程是 dev → 促進 → main。現行 runtime **正在破壞這個流程**。
修復在 PR #611（已合併進 dev，但未生效）。

### 2.3 agent 名稱比對大小寫敏感

`supervisor.py` 舊版直接比對 `task.get(reviewer_field) == agent_name`。
config 用小寫（`codex2`）而 board 用大寫（`Codex2`），派工與審查配對會失敗。
新版改用 `normalize_agent_id()`。

## 3. Rollout 範圍：比 126 小得多

| 面向 | 數量 |
|---|---:|
| 總 commit | 126 |
| 動到 `.orchestrator/` | **17** |
| 動到 `scripts/ai_status.py` | **1** |
| production 程式碼淨變動 | **約 90 行 / 5 檔** |
| 測試 | 369 行 |
| 新增 config 檔 | 1（52 行） |

逐檔行為變更：

| 檔案 | 行數 | 性質 | 風險 |
|---|---:|---|---|
| `supervisor.py` | 28 | agent 名稱正規化 | 低（修 bug） |
| `github_bus.py` | 20 | task PR base 改走 branch workflow | **中，但修的是 §2.2** |
| `permission_broker.py` | 7 | 禁 agent 自行 `gh pr create` | 低（配套 §2.2） |
| `provider_permissions.py` | 5 | 同上，移出 allow list | 低 |
| `templates/wakeup.txt` | 6 | 提示文字 | 無 |
| `ai_status.py` | 23 | merge commit metadata 解析增強 | 低 |
| `config_wiring_allowlist.json` | +52 | 新增檢查用清單 | 見 §4.1 |

## 4. 預檢結果（2026-08-04 已執行）

### 4.1 config wiring guard — 通過，且原先的疑慮不成立

```
$ python3 scripts/check_config_wiring.py
All 245 config keys are read by code or allowlisted.
exit=0
```

初版風險評估曾標記「rollout 後若現行 config 有未接線設定，CI 會開始失敗」。
**該疑慮不成立**：`scripts/check_config_wiring.py:26` 讀的是
`.orchestrator/config.example.json`（committed），不是生產用的 `config.json`。
這是 repo 內的規範檢查，與 live config 無關。

注意此檢查是**雙向**的：宣告了沒接線會失敗，allowlist 中的項目變成已接線也會失敗，
所以清單不會悄悄過期。

### 4.2 dev tip orchestrator 測試 — 通過

```
$ uv run pytest .orchestrator/ -q
56 passed
```

在現行機器環境下，dev tip 的 orchestrator 測試全數通過。

## 5. 建議：一次推到 dev tip，不要分批

那 17 個 commit 高度互相依賴——#611 的 base 修正、#612 的權限配套、
#614 的 config 檢查屬同一組設計。拆開推送會產生中間狀態不一致，
例如只推 #612 的權限限制而沒有 #611 的 base 修正，會讓 task PR 完全無法建立。

## 6. 執行步驟

```bash
RUNTIME=/home/lupin/oday-plus-supervisor-runtime-d9c4b474
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

# 1. 確認沒有 task 正在 handoff（ai-status.json 無 file lock）
python3 scripts/orchestrator/finalize_lane_doctor.py \
  --status /home/lupin/oday-plus-supervisor-live/ai-status.json --repo .

# 2. 備份狀態（狀態與程式碼分屬不同 checkout，兩者都要）
cp /home/lupin/oday-plus-supervisor-live/ai-status.json \
   "/home/lupin/oday-plus-supervisor-live/ai-status.json.bak-${STAMP}"
tar czf "/home/lupin/oday-plus-supervisor-live/ai-task-archive.bak-${STAMP}.tgz" \
   -C /home/lupin/oday-plus-supervisor-live ai-task-archive

# 3. 記錄目前 runtime 版本以便回滾
git -C "$RUNTIME" rev-parse HEAD | tee "/tmp/runtime-rollback-${STAMP}.sha"

# 4. 停止 supervisor（PID 於執行時重新確認）
#    ps aux | grep supervisor.py

# 5. 推進 runtime 到 dev tip
git -C "$RUNTIME" fetch origin
git -C "$RUNTIME" checkout -q origin/dev

# 6. 驗證
git -C "$RUNTIME" rev-parse HEAD
grep -c "task_pr_base_branch" "$RUNTIME/.orchestrator/github_bus.py"   # 應為 >=1
python3 "$RUNTIME/scripts/check_config_wiring.py"

# 7. 重啟 supervisor
```

### 6.1 關於步驟 5 的 detached HEAD

`git checkout origin/dev` 會再次進入 detached HEAD，也就是 §2.1 的觸發條件。
在 PR #616 合併並生效前，這個問題不會消失。

若要立即避開，改用具名分支：

```bash
git -C "$RUNTIME" checkout -B runtime-live origin/dev
```

這樣 `current_branch()` 回傳 `runtime-live` 而非 `"HEAD"`，
在 #616 生效前先行阻斷孤兒的產生。**建議採此作法。**

## 7. 回滾

```bash
git -C "$RUNTIME" checkout -q $(cat "/tmp/runtime-rollback-${STAMP}.sha")
cp "/home/lupin/oday-plus-supervisor-live/ai-status.json.bak-${STAMP}" \
   /home/lupin/oday-plus-supervisor-live/ai-status.json
```

再重啟 supervisor。

## 8. 尚未涵蓋的問題

**rollout 本身沒有自動化。** 每個 orchestrator 修復都需要一個對應的
`LIVE-ROLLOUT` task 才會生效，而這些 task 自己也卡在 finalize 車道：

| Rollout task | 狀態 |
|---|---|
| `ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001` | blocked |
| `ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001` | review_approved，CI 失敗 |

形成三層堆疊：修復卡在 finalize → 合併後要等 rollout task → rollout task 也卡在同一車道。

在 rollout 具備自動化或至少具備「runtime 落後告警」之前，這個落差會持續累積。
建議後續建立一個檢查：**runtime HEAD 與 `origin/dev` 的距離超過門檻即告警**，
這樣落後 126 個 commit 的狀況不會再無聲累積數日。

## 9. 執行記錄（2026-08-04）

### 9.1 主 supervisor — 已完成

```
before  detached HEAD @ d9c4b474,  落後 origin/dev 126
after   branch runtime-live @ 7367f37f,  落後 0
```

服務 `active`、`NRestarts=0`、無 error 日誌。新 supervisor 起來後立即接手佇列中的工作。

備份：`ai-status.json.bak-20260804T131655Z`、
`ai-task-archive.bak-20260804T131655Z.tgz`、回滾 SHA `d9c4b4740cf8`。

**執行時發現的關鍵事實**：`pantheon-supervisor.service` 是 systemd user service 且
`Restart=always`。直接 `kill` PID 會在 3 秒後用**舊程式碼**拉回，rollout 等於未發生。
必須走 `systemctl --user stop/start`。`KillMode=process` 只殺主程序，
執行中的 worker 得以跑完，不會被中斷。

### 9.2 穩定路徑 — 已改用 symlink

目錄名 `oday-plus-supervisor-runtime-d9c4b474` 在推進後裝的是 `7367f37f`，名稱誤導。
改名不可行——`oday-plus-supervisor-runtime` 已被一個廢棄的 checkout 佔用
（450 落後、無程序使用）。

改用 symlink，保留 SHA 命名慣例的同時給 systemd 穩定指向：

```
oday-plus-supervisor-runtime-current -> oday-plus-supervisor-runtime-<sha>
```

`pantheon-supervisor.service` 與 `pantheon-runtime-freshness.service` 都已改指 symlink。
**往後 rollout 只需重指 symlink，不必再編輯 systemd unit。**

### 9.3 定期檢查 — 已啟用

`pantheon-runtime-freshness.timer`（每小時，`Persistent=true`）執行
`ops/check_runtime_freshness.py`，同時檢查兩個 runtime。

首次執行即抓到真問題（見 §9.4），service 以 `status=1/FAILURE` 收場——
這正是預期行為：漂移不再無聲。

另外，`scripts/supervisor_runtime_health.py` 現在內建 git freshness 支援
（`--check-git-freshness` flag，ODP-ORCH-RUNTIME-ROLLOUT-PRECHECK-001），
可作為即時健康檢查的一環，並在 JSON 輸出中暴露
`runtime_git_not_detached` 與 `runtime_git_not_behind` 兩個具名 check：

```bash
python3 "$PANTHEON_STATUS_ROOT/scripts/supervisor_runtime_health.py" \
  --repo "$RUNTIME" \
  --check-git-freshness \
  --json | python3 -c "
import json, sys
r = json.load(sys.stdin)
for c in r.get('checks', []):
    if c['name'].startswith('runtime_git'):
        print(c['name'], '->', 'OK' if c['ok'] else 'FAIL')
"
```

### 9.4 未處理：watchdog runtime

`pantheon-supervisor-watchdog.service` 指向**另一個** runtime
`oday-plus-supervisor-runtime-945a8366`，**落後 origin/dev 381 個 commit**，
且處於 detached HEAD。

**刻意未推進。** 該 checkout 有 6 個未提交的程式碼修改（`supervisor.py` +169/-75、
`ai_status.py` +68/-4、`supervisor_runtime_health.py` +28/-6、`wakeup.txt` +3/-3）。
比對後其中 `remote_branch_exists` 在 `origin/dev` **完全不存在**——推進會直接覆蓋掉
未進版控的工作。

完整 diff 已保全於
`oday-plus-supervisor-live/watchdog-runtime-uncommitted-20260804.diff`（604 行）。

處理前必須先由知情者判斷那些修改是否仍需要。在那之前，watchdog 持續執行 381 個
commit 之前的程式碼。

### 9.5 其他堆積的 runtime 目錄

現場另有數個 SHA 命名的 runtime checkout（`f7e76207`、`c4a5c106`、`ed74fbfa`、
`59b43428`、`4bba7ca3` 等），多數無程序使用。建議建立清理政策，
否則每次 rollout 都會再留下一個。
