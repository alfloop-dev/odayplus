---
doc_id: ODP-RUNBOOK-SUPERVISOR-RUNTIME-ROLLOUT
title: Supervisor Runtime Rollout
status: maintained
date: 2026-08-11
language: zh-TW
owner: "Platform/Ops"
---

# Supervisor Runtime Rollout

## 0. 設定檔 contract（2026-08-14 起）

- `.orchestrator/config.schema.json` 是唯一欄位與型別 contract；固定 object 拒絕未知鍵，worker、provider、account pool 等具名 map 才允許動態 id。
- `config.example.json` 只供 `make bootstrap` 建立開發用、gitignored 的 `config.json`，runtime 缺檔時不會 fallback 到 example。
- 正式服務必須用 `--config /absolute/path/to/runtime.json`；若 config 位於 canonical status root，runtime launcher 也必須設定 `PANTHEON_STATUS_ROOT`，讓相對 state/repository path 固定錨在該 root，而不是每次 rollout 的 code worktree。Supervisor 會以 `PANTHEON_CONFIG_PATH` 把同一路徑傳給 worker、permission broker 與 `ai_status.py`。
- 只有預設的 `.orchestrator/config.json` 會自動套用同目錄 `config.local.json`；外部正式 config 預設為 self-contained。
- rollout 前依序執行 `check_orchestrator_config.py` 與 `check_config_wiring.py`，並用 `check_orchestrator_config.py --config <runtime.json>` 驗證正式檔。缺檔、非標準 JSON、未知鍵與錯誤型別一律 fail closed。
- schema 中的固定設定必須有 production code 讀取；不再使用 dead-key allowlist。

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

### 4.1 config contract 與 wiring guard

```
$ uv run python delivery_toolchain/governance/check_orchestrator_config.py
Validated 3 config documents and their merged runtime views.
$ uv run python delivery_toolchain/governance/check_config_wiring.py
All 148 config keys are read by production code.
exit=0
```

現在的 guard 直接讀 authoritative schema，且同時驗證 bootstrap config、local overlay
與傳入的正式 runtime config。任何未接線設定都必須刪除或先完成程式實作，不能放入
allowlist 延後處理。

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

從 2026-08-11 起，**不得在既有 runtime checkout 直接 fetch/checkout 或覆蓋檔案**。
這種做法會讓 Git 的 HEAD 看似最新、實際執行檔卻是舊的 dirty overlay。請一律用
`rollout_supervisor_runtime.py` 建立乾淨、具名分支的新 worktree，預檢 HEAD、距離與
tracked working tree 後，才原子切換 `runtime-current` symlink：

在首次部署 registry-base worktree 版本前，先檢查 live config；它會移除已失效的
`worker_worktrees.enabled`、`base_ref`、`reuse_existing`、`execution_reasons` 與 clean-divergence
reset 開關。先 dry-run，再明確指定同一個 live config 寫入：

```bash
python3 scripts/orchestrator/migrate_worker_worktree_config.py \
  --config /absolute/status-root/.orchestrator/config.json
python3 scripts/orchestrator/migrate_worker_worktree_config.py \
  --config /absolute/status-root/.orchestrator/config.json --write
```

這只遷移設定；不會 fetch、reset 或更新任何開發者 checkout 的本地 branch。

```bash
python3 scripts/orchestrator/rollout_supervisor_runtime.py \
  --source-root /path/to/clean-origin-dev-worktree \
  --runtime-link /home/lupin/oday-plus-supervisor-runtime-current \
  --runtime-parent /home/lupin \
  --status-root /home/lupin/oday-plus-supervisor-live \
  --service pantheon-supervisor.service
```

若現場唯一的 process manager 是既有 watchdog cron（沒有 supervisor systemd unit），
不要再安裝一套重疊 service；改讓同一個 rollout primitive 只替換 supervisor PID，
並由 stable runtime link 立即執行 watchdog：

```bash
python3 scripts/orchestrator/rollout_supervisor_runtime.py \
  --source-root /path/to/clean-origin-dev-worktree \
  --runtime-link /home/lupin/oday-plus-supervisor-runtime-current \
  --runtime-parent /home/lupin \
  --status-root /home/lupin/odayplus \
  --watchdog-pid-file /home/lupin/odayplus/.orchestrator/supervisor.pid
```

此指令拒絕 dirty source、非 `origin/dev` 的 source HEAD、dirty target、detached target
與落後目標 ref。它也會原子更新 canonical status root 的 `scripts/ai-status.sh`，使所有
supervisor、worker 與人工狀態命令都由 `runtime-current/scripts/ai_status.py` 執行，避免
舊 checkout 的 writer 把已修正狀態寫回舊格式。重啟失敗時會把 runtime symlink 與
status launcher 一起回復，再重新啟動前一版服務。

### 6.0 封存保護來源邊界（protected body domain）

`.orchestrator/worktree_cleanliness.py` 是唯一一份規則：`PROTECTED_BODY_PREFIXES`
（`tests/sources`，於任意深度以連續路徑元件比對，例如
`docs/audits/external-source-ingestion-20260913/implementation-handoff/untracked/tests/sources/...`）
與 `PROTECTED_BODY_NAMES`（任何以 `test_listing_production_asset.py` 開頭的路徑元件）。
判定只看路徑名稱；被拒絕的 body 不讀、不 hash、不複製、不執行、不刪除。對應的
non-cone sparse patterns 由 `protected_body_sparse_patterns()` 產生：

```text
/*
!**/tests/sources
!**/tests/sources/**
!**/test_listing_production_asset.py*
!**/*\\*
!**/*\\*/**
```

`!**/tests/sources` 排除「路徑本身就是 prefix」的條目（例如追蹤在 `.../tests/sources` 的一般檔案），
`!**/tests/sources/**` 排除其下所有條目。POSIX 上的 Git 允許名稱含字面反斜線，且把它留在同一個
路徑元件裡；`in_protected_body_domain` 卻把反斜線當分隔符（`archive\tests/sources/x`、
`archive/tests\sources/x` 都屬 domain），有限的 slash patterns 無法跟上所有拼法，所以最後兩行把
任何含反斜線的名稱整個排除（pattern 中 `\\` 是跳脫後的一個反斜線）。只含 slash 的 Git 名稱沒有空的
或 `.` 路徑元件，domain 依字面判定，前四行即涵蓋；因此 patterns 涵蓋 domain 拒絕的每一種 Git 名稱，
在 Git 寫檔前排除，不靠事後拒絕或刪除。含反斜線但不屬 domain 的名稱也會被排除（無法在 Windows
checkout 的名稱）。
證明要求 worktree 的 patterns 與上表完全一致。先前用較舊 patterns（三行，或沒有反斜線兩行的四行）準備的既有
worktree 會被 fail-closed 拒絕（`sparse patterns differ from the protected body domain`），不會自動改寫
patterns（對已 materialize 的 worktree 重設 patterns 可能刪除檔案）；採入新版 runtime 前，由 Root 以
`--no-checkout` 加新 patterns 重新準備這些 worktree。

同一規則接回的原入口：

| 入口 | 行為 |
|---|---|
| `rollout_supervisor_runtime.add_runtime_worktree` | `--no-checkout` 註冊並證明 HEAD 後，先 `install_protected_body_sparse`，才 `read-tree --reset -u`；之後以 `protected_body_checkout_refusal(require_sparse=True)` 證明 domain 未落地 |
| `rollout_supervisor_runtime.clean`（source、既有 target 重用、新 target） | 先跑 domain 證明，未通過即不 clean，`git status` 不會執行 |
| `worker_workspace._create_worker_worktree` | 同上順序：註冊 → 證明 HEAD／task 保護 → sparse → materialize → 證明 |
| `worker_workspace._protected_commit_refusal`（`check_index=True` 的所有呼叫者：重用 refresh、state identity、activity observer、handoff seal、merge continuation、preempt、quarantine、pruner） | task 保護之前先跑 domain 證明 |
| `worker_workspace._refresh_reused_worker_worktree` 的每一個 checkout／merge（review pin、detached 重新 attach、base fast-forward） | 變更前以 `checkout_into` 把目標 commit 交給同一個 `protected_body_checkout_refusal(target=...)`：目標 tree 有 domain 條目時，這個既有 worktree 必須已有完全一致的 sparse 排除，否則在 Git 寫檔前拒絕（即使 task `protected_paths` 為空、目前 checkout 尚無 domain 條目）；不遮蔽、不刪除任何已落地內容 |
| `worktree_cleanliness.inspect_worktree`（含 `task_finalize.sh`） | status 之前先跑 domain 證明；domain 內的未追蹤 dirt 與同路徑漂移在 status 之前即以 `protected_metadata_refused` blocking，不可當 seed、不產生 fingerprint；fingerprint 對 domain 路徑仍只用 lstat（防禦性保留） |
| quarantine／interrupted merge seal | domain 內的 dirt 拒絕備份與 seal，不讀 body |
| context seeding | 對所有 task（不只 `protected_paths` task）拒絕 domain 內的 context 來源或目的地；task brief 的每個相容候選路徑（實際被讀取／複製的來源）與生成時寫回的 canonical brief 也在 metadata 探測與寫入前經同一個 `context_path_protection_error` 判定，拒絕即 fail-closed，不會改走下一個候選 |
| 7 個既有 workflow 的 22 個 checkout | 全部：Git preflight → checkout（上述 sparse patterns）→ 同一份證明腳本，證明在任何 reader 之前（deploy-dev 緊接在 HEAD 身分 assert 之後）；比較端點時另拒絕 domain 在兩端不一致；唯一的結構驗證是既有 `tests/tooling/test_ci_product_parallel.py`（逐 job 鎖定 checkout 數、full history、精確 ref、HEAD 身分 assert、比較端點與下游 consumer 一致） |

Repository 身分只有一條規則：`worktree_cleanliness.shares_git_common_dir`（路徑必須是 worktree 的
top level，且解析後的 Git common dir 與 source 相同；metadata 無法回答即拒絕）。rollout 既有
target 重用、`worker_workspace._refresh_reused_worker_worktree`（`wrong_worktree: ...`）與
`_quarantine_and_preserve_dirty_worktree`（另保留兩端 git-dir 有效性；`worktree_belongs_to_another_repository`）
都直接呼叫它，各自在當下重新判定；另一個 clone 即使同 HEAD、同 named branch 也拒絕。

`protected_body_checkout_refusal` 的判定（全部只用 Git metadata 與 lstat）：

1. HEAD tree 與 index 取聯集。HEAD 或 index 追蹤任何 domain entry（或 `require_sparse`）時，
   worktree 必須 `core.sparseCheckout=true`、非 cone、patterns 完全相同。
2. 同一 domain 路徑在 HEAD 與 index 必須是同一個 (mode, object)：只在 HEAD（從 index
   移除、body 可能還在磁碟上）、只在 index、unmerged、object 或 mode 不同，一律以
   `protected_body_drift` 拒絕。
3. 每個 domain entry 都是 skip-worktree（`S`），並以 lstat 證明本身不在磁碟上；祖先為
   symlink 亦拒絕。
4. 以不帶任何 exclude 選項的 `git ls-files -z --others` 列出未追蹤名稱（Git 此時不開任何
   `.gitignore`）；任何未追蹤的 domain entry（含 domain 內的 `.gitignore`）都以
   `protected_body_present: ... is untracked domain content` 拒絕，`git status` 不會執行，
   因此 status 不會走進 domain 目錄讀 ignore 檔。空目錄（例如 protected launch 的 mask
   掛載點）沒有 body，不被列出也不拒絕。
5. metadata 無法讀取一律拒絕，不當成「沒有東西」。

root `tests/sources` 的 task `protected_paths` 拒絕（tracked 即拒絕）完全保留，sparse
不會把原本拒絕的 task／ref 變成允許。

**已知限制。** 這是對已知 metadata 命中路徑的保護，不是任意內容副本的證明。sparse
bootstrap 只證明 working tree 上沒有 body，不證明沒有 Git object 存取；先前不受限的
`git grep` 暴露仍為 UNKNOWN。被 `.gitignore` 忽略的目錄（例如 `.venv`、`node_modules`）
內若有符合 domain 名稱的路徑，也會被拒絕（fail-closed），需人工確認。

**採用後果（Root serial adoption 必讀）。** 在此規則之前建立、未設 sparse 的 worktree
（包含既有 worker worktree、既有 runtime 目錄與 rollout `--source-root` checkout）若
materialize 了 domain 副本，所有讀取入口都會以 `protected_metadata_refused:
protected_body_not_excluded` 拒絕，且不會自動修復（修復需刪除副本，本規則禁止）。
因此：

1. rollout 的 `--source-root` 必須是以 `--no-checkout` 建立、先套上述 sparse patterns 再
   materialize 的 worktree，否則 rollout 以「dirty source checkout」拒絕。
2. 合併後、runtime 尚未 rollout 前，舊 supervisor 建立的 worktree 沒有 sparse；若其 task
   branch 已包含此規則，`task_finalize.sh` 會在該 worktree 拒絕送審。
3. 既有 worker worktree 需由 Root 依同一順序重建，pruner 不會回收被拒絕的 worktree。

watchdog 的 systemd/cron 安裝根目錄同樣必須是 `runtime-current`（或由 service 直接
執行該 symlink 內的 watchdog），絕不可指向開發者的 `dev` checkout；watchdog 只負責
liveness/restart，不負責 fetch、merge 或任何 Git promotion。`restart-supervisor.sh` 已退役。

以下是舊版的人工流程，僅保留作為歷史與回復說明：

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
python3 "$RUNTIME/delivery_toolchain/governance/check_config_wiring.py"

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
`scripts/orchestrator/check_runtime_freshness.py`。

原本同時檢查兩個 runtime。2026-08-04 起只檢查 `runtime-current`——watchdog 已改為
共用同一份 checkout（§9.4），`945a8366` 不再服務任何東西，留在清單裡只會每小時
回報一個持續擴大、且沒有任何動作能清除的 FAIL。一個永遠不會轉綠的告警，
只會讓人學會忽略整組告警。

unit 從 `/home/lupin/oday-plus`（dev checkout）執行這支腳本，不是從 runtime
執行：檢查程式若住在它要稽核的東西裡面，會在那個東西過期時一起消失。

> 這行原本寫 `ops/check_runtime_freshness.py`，unit 也照抄。實作最後落在
> `scripts/orchestrator/`，於是 unit 從安裝當天起每小時以
> `No such file or directory` 失敗，從未真的檢查過任何東西——直到
> 2026-08-05 才發現。改動 unit 路徑後，務必 `systemctl --user start
> pantheon-runtime-freshness.service` 確認它真的跑得出結果。

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

### 9.4 watchdog runtime（2026-08-04 已退役）

`pantheon-supervisor-watchdog.service` 指向**另一個** runtime
`oday-plus-supervisor-runtime-945a8366`，**落後 origin/dev 381 個 commit**，
且處於 detached HEAD。

**刻意未推進，後改為退役。** watchdog 已改指 `runtime-current`，兩個服務共用同一份
checkout，一次 rollout 同時更新兩者；`945a8366` 保留原地但退出服務與監控。
詳見 `ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001`。原始判斷仍記錄如下：

該 checkout 有 6 個未提交的程式碼修改（`supervisor.py` +169/-75、
`ai_status.py` +68/-4、`supervisor_runtime_health.py` +28/-6、`wakeup.txt` +3/-3）。
比對後其中 `remote_branch_exists` 在 `origin/dev` **完全不存在**——推進會直接覆蓋掉
未進版控的工作。

完整 diff 已保全於
`docs/evidence/runtime/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001/uncommitted-945a8366.diff`
（604 行）。原先保全的路徑
`oday-plus-supervisor-live/watchdog-runtime-uncommitted-20260804.diff` 已隨該 repo
重置消失——稽核產物不能住在被稽核的系統裡，故改放版控。

處理前必須先由知情者判斷那些修改是否仍需要。逐項比對結果見上述 evidence 目錄的
`README.md`：多數已由 PR #602 等正式合併，`task_is_in_transition` 是唯一建議單獨
評估的。watchdog 已不再執行這份程式碼。

### 9.5 其他堆積的 runtime 目錄

現場另有數個 SHA 命名的 runtime checkout（`f7e76207`、`c4a5c106`、`ed74fbfa`、
`59b43428`、`4bba7ca3` 等），多數無程序使用。建議建立清理政策，
否則每次 rollout 都會再留下一個。
