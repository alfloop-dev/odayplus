# 啟用 Agy／Claude 實作、Codex Astra ultra 審查政策與 Supervisor Runtime 部署收據

- 任務：`ODP-ROLE-PROVIDER-CODEX-LIVE-ROLLOUT-001`
- 日期：2026-09-06（UTC）
- 執行角色與所有者：Antigravity
- 獨立審查者：Codex2
- 範圍：本機開發與執行控制面（`/home/lupin/odayplus/.orchestrator/config.json`、`scripts/ai-status.sh`、`/home/lupin/oday-plus-supervisor-runtime-current` 軟連結及 exact-SHA runtime 工作樹）；非 GCP 產品部署、非第三方來源啟用、無 Human GO 授權。

---

## 1. 前置依賴與 PR #1221 版本溯源

依任務驗收條件，本 live rollout 嚴格等待前置政策程式任務 `ODP-ROLE-PROVIDER-CODEX-REVIEW-001` 完成獨立審查、exact-head CI、合併 `dev` 並完成 immutable closeout 後才啟動執行。

### 1.1 前置 PR 與 Exact Merge CI 查核

| 項目 | 數值 / 狀態 | 說明 |
|---|---|---|
| 前置任務 | `ODP-ROLE-PROVIDER-CODEX-REVIEW-001` | 狀態：`done`（已於 `2026-09-06T05:57:47Z` 封存） |
| 對應 PR | [PR #1221](https://github.com/alfloop-dev/odayplus/pull/1221) | `ODP-ROLE-PROVIDER-CODEX-REVIEW-001: fix reviewer author pool & policy` |
| 核准 PR Head | `c1a382416e4423e22f3bf5dfe86a37d93597e583` | Codex 獨立審查 approved exact head |
| Merge Commit SHA | `64f3b2399442e8cd7531284d1e7a3e33bcc3ca9a` | 合併入 `origin/dev`（時間：`2026-09-06T05:56:17Z`） |
| CI Check Runs | 全綠通過 | `change-scope`、`boundary`、`classify`、`orchestrator`、`task-review-gate` 均 `SUCCESS`（tooling scope 略過產品 job） |
| 部署來源一致性 | `HEAD == origin/dev == 64f3b2399442` | 乾淨工作樹，無 dirty 檔案或未追蹤變更 |

---

## 2. Runtime 替換與程序版本一致性（Runtime Rollout & Process Integrity）

本次部署沿用既有唯一 `scripts/orchestrator/rollout_supervisor_runtime.py` 部署原語與 watchdog 機制，無新增任何平行 scheduler、router 或 rollout 腳本。

### 2.1 部署執行參數與程序讀回

| 項目 | 實測結果 | 說明 |
|---|---|---|
| 入口腳本 | `scripts/orchestrator/rollout_supervisor_runtime.py` | 既有唯一原子 rollout 原語 |
| 退出碼（Exit Code） | `0` | 部署成功，未觸發 rollback |
| 替換前 Runtime SHA / PID | `ba58ed6723960244d567c5314bfc200ffdf7bae8` / `1544799` | 舊版 Supervisor runtime 與程序 |
| 替換後 loaded SHA / PID | `64f3b2399442e8cd7531284d1e7a3e33bcc3ca9a` / `1692417` | 經 watchdog 重啟之新 Supervisor 程序 |
| 程序啟動時間 | `2026-09-06T06:00:59Z` | Supervisor 程序成功啟動 |
| stable runtime symlink | `/home/lupin/oday-plus-supervisor-runtime-current` | 指向 `/home/lupin/oday-plus-supervisor-runtime-64f3b2399442` |
| 真實 Process CWD | `/home/lupin/oday-plus-supervisor-runtime-64f3b2399442` | `os.readlink("/proc/1692417/cwd")` 逐字吻合 |
| 狀態發布 Launcher | `/home/lupin/odayplus/scripts/ai-status.sh` | SHA256: `3660f2423ddf5169c86199d3bf1699ebb34e733ebe9add2182483a9cfb5be9d1` |

---

## 3. 設定變更、Schema 檢驗與不變式保留（Configuration & Integrity）

### 3.1 Hash 與 Validator 檢驗

| 設定項目 | 數值 / 檢驗結果 |
|---|---|
| 替換前 Config SHA256 | `5e9f4279b14ba6f4595317d4064ccebc952c678693ec2abd2bb243a89be8a12c` |
| 替換後 Config SHA256 | `54110ea0cef280a822484067185076b35600accf58dbb24d09adc6c64bbb6cd8` |
| Loaded Config Digest | `54110ea0cef280a8`（與 Supervisor 內部狀態 `loaded_config_digest` 逐字相符） |
| 靜態驗證指令 | `python3 -B delivery_toolchain/governance/check_orchestrator_config.py --config /home/lupin/odayplus/.orchestrator/config.json` |
| 靜態驗證結果 | `Validated 3 config documents and their merged runtime views.`（通過） |
| 私有備份路徑 | `/tmp/odp-role-provider-codex-live-rollout-backup-fswthfki/`（私有保存，未 commit、未印出 secret） |

### 3.2 授權變更精準差異（Exact Authorized Diffs）

本次 live config 僅套用授權之兩類差異：

```json
{
  "ready_dispatcher": {
    "role_provider_policy": {
      "enabled": true,
      "rules": [
        {
          "roles": [
            "reviewer"
          ],
          "providers": [
            "codex"
          ]
        },
        {
          "roles": [
            "owner",
            "helper"
          ],
          "providers": [
            "antigravity",
            "claude"
          ]
        }
      ],
      "unclassified_owned_work": [
        "antigravity",
        "claude"
      ]
    }
  },
  "providers": {
    "codex": {
      "codex": {
        "model": "gpt-6-astra",
        "model_reasoning_effort": "ultra"
      }
    }
  }
}
```

### 3.3 嚴格保留參數（Preserved Invariants）

- **Antigravity 容量**：5 個實體 slot（`antigravity_slot_1` ~ `antigravity_slot_5`），account pool `antigravity_main`，`max_concurrent: 5`。
- **Claude 容量**：5 個實體 slot（`claude_slot_1` ~ `claude_slot_5`），account pool `claude_main`，`max_concurrent: 5`。
- **Codex 雙獨立 Pool 與實體 Slot**：2 個獨立 account pool，共 4 個實體 slot：
  - `codex_bjoe`: `max_concurrent: 2`，slot `codex_bjoe_slot_1`、`codex_bjoe_slot_2`
  - `codex_lupin`: `max_concurrent: 2`，slot `codex_lupin_slot_1`、`codex_lupin_slot_2`
- **Owner Provider 偏好**：保留 `ready_dispatcher.owner_provider_preference`（`enabled: true`, `preferred_providers: ["antigravity", "claude"]`）。
- **全域控制參數**：`watchdog_max_active_workers: 14`、`poll_interval_seconds: 180`、既有 timeout、auth、sandbox、外部 source disabled/egress 均未變動。
- **未動個人設定**：未修改任何個人 Codex config，未讀取或印出 auth secrets，未清除 cooldown、lease 或 reopen 紀錄。

---

## 4. 服務健康與連續迴圈驗證（Health Loops & State Consistency）

Supervisor 重啟後，完成連續 2 次完整健康迴圈，無任何新增錯誤，既有背景 Worker 持續被追蹤。

### 4.1 健康迴圈紀錄

| 迴圈 | 啟動時間 | 完成時間 | 耗時 (ms) | Loop Error |
|---|---|---|---|---|
| Loop 1 | `2026-09-06T06:01:00Z` | `2026-09-06T06:01:18Z` | 18,000 | `null`（無錯誤） |
| Loop 2 | `2026-09-06T06:01:59Z` | `2026-09-06T06:02:24Z` | 25,000 | `null`（無錯誤） |

- **累計 Loop Errors**：`0`（`last_loop_error: null`）
- **Watchdog 狀態**：`decision=observe_only`、`reason=supervisor_healthy`、`circuit_open=false`、`heartbeat_age_seconds=2.0`。
- **Stage Timings 正常**：`boot_and_provider` 6.5s、`scan_coordination_and_poll` 1.1s、`github_bus` 14.9s、`branch_drift` 0.7s。

---

## 5. 政策與模型解析回讀（Policy & Provider Resolution Readback）

### 5.1 派工角色資格矩陣（Role Provider Eligibility Matrix）

經新 runtime 之 `dispatch_policy.py` 實測，所有任務類別（包含 `implementation`、`remediation`、`documentation`、`rollout`、`integration`、`verification`、`unknown` 及 `None`）的資格解析結果如下：

| 角色（Role） | 允許 Provider 集合 | 說明 |
|---|---|---|
| `owner` | `["antigravity", "claude"]` | 禁止 Codex 擔任自動 owner，涵蓋所有 task classes |
| `helper` | `["antigravity", "claude"]` | 禁止 Codex 認領 helper lease |
| `reviewer` | `["codex"]` | 僅允許 Codex 擔任 reviewer，排除其他 provider |

### 5.2 Codex 雙 Pool 與實體 Slot 模型／Effort 繼承回讀

經新 runtime 之 `provider_runtime.py` 與 `adapters/codex.py` 實測，兩個 Codex account pool 及所有實體 slot 均正確繼承配置：

| Agent / Slot | 歸屬 Account Pool | 解析 Provider | 解析 Model | 解析 Effort 參數 |
|---|---|---|---|---|
| `codex` | `codex_bjoe` | `codex` | `gpt-6-astra` | `['-c', 'model_reasoning_effort="ultra"']` |
| `codex2` | `codex_lupin` | `codex` | `gpt-6-astra` | `['-c', 'model_reasoning_effort="ultra"']` |
| `codex_bjoe_slot_1` | `codex_bjoe` | `codex` | `gpt-6-astra` | `['-c', 'model_reasoning_effort="ultra"']` |
| `codex_bjoe_slot_2` | `codex_bjoe` | `codex` | `gpt-6-astra` | `['-c', 'model_reasoning_effort="ultra"']` |
| `codex_lupin_slot_1` | `codex_lupin` | `codex` | `gpt-6-astra` | `['-c', 'model_reasoning_effort="ultra"']` |
| `codex_lupin_slot_2` | `codex_lupin` | `codex` | `gpt-6-astra` | `['-c', 'model_reasoning_effort="ultra"']` |

### 5.3 真實 Review 派工觀測說明

- 盤點當前看板任務，當下無處於可派工狀態的 ready review 任務（例如 `ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002` 因 PR CI 失敗而依安全機制暫停派工，`ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001` 保留 `non_dispatchable: true` 前景 hold）。
- 依驗收規定，**誠實記錄當下無 ready review 任務，嚴格禁止製造假任務（synthetic tasks）或故意製造故障以取得派工證據**。實際 review worker 啟動證據將於本任務正常送審階段由 Codex2 審查時補讀真實執行收據。

---

## 6. Worker 存活與任務狀態保護（Worker Preservation & Task Invariants）

1. **現有 Worker 存活保護**：當前任務 worker `antigravity-20260906T055806Z-25f67a69`（PID `1689846`）於 rollout 前後持續存活並由新 Supervisor 正常追蹤，無任何 worker 被殺除或 lease 遺失。
2. **在途與歷史任務保護**：
   - 歷史任務與已封存之 approval/archive（如 `ODP-ROLE-PROVIDER-CODEX-REVIEW-001`、`ODP-DRIFT-NATIVE-MIGRATION-001`）完全保留，未追溯改寫。
   - `ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001` 維持 `non_dispatchable: true` 與前景持有說明，未被自動搶派。
   - 人工閘門任務（`Human/Ops`）與生產保護閘門完全保留。

---

## 7. 回滾程序與應變措施（Rollback Procedures）

若 live 環境需要回滾，提供以下兩層級處置路徑：

### 7.1 第一級：設定層停用政策或切換模型（需重啟程序生效）

1. 將 live config 中的 `ready_dispatcher.role_provider_policy.enabled` 設為 `false`。
2. 若需切換 Codex 模型，將 `providers.codex.codex.model` 改回 `gpt-5.6-luna` 並移除 `model_reasoning_effort`。
3. 沿唯一入口 `scripts/orchestrator/rollout_supervisor_runtime.py` 重啟 Supervisor。

### 7.2 第二級：完整 Runtime 與 Config 回滾（Restore from Snapshot）

1. 自私有備份目錄 `/tmp/odp-role-provider-codex-live-rollout-backup-fswthfki/` 將 `config.before.json` 與 `launcher.before.sh` 還原至 `/home/lupin/odayplus/.orchestrator/config.json` 及 `scripts/ai-status.sh`。
2. 沿用唯一 `scripts/orchestrator/rollout_supervisor_runtime.py`，以乾淨的 `ba58ed6723960244d567c5314bfc200ffdf7bae8` 為 `--source-root`，`--tracking-ref` 釘住 `ba58ed6723960244d567c5314bfc200ffdf7bae8`，完成原子切換與重啟。
3. 唯讀核對程序 PID、cwd、`loaded_code_sha`（`ba58ed672396`）、`loaded_config_digest`（`5e9f4279b14ba6f4`）及連續健康 loops。

---

## 8. 未執行事項與邊界宣告（Unperformed Operations & Disclosures）

1. **未部署 GCP 產品環境**：未執行 GCP release，未啟用第三方外部 data sources，未進行 GCP live source readback。
2. **未捏造測試任務**：無 ready review 任務時如實記錄，未人工建立 synthetic tasks 搶跑測試。
3. **未重跑已核准測試**：本任務為執行控制面 live rollout，不重跑無關的完整測試套件或已核准 PR 的測試。
4. **未外洩密鑰**：私有 config 備份與 auth secrets 均未 commit 或包含於 PR 中。
