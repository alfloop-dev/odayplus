# Python 程式碼與責任邊界盤點 — 2026-08-13

> This file preserves the original `.orchestrator` / `scripts` / `apps/api` /
> `tests` audit scope. Repository-wide classification is now governed by
> [`config/code-boundaries.yaml`](../../config/code-boundaries.yaml) and the
> generated [`code-boundary-inventory.csv`](code-boundary-inventory.csv), which
> cover product domain packages, workers, pipelines, services, evidence, and
> archive code as well.

基準：branch `task/ODP-ORCH-AGENT-LOAD-BALANCE-001`，起始 commit
`61d90cca`。範圍是 `.orchestrator/`、`scripts/`、`apps/api/`、`tests/`
的 active Python；`archive/` 不算 active code。逐檔結果在
[`python-inventory-2026-08-13.csv`](python-inventory-2026-08-13.csv)。

## 結論

先前的盤點確實沒有把邊界分乾淨。特別是 `scripts/ai_status.py` 被歸在
script/tool，但 supervisor 會直接載入它；它是開發平台的 live runtime，
不是一般開發腳本。舊 CSV 也把 `.orchestrator/test_*.py` 等 31 個測試歸在
runtime/tool，逐檔分類與報告總數互相矛盾。

本次改成以程式實際責任、import/call path、讀寫狀態與外部副作用分類，
不是以目錄名稱或開發紀錄分類。分類採兩層：第一層先回答「是不是產品」，
第二層才描述 runtime/tool 的具體責任。

| 第一層範圍 | 檔案 | 行數 | 算不算產品本身 |
|---|---:|---:|---|
| **產品系統** | **41** | **17,201** | **是；正式 BFF request-serving runtime** |
| 產品維運工具 | 20 | 12,939 | 否；操作產品部署、資料與模型 |
| 開發平台系統 | 53 | 34,991 | 否；內部 orchestrator/control plane，是另一套系統 |
| 開發／交付工具 | 67 | 19,020 | 否；Build、CI、release、OpenAPI、安全與 Git 工具 |
| 測試驗證 | 240 | 112,936 | 否；verification only |
| **合計** | **421** | **197,087** | |

逐檔 CSV 新增 `system_scope` 與 `is_product_runtime`。只有
`system_scope=product_system` 的 41 個檔案會標記
`is_product_runtime=true`；其餘 380 個一律不算產品 runtime。

第二層責任如下：

| 邊界 | 檔案 | 行數 | 定義 |
|---|---:|---:|---|
| 產品 BFF runtime | 41 | 17,201 | FastAPI request path、產品 API composition、auth 與 routes |
| 產品維運 | 20 | 12,939 | 產品 deployment、migration、data/model backfill；不在 BFF request path |
| 開發平台 runtime | 45 | 32,702 | supervisor、worker/provider、approval、GitHub bus、runtime/task state |
| 開發平台維運 | 8 | 2,289 | 安裝、rollout、doctor、freshness、archive/dependency maintenance |
| Build/CI/release 工具 | 67 | 19,020 | E2E gate、release evidence、OpenAPI、安全與 Git 工具 |
| 測試 | 240 | 112,936 | verification，不是部署產品程式 |
| **合計** | **421** | **197,087** | |

因此不能再把 `.orchestrator + scripts + BFF` 當成一個「被開發的產品」。
產品的 request-serving BFF 是 17,201 行；開發平台本身是另一套 34,991 行的
live control plane 與維運程式；其餘是產品維運、release/build 工具與測試。

### 第一層分類規則

- `product_system`：只有 `apps/api/**` 的 BFF request path 與 composition。
- `product_operations_tooling`：deployment、migration、backfill、data/model jobs；
  它們服務產品，但不是產品 request runtime。
- `development_platform_system`：`.orchestrator/**`、`scripts/ai_status.py` 與
  orchestrator rollout/doctor；這是內部開發平台，不是終端產品。
- `development_delivery_tooling`：`delivery_toolchain/e2e/**`、Build、CI、release、OpenAPI、
  security、Git utilities；全部屬開發工具。
- `verification`：所有測試；不計入任何 deployed system code。

## 盤點方法

每個 active Python 檔案都重新解析 AST，記錄函式、class、CLI entrypoint、
責任類別與 disposition。判定再用下列程式內證據校正：

- 誰 import/呼叫它，而不是 README 是否提到它；
- 它讀寫 `ai-status.json`、`.orchestrator/state.json`、GitHub、產品資料庫或
  HTTP request/response 的哪一種狀態；
- 它是長駐 runtime、一次性 mutation、release gate 或 deterministic test；
- 重複的是文字 helper、同一個 state authority，還是同一個業務責任。

Git 歷史與 runbook 只用來確認使用情況，不作主要分類依據。

## 程式內部責任圖

### 開發平台 runtime（不是產品 BFF）

- `.orchestrator/supervisor.py`：10,495 行、275 個 top-level function。內含
  process singleton、provider capacity、worktree、queue、failure/retry、lease、
  approval resume、task scheduling 與 dispatch；`poll_workers` 約 716 行，
  `dispatch_ready_tasks` 約 395 行。它是 active monolith，不是 dead code。
- `scripts/ai_status.py`：6,495 行。前段處理 canonical board/config/actor，
  中段處理 GitHub/PR/CI/worktree delivery truth 與 archive，後段同時負責
  task mutation、dashboard projection、GitHub status/outbox。supervisor 直接載入
  此檔，故已改列開發平台 runtime。
- `.orchestrator/runtime_state.py`：`.orchestrator/state.json` 的 migration、
  transaction lock、merge 與 event/approval state authority。
- `.orchestrator/github_bus.py`：2,226 行，自行實作 Git/remote/`gh`、PR/issue、
  webhook、cloud relay 與 outbound sync。
- `.orchestrator/permission_broker.py` 與 `provider_permissions.py`：shell policy、
  approval policy、provider auth/config/capability 與設定備份。
- `.orchestrator/common.py`：1,424 行，混有 JSON/config/path/auth/subprocess、
  provider CLI、process spawn、task snapshot/brief/evidence；它是下一輪拆分點，
  不是可直接刪除的廢碼。

### 產品 BFF

- `apps/api/oday_api/main.py`：1,453 行；`create_app()` 是產品 composition root。
- `apps/api/app/routes/listings.py`：4,173 行，混合約 500 行 hand-written schema、
  listing router、in-memory repository/adapters 與 assisted-intake router。這是產品
  複雜度與 schema duplication，不是 orchestrator 開發工具。
- `apps/api/app/routes/operator.py`：1,223 行；live composition 與 fixture/local
  composition 各自組裝大量相同 sub-router。兩條路徑目前都有測試與 caller，
  不能當 dead code 刪除，但可抽 shared composition plan。

### Build/CI/release 工具

- `delivery_toolchain/e2e/check_product_release_gate.py` 主要 subprocess 呼叫其他 checker，
  再掃描 workflow/docs token；它是 release governance aggregator，不是產品 runtime。
- `delivery_toolchain/e2e/check_release_fleet_dispatch_status.py` 同樣聚合六個 checker 與報表。
- external-proof 約二十支 CLI 共用同一 release queue/status/template；保留多個 CLI
  是操作介面需求，但 JSON/GitHub/module/retry transport 不應各自重寫。

## 已確認且已修正的重複／錯誤機制

1. **Runtime state 多 writer 旁路**：watchdog 原本直接 `write_json` 覆寫整份
   `.orchestrator/state.json`，可蓋掉 supervisor 同時更新的 worker/queue。現在
   經 `save_runtime_state()` 的 lock + merge canonical writer。
2. **Task board 多 writer 旁路**：CI repair 原本直接覆寫 `ai-status.json` 再 sync，
   繞過 revision/CAS。現在統一走 `commit_canonical_task_transition()`，stale snapshot
   會 fail closed。
3. **Approval signature 重複**：permission broker 與 approval queue 原有兩份；
   現在只保留 `approval_queue.approval_signature`。
4. **Provider CLI/auth lookup 重複**：Copilot local/cloud 與 provider permissions
   已改共用 `provider_runtime.py` 的 binary/token resolver。
5. **E2E transport helper cloning**：新增 `delivery_toolchain/e2e/_support.py`，已遷移四個
   external-proof checker 的 JSON/issue URL/GitHub issue loader、三個 dynamic module
   loader 使用點與 escalation retry policy。這一批移除 100+ 行重複 mechanics，
   checker 保留各自 domain validation。
6. **BFF reset policy 四份複製**：network reviews/rebalance/listings/scoring 已統一
   使用 `apps/api/app/routes/_common.py::reset_allowed_guard`，HTTP status、error code
   與 fail-closed 語意集中管理。
7. **表面 manifest-driven、實際綁死 PR 82**：移除 `current_pr82_*`、
   `validate_pr82_*`、`load_pr82_*` compatibility wrapper 與
   `--release-sha-from-pr82`。正式 command 改為 `--release-sha-from-pr`，release PR
   從 queue 的 `release_target.pr` 解析；數字 82 只作當前 manifest 資料或 fixture，
   不再是 executable interface。
8. **工具與產品只有邏輯分類、沒有實體邊界**：建立 `delivery_toolchain/`，將
   OpenAPI、Git、security、release、load、chaos、GitHub policy 與 E2E gate 的
   canonical implementation 全數移出 `scripts/`；CI、測試與操作命令已改用新路徑。
9. **E2E JSON／GitHub transport cloning**：所有 E2E JSON 讀取只保留
   `delivery_toolchain/e2e/_support.py` 的 canonical implementation；特殊 fleet issue
   sync 也改用同一個 retry/captured-output helper。
10. **Provider adapter lifecycle 與 Gemini auth authority 重複**：六個 CLI adapter
    已共用 `BaseAdapter.spawn_cli_delivery()` 與 `unavailable_or_inbox()`；Gemini
    home/env/settings/auth 判斷只保留在 `provider_runtime.py`，adapter 僅組 provider
    專屬 argv 與環境差異。

## 尚未完成、但已由程式碼確認的重複

| 機制 | 實際位置 | 判定 | 下一步 |
|---|---|---|---|
| Git/GitHub command 與 delivery truth | `supervisor.py`、`ai_status.py`、`github_bus.py` | 三者都做 branch/PR/CI/subprocess，但 authority 不同 | 先定義 GitHub client 與 delivery snapshot contract，再遷移 |
| Release evidence 手工鏡像 | queue、pickup board、handback template、example | 同一 command/evidence 需同步四份；本輪完整測試已實際抓到 drift | 建單一 renderer，由 queue 產生三份衍生 evidence |
| BFF receipt-store factory | adlift/sitescore/forecastops 三份 | 結構與 tenant-scoped factory 相同 | 抽 typed factory，保留不同 store protocol |
| BFF command-store factory | avm/interventions/priceops 三份 | 結構相同，錯誤文案與 store type略異 | 抽 durable command-store resolver |
| Operator composition | `operator.py` live/fixture branches | 相同 router topology 重複組裝 | 建 shared composition descriptor，不混合 runtime dependencies |
| Listing schema | `routes/listings.py` 與 OpenAPI effective schema | 手寫 DTO 與 contract 同步成本高 | 先做 contract-drift test，再決定 codegen；不直接刪 DTO |

## 廢碼與封存判定

本輪之前選出的七個封存候選，現在也補上程式內證據，而非只靠歷史：

- account-pool configurator 內含固定 pool/agent mapping，live config 已完全具備目標值；
- dependency migration 只處理一組固定舊 mapping，live dry-run 無 mutation；
- 2026-07-31 execution-pack sync/validator/test 綁定單一 dated packet，且 validator
  已不能驗證目前 canonical archive。

它們共 2,263 行，已移到 `archive/retired-dev-tools/2026-08-13/`，未從 Git
歷史刪除。其餘 active 大檔多數是責任過多或位置錯誤，不能因為龐大就稱為廢碼。

仍需 owner/usage 決策的兩項是：

- `.orchestrator/github_webhook_server.py`：github bus 有 consumer，但目前主機沒有
  installed service 啟動它；
- `delivery_toolchain/github/apply_branch_protection.py`：會改 repository setting，active reference
  主要來自舊 evidence。

在取得 owner 決策前保留，逐檔表標為 `owner_usage_review`，不冒然刪除。

## 下一輪順序

1. 將 `ai_status.py` 拆成 canonical state service、delivery truth、archive、projection；
   原 script 留薄 CLI，避免 supervisor 與操作命令同時中斷。
2. 依 worker lifecycle、worktree、dispatch、reconciliation 漸進拆 supervisor。
3. 最後處理 BFF receipt/command store 與 operator composition；它們屬產品程式，
   不與開發平台重構混在同一批部署。
