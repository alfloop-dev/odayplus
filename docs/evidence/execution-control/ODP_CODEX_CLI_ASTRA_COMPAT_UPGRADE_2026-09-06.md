---
evidence_id: ODP-CODEX-CLI-ASTRA-COMPAT-UPGRADE-001
title: "升級既有 Codex CLI 以恢復 Astra ultra 背景審查"
date: 2026-09-06
status: IMPLEMENTED
owner: Claude
reviewer: Codex
repository: alfloop-dev/odayplus
task: ODP-CODEX-CLI-ASTRA-COMPAT-UPGRADE-001
base_ref: ef76cf6d
last_updated: 2026-09-09
---

# 升級既有 Codex CLI 以恢復 Astra ultra 背景審查

## 1. 問題與授權邊界

背景 Codex review 被 OpenAI backend 以 HTTP 400 拒絕，原因是既有 Codex CLI 版本過舊，不支援 `gpt-6-astra`。本次工作只做一件事：**把既有官方 Codex CLI 套件升級到官方 stable pinned 版本**，讓原本就已設定好的 `gpt-6-astra` / `ultra` 背景審查恢復可用。

### 1.1 授權範圍內

- 升級既有官方 npm 全域套件 `@openai/codex` 與其必要平台依賴。
- 沿用既有解析路徑 `/usr/bin/codex`，不新增 wrapper、不新增第二個 dispatch 入口。

### 1.2 明確未做（禁止事項）

- 未執行 apt 全系統 upgrade，未執行任何不明 curl 安裝腳本。
- 未變更任何 SDK 或產品依賴。
- 未變更個人 Codex config、auth token、sandbox 或 approval 模式。
- 未變更其他 provider（Claude／Antigravity）的任何設定。
- 未新建服務、排程或 CLI wrapper；未為本次升級重啟 Supervisor。
- 未清 cooldown／lease／失敗計數，未重寫任何歷史，未手改 canonical JSON／state／queue。
- 未降級模型或 reasoning effort（不退回 Luna／xhigh／max），未偽造 user-agent 或 version header。
- 未建立假 task 或 API probe 以取得「成功」證據。

---

## 2. 失敗事實（升級前）

真實背景 review run 的 terminal 收據，取自 canonical runtime state 與 runner status 檔：

| 項目 | 數值 |
| --- | --- |
| Run ID | `codex-20260906T060804Z-2ab90176` |
| Provider / 邏輯 agent | `codex` / `Codex2` |
| Dispatch slot | `codex_lupin_slot_1`（quota group `codex_lupin`） |
| Task | `ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001`（PR #1223 的 review） |
| 啟動 | `2026-09-06T06:08:04Z` |
| 結束 | `2026-09-06T06:08:11Z` |
| Terminal status | `failed` |
| `exit_code` / `signal` | `1` / `null` |
| PID / child PID | `1697082` / `1697084` |

Runner 實際 argv 已確認**模型與 effort 傳遞正確**：
`codex exec -C <isolated worktree> -c ask_for_approval="never" -s workspace-write --skip-git-repo-check -c model_reasoning_effort="ultra" --model gpt-6-astra --dangerously-bypass-approvals-and-sandbox <wakeup>`

Run log（runtime `64f3b239` 的 `.orchestrator/logs/20260906T060804653019Z-codex-codex_lupin_slot_1-187c5b.log`）第 69–70 行為 backend 拒絕：

```
ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error",
"message":"The 'gpt-6-astra' model requires a newer version of Codex. Please upgrade to the latest app or CLI and try again."}}
```

同一份 log 另有 CLI 端的先行徵兆：`Unknown model gpt-6-astra is used. This will use fallback model metadata.`（`codex_models_manager::model_info`）。

**判定**：argv 正確不等於 backend 接受。這不是 quota，不是模型拼字錯誤，而是 CLI 版本相容性問題；升級前 `/usr/bin/codex` 為 `codex-cli 0.147.0`。

### 2.1 升級前的行程安全確認（唯讀）

- PID `1697082` 與 `1697084` 已不存在（`ps` 查無），失敗 run 確為 terminal，非仍在執行。
- 主機上唯二仍在執行的 `codex` 行程屬於 VS Code 擴充套件自帶的 app-server（`~/.vscode-server/extensions/openai.chatgpt-*/bin/linux-x86_64/codex`，版本 `0.153.0`），與 orchestrator 使用的 binary 不同、且不在 orchestrator 的解析路徑上。**未終止任何既有行程。**

---

## 3. 升級內容：舊／新版本、路徑與套件 pin

### 3.1 升級前後對照

| 項目 | 升級前 | 升級後 |
| --- | --- | --- |
| npm 全域套件版本 | `@openai/codex@0.147.0` | `@openai/codex@0.153.4` |
| `codex --version` | `codex-cli 0.147.0` | `codex-cli 0.153.4` |
| 平台依賴 | `@openai/codex@0.147.0-linux-x64` | `@openai/codex@0.153.4-linux-x64` |
| `/usr/bin/codex` | → `../lib/node_modules/@openai/codex/bin/codex.js` | **不變**（同一 symlink、同一目標） |
| npm prefix | `/usr` | **不變** |

路徑與 symlink 完全不變，因此 `providers.codex.codex.cli` 維持 `"codex"`，**本次不需要也未修改 orchestrator config 的 cli 欄位**，亦不需要重啟 Supervisor。

### 3.2 官方來源與 pin

- 來源：官方 npm registry（`https://registry.npmjs.org`），官方套件 `@openai/codex`，安裝升級路徑依官方 Codex CLI 文件（`https://learn.chatgpt.com/docs/codex/cli`）。
- 本輪 registry `latest` = `0.153.4`（官方 stable，**非 alpha**），與本次 pin 的版本一致。
- 安裝命令（pin 到確切版本，非 range）：

```bash
sudo -n npm install -g --no-fund --no-audit @openai/codex@0.153.4
```

- 結果：`changed 2 packages in 11s`，exit code `0`。
- 套件完整性（registry 記錄的 `dist.integrity`）：
  - `0.153.4`：`sha512-wbHDmit7S/YvBGVX1DQmk13xtWblZ2cApeJ/pB7xDZ10Cna+DZc5ij7f0F4OxdsXN4FW1oLT48OpogUI1+8Y2w==`
  - `0.147.0`（rollback 目標）：`sha512-EQLEXecAG2ptxI7UpBMo2TR/ga5596/c/OsYF/0LoUDh5JANZ7IoGqlzBEWbuEVQ76JePIbtTW/ihCkp1a7Z3w==`

只安裝了此官方套件與其必要的 `linux-x64` 平台依賴（`changed 2 packages`），沒有其他系統套件變動。

---

## 4. Readback：實際背景 worker 會用的 binary

### 4.1 版本與解析路徑

```
/usr/bin/codex -> ../lib/node_modules/@openai/codex/bin/codex.js
readlink -f /usr/bin/codex          = /usr/lib/node_modules/@openai/codex/bin/codex.js
package.json version                 = 0.153.4
npm ls -g --depth=0                  = @openai/codex@0.153.4
codex --version                      = codex-cli 0.153.4
/usr/bin/codex --version             = codex-cli 0.153.4
平台套件 package.json                 = @openai/codex 0.153.4-linux-x64
```

### 4.2 兩個 pool、所有 physical slot 一致

以 orchestrator 自己的解析程式（`provider_runtime.provider_key` / `provider_section` 搭配 `common.command_exists`，讀取 live config）逐一核對：

| Agent / Slot | Account pool | provider | `cli` | 解析結果 | model | effort |
| --- | --- | --- | --- | --- | --- | --- |
| `codex` | `codex_bjoe` | `codex` | `"codex"` | `/usr/bin/codex` | `gpt-6-astra` | `ultra` |
| `codex2` | `codex_lupin` | `codex` | `"codex"` | `/usr/bin/codex` | `gpt-6-astra` | `ultra` |
| `codex_bjoe_slot_1` | `codex_bjoe` | `codex` | `"codex"` | `/usr/bin/codex` | `gpt-6-astra` | `ultra` |
| `codex_bjoe_slot_2` | `codex_bjoe` | `codex` | `"codex"` | `/usr/bin/codex` | `gpt-6-astra` | `ultra` |
| `codex_lupin_slot_1` | `codex_lupin` | `codex` | `"codex"` | `/usr/bin/codex` | `gpt-6-astra` | `ultra` |
| `codex_lupin_slot_2` | `codex_lupin` | `codex` | `"codex"` | `/usr/bin/codex` | `gpt-6-astra` | `ultra` |

四個 physical slot 與兩個邏輯 agent 全部指向同一個已升級的 binary。

### 4.3 以 Supervisor 行程的實際 PATH 核對

背景 worker 由 Supervisor 行程 spawn 並繼承其環境，因此以該行程的實際 `PATH` 解析（Supervisor PID `1692417`，未重啟）：

```
env -i PATH="<supervisor PATH>" sh -c 'command -v codex'   -> /usr/bin/codex
env -i PATH="<supervisor PATH>" sh -c 'codex --version'    -> codex-cli 0.153.4
```

該 `PATH` 中位於 `/usr/bin` 之前的 `~/.codex/tmp/arg0/...` 目錄**不含** `codex` 項目（僅 `apply_patch`、`applypatch`、`codex-execve-wrapper`、`codex-linux-sandbox`）；VS Code 擴充套件目錄位於 `/usr/bin` 之後，因此不會遮蔽。解析結果確定為已升級的 `/usr/bin/codex`。

---

## 5. 未變動欄位（明確清單）

live config `.orchestrator/config.json` 本次**完全未修改**。以下經 readback 確認維持原值：

| 欄位 | 值 |
| --- | --- |
| `providers.codex.codex.model` | `gpt-6-astra` |
| `providers.codex.codex.model_reasoning_effort` | `ultra` |
| `providers.codex.codex.cli` | `"codex"` |
| `providers.codex.codex.ask_for_approval` / `sandbox_mode` / `dangerously_bypass` | `never` / `workspace-write` / `true` |
| `ready_dispatcher.role_provider_policy` | 不變（reviewer→`codex`；owner/helper→`antigravity`,`claude`） |
| `account_pools.codex_bjoe.max_concurrent` | `2` |
| `account_pools.codex_lupin.max_concurrent` | `2` |
| `account_pools.antigravity_main.max_concurrent` | `5` |
| `account_pools.claude_main.max_concurrent` | `5` |
| `supervisor.poll_interval_seconds` | `180.0` |

Codex 雙 pool 四 slots、Agy 5、Claude 5 與 quota 皆未變動。

---

## 6. Rollback 邊界

- **回滾方式**：官方 registry 仍提供 `0.147.0`，回滾即為以同一官方路徑重新 pin 安裝：
  `sudo npm install -g @openai/codex@0.147.0`，並以 §4 相同的 readback 確認。
- **回滾範圍**：僅此一個全域 npm 套件。由於 symlink、npm prefix 與 orchestrator config 皆未變動，回滾不需要改動任何 config、launcher 或 link，也不需要重啟 Supervisor。
- **未保留私有備份於版本庫**：本 PR 只 commit 這份 receipt，未 commit 任何全域 CLI／package／auth／config 的私有備份。回滾依據為上方記錄的官方版本號與 `dist.integrity`。
- **失敗處置**：若升級後 backend 仍拒絕，維持 Codex review hold，不靜默降模型或降 effort。
- **回滾邊界的明確排除**：本節的「回滾」只指 npm 套件版本 pin（`0.153.4` ⇄ `0.147.0`）。退回 Luna、移除／降低 `model_reasoning_effort=ultra`、還原舊 `role_provider_policy` 或還原舊 config **不在本次回滾範圍內**，且會違反現行模型與角色政策；CLI 不相容的正解是修 CLI 版本，任何模型／effort／角色政策降級都需要另有明確授權。

---

## 7. Backend 接受度驗證與 hold 解除（現況如實記錄）

### 7.1 驗證標準

僅 `codex --version` 或 features list 成功**不足以結案**。結案需要一次**正常的** Codex review 派工實際進入模型工作（不再是 HTTP 400 拒絕），且不得以假 task 或 API probe 取得。

### 7.2 backend 成功證據：已取得（正常 review 派工，terminal exit 0）

本 receipt 的 PR #1224 submission 觸發了本任務 reviewer `Codex` 的**正常** review 派工（dispatch 原因 `review_ready_dispatch`），該 run 使用的正是 §4 核對過的 `/usr/bin/codex` 0.153.4 與 `gpt-6-astra` / `ultra`。**這不是假 task，也不是 API probe**，而是本任務自己的正式審查工作。

| 項目 | 數值 |
| --- | --- |
| Run ID | `codex-20260906T062839Z-f98b97cc` |
| Provider / 邏輯 agent | `codex` / `Codex` |
| Dispatch slot | `codex_bjoe_slot_1`（quota group `codex_bjoe`） |
| Dispatch 原因 | `review_ready_dispatch`（PR #1224 的正常 review） |
| 啟動 | `2026-09-06T06:28:39Z` |
| 結束 | `2026-09-06T06:31:04Z` |
| Terminal status | `completed` |
| `exit_code` / `signal` | `0` / `null` |
| `progress_outcome` | `review_decided` |
| PID / child PID | `1717437` / `1717438` |

Terminal 依據為 runner 自己的收據 `.orchestrator/worker-runtime/status/codex-20260906T062839Z-f98b97cc.json`（`status=completed`、`finished_at=2026-09-06T06:31:04Z`、`exit_code=0`、`signal=null`），與 canonical `state.json` 的 worker 記錄一致；非以摘要或行為推論。

Run log（runtime `64f3b2399442` 的 `.orchestrator/logs/20260906T062839303947Z-codex-codex_bjoe_slot_1-e1ff05.log`）第 5–12 行為 session header，直接顯示 CLI 版本與生效的模型／effort：

```
OpenAI Codex v0.153.4
--------
workdir: /tmp/pantheon-worker-worktrees/pantheon/odp-codex-cli-astra-compat-upgrade-001
model: gpt-6-astra
provider: openai
approval: never
sandbox: danger-full-access
reasoning effort: ultra
```

同一份 log 第 61 行起為**模型實際回應**（reviewer 的中文工作計畫），第 63–70 行起為模型發起並 `succeeded` 的工具 `exec` 呼叫，代表已完整進入模型工作而非在請求階段被拒。

與 §2 的對照（同一 CLI，不同版本）：

| 檢查點 | 升級前 run `…T060804Z-2ab90176` | 升級後 run `…T062839Z-f98b97cc` |
| --- | --- | --- |
| CLI 版本 | `0.147.0` | `0.153.4` |
| Quota group / slot | `codex_lupin` / `codex_lupin_slot_1` | `codex_bjoe` / `codex_bjoe_slot_1` |
| `Unknown model gpt-6-astra … fallback` 警告 | 有 | **無** |
| backend HTTP 400 `invalid_request_error` | 有（log 69–70） | **無** |
| Terminal | `failed` / exit `1` | `completed` / exit `0` |

log 全長 5040 行，全文僅第 150 與 4574 行出現 `invalid_request_error` 字串，且兩處都是 reviewer 讀取 task brief 與 `ai-status.json` 時回顯的**本任務 acceptance 文字**，不是本次 run 的 backend 錯誤。

**判定**：acceptance「需要一次正常 Codex review 成功進入模型工作（非 HTTP 400 拒絕）」已滿足。兩個 pool 各有一次真實 run 佐證：`codex_lupin` 在 0.147.0 被 backend 拒絕、`codex_bjoe` 在 0.153.4 正常完成。該 run 的 review 決定是把 PR #1224 reopen 退回本任務 owner，本 receipt 的 §5 L159 欄位更正與本節證據補入即為回應該次退回。

### 7.2.1 第二次真實成功：**原失敗 slot** 於 2026-09-08 的正常 review run

§7.2 的成功 run 落在 `codex_bjoe` pool；§2 的失敗 run 落在 `codex_lupin` pool。2026-09-08 hold 解除後（§7.3），
`codex_lupin_slot_1`——**與 §2 失敗 run 完全同一個 physical slot**——執行了一次正常 review 派工並正常結束，
補上了同一 slot 的升級前後對照：

| 項目 | 數值 |
| --- | --- |
| Run ID | `codex-20260908T220840Z-e9177416` |
| Provider / 邏輯 agent | `codex` / `Codex2` |
| Dispatch slot | `codex_lupin_slot_1`（quota group `codex_lupin`） |
| Task | `ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001`（PR #1223 的 review） |
| Dispatch 原因 | `review_ready_dispatch`（queue event `evt-20260908T220837Z-57f61b6e`） |
| 啟動 | `2026-09-08T22:08:40Z` |
| 結束 | `2026-09-08T22:11:06Z` |
| Terminal status | `completed` |
| `exit_code` / `signal` | `0` / `null` |
| `progress_outcome` | `review_decided` |
| PID / child PID | `1288635` / `1288636` |

Terminal 依據為 runner 自己的收據 `.orchestrator/worker-runtime/status/codex-20260908T220840Z-e9177416.json`
（`status=completed`、`finished_at=2026-09-08T22:11:06Z`、`exit_code=0`、`signal=null`），與 canonical
`state.json` 的 `workers` 記錄一致（`runner_status=completed`、`progress_outcome=review_decided`）。

Run log（`.orchestrator/logs/20260908T220840872968Z-codex-codex_lupin_slot_1-f3c449.log`，全長 3880 行）
第 7–14 行 session header：

```
OpenAI Codex v0.153.4
--------
workdir: /tmp/pantheon-worker-worktrees/pantheon/odp-role-provider-codex-live-rollout-001
model: gpt-6-astra
provider: openai
approval: never
sandbox: danger-full-access
reasoning effort: ultra
```

全文 `grep -n 'invalid_request_error'` 與 `grep -n 'fallback model metadata'` 皆 **0 命中**——
與 §2 同一個 slot 在 0.147.0 時兩者皆有的情況正好相反。該 run 的產出是 Codex2 於 `2026-09-08T22:10:41Z`
對 PR #1223 做出的獨立 review 決定（canonical 事件 `reopen`、`reason=review_finding`、
`category=substantive_review`），屬正常審查工作，非假 task、非 API probe。

同一 slot 的升級前後對照：

| 檢查點 | `codex_lupin_slot_1` @ 0.147.0（`…T060804Z-2ab90176`） | `codex_lupin_slot_1` @ 0.153.4（`…T220840Z-e9177416`） |
| --- | --- | --- |
| `Unknown model gpt-6-astra … fallback` 警告 | 有 | **無** |
| backend HTTP 400 `invalid_request_error` | 有 | **無** |
| Terminal | `failed` / exit `1` | `completed` / exit `0` |
| 產出 | 無（請求階段被拒） | 完整 review 決定（`review_decided`） |

至此兩個 quota pool 的 physical slot 各自都有一次 0.153.4 上的真實成功 run。

### 7.3 `ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001` hold 解除：已解除（本輪 readback 確認）

本輪（`2026-09-09T00:07Z`）直接從 live canonical `ai-status.json` 重新讀取該任務狀態，**未引用前一輪結論、
也未採信 reopen note 的轉述**：

| 欄位 | 2026-09-06 hold 當時 | 2026-09-09 本輪 readback |
| --- | --- | --- |
| `non_dispatchable` | `true` | **`false`** |
| `status` | `review` | `in_progress` |
| `owner` / `reviewer` | `Antigravity` / `Codex2` | `Antigravity` / `Codex2`（不變） |
| `pr_number` | `1223` | `1223`（不變） |
| `review_submission.remote_sha` | `03ef1d0151ac50ba928c6e7df796404985df4684` | 同值（不變） |
| `last_update` | `2026-09-06T06:17:16Z` | `2026-09-08T22:10:41Z` |

**解除動作的 canonical 事件**：`ai-activity-log.jsonl` 中該 task 的 `assign` 事件僅一筆——
`2026-09-08T22:08:24Z`、`agent=Codex`、`Assigned ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001 to Antigravity
with reviewer Codex2`，即前一版 §7.3 所列的同一條 canonical 路徑（`assign` + `non_dispatchable=false`
metadata、原 owner／reviewer 不變），非手改 canonical JSON／state／queue。

**解除是實際生效的，不只是欄位翻轉**：同一分鐘內 `2026-09-08T22:08:37Z wake_queued` →
`22:08:40Z worker_worktree_allocated` → `22:08:41Z worker_started`（`review_ready_dispatch`），
產生的正是 §7.2.1 那個 run。`non_dispatchable=true` 時這串派工不會發生。

**未被其他 actor 核准或重交**：該 task 全部 canonical 活動事件依型別統計為
`assign` 1、`reopen` 1、`worker_*` 5、`wake_queued` 1、`github_review_pr_synced` 322，
**`approve` 0 筆、`submit_review` 0 筆**。owner／reviewer／`pr_number`／`review_submission.remote_sha`
與 hold 當時完全一致；唯一的 status 變化是 `2026-09-08T22:10:41Z` Codex2 的 `reopen`
（`reason=review_finding`、`category=substantive_review`），屬正常 review 決定，不是歷史改寫。

**與 acceptance 文字的落差（如實記錄）**：acceptance §6 期望「由本任務 owner 透過 canonical CLI 恢復
`non_dispatchable=false`」。實際上該恢復由 `Codex` 於 `2026-09-08T22:08:24Z` 以同一 canonical CLI 路徑完成，
而非本任務 owner。本輪 owner **刻意未重跑**該恢復命令：欄位已是 `false`，重跑只會再次寫入另一個任務的
canonical 狀態（並可能重置其 dispatch），而本輪 reopen 指示明確要求「不處理或繞過其他任務的
continuation/release gate」。恢復所要求的實質條件——hold 解除、原正常 review 派工恢復、
owner／reviewer／submission／CI 不變、不清失敗計數、不重寫歷史——經上表與事件清單逐項確認全部成立。

**前一版 §7.3 記載的「被 harness 權限分類器阻擋、待操作者授權」已過時**，本節以本輪 live canonical
readback 取代；該 blocker 不再沿用。依 task 規範，本任務仍**未**將 `depends_on` 指向該 live rollout，
以免造成 review／CLI 升級的循環依賴。

### 7.4 結案狀態總結（如實）

| 驗收項 | 狀態 |
| --- | --- |
| 失敗事實與非 quota 判定（§2） | 已完成 |
| 官方 pinned CLI 升級 `0.147.0 → 0.153.4`（§3） | 已完成 |
| 兩 pool／四 physical slot binary 與模型／effort readback（§4） | 已完成 |
| 未變動欄位與 rollback 邊界（§5／§6） | 已完成 |
| 一次正常 review 成功進入模型工作、非 HTTP 400（§7.2） | 已完成（run `codex-20260906T062839Z-f98b97cc`，`codex_bjoe_slot_1`，exit 0） |
| **原失敗 slot** 於 0.153.4 上的正常成功 run（§7.2.1） | 已完成（run `codex-20260908T220840Z-e9177416`，`codex_lupin_slot_1`，exit 0） |
| 恢復 `ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001` 的 `non_dispatchable=false`（§7.3） | 已完成（`2026-09-08T22:08:24Z` 由 `Codex` 以 canonical `assign` 解除；本輪 readback 確認 `false`，原派工已恢復並實際跑出 §7.2.1 的 run） |

CLI 升級、backend 接受度驗證與 hold 解除三項均已成立，且各自都有 canonical 收據佐證，
不是以行為或摘要推論。唯一與 acceptance 字面不同之處是 §7.3 記載的「執行恢復命令的 actor 是 `Codex`
而非本任務 owner」，該差異已明記、未被隱去，實質恢復條件則全部滿足。

本 receipt 不宣稱 `ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001` 這個任務本身已完成——它在
`2026-09-08T22:10:41Z` 被其 reviewer Codex2 以 review finding 退回，屬該任務 owner 的後續工作，
與本任務的 CLI 升級交付範圍無關。

## 8. 測試邊界

本次為工具依賴升級，未變更任何已核准的產品或 orchestrator 程式碼，因此不重跑整套測試；驗證限於 §2.1 的唯讀行程確認與 §4 的唯讀版本／解析 readback。CI 走正常 receipt scope。
