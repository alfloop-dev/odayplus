# ODP-ORCH-VERIFICATION-EVIDENCE-001 驗收與證據紀錄

## 任務摘要

- **任務 ID**: ODP-ORCH-VERIFICATION-EVIDENCE-001
- **標題**: 收斂 Worker 驗證證據與重跑控制
- **負責人**: Claude
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
  | `masked_pipeline` | 有 `\|` 但未宣告 `set -o pipefail` |
  | `forced_success` | `\|\| true`、`\|\| :`、`\|\| echo ...`、`\|\| exit 0` |
  | `trailing_command_mask` | runner 之後接 `; <其他命令>`（`; exit $?` 例外放行） |
  | `disabled_errexit` | `set +e` |
  | `backgrounded_command` | runner 被 `&` 丟到背景 |
  | `unparsable_command` / `empty_command` | 無法 tokenize／空字串 |

  刻意**放行**的樣態：`&&` 串接（會短路，失敗仍會浮現）、輸出重導向
  （`> log 2>&1` 不影響 exit code）、runner 之前的 `export X=1;` 前置設定、
  以及引號內的 `\|`（`-k 'alpha\|beta'` 不是 pipeline）。

- **`extract_selection(command)`** — 只採計 runner token **之後**的參數，
  因此 `uv run --with pytest pytest x.py` 讀到的是 `x.py` 而不是 launcher 自己的旗標。
  產生 `{scope, items, fingerprint}`，fingerprint 對順序不敏感。

- **`classify_outcome(exit_code, runner, timed_out)`** — 只有 `0` 是 `passed`。
  signal（負數 return code 或 `128+N`）、timeout、pytest 的 `2`（INTERRUPTED）
  都是 `interrupted`；pytest 的 `5`（NO_TESTS_COLLECTED）是 `no_tests_collected`，
  不是通過也不是失敗。

- **`build_receipt(...)`** — 綁定 head SHA、完整命令、真實 exit code、耗時、測試選擇。
  少任何一項就 `raise`，不產生半份 receipt 讓後人被迫相信。

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

## 驗證證據

`verification_receipt.json` 中的 receipt **由本次新增的機制自己產生**，
不是人工撰寫的摘要；其中同時包含一次被拒絕的重複 baseline 決策、
一次帶明確理由的 retry、以及一份 signal 中斷的 receipt 與其被拒絕的擴大重跑計畫。

receipt 裡的 `head_sha` 是**被量測的那個 commit**，也就是收錄這份 receipt 的 commit 的父節點；
一份 receipt 不可能包含自己所在 commit 的 SHA，硬要對齊只會讓 receipt 說謊。

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
- `.orchestrator/test_verification_evidence_integration.py` — 整合測試，實際起 subprocess：
  真實 exit code 保存、被遮蔽的命令**完全沒有執行**（以 sentinel 檔驗證）、
  SIGTERM 與 timeout 判為 interrupted、真實 pytest 的 `1`／`0`／`5`、
  receipt 經 `common` 寫入與讀回、端到端的重複 baseline 拒絕與明確 retry、
  以及 task brief 兩條渲染路徑的 REJECTED 標記。

其中 `test_piped_failing_suite_would_have_reported_success` 直接示範這道閘要擋的東西：
一個失敗的 pytest 接上 `| tail -1` 之後，shell 回報 `0`。

## 邊界

- 不改動 supervisor 的派工、排程或 worker lifecycle。
- 不自動把政策套用到既有 task 的 `verification` 欄位上做批次改寫；
  現有命令會在 brief 中被標示，由 owner 修正。
