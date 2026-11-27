# core-ops 營運 runbook 差異（DRAFT · 未審閱 · 2026-11-27 版）

本文件只描述 `core-ops` 相對於既有 runbook 的**差異**：

- 營運人員在模型被拒絕時會看到什麼；
- 值班人員如何驗證 release identity；
- 首次 release 已 commit 後的下線程序（負責人決定 D）；
- 已知限制。

一般事故流程仍依照 `docs/runbooks/incident-management.md` 與 `docs/runbooks/observability-and-runbook.md`。

本文件尚未併入 `docs/runbooks/`。併入需要另開 task 並經審閱。`docs/runbooks/` 屬於 evidence-only 路徑（`delivery_toolchain/e2e/check_release_gate_registry.py:710`），所以併入**不會**讓 candidate 失效，凍結期間也可以合併（定義見 `README.md` §1）。

- 行號以 `origin/dev` @ `48c5271b5d882d824d432f3483ab4415030bb8e6` 為準。
- GCP／GitHub 現況引用 `live-readback-20261127.md`（`L-…`）；負責人決定 A–H 見同檔 §2。

## 1. core-ops 是什麼（給值班人員）

- **有上線、而且必須正常的功能**：登入與 session、RBAC、租戶隔離、稽核、PostgreSQL 持久化、worker 佇列（`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:22-24`）。
- **不宣稱就緒、而且必須如實拒絕的功能**：所有模型相依功能，包括 ForecastOps、AVM、HeatZone、SiteScore 與 Learning Hub registry（design `:240-245`）。
- **外部資料來源一律關閉**：部署時 `ODP_EXTERNAL_PROVIDER_MODE: disabled`（`.github/workflows/deploy-dev.yml:1745`）。
- **登入方式**：production 範本是本機密碼（`auth_mode = "local"`，D15），沒有 OIDC（`infra/terraform/env/prod.tfvars.example:4-6`、`:15`）。

**模型被拒絕是預期行為，不是事故。** 只有在模型功能**沒有**拒絕時才是事故：例如出現替代預測、假模型版本，或 `productionBindingsReady=true` 卻沒有核准的模型。這種情況依 P0（資料完整性）處理。

## 2. 驗證 release identity

### 2.1 端點

| 端點 | 回傳的關鍵欄位 | 依據 |
|---|---|---|
| `GET /platform/version` | `release_sha`、`api_version`、`correlation_id` | `apps/api/oday_api/main.py:75-82`、`:906-908` |
| `GET /api/v1/platform/release-identity` | 上一列的欄位，加上 `release_profile`、`release_profile_valid`、`manifest_digest`。只讀本機部署 metadata，不觸發 readiness、provider 或 model probe。經 Web 讀取時，Web 另外加上 `web_release_sha`、`web_release_profile`、`web_manifest_digest` | `main.py:912-921`、`:965`；`apps/web/src/app/api/v1/[...path]/route.ts:18-32` |
| `GET /readiness` | `status`；`details.deploymentMode`；`details.releaseProfile`；`details.models` | `main.py:789-835`、`:526-560`；`apps/api/oday_api/runtime_mode.py:52-82` |

### 2.2 core-ops production 的正確讀值

| 欄位 | 期望值 | 不符時 |
|---|---|---|
| `release_sha`（與 `web_release_sha`） | 等於 registry 的 `candidate_sha`，也就是 dispatch 時的 `release_sha` | P0：部署了非預期的版本。依 §6 判斷是否撤銷 |
| `release_profile`（與 `web_release_profile`） | `core-ops` | 同上 |
| `release_profile_valid` | `true`。core-ops 只允許 dev、staging、production（`runtime_mode.py:45-49`） | readiness 回 503，並在 `blockingReasons` 帶 `RELEASE_PROFILE_INVALID`（`main.py:556`、`:811`） |
| `manifest_digest`（與 `web_manifest_digest`） | 等於 admission receipt 的 `manifest_digest`。部署腳本把 `MANIFEST_DIGEST` 匯出成 `ODP_RELEASE_MANIFEST_DIGEST`（`deploy_cloud_run_waji.sh:415`） | 比對 artifact `release-admission-receipt-production`（`deploy-dev.yml:1443-1449`） |
| `details.deploymentMode` | `production` | |
| `details.releaseProfile.modelReadinessClaimed` | `false`。只有 `full` 會是 `true`（`runtime_mode.py:79`） | |
| `details.models.productionBindingsReady` | `false`，除非真的有核准的模型 | 見 §1 |
| `details.models.blockingReasons` | 包含 `PRODUCTION_MODEL_BINDINGS_UNVERIFIED`（`main.py:533-537`） | |
| `/readiness` 的 HTTP 狀態 | **200**。`overall_ok` 只看 persistence、provider、live data 與 profile（`main.py:811`） | 503 代表其中一項有問題，看 `details` |

### 2.3 值班人員怎麼讀（含所需身分）

production 的 API 是私有 service（`--no-allow-unauthenticated`，`deploy_cloud_run_waji.sh:1148`），ingress 是 `internal-and-cloud-load-balancing`（`:271`），所以 `*.run.app` 網址從外部讀不到。有兩條路：

**路徑 A：經 Web 讀（建議，不需要額外 IAM）**

1. 以任一 production 帳號登入 `https://console.oday-plus.com.tw`。首次登入的帳號必須先換密碼（`apps/api/oday_api/security/dependencies.py:306-332`）。
2. 在同一個瀏覽器開 `https://console.oday-plus.com.tw/api/v1/platform/release-identity`。
3. 確認 API 與 Web 兩組欄位都符合 §2.2。

- 所需身分：一個有效的 production Web session。production 的 Web BFF 沒有 session 會回 401 `WEB_SESSION_REQUIRED`（`apps/web/src/lib/auth/proxy.ts:136-159`）。端點本身沒有角色要求（`main.py:912-921`）。
- 這條路**同時**證明 Web 與 API 的版本；部署工具也是用這條路讀（`delivery_toolchain/release/provision_dev_smoke.py:845-853`）。
- 依 §6 撤銷 Web 的 `allUsers` 之後，這條路就讀不到。這時要用路徑 B。

**路徑 B：直接讀 API（需要 invoker 身分）**

```bash
curl -sS -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://api.oday-plus.com.tw/api/v1/platform/release-identity
```

- 所需身分：對 `oday-prod-api` 有 `roles/run.invoker`。部署腳本只授予 smoke 身分、runtime 身分、Cloud Monitoring 的通知 service agent，以及 `ODP_PROD_API_INVOKER_GROUP`（若有設定）（`deploy_cloud_run_waji.sh:1028-1039`、`:1163`；`cloud_run_release_traffic.sh:150-160`）。
- `ODP_PROD_API_INVOKER_GROUP` 目前**沒有設定**（`L-4.4`），所以目前沒有任何人類身分能走路徑 B。要讓值班人員能用，需要負責人決定（HD-22）：
  - 設定 `ODP_PROD_API_INVOKER_GROUP`（一個 Google Group email，`deploy_cloud_run_waji.sh:165-168`），並把值班人員加入。binding 在部署時才套用，所以要在首次 production 部署**之前**設好。
  - 或另行授權，例如讓值班人員能以 smoke 身分簽 ID token。這需要另外的 IAM 決定。
- 上面的 `curl` 形式**未在 production 驗證**。

### 2.4 部署當下的 receipt（GitHub Actions artifacts）

| Artifact | 內容 | 依據 |
|---|---|---|
| `release-admission-receipt-production` | lease 驗證結果、`manifest_digest`、`release_profile` | `deploy-dev.yml:1443-1449` |
| `production-image-promotion-receipt-<sha>-<attempt>` | 四個 image 的來源、promoted reference、digest、簽章與 attestation | `deploy-dev.yml:2025-2032` |
| `production-bluegreen-prerelease-state-<sha>-<attempt>` | 切換前的 API／Web traffic 與兩個 scheduler 的 snapshot；手動 rollback 的 `--state-file` 來源 | `deploy-dev.yml:2076-2083` |
| `production-predeploy-cloud-sql-backup-<sha>-<attempt>` | migration 前備份的 backup ID 與狀態 | `deploy-dev.yml:2168-2175` |
| `runtime-release-production-validation` | preflight、smoke、migration compatibility、`live-e2e-gate.json`、job validation、public-egress probe | `deploy-dev.yml:2415-2449` |
| `runtime-release-production-staging-closeout-receipts-<attempt>` | staging closeout、watch receipt、cleanup。watch receipt 不存在時只有 environment receipt | `deploy-dev.yml:2725-2735` |

## 3. 營運人員看到的畫面（模型被拒絕時）

| 使用者 | 位置 | 看到的內容 | 依據 |
|---|---|---|---|
| 管理員（`platform_admin`） | `/operator?view=admin` | 「發布範圍與模型狀態」通知：「core-ops：核心營運功能（登入、權限、租戶隔離、稽核）已上線；本發布範圍不宣稱模型功能就緒…」。模型缺少時加一行「正式模型綁定尚未就緒；缺少模型的功能不可用，模型相依操作仍會拒絕，不提供替代預測。」，並列出不可用的模型功能（AVM、ForecastOps、HeatZone、SiteScore） | `apps/web/features/operator/OperatorReleaseNotice.tsx:20-35`；`apps/web/src/app/operator/page.tsx:31-35`；`apps/web/src/lib/auth/operatorReleaseStatus.ts:8-13` |
| 管理員，但 runtime 回報的 profile 與部署不符 | 同上 | 「無法確認發布範圍；請檢查部署設定。」 | `OperatorReleaseNotice.tsx:24`；`operatorReleaseStatus.ts:26-31` |
| 非管理員營運人員 | `/operator` | **沒有**這個通知；只有 `view=admin` 會 render `OperatorReleaseNotice`（`page.tsx:31-44`）。模型相依頁面實際顯示的錯誤文案，本包**未驗證** | `page.tsx:31-44` |
| API 呼叫者 | `GET /api/v1/learninghub/models` | registry 未綁定時回 **503**；registry 已綁定時才可能是 200 加空清單 | design `:34`、`:108-109` |

**建議（需人工決定，屬於產品變更，不在本 task）：** 非管理員營運人員看不到 core-ops 範圍說明。是否在營運主控台也顯示，由 Product Owner 決定。在此之前請以 release notes（`release-notes-draft.md`）說明。

## 4. 症狀與處置

| 症狀 | 先檢查 | 判斷與處置 |
|---|---|---|
| 使用者回報「預測、估價、選址分數不能用」 | `/readiness` 的 `details.models` | core-ops 預期行為。回覆使用者模型功能尚未上線，不開事故 |
| 模型功能**有回傳**預測或分數 | `details.models.productionBindingsReady`、`autoSeeded`、`capabilities` | 可能是替代預測或 surrogate，以 P0 處理；首次 release 期間依 §6 撤銷 |
| `/readiness` 回 503 | `details.persistence`、`details.provider`、`details.data`、`details.releaseProfile` | 依 `blockingReasons` 判斷。`RELEASE_PROFILE_INVALID` 代表部署設定錯誤，屬 P0 |
| 無法登入、登入後被要求換密碼 | 帳號是否 `must_change=true` | 首次登入必須換密碼，未換之前 API 回 403 `PASSWORD_CHANGE_REQUIRED`（`dependencies.py:303`、`:332`）。這是預期行為 |
| 管理員看不到營運資料 | 角色 | `platform_admin` 只有 feature_flag、user、role 權限，沒有業務資料可見性（`shared/auth/rbac.py:75-81`）。這是預期行為 |
| 跨租戶資料出現 | audit log 中的 `operator.tenant_isolation` | P0（權限繞過），依 `incident-management.md:11`；首次 release 期間依 §6 撤銷 |
| audit 寫入失敗 | 應用程式內的 `audit-write-failure` 告警**沒有**接到真實通道（`monitoring-oncall-plan.md` §1.3）；看 Cloud Logging 的錯誤 | P0／P1（`observability-and-runbook.md:116`） |
| 收到「web uptime check failing」email | `console.oday-plus.com.tw/login` 是否可達；Web revision 與流量 | 若是剛依 §6 撤銷，這是預期的告警。否則依 PROD-OPS-05 判斷（§6.1） |
| 收到「5xx ratio above 5%」或「p95 above 3000 ms」email | Cloud Monitoring 的實際比率與持續時間 | 告警門檻比回滾門檻嚴。> 10% 或 > 10 秒、持續 5 分鐘才達回滾條件（§6.1） |
| 收到「Cloud Run job execution failed」email | 哪個 job、execution log | job failure 是 PROD-OPS-05 的回滾條件之一，由 on-call 判斷 |
| 外部資料沒有更新 | provider 模式 | core-ops 下外部來源一律關閉，這是預期行為 |

## 5. 已知限制

1. **模型功能不可用**，不提供替代預測（design `:240-245`）。`infra/monitoring/alerts.json` 的 `forecast-daily-failed`、`model-drift-high` 與 `slo.json` 的 `forecast-batch-score` 在 core-ops 下沒有意義（`alerts.json:23`、`:78`；`slo.json:22-26`）。
2. **完整產品驗收不成立。** core-ops 的 `evaluate_full_acceptance` 一律是 `NOT_ADMITTED`（design `:43`）。
3. **watch receipt 無法以真實資料產生（pending）**，staging 不會在 production 上線後自動清理（`deployment-plan.md` §5 第 1 點）。
4. **watch window 期間沒有自動回滾**，見 `rollback-plan.md` §1。
5. **帳號模式是 invite-only。**
   - 第一個 `platform_admin` 只能由部署時的 bootstrap 建立，而且 `must_change=true`（`shared/identity/bootstrap.py:6-9`）。
   - 其他帳號由 `platform_admin` 經 `POST /api/v1/operator/users/invite` 發邀請（`apps/api/app/routes/operator_modules/users_roles.py:511-547`），接受後同樣是 `must_change=true`（`shared/identity/invitation_service.py:15-18`）。
6. **非管理員營運人員看不到 core-ops 範圍通知**（§3）。
7. **目前沒有人類身分能直接讀 production API**（§2.3 路徑 B）。

## 6. 首次 release 已 commit 後的下線（負責人決定 D，2026-11-27）

**適用範圍：** production 的第一個 release 已通過 live gate（`DEPLOYMENT_COMMITTED=true`），之後發現必須停止對外服務。production 有前一版之後，改用 `rollback-plan.md` §3 的 blue-green rollback。

**做法：** 撤銷 Web 的 `allUsers` invoker；保留資料、API、Web、job 與 scheduler；修正後以下一個 release 前滾，再重新開放。完整說明與收據欄位見 `rollback-plan.md` §2.4。

> **不要**對首次 release 執行 `bluegreen_release.py rollback`。切換前狀態把兩個 service 記為 absent，rollback 會**刪除** API 與 Web service（`rollback-plan.md` §2.3）。

### 6.1 何時觸發（PROD-OPS-05）

依 `docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/README.md:24`，任一條件成立：

1. 錯誤率 > 10% 持續 5 分鐘；
2. p95 > 10 秒持續 5 分鐘；
3. health check 連續失敗 3 次；
4. auth failure、資料品質異常、job failure、queue lag、audit 缺失，或 operator 判定異常。

另外，§4 標為 P0 的情境（跨租戶資料、替代預測、SHA 或 profile 不符）應立即撤銷。這些門檻是寬鬆初始值，不是自動閘門（同檔 `:31-36`）。告警與門檻的對應見 `rollback-plan.md` §2.4.1。

### 6.2 誰

- **判定**：負責人（on-call，決定 C）。
- **執行**：提議（待負責人確認）為負責人本人，或負責人在該次事件中明確指示後由 Claude 執行。執行身分需要對 `oday-prod-web` 的 `run.services.getIamPolicy` 與 `run.services.setIamPolicy`；唯讀的 `deborah.lu` 不能用。實際身分**仍待決**（`rollback-plan.md` §2.4.2）。

### 6.3 撤銷

```bash
PROJECT=odayplus-prod-20260826
REGION=<REGION>          # production 的 GCP_REGION；範本是 asia-east1
WEB=oday-prod-web

gcloud run services get-iam-policy "$WEB" --project="$PROJECT" --region="$REGION" --format=json > web-iam-before.json
gcloud run services remove-iam-policy-binding "$WEB" --project="$PROJECT" --region="$REGION" \
  --member=allUsers --role=roles/run.invoker
```

### 6.4 撤銷後讀回

```bash
gcloud run services get-iam-policy "$WEB" --project="$PROJECT" --region="$REGION" --format=json > web-iam-after.json
# web-iam-after.json 不得有 allUsers
curl -sS -o /dev/null -w '%{http_code}\n' https://console.oday-plus.com.tw/login
# 預期 401 或 403；IAM 生效有延遲，每 5 秒重試，最多 2 分鐘
```

- 約 3 分鐘後會收到「web uptime check failing」email。這是預期的。
- 撤銷期間要讀 release identity，只能走 §2.3 路徑 B。

### 6.5 恢復

- **主要路徑：下一個 release 前滾。** 部署腳本會在部署 Web candidate 時就把 `allUsers` 加回來（`deploy_cloud_run_waji.sh:1302-1306`、`:1323`），比流量切換更早；這段期間舊 revision 是公開的。**前滾 release 若在 commit 前失敗，Web 會維持公開、跑在舊 revision 上，必須立刻再做一次 §6.3。**
- **例外路徑：負責人判定舊 release 沒有問題時直接重開。**

```bash
gcloud run services add-iam-policy-binding "$WEB" --project="$PROJECT" --region="$REGION" \
  --member=allUsers --role=roles/run.invoker
```

### 6.6 恢復後讀回

```bash
gcloud run services get-iam-policy "$WEB" --project="$PROJECT" --region="$REGION" --format=json > web-iam-restored.json
# 必須含 allUsers / roles/run.invoker
curl -sS -o /dev/null -w '%{http_code}\n' https://console.oday-plus.com.tw/login
# 預期 200
```

再以 §2.3 路徑 A 讀 release identity，確認 `release_sha` 與 `web_release_sha` 都是預期的版本。uptime 告警應在下一個檢查週期後自動恢復。

### 6.7 演練

上線前要在 staging 演練一次，步驟與收據見 `rollback-plan.md` §5.1。
