# ODP-OPERATOR-AUTHORITY-UI-PRESENTATION-001 — 實際頁面授權拒絕與操作權限呈現

- **Task ID**: `ODP-OPERATOR-AUTHORITY-UI-PRESENTATION-001`
- **Owner / Reviewer**: `Pi` / `Codex`（前段 source implementation：`Claude`）
- **採集基準（collection baseline）**: `dev` 31785c571e062b9bef4512ec3d848391c47621d3（RuntimeRelease 38044574206 部署的 SHA）
- **缺陷來源**: `support/handoffs/dev-live-31785c57-20261010/README.md`「New UI presentation defects」三項
- **範圍**: 只修 UI 呈現。沒有改 IAM／RBAC／ABAC grant，沒有新增 viewer／業務角色，沒有修改既有帳號，也沒有改 API write guard。fixture、source、model、backfill 都沒碰，也沒有使用 live credential。

## 1. 三個缺陷的根因

| # | 實際頁面現象（2026-10-10 12:08–12:17 UTC） | 根因 |
|---|---|---|
| 1 | Find Areas 顯示「連線失敗／瀏覽器無法連到營運資料服務」，但實際回應是 403 `error.code=forbidden`，也帶 correlation ID | `NetworkFindAreasWorkspace` 的四個讀取函式遇到 `!response.ok` 就回 `null`，status、canonical error 與 correlation 全被丟掉。gate 只能從 `"network-listings API unavailable"` 這段字串推斷 kind，而 `classifyLoadFailure` 的 `/network/` 規則剛好命中路徑字樣，於是判成 `network`。Candidates、Score、Compare、Review 則落到 generic unavailable。HeatZone merge／split proposals 遇到 403 時回 `[]`，被當成已授權的空佇列。 |
| 2 | 被拒絕讀取的集合顯示 0 listings／candidates／reviews，以及「No listings match the selected filter」 | header 的計數直接取 `viewModel.totals`。讀取失敗後集合是空陣列，所以顯示 0。Listing Radar 分頁從不設 gate，因此空集合會顯示「0 筆」與 no-match 文案。 |
| 3 | 伺服器驗證的角色是 auditor+platform_admin，沒有業務決策權限，Governance 卻顯示「營運主管／可決策」並提供核准按鈕 | `GovernanceWorkspace` 的 `canDecide` 預設為 `true`，Console 又沒有傳入這個值。伺服器原本沒有回傳任何操作授權，前端只能依賴 sessionStorage 裡的 persona。platform_admin 本來就可以選 `ops-lead` persona（`_OPERATOR_ROLE_BY_PLATFORM_ROLE`），所以 persona ≠ 授權。 |

## 2. 修正

1. **Typed failure**（`operatorDataMode.ts`）
   - 新增 `operatorLoadFailureFromResponse`：依 HTTP status 與 canonical envelope（`error.code`、`error.correlation_id`，見 `shared/api/errors.py`）決定 kind 與 correlation。
   - 新增 `operatorLoadFailureFromError`（沒有收到 HTTP 回應時）與 `OperatorLoadFailureError`。
   - 收到 403 一律判為 `forbidden`，不受路徑字樣影響。forbidden 文案明說「這不是連線問題，也不代表資料為空」。
2. **Network 讀取**（`NetworkFindAreasWorkspace.tsx`）
   - 四個 snapshot 讀取改回傳 `NetworkRead<T>`，不再以 `null` 表示失敗。每個 tab 的 gate 都收到自己那個讀取的 typed failure（`failure` prop），會顯示 HTTP 狀態與追蹤編號。
   - `heatZoneCompositionClient.fetchProposals` 遇到 non-2xx 改為丟出 typed failure，不再回 `[]`。
3. **不偽造計數**
   - 新增 `resolveNetworkCountState`：只有已回應的讀取（ready，或已授權的 200 空結果）會顯示數字。被拒、失敗、來源未通過或仍在載入時，顯示「— listings（未授權／無法取得／載入中）」。
   - Listing Radar 新增 `listingsReadFailure` prop：清單讀取被拒時，來源卡、計數與收件匣改為 typed 拒絕 gate。自帶 binding 的 intake 佇列（AssistedIntakeSection）照常顯示。
   - 真正的 200 空結果仍顯示權威的 0（rebalance 200／空 → `0 rebalances`）。
4. **伺服器驗證的操作授權**
   - API `GET /operator/governance/snapshot` 新增唯讀欄位 `actionAuthority`：`{verified, systemRoles, decide, exportEvidence}`。值由 read guard 已驗證的 `request.state.operator_principal`，用寫入 guard 同一張 RBAC 表算出（`intervention` APPROVE／CREATE）。
   - 這只是呈現證據。guard、grant、角色都沒有改；寫入仍由原本的 guard 決定。
   - Web 端 `resolveGovernanceDecisionAuthority`：只有 server 驗證的 `decide=true` 才會啟用核准／退回／駁回並標示「可決策」，`canDecide` prop 只能再收窄。
   - 驗證過但沒有權限時顯示「僅可查看」，並寫出伺服器驗證的帳號角色。production 沒有驗證結果時顯示「決策權限未確認」，不提供決策控制項。
   - persona 改標示為「視角：營運主管」。Evidence Package 匯出同樣綁定 `exportEvidence`。
   - platform_admin 的 Users 角色權限、Feature Flags 分頁不受影響。合法的 scoped rows（核准佇列內容）照常顯示，只移除決策控制項。

## 3. 驗證

### 3.1 宣告的驗證命令

task 宣告的三條命令（`git diff --check`、四個 vitest 檔、`pnpm --dir apps/web typecheck`），由 `delivery_toolchain/git/task_verification.py run` 在最終 head 各跑一次。receipt 存在 supervisor evidence store。最終 head 的 exit code 與 duration 在送審後以 ai-status note 公布，本文件不引用承載自己的 commit。

本機沒有全域 `pnpm`。執行時用 corepack 在 scratch 目錄建立 pnpm shim，並把 repo 根目錄的 `node_modules/.bin`（npm workspaces 把 tsc、vitest hoist 到這裡）加進 PATH。命令字串本身沒有改。

### 3.2 補充量測（非宣告命令，供 reviewer 參考）

以下量測不綁定任何 head：

- **A/B**：把四個測試檔原樣套到 base 31785c571 上執行，有 11 個測試失敗（403 被判為非 forbidden、header 顯示 0、persona 宣稱可決策、缺少 authority 呈現）。套上本修正後全部通過。已授權空 200 的回歸測試在 base 上原本就是綠的，這符合預期。
- **整個 web vitest**：67 個檔案、715 個測試全部通過。量測時 working tree 是 anchor 10bd61a36 加上測試修改。
- **API**：`tests/security/test_operator_read_authorization.py`（含新增 3 組參數化測試）、`tests/contract/test_operator_governance_api.py`、`tests/integration/test_operator_canonical_wiring.py`、`tests/integration/test_operator_live_domain_modules.py` 全部通過。對兩個修改過的 Python 檔跑 `ruff check` 也通過（`uv run --frozen --python 3.12`）。
- 新增的 API 測試把 snapshot 回報的 `decide` 和 `POST /operator/governance/decisions` 的實際 guard 結果對照：auditor+platform_admin 與 platform_admin+operator_viewer 拿到 `decide=false` 時，POST 一律 403；operations_manager 拿到 `decide=true` 時，POST 通過 RBAC。三組都以 `ops-lead` persona 送出，用來證明 persona 不影響授權判斷。
- `delivery_toolchain/governance/check_code_boundaries.py` 通過。沒有新增需要登錄的測試檔：新測試都加在既有檔案裡。

### 3.3 測試覆蓋對照驗收

| 驗收 | 測試 |
|---|---|
| 收到 403 呈現為已驗證身分的拒絕，而非傳輸失敗 | Console mount：`NetworkConsoleScopedSnapshot.test.tsx` 中 "presents the deployed auditor+platform_admin 403 reads…"。workspace mount：`NetworkFindAreasWorkspace.route-gate.test.tsx` 中每個 tab 的 403 測試，涵蓋 overview、candidates、sitescore、review、merge-split，並檢查 correlation ID 與 HTTP 403。純函式：`productionWorkspaceData.test.tsx`。 |
| 被拒或無法取得的集合不偽裝成權威的 0 | 同上，header 的 `— …（未授權）` 與 Listing Radar gate、沒有「No listings match」也沒有「0 筆」。"keeps a genuine authorized empty 200 as an authoritative zero"、`resolveNetworkCountState` 單元測試。 |
| persona 不能宣稱業務決策權限，帳號設定權限保留 | `GovernanceWorkspace.test.tsx` 的 Console mount 三案：auditor+platform_admin 拿到 denied 且沒有核准按鈕，Users／Feature Flags 分頁仍可用；operations_manager 拿到 granted；未驗證時為 unverified。API 對照測試見 3.2。 |
| 實際寫入 | 所有新測試都斷言沒有非 GET 請求，也沒有 users／roles grant 請求。 |

## 3.4 Required-CI bundle recovery（Pi 接手）

- 原始 PR #1451 head `6d9c566eafde0d0da49b205f1b32227d97fd9eae` 的 CI `38053549581`／product-node job `114217385830` 在原有 `/operator` gzip **300.0 kB** 門檻失敗（300.4 kB）。原始 log `/tmp/odp-read-auth-integration-20261009/pr1451-product-node-failed.log` 和 GitHub comment `6097894899` 是保留的失敗收據，不是成功證據。
- `OperatorConsole.tsx` 用 `next/dynamic` 隔離只在選中時才掛載的 Governance workspace，含其 admin controllers；載入期間只顯示工作區載入訊息，不宣稱 action authority。既有 typed 403／correlation、withheld counts、server authority 與 account/config controls 都保留。
- 第一個 anchor `1c18c50755c3` 的三條原宣告驗證都通過（exit 0；diff 0.023s、四檔 vitest 10.097s、typecheck 56.986s）。補充 build 成功，但 budget 命令 **exit 1，301.3 kB**；不能把該 anchor 當成 budget repair 完成。
- 根因是 canonical `operator/page.tsx` 從 barrel 引入 client components；barrel 同時重新匯出 Governance，抵消 dormant module isolation。改為三個明確的 leaf imports，沒有更動 business／admin／password routing、cookie/session、release notice 或 API auth path。canonical task metadata 已新增這兩個 auth-page source/test 路徑與驗證命令。
- 新增 production Console Today → Govern 導航測試：Today 不掛載 Governance、不讀 snapshot；選中後合法 scoped rows 保留、server-verified read-admin 仍沒有核准控制項、Users／Feature Flags 入口仍可用，全程沒有寫入。auth-page 測試仍檢查 admin 的 server release status 與 password/business 路由。
- 最終 head 的全部宣告驗證由 `task_verification.py run` 產生 exact-command／SHA／exit code／duration receipts，包括原三條、單一 `operatorReleasePage.test.ts`、`npm run build --workspace=@oday-plus/web` 和 `npm run bundle:budget --workspace=@oday-plus/web`。送審 note 記錄確切最終 head 與量測結果；本文件不以預測結果取代收據。新增的執行不重跑全庫測試。
- **未提高 budget、刪功能、放寬 gate 或更動 roles/grants。** Required exact-head CI 和獨立 Codex review 仍是合併前提；本機 source 測試不是部署／完整產品 acceptance。

## 4. 未完成、不在本 task 範圍

- 尚未部署。合併後要由 root 以已同意的帳號，對 authenticated foreground 頁面重新採集；本 task 不執行。
- source 驗收 ≠ full acceptance。Network 403、StoreOps 503、資料／模型 hold 仍然存在。本修正只讓它們如實呈現，不讓任何 gate 變成通過。
