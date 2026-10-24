# core-ops 營運 runbook 差異（DRAFT · 未審閱）

本文件只描述 `core-ops` 相對於既有 runbook 的**差異**：

- 營運人員在模型被拒絕時會看到什麼；
- 如何驗證 release identity；
- 已知限制。

一般事故流程仍依照 `docs/runbooks/incident-management.md` 與 `docs/runbooks/observability-and-runbook.md`。

本文件尚未併入 `docs/runbooks/`。併入需要另開 task 並經審閱，因為改到 evidence 以外的路徑會讓 candidate 失效（見 `README.md` §1）。

行號以 `origin/dev` @ `5d761526` 為準。

## 1. core-ops 是什麼（給值班人員）

- **有上線、而且必須正常的功能**：登入與 session、RBAC、租戶隔離、稽核、PostgreSQL 持久化、worker 佇列（`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:24`）。
- **不宣稱就緒、而且必須如實拒絕的功能**：所有模型相依功能，包括 ForecastOps、AVM、HeatZone、SiteScore 與 Learning Hub registry（design `:160-165`）。
- **外部資料來源一律關閉**：部署時 `ODP_EXTERNAL_PROVIDER_MODE: disabled`（`.github/workflows/deploy-dev.yml:1268-1270`）。

**模型被拒絕是預期行為，不是事故。** 只有在模型功能**沒有**拒絕時才是事故：例如出現替代預測、假模型版本，或 `productionBindingsReady=true` 卻沒有核准的模型。這種情況應依 P0（資料完整性）處理，並通知 ML Risk Owner。

## 2. 驗證 release identity

### 2.1 端點

| 端點 | 回傳的關鍵欄位 | 依據 |
|---|---|---|
| `GET /platform/version` | `release_sha`、`api_version`、`correlation_id` | `apps/api/oday_api/main.py:75-82`、`:897-899` |
| `GET /platform/release-identity` | 上一列的欄位，加上 `release_profile`、`release_profile_valid`、`manifest_digest`。只讀本機部署 metadata，不觸發 readiness、provider 或 model probe | `main.py:903-912` |
| `GET /readiness` | `status`；`details.deploymentMode`；`details.releaseProfile`（含 `name`、`valid`、`modelReadinessClaimed`、`error`）；`details.models` | `main.py:780-825`、`:548-584`；`apps/api/oday_api/runtime_mode.py:52-82` |

### 2.2 core-ops production 的正確讀值

| 欄位 | 期望值 | 不符時 |
|---|---|---|
| `release_sha` | 等於 registry 的 `candidate_sha`，也就是 dispatch 時的 `release_sha` | P0：部署了非預期的版本，立即通知 rollback owner |
| `release_profile` | `core-ops` | 同上 |
| `release_profile_valid` | `true`。core-ops 只允許 dev、staging、production（`runtime_mode.py:45-49`） | readiness 會回 503（`main.py:801-802`），並在 `blockingReasons` 帶 `RELEASE_PROFILE_INVALID`（`main.py:546-547`） |
| `manifest_digest` | 等於 admission receipt 的 `manifest_digest`。部署腳本把 `MANIFEST_DIGEST` 匯出成 `ODP_RELEASE_MANIFEST_DIGEST`（`deploy_cloud_run_waji.sh:303`） | 先比對 admission receipt artifact `release-admission-receipt-production`（`deploy-dev.yml:1069-1075`） |
| `details.deploymentMode` | `production` | |
| `details.releaseProfile.modelReadinessClaimed` | `false`。只有 `full` 會是 `true`（`runtime_mode.py:79`） | |
| `details.models.productionBindingsReady` | `false`，除非真的有核准的模型 | 見 §1 |
| `details.models.blockingReasons` | 包含 `PRODUCTION_MODEL_BINDINGS_UNVERIFIED`（`main.py:524-527`） | |
| `/readiness` 的 HTTP 狀態 | **200**。模型未就緒不會讓 readiness 失敗，因為 `overall_ok` 只看 persistence、provider、live data 與 profile（`main.py:802`） | 503 代表 persistence、provider、live data 或 profile 有問題，要查 `details` |

### 2.3 怎麼讀到這些端點（**未驗證**）

- production API 是以 `--no-allow-unauthenticated` 部署的私有 service（`deploy_cloud_run_waji.sh:905`）。`/readiness` 與 `/platform/*` 只是在**應用層**不驗 bearer（design `:115`），Cloud Run 這一層仍然要求 invoker 身分。
- 部署腳本的 live gate 經由 `ODP_PROD_API_URL` 這個 custom domain 連進來，transport token 的 audience 是 API 的 Cloud Run service URL（`deploy_cloud_run_waji.sh:1224-1226`、`:1259-1261`）。
- 值班人員需要一個具 `roles/run.invoker` 的身分，以 `X-Serverless-Authorization` 或 `Authorization` 帶 ID token 讀取。具體做法與授權對象在 repo 內沒有定義，**需人工決定**。

### 2.4 部署當下的 receipt（GitHub Actions artifacts）

| Artifact | 內容 | 依據 |
|---|---|---|
| `release-admission-receipt-production` | lease 驗證結果、`manifest_digest`、`release_profile` | `deploy-dev.yml:1069-1075` |
| `runtime-release-production-validation` | preflight、smoke、migration compatibility、`live-e2e-gate.json`、job validation | `deploy-dev.yml:1638-1671` |
| `runtime-release-production-staging-closeout-receipts` | staging closeout、watch receipt、cleanup | `deploy-dev.yml:1815-1824` |

## 3. 營運人員看到的畫面（模型被拒絕時）

| 使用者 | 位置 | 看到的內容 | 依據 |
|---|---|---|---|
| 管理員（`platform_admin`） | `/operator?view=admin` | 「發布範圍與模型狀態」通知：「core-ops：核心營運功能（登入、權限、租戶隔離、稽核）已上線；本發布範圍不宣稱模型功能就緒…」。模型缺少時加一行「正式模型綁定尚未就緒；缺少模型的功能不可用，模型相依操作仍會拒絕，不提供替代預測。」，並列出不可用的模型功能（AVM、ForecastOps、HeatZone、SiteScore） | `apps/web/features/operator/OperatorReleaseNotice.tsx:20-35`；`apps/web/src/app/operator/page.tsx:31-35`；`apps/web/src/lib/auth/operatorReleaseStatus.ts:8-13` |
| 管理員，但 runtime 回報的 profile 與部署不符 | 同上 | 「無法確認發布範圍；請檢查部署設定。」 | `OperatorReleaseNotice.tsx:24`；`operatorReleaseStatus.ts:26-31` |
| 非管理員營運人員 | `/operator`（營運主控台） | **沒有**顯示這個通知；只有 `view=admin` 會 render `OperatorReleaseNotice`（`page.tsx:31-44`）。模型相依頁面實際顯示的錯誤文案，本包**未驗證** | `page.tsx:31-44` |
| API 呼叫者 | `GET /api/v1/learninghub/models` | registry 未綁定時回 **503**；registry 已綁定時才可能是 200 加空清單 | design `:34`、`:109`、`:114` |

**建議（需人工決定，屬於產品變更，不在本 task）：** 非管理員營運人員目前看不到 core-ops 範圍說明。是否要在營運主控台也顯示，由 Product Owner 決定。在此之前，請由 Support Lead 以 release notes（`release-notes-draft.md`）對使用者說明。

## 4. 症狀與處置

| 症狀 | 先檢查 | 判斷與處置 |
|---|---|---|
| 使用者回報「預測、估價、選址分數不能用」 | `/readiness` 的 `details.models` | core-ops 預期行為。回覆使用者模型功能尚未上線，不開事故 |
| 模型功能**有回傳**預測或分數 | `details.models.productionBindingsReady`、`autoSeeded`、`capabilities` | 可能是替代預測或 surrogate，以 P0 處理，通知 ML Risk Owner 與 rollback owner |
| `/readiness` 回 503 | `details.persistence`、`details.provider`、`details.data`、`details.releaseProfile` | 依 `blockingReasons` 判斷。若是 `RELEASE_PROFILE_INVALID`，代表部署設定錯誤，屬 P0 |
| 無法登入、登入後被要求換密碼 | 帳號是否 `must_change=true` | 首次登入必須換密碼，未換之前 API 回 403 `PASSWORD_CHANGE_REQUIRED`（`apps/api/oday_api/security/dependencies.py:280-310`）。這是預期行為 |
| 管理員看不到營運資料 | 角色 | `platform_admin` 只有 feature_flag、user、role 權限，沒有業務資料可見性（`shared/auth/rbac.py:75-81`）。這是預期行為；營運工作要用營運角色帳號 |
| 跨租戶資料出現 | audit log 中的 `operator.tenant_isolation` | P0（權限繞過），依 `incident-management.md:11` |
| audit 寫入失敗 | `audit-write-failure` 告警。注意：告警目前沒有接到真實通道（見 `monitoring-oncall-plan.md`） | P0／P1（`observability-and-runbook.md:116`） |
| 外部資料沒有更新 | provider 模式 | core-ops 下外部來源一律關閉，這是預期行為 |

## 5. 已知限制

1. **模型功能不可用**，不提供替代預測（design `:160-165`）。`forecast-daily-failed`、`model-drift-high` 告警與 `forecast-batch-score` SLO 在 core-ops 下沒有意義（`infra/monitoring/alerts.json:23`、`:78`；`infra/monitoring/slo.json:22-26`）。
2. **完整產品驗收不成立。** core-ops 的 `evaluate_full_acceptance` 一律是 `NOT_ADMITTED`（design `:43`），live gate receipt 寫著 `full_product_acceptance_claimed=false`（design `:14`）。
3. **告警沒有接到真實通道，watch 是純人工**，見 `monitoring-oncall-plan.md`。
4. **watch window 期間沒有自動回滾**，見 `rollback-plan.md` §1。
5. **staging 的 `worker_idempotency` stage 有既有問題。** 佇列為空時，它可能以 `required_job_not_claimed` 失敗（design `:285`）。
6. **帳號模式是 invite-only。**
   - 第一個 `platform_admin` 只能由部署時的 bootstrap 建立，而且 `must_change=true`（`shared/identity/bootstrap.py:6-9`）。
   - 其他營運帳號由 `platform_admin` 發邀請，接受後同樣是 `must_change=true`（`shared/identity/invitation_service.py:15-18`）。
7. **非管理員營運人員看不到 core-ops 範圍通知**（§3）。
8. **認證模式預設是 password-first，OIDC 是選用。** 兩個變數不一致時，preflight 會失敗（`deploy-dev.yml:1245-1252`）。
