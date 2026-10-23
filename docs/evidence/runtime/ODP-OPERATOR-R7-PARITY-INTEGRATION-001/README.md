# ODP-OPERATOR-R7-PARITY-INTEGRATION-001：收編 PR #1470 的 R7 營運工作流整合

## 決策依據與交付方式

- 2026-10-20 負責人蔡尚志裁示：draft PR #1470「登記成 task 送審」，由獨立 reviewer 審查。
- 2026-10-22 負責人裁示「全部由 Claude 主導」，Codex（Root）停止推進 #1470，並採 blocker 選項 1：
  從最新 `origin/dev` 開出，以**單一 task-id commit** 承載「`codex/operator-r7-parity-20261012` 最新 head 與 `origin/dev` 合併後」的完整 tree。
- 原因：`task_finalize.sh` 的 `check_task_delivery_identity` 要求 `origin/dev..HEAD` 每個非 merge commit 的 subject 都以本 task id 開頭。保留 #1470 的 129 個 Root commit 就無法送審。
- 原 codex 分支與 PR #1470 原樣保留為歷史：不改寫、不 force push、不刪除。

### 分支

- 送審分支是 `task/ODP-OPERATOR-R7-PARITY-INTEGRATION-001-clean`。
- 舊的 `task/ODP-OPERATOR-R7-PARITY-INTEGRATION-001` 在 origin 上指向 `df300dc1`，也就是 #1470 的舊 head、整串 Root 歷史。要把它換成單一 commit 只能 force push，但 permission broker 禁止 force push 與刪除遠端分支。
- 所以改用 `-clean` 分支，再以 `retarget_branch` 指向它。這是 `supervisor.review_submission_is_complete` 與 `resolve_task_sha` 明文支援的「舊 PR ref 保留作稽核、另開乾淨替代分支」路徑。
- 舊 ref 保留不動。

## 來源與 base 實測（2026-10-22）

| 項目 | 值 |
|---|---|
| codex 分支最新 head | `1f074f0d400f8fb0b2b30bc690051f4ceaad579e`（`origin/codex/operator-r7-parity-20261012`，也是 #1470 目前 head） |
| `origin/dev` | `1240dbe488dd`（#1581，ODP-OPERATOR-WORKSPACE-RESET-261021） |
| merge-base | `492dd4deb`（#1564）；codex head 之後 dev 只多 3 個 PR：#1579、#1578、#1581，共 9 檔 |
| `git merge-tree --write-tree 1f074f0d4 origin/dev` | 3 檔衝突，見下節 |
| 本 commit 對 `origin/dev` | 877 檔，+221,398／−12,954 行；新增 519 檔、修改 358 檔、刪除 0 檔 |

### df300dc1 之後 codex 分支新增的 commit

這幾個 commit 在 2026-10-20 第一版 README 之後才進 codex 分支，本 commit 一併承載：

| commit | task id | 內容 |
|---|---|---|
| `1e3c71dd3` | ODP-ECON-FITOUT-SALVAGE-WIRING-261020 | site_economics 裝修殘值現金流 |
| `44982bf81` | ODP-PR1470-DEV-INTEGRATION-261020 | 把當時的 dev（含 #1562 core-ops profile、#1564 staging live gate）合進 #1470 |
| `7d0945805` | ODP-ECON-NUMERIC-ROUNDTRIP-261021 | 財務輸出數值 round-trip 穩定 |
| `b5588835a` | ODP-DEPENDENCY-SECURITY-PATCH-261021 | uv.lock／SBOM／NOTICE 相依修補 |
| `dc1672896` | ODP-ECON-CAMERA-FURNITURE-KINDS-261021 | camera／furniture 資產種類 |
| `0b31ad57d` | ODP-FULL-ACCEPTANCE-CALLER-261021 | `deploy_cloud_run_waji.sh` 只在 `ODP_RELEASE_PROFILE` 為 `full`（含未設定）時，對 live E2E gate 加 `--require-full-acceptance` |
| `1f074f0d4` | ODP-FULL-ACCEPTANCE-CALLER-261021 | production 非 digest 部署在建 tag 前直接拒絕 |

ODP-FULL-ACCEPTANCE-CALLER-261021 對 core-ops 不會變嚴：條件是 `"${ODP_RELEASE_PROFILE:-full}" = "full"`，所以 `core-ops` 與 `dev-admin` 不會加這個旗標。digest 檢查只針對 `prod|production`。dev 部署的 `ODP_RELEASE_PROFILE` 來自 admission 輸出（`deploy-dev.yml` 1056–1064 行）；profile 空白會直接 error，不會悄悄落回 `full`。

## 與 dev 的衝突與取捨

dev 側的 9 檔裡，6 檔自動合併：

- `NOTICE-THIRD-PARTY.md`、`uv.lock`：與 codex head 完全相同，因為兩邊都帶了 DEPENDENCY-SECURITY-PATCH-261021。
- `apps/web/features/operator/OperatorConsole.tsx`：dev 的 `useLayoutEffect` 已保留。
- market intelligence 的 `data_platform_client.py`、`repositories.py`、`tests/contract/test_market_intelligence_api.py`：照 dev。

3 檔衝突的處理如下。

1. **`apps/web/features/operator/__tests__/OperatorConsole.test.tsx`**：兩邊都只新增測試，採聯集。
   - 保留 codex 的兩組測試：Back/Forward grant、pending history intent。
   - 保留 dev 的「keeps Store Ops selected ... only while bootstrap grants」。
   - import 合併為 `Profiler` 加上 `act, cleanup, ...`。
   - dev 這組測試在 codex 的授權模型下要做兩處必要調整，斷言本身未改：
     - 改用檔內既有的 `stubAuthenticatedFetch`，並 `waitFor` bootstrap fetch 發出。codex 版的 production bootstrap 會先打 `/auth/session`，這是檔內其他測試的共同前提。
     - bootstrap 回應把 grants 同時投影到 `meta.role`。codex 版以已驗證主體的 `meta.role.allowedWorkspaces` 為授權來源，`navigation` 只是鏡像；檔內 codex 測試的 `projectedRole` 也是這樣寫。只授權 `today` 的那組仍驗 fail-closed。
   - A/B 實測：把 dev 的 `useLayoutEffect` 暫改回 `useEffect`，這組測試仍 20/20 通過。原因是 codex 版另有 `workspaceIntent` 防護（pending effect 不覆寫較新的點選）。所以在合併後的 tree 裡，這組測試**不能單獨鑑別** layoutEffect 回歸。兩道防護都保留。
2. **`tests/e2e/operator-store-ops.spec.ts`**（camera evidence 測試）：
   - 保留 dev 新增的 URL `ws=store` 與 `data-screen-label="Store Ops 門市營運"` 等待。
   - 接著使用 codex 的 camera tile 斷言（影像內容不可用、無媒體元素、tile 內的「填寫調閱目的」按鈕）。
   - dev 那行沿用自 base 的 `影像鎖定 • 需授權` 不保留：codex 已把該處改成 tile 斷言，兩邊在 base 上的意圖都已涵蓋。
3. **`docs/evidence/sbom.json`**：不手改。合併後以 `delivery_toolchain/security/generate_sbom.py` 重產，工作樹裝有 `node_modules`，supplier 數 536 與 codex head 相同。`--check` 通過。對 codex head 只差 6 行（dev 的相依修補）。

### 送審後的追加 commit：pypdf CVE

#1583 第一輪 CI 的 product-security 紅在 pip-audit：`pypdf 6.20.1 (CVE-2026-113786／GHSA-5hx5-vpf9-5wcv)`。

- pypdf 由 #1470 引入（`pyproject.toml` 的 `pypdf>=6.20,<7`），dev 沒有這個套件，所以只能在本分支修。
- 第一顆 commit 已推送，不能改寫，因此另加一顆同 task id 的 commit。
- 修法：用 `uv lock --upgrade-package pypdf==6.21.1` 只升級 pypdf（PyPI 標示的最小修正版），再以原 generator 重產 `NOTICE-THIRD-PARTY.md` 與 `docs/evidence/sbom.json`。
- 本機 `make dependency-audit`：PASS，216 個相依。

### 送審後的追加 commit：#1583 第一輪 CI 的 node／unit 紅燈

CI run 39812391674（head `61ae630c`）除上面的 security 外還有兩項紅：

1. **product-node**：只有 `OperatorConsole.workspaceIntent.test.tsx` 兩例（fixture、authenticated）失敗，訊息 `expected null to be type of 'function'`。
   - 原因：這支測試來自 #1470，攔的是 `useEffect`。dev 的 ODP-OPERATOR-WORKSPACE-RESET-261021（#1581）已把偏好驗證改成 `useLayoutEffect`，deps 順序不變，所以測試永遠攔不到那個 effect。
   - 修法：測試改攔 `useLayoutEffect`，比對條件（6 個 deps、`today`、模式、hydrated、狀態）不變。元件與 `workspaceIntent` guard 都沒改，沒有回退 dev 的修正。
   - 本機：修正後 2/2 通過。暫時拿掉元件的 `if (workspaceIntent.current !== activeWorkspaceId) return;` 再跑，兩例都失敗（`expected 'today' to be 'network'`），之後已還原元件。
2. **product-lint-unit**：14 個 SiteScore 測試都是 `feature snapshot is stale`。
   - 原因：`test_sitescore_production_runtime.py` 與 `tests/integration/test_production_model_runtime.py` 的 `NOW` 固定在 2026-07-24。`modules/sitescore/domain/scoring.py` 用牆鐘和 `SITESCORE_FEATURE_MAX_AGE`（90 天）檢查新鮮度，所以 2026-10-22 之後就過期。dev 上這兩支測試也是同樣的固定日期。`test_intake_score_worker.py` 匯入同一個 `NOW`。
   - 修法：只把兩個 fixture 的 `NOW` 改成「牆鐘減 1 小時」，寫法與 open PR #1565 對同兩檔的改法逐字相同，避免出現兩套做法。正式程式的時間檢查、90 天門檻、未來時間拒絕都沒改。
   - 本機：`uv run --frozen pytest modules/sitescore/tests/test_intake_score_worker.py modules/sitescore/tests/test_sitescore_production_runtime.py tests/integration/test_production_model_runtime.py tests/unit/test_candidate_score_context.py` exit 0。

### 送審後的追加 commit：#1583 第二輪 CI（head `be799011`，run 39919628893）

1. **product-security**：pip-audit 回報上游新公告的 CVE，dev 的 `uv.lock` 也是同樣版本，與 #1470 內容無關。
   - `gitpython 3.1.61`（CVE-2026-114145，最小修正 3.2.1）、`graphql-core 3.2.11`（CVE-2026-75507／75508，最小修正 3.2.12）、`opentelemetry-api 1.44.0`（CVE-2026-114603，最小修正 1.45.1）。
   - commit `68de9f24` 曾在本分支自行升這三個套件；這與 dev 上既有的 ODP-DEV-DEPENDENCY-CVE-PATCH-261023（Claude2／Codex2，候選 commit `ba3227a99`，升到相同版本）重複。依 #1563 於 2026-10-23 07:56 的更正，本分支不另做這組升版，已把 `uv.lock`、`NOTICE-THIRD-PARTY.md`、`docs/evidence/sbom.json` 還原為 `be799011` 的內容。
   - 因此在 ODP-DEV-DEPENDENCY-CVE-PATCH-261023 經獨立審查合入 dev 之前，本 PR 的 product-security 會維持紅燈。合入後沿原 base advance（merge origin/dev）整合，並以原 generator 重產 NOTICE／SBOM，再送同一個 #1583。
2. **product-lint-unit**：11670 通過、1 失敗：`tests/integration/test_intervention_workflow.py::test_sqlite_adjust_revalidation_stale_window_rejected_against_competitor_assignment`（`resume_adjust.wait(10)` 逾時，adjust 回 500）。
   - 這支測試來自 dev，本分支沒有改它。第一輪 CI（run 39812391674）同一支測試沒有失敗；本機在本分支連跑 6 次 `-k stale_window`（兩支測試）都是 12/12 通過。
   - 判定為 CI 長時間（34 分鐘）負載下的計時 flake，本 commit 不改測試或程式，以新 head 的 CI 為準。若新 head 又在同一支紅，就不是 flake，要回頭查 #1470 對 intervention adjust 路徑的鎖序變更。
3. **product**：只是彙整上面兩條 lane，跟著紅。

dev 上沒有與 #1470 同名、已單獨合併的 task，所以沒有「以 dev 為準而丟掉 #1470 內容」的檔案。

## 本 commit tree 與「codex head merge origin/dev」的對照

基準是 `git merge-tree --write-tree 1f074f0d4 origin/dev` = `178a14e10`（3 個衝突檔仍帶衝突標記）。本 commit tree 與它只差下列 11 檔：

| 檔案 | 差異原因 |
|---|---|
| `OperatorConsole.test.tsx` | 衝突解法（見上） |
| `tests/e2e/operator-store-ops.spec.ts` | 衝突解法（見上） |
| `docs/evidence/sbom.json` | 以原 generator 重產 |
| `apps/web/features/operator/network/__tests__/ReviewDecisionAdmission.real-dom.test.tsx` | 刪 EOF 多餘空行 |
| `apps/web/features/operator/shellEnvelope.ts` | 刪 EOF 多餘空行 |
| `infra/db/migrations/000030_intake_audit_retention_kind.sql` | 刪 EOF 多餘空行；digest 由 `tests/ops/test_migration_backfill.py` 執行時計算，沒有寫死的 manifest |
| `modules/opsboard/audit/application/evidence_general_document.py` | 刪 EOF 多餘空行 |
| `tests/models/test_sitescore_v3_contract.py` | 刪 EOF 多餘空行 |
| `tests/security/test_feature_flag_admin_authorization.py` | 刪 EOF 多餘空行 |
| `product_ops/deployment/validate_cloud_run_live_deployment.py` | 只調 import 順序（`gcs_identity` 排到 `worm` 前） |
| 本 README | 新增 |

EOF 修正只刪檔尾空行，讓宣告的 `git diff --check origin/dev...HEAD` 通過，沒有改程式內容。

import 順序的修正來自 `task_finalize.sh` 的 ruff 前置檢查，也就是 CI product job 的 lint step。這個 I001 錯誤在 codex head 就已存在；`product_ops` 不在本 task 宣告的 ruff 目錄內，所以宣告驗證抓不到。修正以 `ruff check --fix` 產生，只交換兩行 import。

對 codex head `1f074f0d4` 本身：本 commit 差 15 檔。其中 7 檔是 dev 側內容：OperatorConsole.tsx、其測試、e2e spec、SBOM、market intelligence 三檔。dev 側另 2 檔 NOTICE 與 uv.lock 與 codex head 相同。其餘是上表的 EOF 修正、import 順序與 README。

## #1470 實際包含的變更範圍

以下依 `git diff --name-status` 與各 commit 實測整理，不照抄 PR body 的結論。

### 產品程式

- **Operator Web（`apps/web`）**：R7／Package10 營運工作台。範圍包含 Store Ops、Growth、Network（Find Areas、Intake、Review、HeatZone）、PriceOps、Governance、Today、Task Center，以及 Console 的 URL 狀態。具體有：
  - 個人 listing 篩選
  - 季度區域規劃草稿
  - 現場勘查九項實體觀察與獨立審查面板
  - 1024px 三欄版面與 sticky rail
  - HeatZone 精確導覽與 location epoch 失效
  - pending preference effect 不會覆寫最新的 workspace 選擇
- **API（`apps/api`）**：
  - 新增 `operator_modules/area_planning_drafts.py`、`listing_searches.py`、`netplan_builder.py`、`avm_dealroom.py`、`market_survey_assignments.py`、`adlift_campaigns.py`、`_native_request_owner.py`
  - 修改 operator、listings、sitescore、netplan、priceops、audit 等路由，以及 `oday_api/security/dependencies.py`、`main.py`、`server.py`
- **Worker（`apps/worker`）**：新增 consumer：canonical_audit、durable_audit、listing_search、retained_evidence、store_ops_audit。
- **稽核（`shared/audit`）**：
  - canonical PostgreSQL audit writer、durable／retained delivery、backlog 守門、GCS workload identity
  - `worm.py` 新增 writer／recovery service account。只有 production 在缺少時 fail-closed
- **授權（`shared/auth`）**：`engine.py`、`rbac.py`。
- **領域模組**：opsboard、listing、sitescore、site_economics、priceops、netplan、market_survey、learninghub、adlift、intervention、forecastops、avm；另有 `models/shared_ml/current_model_admission.py`。
- **G2 與附屬設備**：
  - G2 新模擬要求三大三中的 all-in-one combo
  - 支付系統、kiosk、販賣機走既有的資產／經濟路徑
- **現場勘查**：
  - canonical 獨立審查：審查人不得是提交者，review／audit／receipt／WORM intent 同一個 SQL 交易提交
  - stale authority 撤回
  - 缺排水證據時進入 FIELD_SURVEY
- **SiteScore／NetPlan／Feasibility**：結果綁 released context 的 digest。NetPlan admission 拒收 provenance 衝突的 score 文件。
- **MLflow**：設定缺失時，模型能力標為不可用，不讓 API 建構中止。

### 資料庫

- 新增 SQL migration `000029`～`000031`、Alembic `0023`、`0024`。`origin/dev` 最新 revision 仍是 0022，沒有號碼衝突。
- 修改既有的 `000008_postgresql_runtime_persistence.sql`。

### 基礎設施與部署（合併前須確認）

- `infra/terraform/main.tf`：
  - Web local JWT audience 改為 `join(",", local.service_auth_audiences)`
  - 新增 `iamcredentials.googleapis.com` 與兩個 WORM service account env
- `infra/terraform/audit/main.tf`：**新增 IAM 資源定義**：
  - `recovery_reader` service account
  - 只含 `storage.objects.get` 的 custom role 與 bucket binding
  - product runtime 對 recovery reader 的 `roles/iam.serviceAccountTokenCreator`
- `cloud_run.tf`、`modules/ephemeral_staging/main.tf`：API liveness probe 從 `/healthz` 改為 `/liveness`；`/liveness` 路由在同一個 commit 內。
- `.github/workflows/deploy-dev.yml`：新增 `ODP_AUDIT_WORM_SINK_URI`、`ODP_AUDIT_WORM_WRITER_SERVICE_ACCOUNT`、`ODP_AUDIT_WORM_RECOVERY_SERVICE_ACCOUNT`，由 `deploy_cloud_run_waji.sh` 轉發給 API。

**dev 自動部署影響實測（2026-10-22）**：

- 以 `gh api --paginate` 讀 GitHub `dev` environment，共 45 個 variable，**沒有**任何 WORM／AUDIT variable，三個 env 會解析為空字串。
- `deploy_cloud_run_waji.sh` 的 serializer 會照原樣轉發空字串。
- `shared/audit/worm.py` 在 `ODP_DEPLOY_ENV=dev` 且 sink 為空時，使用 local append-only sink，與合併前相同，不會 raise。
- `validate_cloud_run_live_deployment.preflight_checks` 只在 `prod|production` 檢查 WORM sink 與 service account。
- deploy-dev 不執行 terraform plan／apply，所以合併不會套用上述 IAM 定義。
- 結論：**dev 部署不會因為缺這三個 variable 而失敗**，合併 dev 不需要 Human/Ops 前置。
- staging／production 上線前，則必須由人設定這三個 variable 並套用 `infra/terraform/audit` 的 IAM；production preflight 與 runtime 都會 fail-closed。本 task 不建立 IAM、variable 或 secret。

### 生成物、文件與治理

- OpenAPI（`packages/openapi-client/*` 等）、`docs/audits/code-boundary-inventory.csv`：#1470 已用原 generator 重產。
- 本 commit 再實測：
  - `delivery_toolchain/openapi/check_drift.py --base-ref origin/dev`：PASS，151 additive、10 approved breaking、0 unapproved
  - `check_code_boundaries.py`：PASS
  - `generate_oss_notice.py --check`：一致
  - `uv lock --check`：一致
- `docs/evidence/human-decisions/HUMAN-OSS-LEGAL-APPROVAL-001/README.md`：#1470 修改了「尚未涵蓋與限制」段落。核准 JSON 與 digest 未改，請 reviewer 確認沒有改寫核准本身。
- 根目錄 `AGENTS.md`（開發與修正原則），並登記到 delivery governance 分類。

## PR #1470 自述仍未完成（依 PR body）

- trusted production decision producer；native area-draft materialization
- trusted source／model calibration、可執行的 MLflow 模型、核定的 G2 設備參數與 cycle／throughput 校準
- financial producer composition；正式 raw financial inputs 尚未對照 EMGI 文件驗證
- 部署端：service identity、API audience、invoker、deployed SHA 都未驗證。BFF 曾回 503 `WEB_SERVICE_IDENTITY_NOT_CONFIGURED`
- canonical Terraform state ownership、private regional HA／CMEK、restore evidence、billing
- 真人 UAT 0/55；production GO=false
- Data engineering 候選 R12 不在本分支
- SDK proposed schema 只由 9 個既有 wire 範例驗證
- HeatZone／Terraform audience 的新測試是靜態 wiring 證據，沒有做 Terraform evaluation

## 已整合的 artifact-only handoff task

| Handoff task | 採入 commit |
|---|---|
| ODP-NETWORK-HEATZONE-EPOCH-FIX-261018 | `abd474d6e` |
| ODP-HEATZONE-HTTP-SCOPE-FIXTURE-261018 | `abd474d6e` |
| ODP-OPERATOR-HEATZONE-URL-CONVERGENCE-261019 | `abd474d6e`、`7ef27d30d`、`3f76b39e1`、`8534c1414` |
| ODP-R7-EXISTING-READINESS-STICKY-FIX-261019 | `855d45885`（只採 CSS 與 visual spec；條件式 PriceOps TSX 未採入） |
| ODP-XR-EXISTING-CONSUMER-FIXTURES-261019 | `f7e076a6f` |
| ODP-R7-FILTER-ATOMIC-GEOMETRY-261019 | `42977ecd2` |
| ODP-TF-EXISTING-LOCAL-AUDIENCE-261019 | `df300dc14` |
| ODP-ECON-FITOUT-SALVAGE-WIRING-261020、ODP-PR1470-DEV-INTEGRATION-261020 | `1e3c71dd3`、`44982bf81` |
| ODP-ECON-NUMERIC-ROUNDTRIP-261021、ODP-DEPENDENCY-SECURITY-PATCH-261021、ODP-ECON-CAMERA-FURNITURE-KINDS-261021、ODP-FULL-ACCEPTANCE-CALLER-261021 | `7d0945805`、`b5588835a`、`dc1672896`、`0b31ad57d`、`1f074f0d4` |

未採入或已被取代的部分：

- ODP-NETWORK-HEATZONE-LINK-CONSUMER-261018：由 EPOCH-FIX 取代。
- ODP-HEATZONE-NATIVE-HTTP-CONTROLS-261018：fixture 由 HTTP-SCOPE-FIXTURE 重做。
- 診斷或失敗產物：ODP-R7-TABLET-EXISTING-GRID-FIX-261019、ODP-R7-SHELL-DESIGN-COMPARISON-261019、ODP-EXISTING-NATIVE-BROWSER-READY-FLOW-261019。
- ODP-SITESCORE-NATIVE-OVERRIDE-261018：仍是 artifact-only，不在本 commit。
- 文件類（ODP-UAT-*、ODP-SPEC-UAT-MATRIX-261018 等）與資料平台類（DPF-*、ODP-NATIVE-*、ODP-NLSC-* 等）：不在本 repo diff。

## 其他 codex/* PR 的涵蓋情形（2026-10-22 以 `git merge-base --is-ancestor`／`git cherry` 對 `1f074f0d4` 實測）

- **已涵蓋**：送審後留言指向新 PR 並關閉，不刪分支。
  - #1460、#1461、#1462、#1463、#1464、#1465、#1467、#1468、#1469：head 是 `1f074f0d4` 的祖先。
  - #1466：head `a64717b18` 不是祖先，但 `git cherry` 判定已有 patch 等價 commit（`14cd1f199`）。
- **未涵蓋**：本 task 不處理，保持開啟。
  - #1472、#1473（同 head `ddd872d69`，9 個 commit 不在本 commit 內）
  - #1474（10 個）、#1475（14 個）、#1476（18 個）、#1477（102 個）

## 給 reviewer 的重點

1. 權限與租戶範圍只收緊、不放寬：`shared/auth/rbac.py`、`engine.py`、`oday_api/security/dependencies.py`、`_native_request_owner.py`，以及各 operator 路由的 scope 檢查。
2. SQL 交易與稽核／WORM 同一個交易提交：`shared/audit/canonical_postgresql.py`、field review、Intake／Store Ops command。
3. 沒有以 fixture 或 synthetic 冒充 production 資料或模型：`models/shared_ml/current_model_admission.py`、G2／ancillary、`ODP_EXTERNAL_PROVIDER_MODE`。
4. Terraform audience 與 audit IAM 對 dev／staging／production 的影響：見上方「基礎設施與部署」。
5. ODP-FULL-ACCEPTANCE-CALLER-261021 對 core-ops 不會變嚴：見「df300dc1 之後 codex 分支新增的 commit」。
6. 衝突解法中 dev 測試的兩處調整：見「與 dev 的衝突與取捨」第 1 點。

## 驗證

- 宣告驗證（`git diff --check origin/dev...HEAD`、`uv run --frozen ruff check apps modules shared delivery_toolchain tests`、`npm --prefix apps/web run typecheck`）由 `task_verification.py run` 在送審 head 各跑一次。receipt 在 supervisor evidence store，數值在送審後以 note 公布。
- commit 前在工作樹另外實跑：
  - `npx vitest run features/operator/__tests__/OperatorConsole.test.tsx`：20/20
  - `pytest tests/contract/test_market_intelligence_api.py tests/contract/test_openapi_artifact_and_client.py tests/security/test_oss_license_gate.py tests/security/test_supply_chain_security_gate.py`：exit 0
- `tests/e2e/operator-store-ops.spec.ts` 沒有在本機跑 Playwright，以新 PR 的 product-e2e-gate 為準。
- exact submitted head 的 required CI（orchestrator、product、product-e2e-gate）以新 PR 上的 GitHub checks 為準。
- 本 task 不部署、不觸發 deploy workflow，也不修改 RELEASE_GATE_REGISTRY、IAM、secret 或 live config。
