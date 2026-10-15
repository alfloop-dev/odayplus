# Operator 導覽與 API RBAC 對齊

Task: `ODP-OPERATOR-NAV-RBAC-ALIGN-001`  
Owner: Pi · Reviewer: Codex  
Baseline: `origin/dev` = `c0c8c8226acaab3cbe8bb76e79f01e5e6694abb3`

## 交付範圍

- fixture/local 與 production 的 `/operator/bootstrap`、`/operator/today` 都以 guard 留在 request.state 的 verified principal 投影導覽，不再把 persona 當授權。
- `operator_permission_decision` 是從既有 Operator guard 原樣抽出的 RBAC + scope 判斷；guard 與 nav 共用它。listings 額外沿用原 `authorize_intake_action(..., "view")` collection 判斷；composition 沿用一般 HeatZone API 的 `rbac_allows`。
- 新增 `navigation.allowedTabs.network`，並同步 workspace allowed flags、allowedWorkspaces 與 persona metadata。其他 workspace 使用它們 API 既有的 `operator_console VIEW` 判斷。
- 前端隱藏無權 workspace、Network tabs、stepper steps 和固定 Network command links；被拒的 tab deep link 回到第一個可讀分頁。production 缺少 tab metadata 時不猜 persona 權限。
- 依投影停止無權 Network snapshot preload；production 不再載入 RBAC-only 的 legacy domain bindings。直接 intake detail 仍由其獨立 API 授權，不將導覽投影當成授權憑證。
- 未改 `shared/auth/rbac.py`、intake authorization 規則、API grants、tenant/object projection、write guards 或部署配置。

## 預期結果（由真實路由對照測試驗證）

| verified roles | 可見 Network 分頁 |
| --- | --- |
| auditor + platform_admin | rebalance |
| auditor | areas、radar、rebalance |
| operator_viewer（含與 platform_admin 組合） | areas、radar、candidates、score、compare、review |
| operations_manager | 無（隱藏 Network workspace） |
| site_reviewer | 全部 8 分頁 |
| executive | candidates、score、compare、review、composition |

特別保留 auditor+admin 的 Rebalance 入口：現有 API 允許 listing VIEW，而 Rebalance 沒有 intake 的額外 ROLE_DENIED 規則。反之 operator_viewer 不得因此取得 Rebalance 或一般 HeatZone API 的讀取權限。

## 驗證（2026-10-15）

以下皆由原 terminal command 完成且 exit 0，不以摘要字串推斷存活或成功：

| 命令 | 結果 |
| --- | --- |
| `uv run --python 3.12 pytest tests/security/test_operator_read_authorization.py tests/security/test_operator_network_read_scope.py tests/contract/test_operator_api.py -q --junitxml=/tmp/odp-nav-security.xml` | 66 tests，0 failures/errors |
| `uv run --python 3.12 pytest tests/integration/test_operator_live_domain_modules.py -q --junitxml=/tmp/odp-nav-live.xml` | 23 tests，0 failures/errors |
| `uv run --python 3.12 pytest tests/security/test_rbac_abac.py -q --junitxml=/tmp/odp-nav-rbac.xml` | 14 tests，0 failures/errors |
| `npm run test --workspace=@oday-plus/web -- features/operator/__tests__ features/operator/network/__tests__ --reporter=default --reporter=junit --outputFile=/tmp/odp-nav-ui-regression.xml` | 28 files / 291 tests passed |
| `npm run typecheck --workspace=@oday-plus/web` | passed |
| `uv run --python 3.12 ruff check apps/api/app/routes/operator.py apps/api/app/routes/operator_modules/shell.py apps/api/oday_api/security/dependencies.py tests/security/test_operator_read_authorization.py tests/integration/test_operator_live_domain_modules.py` | passed |
| `python3 delivery_toolchain/governance/check_code_boundaries.py` | passed，1221 files |
| `git diff --check` | passed |

新增覆蓋：7 組角色與 8 分頁的實際 API 拒絕結果對照、bootstrap/today local/live 兩條 wiring、共用 predicate 變更同步影響 nav/guard、auditor+admin 只發一個可讀 preload、viewer 保留六頁且不發 Rebalance/composition 讀取、缺 metadata fail-closed、無可讀分頁時隱藏 workspace，以及 bootstrap 後 API 仍拒絕時的原有 403 呈現。

## PR #1482 review reopen 修正（2026-10-15）

針對 reviewer 在 `b2e81b379af82d0660b751519bef4446b0e536e9` 提出的兩項 P2：

- production hydration 保留 deep-link／session workspace 意圖，bootstrap ready 後才用 `navigation.allowedWorkspaces` 驗證並保存；角色切換也使用 bootstrap grants，不再以靜態 persona 覆寫 principal 的權限。補上 pm-audit Growth、expansion-manager Store 的延遲 bootstrap、重新載入（URL／stored）與角色切換回歸。
- 不可讀的 Network snapshot 清除舊 snapshot、failure、API error 並結束內部 loading。摘要 chips 優先呈現 permission-withheld（`— …（未授權）`），不顯示舊數字、虛構 0 或永久 pending。覆蓋 auditor+admin 只讀 Rebalance、viewer 六頁及授權撤回前既有成功／失敗資料；確認不送 forbidden reads。
- API 與其授權規則未再變更。Anchor: `6f8122213c137078ca944ee78d6a41778e2cf7a5`。

本輪修正後的原 terminal 收據（均 exit 0）：

| 命令 | 結果 |
| --- | --- |
| `npm run test --workspace=@oday-plus/web -- features/operator/__tests__ features/operator/network/__tests__ --reporter=default --reporter=junit --outputFile="$ORCH_SCRATCH_DIR/ui-regression.xml"` | 28 files / 300 tests passed |
| `npm run typecheck --workspace=@oday-plus/web` | passed |
| `python3 delivery_toolchain/governance/check_code_boundaries.py` | passed，1223 files |
| `git diff --check` | passed |

本輪未重跑 backend／Playwright；前述 backend 結果及既有 E2E packet 均為先前 source 的歷史證據，不宣稱是本輪 source 的新執行結果。修正過程第一次 focused UI invocation 為 51 passed / 2 failed（exit 1，測試尚未等待子元件 preload effect）；補上 request 等待後 focused 53 tests passed，之後完整 300 tests passed。

### 未算成功的嘗試／限制

- 預設 `uv run` 選到 Python 3.14，pgserver 沒有相容 wheel（exit 2）；後續明確使用專案支援的 Python 3.12。
- 開發過程曾修正新測試的 persistence bundle cleanup 與舊 frontend mocks 缺少新增 tab metadata 的問題；上表為修正後收據。
- 首次把 security/contract/live-wiring 合為同一 pytest invocation 達 120 秒 timeout，沒有完整 JUnit 或 exit-success 收據；上表改為各組獨立執行且完成。
- 額外的 security-platform/shell/intake-matrix/RBAC 合併回歸達 240 秒 timeout；後續 intake-matrix + RBAC 嘗試亦達 60 秒 timeout。這些套件未宣稱全通過；獨立 RBAC 測試如上完成。未為此修改無關 API 或測試流程。
- 此處 live-wiring 指本機 production router + durable SQLite test repositories，不是線上部署、真實登入帳號或 live 業務驗收；未修改線上系統。
