# ORCH-REVIEW-CI-RECOVERY-001：讓 review CI 失敗可沿原任務回派 owner 修復

- Task: ORCH-REVIEW-CI-RECOVERY-001
- Owner: Claude（第三輪；前兩輪為 Antigravity，2026-09-10 quota terminal 後自動改派）／ Reviewer: Codex2
- Base commit（dev）: `91dd050a`（第三輪 base advance 後；前兩輪為 `1260d977`）
- 交付 branch: `task/ORCH-REVIEW-CI-RECOVERY-001`
- 本文件不宣稱 live runtime 已部署；修復在於本 branch 程式碼與回歸測試，等待 PR 審查與合併。

---

## 1. 缺陷與根因分析

在修復前，未核准的審查任務（`status == "review"`）在遇到 required CI 失敗時（`task_pr_ci_status` 回傳 `failure`），dispatcher 在審查派工迴圈（`dispatch_engine.py`）僅記錄：
```text
PR for task <task_id> has CI failure (failure); review dispatch suppressed until CI is repaired.
```
並寫入 `review_dispatch_suppressed` activity log 後直接 `continue`。

這造成了致命的排程停滯（stagnation gap）：
1. **Reviewer 不會被派工**：因為 required CI 必須為 `success` 才能進入 `review_ready_dispatch`。
2. **Owner 不會被回派**：任務狀態依然停留在 `status == "review"`，且 `waiting_for` 與 handoff 指向 reviewer，owner 派工迴圈無法取得該任務。
3. **實際案例**：`ODP-DATA-PLANE-DELETE-PROPAGATION-001` PR #1282 head `a964c83d66103444953e4ed2e8741b8e484a0f42`，因 GitHub run `34386285095` 之 `product-e2e-gate` 套件 index Hash Sum mismatch 導致 CI 退出，任務即陷入此停滯狀態。

---

## 2. 審查反饋修復（Codex2 PR #1288 審查要點）

根據 Codex2 在 PR #1288 審查提出的五大發現，本變更進行了嚴格而深入的加固：

### 2.1 消除 CI 解析分歧與強化 Check Rollup 驗證（Finding 1）
- **雙重驗證機制**：利用 `runtime_ai_status.latest_status_check_runs` 解析 GitHub `statusCheckRollup`，統一 CI 判斷標準。
- **嚴格驗證 Terminal Conclusion**：
  - 明確排除含有任何 pending / in-progress 狀態的檢查。
  - 排除 unknown conclusion 或非 dictionary 之畸形 rollup 項目。
  - 只有在所有檢查皆有確定結論且包含 failure 時才認定為 terminal CI failure。
- **豐富診斷資訊**：提取具體失敗 checks 之名稱、結論與 URL，寫入 `task["next"]` 與 activity log，使 Owner 能精確定位失敗原因。

### 2.2 CAS / 狀態發散防禦（Finding 2）
- 在 `recover_failed_ci_review_prs` 與 `recover_conflicted_review_prs` 中，當 `requeue_task_for_ci_repair` 或 canonical transition commit 因 CAS mismatch 失敗時：
  - 立即還原 in-memory marker。
  - 立即清空並自磁碟重新載入最新 `status`（`status.clear(); status.update(load_status(config))`）。
  - 中斷該 tick 之進一步 recovery 操作並安全退出，防止在過時 snapshot 上繼續操作。

### 2.3 派工候選狀態同步（Finding 3）
- 在進入候選任務派工迴圈前，`tasks` 與 `task_map` 必須來自最新的 canonical snapshot，杜絕因記憶體中物件曾被暫時修改但未成功持久化而錯誤派發 `owned_in_progress_dispatch` 的風險。
- 本輪（見第 2A 節）已修正此處的實作方式：改為與其他所有 reconciliation lane 一致的條件式重載，而非無條件重載。CAS 遭拒時 `recover_failed_ci_review_prs` 本就會就地 `status.clear(); status.update(load_status(config))`，caller 只需自該已刷新的 snapshot 重建索引，不需再讀一次磁碟。

### 2.4 Human Gate 與 Human Waiting 完整保護（Finding 4）
- 在 `requeue_task_for_ci_repair`、`recover_failed_ci_review_prs` 與 `recover_conflicted_review_prs` 中：
  - 除既有的 `task_is_human_gate` 與 `non_dispatchable` 檢查外，增加 `is_human_gate_agent(task.get("owner"))` 與 `is_human_gate_agent(task.get("waiting_for"))` 雙重防護。
  - 確保處於人工審查閘門或等待 Human/Ops 介入的任務絕不會被自動轉移或錯誤重開至 AI Owner。

### 2.5 測試名稱與覆蓋加固（Finding 5）
- 新增完整的 pending checks、unknown conclusion、畸形 rollup、human gate 防護、真實 CAS race 中斷、完整生命週期（CI failure -> recovery -> owner dispatch -> resubmission -> review dispatch）等回歸測試。
- 統一測試命名，確保 pytest `-k "ci_repair or ci_failure or conflicted_review"` 完整選中所有相關回歸測試。

---

## 2A. 第二輪審查回應：PR #1288 CI 失敗（run 34420166244 / job 102693620862）

### 2A.1 實測失敗
Reviewer 於 2026-09-10T08:57:27Z 以 review_finding 回派，指出 PR #1288 head `e60a8a6f5afc62465b6181aea8e6fb6b63699121` 的 `orchestrator` job 真實失敗，三項既有測試同時 `StopIteration`：

- `.orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_dispatcher_reassigns_mainline_helper_owner_before_dispatch`
- `.orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_dispatcher_reassigns_mainline_helper_reviewer_before_dispatch`
- `.orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_dispatcher_spreads_paused_review_to_registered_idle_reviewer`

本地已重現，堆疊終點為 `.orchestrator/dispatch_engine.py:2687` 的 `status = load_status(config)`。

### 2A.2 根因
`dispatch_ready_tasks` 中每一個 reconciliation lane 都遵循同一個慣例：**只有在該步驟回報有變更時才重載** canonical snapshot。上一輪為了涵蓋 CAS 遭拒的情境，把 CI-failure lane 之後的重載寫成**無條件執行**，位於 `if` 之外。

後果是每一個 tick（即使完全沒有任何 recovery）都多付一次 `load_status`。以這三項測試的情境量測：**修復前 3 次、修復後 2 次**。它們以 `side_effect=[initial_status, normalized_status]`（恰好兩筆）驅動 dispatcher，第三次讀取即耗盡迭代器而 `StopIteration`。

這不是 fixture 過時，而是產品碼多做了一次不必要的 canonical 讀取；因此修的是產品碼，三項測試與其 dispatch 行為斷言完全未更動。

### 2A.3 修正
`dispatch_engine.py` 恢復與其他 lane 一致的條件式重載，並以 `else` 分支保留上一輪的 CAS 防禦：

- 有 recovery（回傳 True）：`changed = True`，重載 canonical snapshot 並重建 `tasks` / `task_map`。
- 無 recovery（回傳 False）：CAS 遭拒時 `recover_failed_ci_review_prs` 已就地刷新 `status`，故僅自該 snapshot **重建索引**，不再讀一次磁碟。若本來就無事發生，這只是以手上同一份 snapshot 重建等值內容。

上一輪的 CAS / pending-check / Human gate 修正全部原樣保留，未刪除或跳過任何測試。

**為何不會削弱 `recover_conflicted_review_prs` 的 CAS 防禦**：該 lane 有完全相同的就地刷新模式（CAS 遭拒時 `status.clear(); status.update(load_status(config))` 後回傳 `changed`，可能為 False），其 caller 同樣沒有 `else` 分支。原本是靠 CI lane 之後那次無條件重載順帶覆蓋。由於本輪的 `else` 分支位置在**兩個 lane 之後**，四種組合都仍能在進入派工迴圈前自當前 snapshot 重建索引：

- conflicted True → 該 `if` 自行重載；
- conflicted False（CAS 遭拒，就地刷新）+ CI True → CI 的 `if` 重新讀取磁碟；
- conflicted False（CAS 遭拒，就地刷新）+ CI False → 本輪 `else` 自已刷新的 snapshot 重建索引；
- 兩者皆無事發生 → `else` 以手上同一份 snapshot 重建等值內容。

### 2A.4 新增回歸（並經變異驗證確認非空測）
兩項新回歸自兩側夾住此修正，各自針對一種失敗模式；已分別以變異版本實測確認會轉紅：

| 新回歸 | 針對的失敗模式 | 變異驗證結果 |
|---|---|---|
| `test_ci_failure_lane_adds_no_canonical_read_on_a_quiet_tick` | 原缺陷：無條件重載（安靜 tick 讀 3 次而非 2 次） | 對「無條件重載」變異版 FAILED |
| `test_ci_failure_rejected_cas_rebuilds_indices_from_the_resynced_snapshot` | 過度修正：直接刪掉重載而不補 `else` 分支，CAS 遭拒後索引仍為脫鉤物件 | 對「僅刪除重載」變異版 FAILED |

第二項刻意讓 CAS 遭拒時的 disk snapshot 換成一筆原清單不存在的任務，唯有真正重建索引才可能派出它。

**必要說明**：此二回歸的第一版設計（差分讀取計數）在缺陷版上同樣通過，屬空測；已改寫並以上述變異測試證實其有效性後才納入。

## 2B. 第三輪：接手、base advance 與再送審（owner：Claude）

### 2B.1 交接事實
- 2026-09-10T14:48:32Z，Antigravity3 連續 quota terminal，orchestrator 將 owner 由 Antigravity3 自動改派為 Claude，task 退回 `todo` 等待新一輪。
- 第二輪修復（見 2A）已由前一位 worker 以 anchor commit `725f919c` 保存並推上 PR #1288。本輪接手後**未改動**該修復的實作、測試或既有結論；本輪的工作是重新實測它，並把 branch 推進到當前 dev。
- 接手時於 `725f919c` 實測基線：宣告 selection 70 passed（exit 0）、2A 指名的三項 supervisor 回歸 3 passed（exit 0）。2A 的宣稱在接手當下成立。

### 2B.2 Base advance（本輪唯一的程式碼樹變動）
接手時 branch 落後 `origin/dev`（`91dd050a`）5 個 commit。依規以 merge 而非 rebase 合併：

- `git merge --no-commit --no-ff origin/dev` → 自動合併成功，**0 個 conflict**。
- dev 這 5 個 commit 完全不觸及 `.orchestrator/`（`git diff --name-only HEAD...origin/dev` 過濾 `^\.orchestrator/` 無輸出），與本任務 surface 無重疊。
- 以 `worker_commit.py` 提交，`--scope` 帶入 staged 的**全部 52 個檔案**：worker_commit 的私有 index 是自 HEAD 重建的，漏檔會在合併裡無聲丟掉 dev 的內容（evil merge）。
- 產生 merge commit `573156a7`，parents = `725f919c` + `91dd050a`。

合併正確性以實測確認，非以「合併成功」推論：

| 檢查 | 命令 | 結果 |
|---|---|---|
| 雙 parent | `git log -1 --format='%P'` | `725f919c 91dd050a` |
| dev 已完整併入 | `git merge-base --is-ancestor origin/dev HEAD` | exit 0 |
| worktree 乾淨 | `git status --porcelain` | 無輸出 |
| 無 evil merge | `git diff --name-only HEAD origin/dev` | 恰為本任務 5 個檔案 |

最後一列是 evil merge 的直接反證：若 scope 漏檔而改寫了 dev 的內容，合併後與 dev 的差集必然出現非本任務檔案。

### 2B.3 宣告命令的執行環境（需要 reviewer 知道）
宣告的 verification 第二條為 `python3 -m pytest ...`。本機 `/usr/bin/python3` **沒有** pytest：

```text
$ python3 -c "import pytest"
ModuleNotFoundError: No module named 'pytest'
```

因此執行時把專案 `.venv/bin` 置於 `PATH` 前綴並設 `PYTHONPATH=.orchestrator:scripts`，使 `python3` 解析到專案 venv（Python 3.12.14）。**命令字串未改寫**，收據記錄的即為宣告的命令；改的是 `python3` 解析到哪個直譯器。若在沒有這個 PATH 前綴的環境重跑收據上的命令，會得到 `No module named pytest` 而非測試結果。


### 2B.4 獨立複驗 2A 的變異測試宣稱
2A 自承其第一版回歸是空測，改寫後才納入。接手者不採信該宣稱，於 `573156a7` 自行重跑兩側變異，兩者都在**乾淨的 worktree** 上施加變異、實測、再 `git checkout --` 還原：

| 變異 | 施加方式 | 受測回歸 | 實測結果 |
|---|---|---|---|
| 還原原缺陷（無條件重載） | `git show 725f919c -- .orchestrator/dispatch_engine.py \| git apply -R` | `test_ci_failure_lane_adds_no_canonical_read_on_a_quiet_tick` | **FAILED**（`assert 3 == 2`，安靜 tick 多讀一次 canonical），exit 1 |
| 過度修正（刪掉 `else` 分支） | 直接刪除該分支 | `test_ci_failure_rejected_cas_rebuilds_indices_from_the_resynced_snapshot` | **FAILED**，exit 1 |

兩次變異中，另一項回歸各自維持 passed，代表兩者確實各自釘住一種失敗模式，而非同一條件的重複。

更重要的是**因果直接對上 reviewer 回報的 CI 失敗**：在第一種變異（即 PR #1288 head `e60a8a6f` 的狀態）下重跑 reviewer 指名的三項測試，

```text
FAILED .orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_dispatcher_reassigns_mainline_helper_owner_before_dispatch
FAILED .orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_dispatcher_reassigns_mainline_helper_reviewer_before_dispatch
FAILED .orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_dispatcher_spreads_paused_review_to_registered_idle_reviewer
3 failed, 635 deselected（exit 1）
```

還原修正後同樣三項為 `3 passed`（exit 0）。這是本 branch 確實修掉 run 34420166244 / job 102693620862 那次失敗的實測證據，而不是「測試現在是綠的」這種相關性陳述。

### 2B.5 全 `.orchestrator/` 套件差集（針對第二輪失敗的那一類）
第二輪之所以被回派，是因為宣告的 selection 選不到被本變更**間接**影響的 `test_supervisor.py`。宣告 selection 綠燈本身無法證明這一類不會再發生，因此本輪額外做了**差集**比對而非只看單邊綠燈：本機在隔離 worktree 執行完整 `.orchestrator/` 套件必定會紅（缺少 gitignored 的 `.orchestrator/config.json`），所以單邊結果不可判讀，只有與乾淨 dev 的差集可判讀。

兩次執行為同一命令、同一直譯器、同一 `-p no:randomly`：

| 執行 | 位置 | 結果 | Exit |
|---|---|---|---|
| 本 branch `573156a7` | 本 task worktree | **20 failed, 1916 passed**, 6 skipped, 450 subtests passed（204.42s） | 1 |
| 乾淨 dev `91dd050a` | `git worktree add --detach /tmp/orch-ci-recovery-devbaseline` 的臨時 worktree | **20 failed, 1872 passed**, 6 skipped, 450 subtests passed（205.17s） | 1 |

兩邊的 FAILED 清單經 `diff` 比對為**逐字元相同**（各 20 筆）：

```text
$ diff /tmp/dev_failures.txt /tmp/head_failures.txt   # 無輸出，exit 0
```

即本 branch **新增 44 個通過的測試、新增 0 個失敗**。

那 20 筆全部屬同一個既有環境類別，與本任務無關：

- 16 筆為 `common.ConfigError: Orchestrator config does not exist: .../.orchestrator/config.json` — 該檔是 gitignored 的本機覆蓋，隔離 worktree 內不存在。
- 其餘為同一成因的下游斷言，例如 `ReviewHeadFreezeTests::test_approve_refuses_to_overwrite_uncleared_approved_head` 因缺 config 而落到 role/provider 審查政策的錯誤訊息，而非預期的 `uncleared approved head`。
- 三個受影響檔案（`test_supervisor.py` 的 `ReviewHeadFreezeTests`、`test_worker_hard_inactivity.py`、`test_worker_settlement_paths.py`）都不觸及本變更的 dispatch CI-recovery lane。

**這不是在主張 CI 會綠**：CI 跑的是 `uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling`，範圍更廣且有 config 存在，與本機環境不同。此處成立的是較窄但可判讀的結論——在同一環境下，本 branch 相對 dev 沒有引入任何新的測試失敗，第二輪那一類間接破壞在本輪未重演。CI 本身的判定仍以 PR #1288 的 required checks 為準。

## 3. 解決架構與設計

### 3.1 `status_transition.requeue_task_for_ci_repair`
- 新增 `allow_failed_ci_review: bool = False` 具名入口旗標。
- 當 `(allow_conflicted_review or allow_failed_ci_review)` 為 True 時，僅在 task 為未核准（`status == "review"` 且無 `approved_head` 與 `merge_route`）且 review submission 完整時放行進入 `in_progress`。
- 清理過時的 reviewer `waiting_for`，並將對應的 pending handoff 標記為 `done`（帶 `resolved_at`）。
- 嚴格守門 `is_human_gate_agent`。
- 使用 `REOPEN_CATEGORY_CONTROL_PLANE_RECOVERY` 與 `REOPEN_REASON_CONTROL_PLANE_RECOVERY`，確保不計入 review churn / review finding 計數。
- 記錄 activity log 事件 `ci_repair_requeued`，其中 `entry` 設為 `"review_ci_failure"`。

### 3.2 `dispatch_engine.recover_failed_ci_review_prs`
- 放置於 `dispatch_ready_tasks` 的 **reconciliation 階段**（與 `recover_conflicted_review_prs` 並列），不依賴 reviewer slot 或 agent loop 順序。
- **嚴格守門條件**：
  1. Task 處於 `review_statuses`（`"review"`）且非 busy（不在 `active_task_ids | pending_task_ids`）。
  2. 非 Human gate（非 `task_is_human_gate`、非 `is_human_gate_agent(owner)`、非 `is_human_gate_agent(waiting_for)`）且非 `non_dispatchable`。
  3. 未 approved（無 `approved_head`）且未 queued（無 `merge_route`）。
  4. 無活躍的 helper execution lease。
  5. 具有完整 verified review submission（`pr_number > 0` 與 `remote_sha`）。
  6. 遠端 HEAD 與 submitted SHA 一致（無 HEAD drift）。
  7. CI 經由 cached read 與 fresh read（`max_age_seconds=0`）皆確定為 `failure` 且 PR 為 `OPEN`。
  8. PR facts（state/mergeStateStatus/headRefOid）在 CI 探測前後一致（防 race）。
  9. GitHub `statusCheckRollup` 經 terminal 驗證確認無 pending/unknown，並取得具體 failed checks 清單。
- **持久化追蹤與重複 tick 冪等性**：
  - 定義 `REVIEW_CI_FAILURE_RECOVERY_HEAD_FIELD = "review_ci_failure_recovery_head"`。
  - 對同一 submitted head 僅執行一次 recovery，後續 tick 不會反覆 reopen。
  - 當 owner 修改程式碼並經由 `task_finalize.sh` 提交新 head 後，若新 head 再次失敗，則可再次安全觸發 recovery。
- **回派診斷訊息**：
  - `task["next"]` 明確指出 PR 編號、失敗的 submitted head 前 8 碼、失敗的 checks 清單，並引導 owner 修復 CI 後透過 `task_finalize.sh` 重新提交。

---

## 4. 驗證收據

所有命令皆在 `/tmp/pantheon-worker-worktrees/pantheon/orch-review-ci-recovery-001` 執行，未經 pipe、未加 `|| true`、未 background，保存工具本身真實 exit code。

- Task Branch：`task/ORCH-REVIEW-CI-RECOVERY-001`
- 執行環境：`PYTHONPATH=.orchestrator:scripts .venv/bin/python`

| # | 命令 | Exit Code | 結果摘要 |
|---|---|---|---|
| 1 | `git diff --check` | 0 | 無任何 whitespace 或 formatting 錯誤 |
| 2 | `PYTHONPATH=.orchestrator:scripts .venv/bin/python -m pytest -v .orchestrator/test_dispatch_policy.py -k "ci_repair or ci_failure or conflicted_review"` | 0 | 68 passed, 107 deselected in 2.23s |
| 3 | `PYTHONPATH=.orchestrator:scripts .venv/bin/python -m pytest -v .orchestrator/test_dispatch_policy.py` | 0 | 175 passed in 30.10s |
| 4 | `PYTHONPATH=.orchestrator:scripts .venv/bin/python -m pytest -v .orchestrator/ -k "ci_repair or review_resubmission or reopen_audit"` | 0 | 12 passed, 1928 deselected in 6.14s |

上表為前兩輪在其各自 head 的量測，保留備查。以下為**第三輪在 base advance 後的 head `573156a7` 重新量測**的收據；上表的數字不代表本 head。

### 4A. 第三輪收據（head `573156a7`）

宣告的兩條 verification 由 `delivery_toolchain/git/task_verification.py run` 執行，收據由該工具寫入 `.orchestrator/evidence/`，各自綁定 head SHA、原命令字串、真實 exit code、duration 與 test selection：

| # | 宣告命令 | Exit Code | Duration | 收據 |
|---|---|---|---|---|
| 1 | `git diff --check` | 0 | 0.015s | `verification-orch_review_ci_recovery_001-1d481ca8486d107d.json` |
| 2 | `python3 -m pytest -q .orchestrator/test_dispatch_policy.py -k "ci_repair or ci_failure or conflicted_review"` | 0 | 5.253s | `verification-orch_review_ci_recovery_001-d22bbc586dee6e90.json` |

宣告以外、本輪另外執行的量測（同一 head，未經 pipe 吃掉 exit code、未 background、未加 `|| true`）：

| # | 命令 | Exit Code | 結果摘要 |
|---|---|---|---|
| 3 | `python3 -m pytest .orchestrator/test_supervisor.py -k "<2A 指名的三項>"` | 0 | 3 passed, 635 deselected |
| 4 | `uv run --frozen ruff check .orchestrator delivery_toolchain scripts` | 0 | All checks passed（與 CI 的 lint 步驟同範圍） |
| 5 | `python3 delivery_toolchain/governance/check_code_boundaries.py` | 0 | 1152 files passed |
| 6 | `python3 -m pytest -p no:randomly .orchestrator/`（本 branch） | 1 | 20 failed, 1916 passed — 差集見 2B.5 |
| 7 | 同上，於乾淨 dev `91dd050a` 的臨時 worktree | 1 | 20 failed, 1872 passed — FAILED 清單與第 6 項逐字元相同 |

量測用的臨時 worktree `/tmp/orch-ci-recovery-devbaseline`（detached at `91dd050a`）**尚未移除**：`git worktree remove` 在本 worker 的權限下被拒絕。它在 `/tmp` 下、與本 repo 交付無關，但仍會出現在 `git worktree list`，留待前景協調者或下一次 prune 清理。此處據實記錄而非宣稱已清理。

第 6、7 兩項的 exit code 為 1 且**不宣稱為通過**：它們是差集量測的兩個端點，可判讀的結論是兩端 FAILED 清單相同（見 2B.5），不是任一端為綠。

第 2 項的 `python3` 需要專案 venv 在 `PATH` 前綴才可解析到有 pytest 的直譯器，理由與實測見 2B.3。

---

## 5. 邊界與規範

1. **窄邊界**：僅修改 `.orchestrator/status_transition.py`, `.orchestrator/dispatch_engine.py`, `.orchestrator/test_dispatch_policy.py` 與本 evidence 文件。
2. **無未授權修改**：未手動修改 live board、未動 runtime symlink、未動 Supervisor 運行中程序。
3. **交付路徑**：透過標準 `worker_commit.py` 與 `task_finalize.sh` 建立 PR，等待獨立 Reviewer（Codex2）審查與 CI 合併。
