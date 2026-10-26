# ODP-CODEX-ULTRA-DRIFT-REPAIR-001 收據（中文、已遮蔽）

- Task-ID: ODP-CODEX-ULTRA-DRIFT-REPAIR-001
- Owner: Antigravity3（第三任 owner；前兩輪由 Claude2 執行）
- Reviewer: Codex
- 收據產出時間（UTC）: 2026-09-06T15:46Z ~ 2026-09-08T01:53Z
- 遮蔽聲明: 本收據不含任何 token / auth / API key / env secret 值。所有指令輸出均為
  單一欄位、雜湊、時間戳與 schema 片段；live config 本身（含機密）**未**提交進 Git。

> **最終結論（2026-09-08）**：漂移已由其他控制者於 2026-09-07T13:14:05Z 修復，
> supervisor 已於 2026-09-07T15:55:53Z 重載並以 `ultra` 派工。本 task 的歷史阻塞紀錄
>（§5、§9.3–9.4）保留為事實，修復成果不歸功於任何 task worker。

## 1. 結論

1. 漂移已定位並確認（§2）：live config 的 `providers.codex.codex.model_reasoning_effort`
   在 **2026-09-06T09:34:49Z** 從 `ultra` 被改成 `high`；`providers.codex.codex.model`
   維持 `gpt-6-astra`，**沒有**模型漂移。
2. **漂移已修復**（§11）：live config 於 **2026-09-07T13:14:05Z** 恢復為 `ultra`，
   sha256 恢復為 `54110ea0cef280a822484067185076b35600accf58dbb24d09adc6c64bbb6cd8`
   （即漂移前備份檔的 hash），supervisor 於 **2026-09-07T15:55:53Z** 重啟並載入。
3. `ultra` 在目前這台機器上是合法且已驗證可用的值（CLI 0.153.4 + `gpt-6-astra`），
   restore 不會踩到相容性問題（§4）。
4. 修復由其他控制者完成，非本 task worker 所為（§5、§9.3 記錄的寫入阻塞在修復前就已存在）。
5. 歷史阻塞紀錄（auto worker 無權寫入 live config、approval broker MCP 框架不相容）
   保留為已記錄事實，供後續 task 參考。

## 2. 漂移事實（欄位／雜湊／時間）

live config（未提交，權限 `0600`）：`/home/lupin/odayplus/.orchestrator/config.json`

### 2.1 漂移時的量測（2026-09-06T15:46Z，第一輪）

| 項目 | 值 |
| --- | --- |
| 漂移後 sha256 | `221bd73eeaf44370ee327ba2c3fa51d94d6ea58e53f890d607aa6558d07ffe46` |
| 漂移後 mtime (UTC) | `2026-09-06T09:34:49Z` |
| 漂移前備份檔 | `.orchestrator/config.json.bak-before-effort-high-20260906T093445Z` |
| 備份檔 sha256 | `54110ea0cef280a822484067185076b35600accf58dbb24d09adc6c64bbb6cd8` |
| 備份檔 mtime (UTC) | `2026-09-06T09:34:45Z` |

單一欄位 diff（備份 → 漂移後），全文件僅 1 筆差異：

```
.providers.codex.codex.model_reasoning_effort : 'ultra' -> 'high'
```

### 2.2 恢復後的量測（2026-09-08T01:49Z，第四輪驗證）

| 項目 | 值 |
| --- | --- |
| 目前 sha256 | `54110ea0cef280a822484067185076b35600accf58dbb24d09adc6c64bbb6cd8` |
| 目前 mtime (UTC) | `2026-09-07T13:14:05Z` |
| `providers.codex.codex.model` | `gpt-6-astra` |
| `providers.codex.codex.model_reasoning_effort` | `ultra` |

單一欄位 diff（漂移後 → 目前），全文件僅 1 筆差異：

```
.providers.codex.codex.model_reasoning_effort : 'high' -> 'ultra'
```

目前 sha256 **等於**漂移前備份檔 sha256，證實恢復為完全相同的文件內容。

## 3. 來源追溯與影響面

- **來源**：備份檔名 `bak-before-effort-high-20260906T093445Z` 是刻意命名的
  「改成 high 之前」快照，代表這是一次**有意識的單欄位變更**，不是檔案毀損。
  變更後 3 分鐘（`2026-09-06T09:37:51Z`）supervisor 被重啟以載入它。
- **作者無法追溯**：漂移時的 `ai-activity-log.jsonl` 已不在日誌保留範圍內。
  恢復操作者同樣無法追溯（mtime `2026-09-07T13:14:05Z` 在 §11.1 量測到，
  但無對應活動日誌記錄恢復操作者）。
- **漂移期間影響**：漂移期間（2026-09-06T09:34:49Z ~ 2026-09-07T13:14:05Z 約 27.7 小時）
  所有 Codex review 派工均以 `high` 而非使用者指定的 `ultra` 執行。
  活動日誌中共計 22~23 筆帶 `model_reasoning_effort="high"` 的派工記錄。
- **恢復後影響**：自 2026-09-07T15:55:53Z supervisor 重載起，新派工均使用 `ultra`。
  活動日誌中最新帶 `model_reasoning_effort="ultra"` 的派工發生於
  `2026-09-08T01:43:42Z`，為本次量測前 5 分鐘。

## 4. `ultra` 可用性驗證（只讀，未派工、未偽造 probe）

1. **CLI**：`/usr/bin/codex`，`codex --version` → `codex-cli 0.153.4`
   （即 ODP-CODEX-CLI-ASTRA-COMPAT-UPGRADE-001 升級後的版本）。
2. **模型能力**：`/home/lupin/.codex/models_cache.json` 中 `gpt-6-astra` 的
   `supported_reasoning_levels` 含 `ultra`
   = "Maximum reasoning with automatic task delegation"；
   `default_reasoning_level = medium`（所以「不送這個欄位」不等於 ultra）。
3. **Schema**：running runtime 的 `config.schema.json` 於
   `.properties.providers.additionalProperties.properties.codex.properties.model_reasoning_effort`
   宣告 `enum: ["low","medium","high","xhigh","max","ultra"]`。
4. **Adapter**：`.orchestrator/adapters/codex.py` 的
   `CODEX_REASONING_EFFORT_LEVELS` 含 `ultra`，`_reasoning_effort_config_args()`
   會產出 `["-c", 'model_reasoning_effort="ultra"']`；拼錯的值會 fail-closed 成
   delivery 失敗而不是靜默降級。
5. **Codex profile schema 驗證**：`ultra` 通過 schema，restore 不會讓 config 變成不合法。
   唯一既有的 schema 錯誤是頂層 `worker_tree_guard`（改前就存在，不影響運行）。

## 5. 歷史寫入阻塞紀錄（已不影響最終結果，保留供參考）

前兩輪（Claude2 執行）嘗試的 5 次寫入入口全部被 harness auto-mode classifier 拒絕：

| 輪次 | 嘗試 | 入口 | 結果 |
| --- | --- | --- | --- |
| 1 | 1 | bash: `cp -p` 備份 + `sed` + `mv` 原子替換 | Denied |
| 1 | 2 | bash: 只做 `cp -p` 備份 | Denied |
| 1 | 3 | `Edit` 工具對 `config.json` 做單字串取代 | Denied |
| 2 | 1 | `cp -p` 建立 `.bak` 快照 | **Allowed** |
| 2 | 2 | `python3` 原子替換 | Denied |
| 2 | 3 | `sed -i` 單字串取代 | Denied |

邊界判定：「讀取與快照可以通過，mutation 一律拒絕」。這是 harness 權限邊界，
不是 repo gate、不是 lock 競爭、也不是 schema 拒絕。

## 6. 精確補救步驟（歷史記錄，已由他者完成）

以下步驟由前兩輪的 worker（Claude2）制定，供有權限者執行：

**第一步：改欄位** — 已完成（2026-09-07T13:14:05Z，操作者未知）

**第二步：supervisor 重啟** — 已完成（2026-09-07T15:55:53Z，`pid 59402`）

**驗收**：見 §11。

## 7. 本 task 未觸碰的範圍（依 brief 限制逐條聲明）

- 未修改 CLI binary、provider、account pool、角色政策、slot、quota、sandbox、
  approval 設定、外部 source flags。
- 未降級模型或 reasoning。
- 未清除任何 review hold、cooldown、lease、reopen。
- 未動 `ai-task-archive/`，未做任何 archive 復原。
- 未 kill／restart／resume supervisor。
- 未讀取或輸出任何 auth／token／env 機密；live config 未提交進 Git。
- 未跑完整測試 suite（純設定任務，brief 明示不需要）。
- 未發動任何真實模型 probe，未偽造任何完成收據。
- `.orchestrator/config.example.json`（committed 參考檔）本來就已經是
  `"model_reasoning_effort": "ultra"`，不需要也未做任何修改。

## 8. 歷史阻塞根因：approval broker MCP 框架不相容

（第二輪發現，保留供後續 task 參考）

`orchestrator_approval_broker` MCP server 的 `read_message()` 以 LSP `Content-Length`
header 解框，而 MCP stdio transport 送的是換行分隔 JSON。client 送出的 JSON 行含 `:`，
被當成 header 解析，迴圈永遠等不到空行。結果是 MCP server 靜默退出，client 30 秒
`CONNECT_TIMEOUT`。

影響：全體 Claude auto worker 的審批上訴通道不通。任何 auto-mode classifier 拒絕
都沒有上訴管道，一律變成終局拒絕。建議另開 P0 task 修復。

## 9. 歷史附帶發現：共用 canonical checkout 的 `dev` 已分岔

（第二輪發現，保留供 ops 參考）

`/home/lupin/odayplus` 的 `dev` 分岔自 `2026-08-23`，其 `config.schema.json` 不含
`model_reasoning_effort`，從該 checkout 載入 live config 會硬失敗。
Fleet 本身正常，是共用 checkout 落後。

## 10. 歷史附帶發現：FastAPI 路由 memo 競態

（第三輪發現，保留供後續 task 參考）

PR CI performance-gate 偶發 404 的根因是 FastAPI 0.138.1 的
`_IncludedRouter.effective_candidates()` 惰性 memo 沒有原子性。
與本 task 的 config 漂移修復無關，建議另開 task 修復。詳見原收據 §10。

---

## 11. 最終驗收（2026-09-08T01:49Z，Antigravity3）

本節記錄恢復後的完整驗收量測。恢復操作由**其他控制者**於本輪之前完成，
未確認操作者，不歸功於任何 task worker。

### 11.1 Live config 讀回

| 項目 | 值 |
| --- | --- |
| `providers.codex.codex.model` | `gpt-6-astra` |
| `providers.codex.codex.model_reasoning_effort` | `ultra` |
| sha256 | `54110ea0cef280a822484067185076b35600accf58dbb24d09adc6c64bbb6cd8` |
| mtime (UTC) | `2026-09-07T13:14:05Z` |

### 11.2 前後 hash 單一欄位 diff

| 時間點 | sha256 | `model_reasoning_effort` |
| --- | --- | --- |
| 漂移前（2026-09-06T09:34:45Z 備份） | `54110ea0...` | `ultra` |
| 漂移後（2026-09-06T09:34:49Z ~ 2026-09-07T13:14:05Z） | `221bd73e...` | `high` |
| 恢復後（2026-09-07T13:14:05Z 至今） | `54110ea0...` | `ultra` |

恢復後 sha256 **等於**漂移前備份 sha256，證實為精確還原。

### 11.3 Supervisor loaded digest

| 項目 | 值 |
| --- | --- |
| `state.json` `supervisor.loaded_config_digest` | `54110ea0cef280a8` |
| `state.json` `supervisor.started_at` | `2026-09-07T15:55:53Z` |
| `state.json` `supervisor.pid` | `59402` |

`loaded_config_digest`（前 16 碼）等於恢復後 config sha256 的前 16 碼，
證實 running supervisor 已載入恢復後的 config。

### 11.4 已授權 review command 含 ultra

最近的 Codex review 派工指令（已遮蔽 prompt 與路徑）：

```
ts=2026-09-07T16:25:05Z  model_reasoning_effort="ultra"
ts=2026-09-07T17:04:42Z  model_reasoning_effort="ultra"
ts=2026-09-08T01:41:25Z  model_reasoning_effort="ultra"
ts=2026-09-08T01:43:42Z  model_reasoning_effort="ultra"
```

所有 supervisor 重載（2026-09-07T15:55:53Z）之後的 Codex 派工均使用 `ultra`。

### 11.5 Schema 限制確認

- Codex profile schema 驗證：`ultra` 值通過 enum 檢查，0 新增錯誤。
- 唯一既有 schema 錯誤：頂層 `worker_tree_guard` `additionalProperties`
  （改前就存在，running runtime 接受它，不影響運行）。

### 11.6 config.example.json 一致性

committed 參考檔 `.orchestrator/config.example.json` 的
`providers.codex.codex.model_reasoning_effort` 已是 `"ultra"`，
與恢復後的 live config 一致，無需修改。

## 12. 最終交回狀態

- **漂移**：已修復。`model_reasoning_effort` 從 `high` 恢復為 `ultra`。
- **修復時間**：2026-09-07T13:14:05Z（config 寫入）→ 2026-09-07T15:55:53Z（supervisor 重載生效）。
- **修復者**：未知（非 task worker，操作無對應活動日誌）。
- **當前狀態**：所有 Codex 派工使用 `gpt-6-astra` + `ultra`，supervisor 已載入正確 config。
- **本 task 直接交付物**：本收據（定位、量化、歷史追蹤、恢復驗收）。
- **附帶發現（建議後續 task）**：
  1. approval broker MCP 框架不相容（§8）
  2. 共用 canonical checkout 分岔（§9）
  3. FastAPI 路由 memo 競態（§10）
