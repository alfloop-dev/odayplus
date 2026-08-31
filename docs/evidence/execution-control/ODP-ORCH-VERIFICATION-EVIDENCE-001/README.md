# ODP-ORCH-VERIFICATION-EVIDENCE-001 驗收與證據紀錄

## 任務摘要

- **任務 ID**: ODP-ORCH-VERIFICATION-EVIDENCE-001
- **標題**: 收斂 Worker 驗證證據與重跑控制
- **負責人**: Antigravity
- **評審人**: Codex2
- **階段**: Wave Auth 0 - Execution Integrity
- **來源文件**: `docs/evidence/execution-control/ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001.md`、`ai-task-archive/tasks/ODP-ORCH-WORKER-ACTIVITY-001.json`

## 問題陳述

驗證結果目前是「宣告」而不是「量測」。三個具體缺口：

1. **exit code 會被 shell 吃掉**。`pytest ... | tee log`、`pytest ... || true`、`pytest ...; echo done` 這三種寫法回報的都不是 pytest 自己的狀態。receipt 上寫 `exit_code: 0`，但那個 0 是 `tee`、`true`、`echo` 的。
2. **receipt 無法回放**。既有的 `verification_receipt.json` 是人工撰寫的摘要，沒有綁定 head SHA、實際耗時、以及究竟選了哪些測試。後續讀者無法判斷「這份綠燈量到的是哪一版、哪一批測試」。
3. **中斷被當成結果**。被 signal 砍掉或 timeout 的執行沒有產生任何量測；把它當成通過是錯的，而「保險起見」自動改跑整個 suite，則會把一次中斷放大成無界的重跑迴圈——正是
   `ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001` 記錄的失效模式。

## 設計原則：不新增 scheduler

本任務**沿用**既有的單一 Supervisor 調度與 `.orchestrator/evidence/` receipt 目錄，
沒有引入任何新的排程器、背景迴圈或平行結果儲存位置。新增的模組只做兩件事：
**審核命令**與**記錄量測**。什麼時候派工、派給誰，仍然完全由既有 supervisor 決定。

## 變更範圍

### 1. 驗證證據政策模組（新增 `.orchestrator/verification_evidence.py`）

單一政策所在地，純標準函式庫、無本地相依，因此 `common.py` 可以單向匯入它。

- **`audit_command(command)`** — 只在「shell 回報的狀態不是 runner 自己的狀態」時拒絕，
  規則刻意收斂：
  | 違規碼 | 觸發樣態 |
  | --- | --- |
  | `masked_pipeline` | 有 `\|` 但在第一個 pipe 之前的 segment 中沒有真正的 `set -o pipefail`（引號內的 `-k 'set -o pipefail'` 不算；pipe 之後的 `set -o pipefail` 也不算） |
  | `forced_success` | `\|\|` 後接任何命令（除 `exit N`（N≠0）與 `exit $?` 外），包含 `\|\| true`、`\|\| python -c 'pass'`、`\|\| cat /dev/null` 等 |
  | `trailing_command_mask` | runner 之後接 `; <其他命令>`（`; exit $?` 例外放行） |
  | `disabled_errexit` | `set +e` |
  | `backgrounded_command` | runner 被 `&` 丟到背景 |
  | `unparsable_command` / `empty_command` | 無法 tokenize／空字串 |

  刻意**放行**的樣態：`&&` 串接（會短路，失敗仍會浮現）、輸出重導向
  （`> log 2>&1` 不影響 exit code）、runner 之前的 `export X=1;` 前置設定、
  以及引號內的 `\|`（`-k 'alpha\|beta'` 不是 pipeline）。

  **第二輪審查阻塞指出：放行重導向只是嘴上放行。** 執行端只在偵測到控制運算子
  （`|`、`&&`、`;`…）時才交給 `bash`，重導向不屬於控制運算子，於是
  `pytest -q > log 2>&1` 被 `shlex.split` 拆成 argv，`>`、`log`、`2>&1`
  三個 token 直接餵給 pytest：檔案從未被寫入，實際跑的命令也不是 receipt 上記的那條。
  現在偵測條件擴及重導向 token（`_is_redirection_token`），有重導向就走 shell；
  同時 `strip_redirections()` 讓重導向的目標路徑不再被誤認為被選中的測試——
  否則 `pytest tests/unit > reports/run.log` 會因為 `reports/run.log` 有斜線
  而進入 selection，讓同一批測試因輸出位置不同而產生兩個 fingerprint，
  直接破壞去重判斷。

- **`extract_selection(command)`** — 只採計 runner token **之後**的參數，
  因此 `uv run --with pytest pytest x.py` 讀到的是 `x.py` 而不是 launcher 自己的旗標。
  產生 `{scope, items, fingerprint}`，fingerprint 對順序不敏感，並排除重導向的目標路徑。

- **`command_key(command)`** — 宣告與 receipt 比對用的正規化鍵，**只**正規化空白。
  `pytest -q tests` 與 `pytest tests` 是不同的鍵：它們選到同一批檔案，
  但不是同一條命令。

- **`classify_outcome(exit_code, runner, timed_out)`** — 只有 `0` 是 `passed`。
  signal（負數 return code 或 `128+N`）、timeout、pytest 的 `2`（INTERRUPTED）
  都是 `interrupted`；pytest 的 `5`（NO_TESTS_COLLECTED）是 `no_tests_collected`，
  不是通過也不是失敗。

- **`build_receipt(...)`** — 綁定 head SHA、完整命令、真實 exit code、耗時、測試選擇。
  少任何一項就 `raise`，不產生半份 receipt 讓後人被迫相信。

- **`validate_receipt(...)`** — **第二輪審查阻塞指出：`command_audit` 不是必填。**
  舊版只在 audit 存在且 `ok` 為 false 時才記問題，因此一份**完全沒有 audit** 的
  receipt 可以通過 `receipt_proves()` 與送審閘；而 audit 的 `ok` 又是 receipt 自報的，
  手寫一份 `{"ok": true}` 就能替被遮蔽的命令背書。
  現在 audit **必填**，且 `ok` 會拿 receipt 記錄的 command **重新推導**：
  自稱乾淨但重跑 audit 過不了、或 audit 記的命令與 receipt 記的命令不同，一律拒絕。

- **`evaluate_baseline_request(...)`** — 同一 head SHA + 同一 selection 已有
  **settled**（passed／failed／no_tests_collected）receipt 時，拒絕重跑，
  除非給出明確 retry reason（≥12 字元且非 `retry`／`flaky`／`n/a` 這類佔位字串）。
  先前若是 **interrupted**，代表根本沒產出 baseline，因此視為 `resume`，不需要理由。

- **`plan_rerun(...)`** — 中斷後唯一站得住腳的下一步是「用同一組 selection 再跑一次」。
  要求擴大範圍（targeted → suite，或 selection 的超集）會被拒絕並標記 `escalated`。

- **`run_verification_command(...)`** — 審核不過的命令**完全不執行**：跑了也只會得到
  一個與 receipt 宣稱意義不符的數字。找不到執行檔時回報 `127`（shell 慣例），
  而不是 `None`，以免被歸類成可以 resume 的中斷。

### 2. Receipt 落地與 worker prompt 驗證（`.orchestrator/common.py`）

- `write_verification_receipt(config, receipt=...)` 寫入既有的
  `evidence_dir(config)`（即 `.orchestrator/evidence/`，已 gitignore），
  與 `write_failure_evidence` / `write_approval_evidence` 並列；
  **驗證不過的 receipt 直接 `raise`，不落地。**
- `load_verification_receipts(config, task_id=...)` 依 task 讀回，供去重判斷使用。
- Task brief 的 `## Verification` 區段改為經過審核後渲染：被拒絕的命令標成
  `REJECTED (<違規碼>): <說明>`，並附上 `### Verification Evidence Policy` 區塊，
  把 receipt 契約直接寫進 worker 的 prompt。

### 3. Fallback worker brief（`.orchestrator/worker_workspace.py`）

`_generated_worker_task_brief` 的 fallback 路徑套用相同標記，
避免兩條 prompt 產生路徑對同一份命令給出不同說法。

### 4. 送審 fail-closed 閘（`delivery_toolchain/git/task_verification.py` + `task_finalize.sh`）

**第一輪審查阻塞指出：只有 brief 提示、沒有 caller，worker 仍可不產生 receipt 就發布。**
這是對的——政策模組若沒有接上任何閘門，它就只是一份建議。因此本輪把它接到
**既有的** `task_finalize.sh` preflight 上（該處已有 boundary inventory 與 ruff 兩道閘），
不新增 scheduler、不新增第二個狀態檔、不開平行流程。

`delivery_toolchain/git/task_verification.py` 是單一受控入口，兩個子命令共用同一個
receipt store（`.orchestrator/evidence/`，即既有 supervisor evidence 目錄）：

| 子命令 | 行為 |
| --- | --- |
| `run` | 執行 task `verification` 欄位宣告的命令，每條產出一份綁定 head SHA／命令／真實 exit code／耗時／selection 的 receipt；審核不過的命令不執行也不落 receipt；同 SHA 重複需 `--retry-reason` |
| `check` | 對每條宣告命令，要求當前 head SHA 上存在「有效且 exit 0」的 receipt，否則拒絕發布 |

`task_finalize.sh` 在 boundary／ruff preflight 之後呼叫 `check`。fail-closed 的具體範圍：

- 宣告了命令但**沒有 receipt** → 拒絕。
- receipt 屬於**別的 head SHA**、**別的 selection**、或**別的命令** → 不算數，拒絕。
- receipt 是 `rejected`／非零 exit／`interrupted`／`no_tests_collected` → 拒絕。
- 宣告的命令本身**過不了遮蔽審核** → 拒絕（它根本不該被執行）。
- **讀不到 status file** → 拒絕；無法判斷是否有宣告時，不能當作沒有宣告。
- 沒有宣告 verification 命令、且沒有被標記 `verification_required` 的 task → 放行。
  **義務跟著宣告走，不是跟著 task 走**；否則這道閘會擋住所有既有 task，
  變成必須繞過的閘，那就等於沒有閘。
- 被標記 `verification_required` 卻**什麼都沒宣告** → 拒絕（見下）。

#### 第二輪審查阻塞：selection 不等於命令

`evaluate_finalize_gate` 原本只用 head SHA + selection fingerprint 找 receipt。
但 fingerprint 只描述「選到哪些測試檔」，不描述**怎麼跑**：
`pytest -q tests/unit` 與 `pytest tests/unit` 的 fingerprint 相同，
於是前者的綠燈 receipt 會被當成後者的證明；`-x`、`-p no:randomly`、
`--maxfail=1` 這類會改變實際執行內容的旗標同樣被抹平。
現在 `matching_receipts(...)` 多了一個 `command` 參數，送審閘**一定**帶上它：
只有跑過**那一條**命令的 receipt 才算數。錯過時的訊息會直接列出
「這個 head 上選到同一批測試、但實際跑的是哪幾條命令」，
因為那正是讀者最容易誤當成證據的東西。

去重判斷（`evaluate_baseline_request`）**刻意不帶** `command`：
兩條選到同一批測試的命令，對「這個 SHA 已經量過了嗎」而言是同一次量測，
不該因為多打一個 `-q` 就換來一次免費的 baseline。
**證明要精確，去重要寬鬆**，兩者的鍵本來就不該相同。

#### 第二輪審查阻塞：宣告義務是死條文

上一輪補上的 `declaration_requirement()` 沒有任何 caller：
一個被標記 `verification_required=true`、卻沒有宣告任何命令的 implementation task，
照樣通過 `task_verification check`。標記等於一份沒有人讀的欄位。
現在 `cmd_check` 會讀它並交給閘門判斷——**沒宣告本身就是違規**，直接拒絕送審。
另一個同樣沒有 caller 的 `task_class_requires_declaration()` 則**移除**：
目前沒有任何路徑會依 task class 蓋這個標記，留著只是第二條死政策。
標記由看板負責寫入，閘門負責執行；缺標記的舊 task 仍視為 legacy，不追溯課責。

`receipt_proves()` 不信任 receipt 自報的 `passed` 旗標：它會重跑 `validate_receipt()`
並要求 `exit_code == 0`，所以把 `passed` 手動改成 `true` 不會讓閘門放行。
receipt 另帶 `produced_by` 標記產生者。誠實地說：receipt store 是 gitignore 的本機目錄，
這個標記是可追溯性用的，不是防偽邊界；要真正防偽需要簽章，超出本 task 範圍。

### 5. Materialized context 不再被誤判成 worker dirt（`.orchestrator/worker_workspace.py`）

送審閘只有在 task 送得出去的時候才有意義。實測到的阻塞：
`worker_workspace` 會把 task 的 `source_docs`（本 task 是
`ai-task-archive/tasks/ODP-ORCH-WORKER-ACTIVITY-001.json`）materialize 進隔離 worktree，
逐 byte 比對 SHA256 後記進 `materialized_source_manifest`——
但寫進 `.git/info/exclude` 的是一份**固定清單**，只涵蓋
`ai-status.json`、`current-work.md`、`.orchestrator/task-briefs/` 這些每個 worker 都拿得到的
canonical reference。`source_docs` 落在哪裡由看板決定，不在那份清單裡，
於是 Supervisor 自己寫進去的檔案成了 untracked dirt，
`task_finalize` 的 worktree cleanliness 檢查 fail closed，**task 永遠送不出去**。

修法是把 manifest 裡**不在已涵蓋前綴清單內**的路徑，各寫成一條 root-anchored、
glob 字元已跳脫的 exclude（例如 `/ai-task-archive/tasks/ODP-ORCH-WORKER-ACTIVITY-001.json`）。
`.orchestrator/` 底下的路徑已被前綴清單覆蓋或 gitignore 處理，
不再額外加入 exclude——否則會隱藏 workspace 回歸測試依賴的 skill 檔案。刻意不做的事：

- **不忽略整個目錄。** `ai-task-archive/` 這種掃除會連 worker 自己在同目錄下
  建立的檔案一起藏起來，那是必須被報出來的 dirt。
- **不忽略未知的 untracked 檔。** 只有通過 hash 驗證、確定就是 Supervisor 那份 seed
  的路徑才進 exclude；沒進 manifest 的東西一律照舊視為 dirt。
- **不忽略 `.orchestrator/` 底下的 manifest 路徑。** 那些路徑已經由
  前綴清單或 gitignore 處理，重複 exclude 會隱藏 regression test 所依賴的
  untracked skill 檔案（`UnversionedOrchestratorWorkspaceLeaseTests` CI regression）。

## 驗證證據

`verification_receipt.json` 中的 receipt **由本次新增的機制自己產生**，
不是人工撰寫的摘要；其中同時包含一次被拒絕的重複 baseline 決策、
一次帶明確理由的 retry、一份 signal 中斷的 receipt 與其被拒絕的擴大重跑計畫，
以及送審閘在各種偽造樣態下的判定。

receipt 裡的 `head_sha` 是**被量測的那個 commit**，也就是收錄這份 receipt 的 commit 的父節點；
一份 receipt 不可能包含自己所在 commit 的 SHA，硬要對齊只會讓 receipt 說謊。

### 第二輪審查阻塞：provenance 是宣稱而不是檢查

上一版 committed 的 receipt 記著 `head_sha = 739fd43f`，而那顆 commit
**不是 PR head 的祖先**——分支中途被重建，舊 SHA 成了孤兒，
README 上「父節點」這句話因此變成假的。真正的問題不是 SHA 填錯，
而是**bundle 是人工手動驅動模組產生的，沒有任何東西能偵測漂移**。

因此這一輪把產生器本身 commit 進來：
`generate_receipt_bundle.py` 與它的輸出放在一起。它會

1. 先要求 worktree 乾淨，否則 `head_sha` 會指向一棵沒有任何 commit 收錄的樹；
2. 取當前 HEAD 當 `head_sha`，所有 receipt 都經
   `verify_and_build_receipt()`（與 `task_verification.py run` 同一個入口）產生；
3. bundle 在**下一顆 commit** 落地，所以 `head_sha` 就是那顆 commit 的父節點。

任何讀者都可以自己重跑並 diff，並用一行指令驗證祖先關係：

```bash
git merge-base --is-ancestor \
  "$(python3 -c 'import json; print(json.load(open("docs/evidence/execution-control/ODP-ORCH-VERIFICATION-EVIDENCE-001/verification_receipt.json"))["head_sha"])')" \
  HEAD && echo "receipt head is an ancestor of HEAD"
```

`bundle` 另含 `redirection_execution` 區塊，記錄重導向確實被 shell 執行
（`ran_under_a_shell: true`、目標檔案有被寫入），
以及 `finalize_gate` 對「缺 audit」「偽造乾淨 audit」「同 selection 不同命令」
「被標記必填卻沒宣告」四種樣態的拒絕判定。

### 全套件回歸基準

`.orchestrator` 全套件在本分支與 `origin/dev` 上以相同指令執行，兩邊都是同樣的 17 個
失敗，全部來自本地 worktree 缺少（gitignore 的）`.orchestrator/config.json` 而拋出的
`ConfigError`，與本次變更無關：

```bash
uv run --no-project --python 3.12 --with pytest --with jsonschema --with pyyaml \
  --with cryptography pytest .orchestrator -q
```

## 回歸測試

- `.orchestrator/test_verification_evidence.py` — 單元測試，涵蓋命令審核（含刻意放行的樣態）、
  selection 指紋、exit code 分類、receipt 必填欄位、重複 baseline 去重、重跑範圍控制。
- `delivery_toolchain/git/test_task_verification.py` — 閘門回歸測試，對一個拋棄式 git repo
  以 subprocess 驅動 CLI（`task_finalize.sh` 就是這樣呼叫它）：無 receipt、跨 SHA receipt、
  非零 exit、interrupted、被遮蔽的宣告命令、讀不到 status file 一律不可送審；
  `run` 產生 receipt 後閘門才放行；同 SHA 重跑需明示 retry reason（`flaky` 這類佔位字串不算）；
  被遮蔽的命令不會被執行（sentinel 檔驗證）也不會落 receipt。
- `.orchestrator/test_verification_evidence_integration.py` — 整合測試，實際起 subprocess：
  真實 exit code 保存、被遮蔽的命令**完全沒有執行**（以 sentinel 檔驗證）、
  SIGTERM 與 timeout 判為 interrupted、真實 pytest 的 `1`／`0`／`5`、
  receipt 經 `common` 寫入與讀回、端到端的重複 baseline 拒絕與明確 retry、
  以及 task brief 兩條渲染路徑的 REJECTED 標記。
- `.orchestrator/test_task_brief_source_docs.py::MaterializedContextExcludeTests` —
  materialize 後的 worktree 必須是乾淨的、exclude 寫的是精確路徑而非目錄掃除、
  沒被 materialize 的 untracked 檔仍然算 dirt、重複派工不會累積重複條目。

### 第二輪審查的四道修補各自帶回歸測試

| 缺口 | 測試 |
| --- | --- |
| selection 相同但命令不同也算證明 | `test_receipt_for_a_different_command_over_the_same_tests_does_not_count`、`test_extra_flags_on_the_receipt_do_not_prove_the_declaration`、CLI 版 `..._cannot_finalize` |
| receipt 可以沒有 `command_audit` | `test_validate_receipt_requires_a_command_audit`、`test_forged_clean_audit_on_a_masked_command_is_rejected`、CLI 版 `test_receipt_without_a_command_audit_cannot_finalize` |
| 宣告義務沒有 caller | `test_required_declaration_with_no_commands_fails_closed`、CLI 版 `test_task_marked_verification_required_must_declare_something` |
| 重導向沒有真的重導向 | `test_redirection_actually_redirects`、`test_command_without_shell_metacharacters_stays_on_argv`、`test_redirect_target_is_not_a_selected_test` |

每一項都先在未修補的程式碼上確認**會紅**，再確認修補後轉綠。

### 第三輪修補（Antigravity 重新實作）帶回歸測試

| 缺口 | 測試 |
| --- | --- |
| `\|\|` 後接任意命令仍可通過 audit | `test_or_python_pass_is_rejected`、`test_or_cat_devnull_is_rejected`、`test_or_arbitrary_command_after_runner_is_rejected`；反例 `test_or_exit_dollar_question_is_allowed`、`test_or_exit_nonzero_preserves_failure`、`test_or_before_runner_is_allowed` |
| pipefail regex 被引號內文字假授權 | `test_pipefail_in_k_expression_is_not_honoured` |
| pipefail 出現在 pipe 之後仍被視為有效 | `test_pipefail_after_pipe_is_not_honoured`；反例 `test_pipefail_before_pipe_is_honoured` |
| `.git/info/exclude` 掃到 `.orchestrator/skills/*` 破壞 `UnversionedOrchestratorWorkspaceLeaseTests` | `test_repeated_dispatch_never_accumulates_a_materialized_context_block`（5/5 通過） |

其中 `test_piped_failing_suite_would_have_reported_success` 直接示範這道閘要擋的東西：
一個失敗的 pytest 接上 `| tail -1` 之後，shell 回報 `0`。

## 邊界

- 不改動 supervisor 的派工、排程或 worker lifecycle；沒有新增 scheduler、
  第二個狀態檔或平行流程。
- 不自動把政策套用到既有 task 的 `verification` 欄位上做批次改寫；
  現有命令會在 brief 中被標示、在送審時被擋，由 owner 修正。
- receipt store 的防偽不在本 task 範圍內（見上）。
- `verification_required` 標記由看板寫入；本 task 只讓閘門讀它並執行，
  不自行決定哪些 task 該被標記，也不批次回填既有 task。
- materialized context 的 exclude 只涵蓋**已 hash 驗證且記入 manifest** 的路徑，
  以精確的 root-anchored pattern 寫入 `.git/info/exclude`。
  不做 `ai-task-archive/` 這類目錄掃除：worker 在同一個目錄下自己建立的檔案
  仍然必須被當成 dirt 報出來。
