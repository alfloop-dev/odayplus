# Evidence: ODP-WAKEUP-STALE-WORKTREE-GUARDRAIL-001

## 任務目標與變更摘要

擴充 `.orchestrator/templates/wakeup.txt` 中既有的 stale 警告，使之不僅涵蓋「禁止從隔離 worktree 執行 status 命令」，同時涵蓋「禁止直接依賴本機工作樹檔案判斷 repo 現況」，並提供具體可執行的查證指引（例如 `git show origin/dev:<path>`）。

同時在 `.orchestrator/worker_workspace.py` 的 `_generated_collaboration_guide` 中同步此守則，確保動態生成的 `AI_COLLABORATION_GUIDE.md` 與 wakeup prompt 措辭一致。

---

## 1. 變更檔案清單

1. `.orchestrator/templates/wakeup.txt`:
   - 擴充第 4 行既有 stale 警語，納入本機工作樹檔案判斷 repo 現況的風險與對 `origin/dev` 查證的具體指引。
2. `.orchestrator/worker_workspace.py`:
   - 在 `_generated_collaboration_guide` 的 `Workspace` 與 `Status & closeout` 區段同步補齊 stale 工作樹與 `origin/dev` 查證規範。
3. `.orchestrator/test_watch_events.py`:
   - 新增 `test_wakeup_prompt_stale_worktree_guardrail_applies_universally` 並在既有測試中斷言各 dispatch 情境均包含此警告。
4. `.orchestrator/test_supervisor.py`:
   - 新增 `test_worker_prompt_stale_worktree_guardrail_applies_across_dispatch_reasons` 與 `test_seeded_collaboration_guide_documents_stale_worktree_guardrail`。

---

## 2. 驗收要點對應

### 要點 1 & 2：擴充既有 stale 警語且具體可執行
- **舊版警語**：
  > 執行任何狀態命令時，必須用 `AI_NAME={{target_agent_display_name}} "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" ...`。`PANTHEON_STATUS_ROOT` 指向 live canonical writer；禁止從隔離 worktree 執行 `scripts/ai-status.sh` 或 `python3 scripts/ai_status.py`，以免 stale branch code 覆寫 dashboard 與 task truth。
- **新版警語**：
  > 執行任何狀態命令時，必須用 `AI_NAME={{target_agent_display_name}} "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" ...`。`PANTHEON_STATUS_ROOT` 指向 live canonical writer；禁止從隔離 worktree 執行 `scripts/ai-status.sh` 或 `python3 scripts/ai_status.py`，亦禁止直接依賴本機工作樹檔案判斷 repo 現況（若需查證 repo 最新現況與配置，應對 `origin/dev` 查證，例如 `git show origin/dev:<path>`），以免 stale branch code 覆寫 dashboard 與 task truth 或據過期檔案做出錯誤判斷。

### 要點 3：未修改 `watch_events.py` 的 `branch_work_guardrails`
- `branch_work_guardrails` 保持原樣（依 finalize / non-mutating / review / 一般 四種 dispatch reason 分流）；stale 警告位於 `wakeup.txt` 本體頂部，對所有 dispatch 情境一體適用。

### 要點 4：`AI_COLLABORATION_GUIDE.md` 來源確認與一致性
- 經查證，`AI_COLLABORATION_GUIDE.md` 在 repo 中無 tracked 檔案，而是由 `.orchestrator/worker_workspace.py` 的 `_generated_collaboration_guide(config)` 在每個 worker worktree 初始化時動態寫入。
- 已同步修改 `_generated_collaboration_guide(config)`，在 `Workspace` 區段加入 `verify against origin/dev (e.g. git show origin/dev:<path>)`，在 `Status & closeout` 區段強調 `$PANTHEON_STATUS_ROOT`，確保與 wakeup prompt 語意完全一致。

### 要點 5：Worker 實際看到的 Render 結果

#### (A) Owner Dispatch (`owned_ready_dispatch`)
```text
你被喚醒了。

你的 auto worker 身分是：Antigravity4。
執行任何狀態命令時，必須用 `AI_NAME=Antigravity4 "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" ...`。`PANTHEON_STATUS_ROOT` 指向 live canonical writer；禁止從隔離 worktree 執行 `scripts/ai-status.sh` 或 `python3 scripts/ai_status.py`，亦禁止直接依賴本機工作樹檔案判斷 repo 現況（若需查證 repo 最新現況與配置，應對 `origin/dev` 查證，例如 `git show origin/dev:<path>`），以免 stale branch code 覆寫 dashboard 與 task truth 或據過期檔案做出錯誤判斷。

請先閱讀這些 task-scoped context 檔案，並以它們作為這次工作的主要上下文：
- AI_COLLABORATION_GUIDE.md
- .orchestrator/task-briefs/odp_wakeup_stale_worktree_guardrail_001.md
- .orchestrator/skills/worker-anchor-commit.md
- .orchestrator/skills/task-closeout-finalization.md
- ai-status.json

不要先掃描 `current-work.md` 或整份 `ai-activity-log.jsonl`。只有在 task brief 明確需要時，才回頭查全域摘要或歷史。

進入 task 工作前，先確認你在正確的 branch 上：
- 預期 branch 名稱：`task/ODP-WAKEUP-STALE-WORKTREE-GUARDRAIL-001`（從 `dev` 開出的 per-task branch；task id kebab: `odp-wakeup-stale-worktree-guardrail-001`）。
- 如果目前 branch 不對，優先使用 `./delivery_toolchain/git/task_start.sh "ODP-WAKEUP-STALE-WORKTREE-GUARDRAIL-001"`，不要手寫臨時 branch 規則。
- 如果 working tree 有未 commit diff 且不屬於這個 task，回報 blocker，不要 stash、不要繼續。
- 任何跨檔案或 routing 接點的 task-owned 改動，到可描述的中間狀態就依 worker-anchor-commit 規則做 anchor commit。
- Anchor commit subject 建議：`ODP-WAKEUP-STALE-WORKTREE-GUARDRAIL-001: anchor <scope>`；commit body 保留必要 trailers。

找出目前分配給你、等待你回應、剛交接給你的 task，或已進入 `review_approved` 並等待你正式收尾為 `done` 的 task，然後直接繼續工作。



狀態更新只能使用 `AI_NAME=Antigravity4 "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" ...`，確保程式與資料都來自 live canonical status root。
不要用臨時 Python/heredoc 直接改 `ai-status.json`、`current-work.md` 或 `ai-activity-log.jsonl`。
你在 background auto worker 裡執行，請不要使用 `git add -p`、`git add -i`、`git commit --interactive`、`git rebase -i` 或其他需要人工輸入的互動式命令。
嚴禁執行 `find /`、`rg /` 等全系統（root-wide）搜尋尋找測試工具或檔案；若工具或依賴缺失，只使用任務 verification 已宣告命令或專案既有 `uv run`（例如 `uv run pytest ...`），不自行掃描主機檔案系統。
測試執行與完成判定規範（僅在任務允許驗證時啟動測試）：
- 完成判定依據：測試完成判定必須以原工具 terminal status 與 exit code，或原背景 job handle 完成收據為準；同一個 shell child 可使用啟動時捕捉的 PID 執行 `wait` 並保存 exit code。
- 嚴禁不安全等待迴圈：禁止用等待 passed 摘要的 grep 迴圈（例如 `until ... grep passed`，在 `pytest -qq` 等無 summary 輸出時會無限等待），亦嚴禁用 `pgrep -f` 命令行片段搭配 `kill -0` 推斷程序存活（極易匹配自身 wait shell 造成無限等待）。合理且單次的 diagnostic grep 或 process 狀態查詢不受此限。
- 區分程序結束與 exit 成功：缺少摘要或測試 count 不代表程序仍在執行；測試結束後讀取既有 log 或 JUnit，不得只為統計 count 重跑測試。
- 退出收據不足處理：退出收據不足或丟失時應回報狀態未知，不得冒充成功，亦不得無限等待。
這次是 owner dispatch。若工作已可送審，程序退出前必須先用 delivery_toolchain/git/task_finalize.sh 推送 task branch、建立 PR 並原子記錄 review submission；不得直接 handoff／re_review 製造沒有遠端 PR 證明的 review。只寫『ready/awaiting review』但不完成正式提交，會被判定為 no-progress failure。若只完成一段增量，至少要留下新的 task branch commit 或實質 next 狀態。


Task ID: ODP-WAKEUP-STALE-WORKTREE-GUARDRAIL-001
原因: owned_ready_dispatch
可能相關檔案:
- .orchestrator/templates/wakeup.txt
- docs/evidence/runtime/ODP-WAKEUP-STALE-WORKTREE-GUARDRAIL-001/
```

#### (B) Reviewer Dispatch (`review_ready_dispatch`)
```text
你被喚醒了。

你的 auto worker 身分是：Claude。
執行任何狀態命令時，必須用 `AI_NAME=Claude "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" ...`。`PANTHEON_STATUS_ROOT` 指向 live canonical writer；禁止從隔離 worktree 執行 `scripts/ai-status.sh` 或 `python3 scripts/ai_status.py`，亦禁止直接依賴本機工作樹檔案判斷 repo 現況（若需查證 repo 最新現況與配置，應對 `origin/dev` 查證，例如 `git show origin/dev:<path>`），以免 stale branch code 覆寫 dashboard 與 task truth 或據過期檔案做出錯誤判斷。

請先閱讀這些 task-scoped context 檔案，並以它們作為這次工作的主要上下文：
- AI_COLLABORATION_GUIDE.md

不要先掃描 `current-work.md` 或整份 `ai-activity-log.jsonl`。只有在 task brief 明確需要時，才回頭查全域摘要或歷史。

進入 task 審查前，先確認你在正確的審查狀態上：
- 預期審查分支或 exact submitted head：`task/ODP-WAKEUP-STALE-WORKTREE-GUARDRAIL-001`（若為 merged PR 則為 exact submitted source 鎖定狀態）。
- 如果目前工作區狀態不對，優先使用 `./delivery_toolchain/git/task_start.sh "ODP-WAKEUP-STALE-WORKTREE-GUARDRAIL-001"` 核對，不要手寫臨時 branch 規則。
- 審查工作區為唯讀核對；如果 working tree 有未 commit diff，回報 blocker，不要 stash、不要續。
- 不得修改程式碼或推送新 commit；審查完成後依審查結果執行 `approve` 或 `reopen`。

找出目前分配給你、等待你回應、剛交接給你的 task，或已進入 `review_approved` 並等待你正式收尾為 `done` 的 task，然後直接繼續工作。



狀態更新只能使用 `AI_NAME=Claude "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" ...`，確保程式與資料都來自 live canonical status root。
不要用臨時 Python/heredoc 直接改 `ai-status.json`、`current-work.md` 或 `ai-activity-log.jsonl`。
你在 background auto worker 裡執行，請不要使用 `git add -p`、`git add -i`、`git commit --interactive`、`git rebase -i` 或其他需要人工輸入的互動式命令。
嚴禁執行 `find /`、`rg /` 等全系統（root-wide）搜尋尋找測試工具或檔案；若工具或依賴缺失，只使用任務 verification 已宣告命令或專案既有 `uv run`（例如 `uv run pytest ...`），不自行掃描主機檔案系統。
測試執行與完成判定規範（僅在任務允許驗證時啟動測試）：
- 完成判定依據：測試完成判定必須以原工具 terminal status 與 exit code，或原背景 job handle 完成收據為準；同一個 shell child 可使用啟動時捕捉的 PID 執行 `wait` 並保存 exit code。
- 嚴禁不安全等待迴圈：禁止用等待 passed 摘要的 grep 迴圈（例如 `until ... grep passed`，在 `pytest -qq` 等無 summary 輸出時會無限等待），亦嚴禁用 `pgrep -f` 命令行片段搭配 `kill -0` 推斷程序存活（極易匹配自身 wait shell 造成無限等待）。合理且單次的 diagnostic grep 或 process 狀態查詢不受此限。
- 區分程序結束與 exit 成功：缺少摘要或測試 count 不代表程序仍在執行；測試結束後讀取既有 log 或 JUnit，不得只為統計 count 重跑測試。
- 退出收據不足處理：退出收據不足或丟失時應回報狀態未知，不得冒充成功，亦不得無限等待。
這次是 reviewer dispatch。程序退出前必須做出可稽核的 review 決定：通過則 approve，發現問題則 reopen／退回 in_progress。只新增 review note、但讓 task 留在 review，會被 Supervisor 判定為 no-progress failure。


Task ID: ODP-WAKEUP-STALE-WORKTREE-GUARDRAIL-001
原因: review_ready_dispatch
可能相關檔案:
- (none inferred)
```

#### (C) Finalize Dispatch (`owned_finalize_dispatch`)
```text
你被喚醒了。

你的 auto worker 身分是：Antigravity4。
執行任何狀態命令時，必須用 `AI_NAME=Antigravity4 "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" ...`。`PANTHEON_STATUS_ROOT` 指向 live canonical writer；禁止從隔離 worktree 執行 `scripts/ai-status.sh` 或 `python3 scripts/ai_status.py`，亦禁止直接依賴本機工作樹檔案判斷 repo 現況（若需查證 repo 最新現況與配置，應對 `origin/dev` 查證，例如 `git show origin/dev:<path>`），以免 stale branch code 覆寫 dashboard 與 task truth 或據過期檔案做出錯誤判斷。

請先閱讀這些 task-scoped context 檔案，並以它們作為這次工作的主要上下文：
- AI_COLLABORATION_GUIDE.md

不要先掃描 `current-work.md` 或整份 `ai-activity-log.jsonl`。只有在 task brief 明確需要時，才回頭查全域摘要或歷史。

這是 reviewer-approved immutable head 的 finalize lane：
- 核准分支是 `task/ODP-WAKEUP-STALE-WORKTREE-GUARDRAIL-001`；只能讀取與核對，不可更新 branch。
- 即使 branch 落後 dev，也不可 merge、rebase、cherry-pick、commit 或 push；merge queue 會在暫存 ref 組合 base。
- working tree 若有 tracked diff，回報 blocker 並停止，不可把它納入已核准交付。
- 禁止執行 pytest、npm test、build、lint、security scan 與 E2E 等測試或驗證命令；僅讀取 exact approved head 的 PR、CI 與 receipt。

找出目前分配給你、等待你回應、剛交接給你的 task，或已進入 `review_approved` 並等待你正式收尾為 `done` 的 task，然後直接繼續工作。

依 `.orchestrator/skills/task-closeout-finalization.md` 的 immutable finalize 流程：僅讀取 exact approved head 的 PR、CI 與 receipt 證據，不得重跑測試；確認 exact approved SHA 的 PR 已 merged，再用 `AI_NAME=Antigravity4 "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" done` 結案。

狀態更新只能使用 `AI_NAME=Antigravity4 "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" ...`，確保程式與資料都來自 live canonical status root。
不要用臨時 Python/heredoc 直接改 `ai-status.json`、`current-work.md` 或 `ai-activity-log.jsonl`。
你在 background auto worker 裡執行，請不要使用 `git add -p`、`git add -i`、`git commit --interactive`、`git rebase -i` 或其他需要人工輸入的互動式命令。
嚴禁執行 `find /`、`rg /` 等全系統（root-wide）搜尋尋找測試工具或檔案；若工具或依賴缺失，只使用任務 verification 已宣告命令或專案既有 `uv run`（例如 `uv run pytest ...`），不自行掃描主機檔案系統。
測試執行與完成判定規範（僅在任務允許驗證時啟動測試）：
- 完成判定依據：測試完成判定必須以原工具 terminal status 與 exit code，或原背景 job handle 完成收據為準；同一個 shell child 可使用啟動時捕捉的 PID 執行 `wait` 並保存 exit code。
- 嚴禁不安全等待迴圈：禁止用等待 passed 摘要的 grep 迴圈（例如 `until ... grep passed`，在 `pytest -qq` 等無 summary 輸出時會無限等待），亦嚴禁用 `pgrep -f` 命令行片段搭配 `kill -0` 推斷程序存活（極易匹配自身 wait shell 造成無限等待）。合理且單次的 diagnostic grep 或 process 狀態查詢不受此限。
- 區分程序結束與 exit 成功：缺少摘要或測試 count 不代表程序仍在執行；測試結束後讀取既有 log 或 JUnit，不得只為統計 count 重跑測試。
- 退出收據不足處理：退出收據不足或丟失時應回報狀態未知，不得冒充成功，亦不得無限等待。
這次是 immutable finalize dispatch。不得修改 tracked files、merge/rebase dev、建立 commit、push branch 或再次執行 task_finalize.sh。明確禁止執行 pytest、npm test、build、lint、security scan 與 E2E 等驗證命令；送審前已完成驗證，finalizer 僅讀取與核對 exact approved head 的 PR、CI 與 receipt。PR 尚未 merge 就保持 review_approved 並退出，merge 後才由 owner 執行 done。


Task ID: ODP-WAKEUP-STALE-WORKTREE-GUARDRAIL-001
原因: owned_finalize_dispatch
可能相關檔案:
- (none inferred)
```

---

## 3. 測試執行紀錄

```bash
PYTHONPATH=.orchestrator:delivery_toolchain:scripts python3 -m unittest discover -s .orchestrator -p 'test_watch_events.py'
```
輸出：
```text
.....
----------------------------------------------------------------------
Ran 5 tests in 0.007s

OK
```

```bash
PYTHONPATH=.orchestrator:delivery_toolchain:scripts python3 -m unittest discover -s .orchestrator -p 'test_supervisor.py' -k test_worker_prompt
```
輸出：
```text
...
----------------------------------------------------------------------
Ran 3 tests in 0.009s

OK
```

```bash
PYTHONPATH=.orchestrator:delivery_toolchain:scripts python3 -m unittest discover -s .orchestrator -p 'test_supervisor.py' -k test_seeded_collaboration_guide
```
輸出：
```text
..
----------------------------------------------------------------------
Ran 2 tests in 0.002s

OK
```

---

## 4. 運行時與主工作目錄無侵入聲明

本任務嚴格限定修改模板與輔助測試，未修改主 checkout 之 git 同步狀態或任何 runtime 狀態檔案。
