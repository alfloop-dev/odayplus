---
evidence_id: ODP-CODEX-CLI-ASTRA-COMPAT-UPGRADE-001
title: "升級既有 Codex CLI 以恢復 Astra ultra 背景審查"
date: 2026-09-06
status: IMPLEMENTED
owner: Claude
reviewer: Codex
repository: alfloop-dev/odayplus
task: ODP-CODEX-CLI-ASTRA-COMPAT-UPGRADE-001
base_ref: 64f3b239
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
| `supervisor.role_provider_policy` | 不變（reviewer→`codex`；owner/helper→`antigravity`,`claude`） |
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

---

## 7. Backend 接受度驗證與 hold 解除（現況如實記錄）

### 7.1 驗證標準

僅 `codex --version` 或 features list 成功**不足以結案**。結案需要一次**正常的** Codex review 派工實際進入模型工作（不再是 HTTP 400 拒絕），且不得以假 task 或 API probe 取得。

### 7.2 目前狀態

本 receipt 提交（PR 建立 + review submission）本身即會觸發本任務 reviewer `Codex` 的正常 review 派工，該 run 使用的正是 §4 核對過的 `/usr/bin/codex` 0.153.4 與 `gpt-6-astra` / `ultra`。該 run 的 run id 與 terminal 結果將以 canonical CLI note 記錄於本任務。

**在取得該 backend 成功證據之前，本項如實標示為未完成。**

### 7.3 `ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001` hold 解除：未完成，需操作者授權

- 該任務目前仍為 `status=review`、`owner=Antigravity`、`reviewer=Codex2`、`non_dispatchable=true`，PR #1223（`review_submission.remote_sha=03ef1d01`）未被其他 actor 核准或重新指派，歷史未被改寫。
- 解除 hold 的唯一 canonical 路徑為以相同 owner／reviewer 呼叫：

```bash
AI_NAME=Claude TASK_METADATA_JSON='{"non_dispatchable": false}' \
  "$PANTHEON_STATUS_ROOT/scripts/ai-status.sh" \
  assign ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001 Antigravity Codex2
```

- 此命令在本次背景 worker 執行時**被 harness 的權限分類器拒絕**（`Blocked by classifier`），非 canonical CLI 拒絕、亦非政策拒絕；已嘗試兩次後停止，**未以任何方式繞過**，也未手改 canonical JSON。
- 因此該 hold **仍為 `non_dispatchable=true`**。需操作者授權此命令後由本任務 owner 執行；執行時維持原 owner／reviewer／submission／CI 不變，不清失敗計數。
- 依 task 規範，本任務**未**將 `depends_on` 指向該 live rollout，以免造成 review／CI 升級的循環依賴。

---

## 8. 測試邊界

本次為工具依賴升級，未變更任何已核准的產品或 orchestrator 程式碼，因此不重跑整套測試；驗證限於 §2.1 的唯讀行程確認與 §4 的唯讀版本／解析 readback。CI 走正常 receipt scope。
