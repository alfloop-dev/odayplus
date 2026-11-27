# core-ops UAT 計畫（DRAFT · 未簽署 · 2026-11-27 版）

- 行號以 `origin/dev` @ `48c5271b5d882d824d432f3483ab4415030bb8e6` 為準。
- 負責人決定 A–H 見 `live-readback-20261127.md` §2。本文件主要依決定 B。
- 本計畫補充既有的 `docs/uat/UAT_ACCEPTANCE_PLAN.md`（`version 0.1.0`、`status: draft`，`:4-6`），範圍只限 core-ops。

**既有 UAT 計畫的角色腳本不適用 core-ops：**

- `UAT_ACCEPTANCE_PLAN.md:37-50` 的角色腳本大多以模型功能為主，例如 HeatZone、SiteScore、AVM、NetPlan、Model Card。
- 該計畫沒有登入、密碼、租戶隔離的 UAT 腳本。

因此本文件另訂 core-ops 的測試案例，ID 以 `UAT-CO-` 開頭。

## 1. 範圍

**在範圍內（core-ops 宣稱上線的功能，`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:8`、`:22-24`）：**

- 登入、session、登出、首次登入換密碼、登入節流
- RBAC：依角色顯示導覽、拒絕越權動作、`platform_admin` 看不到業務資料
- 租戶隔離（staging 的限制見 §3.3）
- 稽核：越權拒絕會寫入稽核、證據匯出會產生稽核事件、hash chain 完整
- 不依賴模型的營運流程：Today 與 shell、Store Ops 生命週期、Governance 核可、assisted listing intake 的權限邊界
- 模型功能「如實拒絕」：確認沒有替代預測，並確認提示文字

**不在範圍內（core-ops 不宣稱）：**

- HeatZone、SiteScore、AVM、ForecastOps 四燈、NetPlan、Learning Hub 與模型卡（design `:240-245`）
- 對應的既有腳本：`UAT-EXP`、`UAT-SITE`、`UAT-OPS`（四燈部分）、`UAT-AVM`、`UAT-NET`、`UAT-ML`

**仍待決（決定 B 保留為開放項）：**

- PriceOps（`UAT-PRICE`）、AdLift（`UAT-AD`）、Growth workspace、Franchisee portal（`UAT-FRAN`）是否納入（`UAT_ACCEPTANCE_PLAN.md:43-44`、`:48`）。
- repo 內沒有把這些功能分類為「模型相依」或「非模型相依」的權威文件。

## 2. 環境與資料（負責人決定 B，2026-11-27）

**UAT 在 candidate C 的 ephemeral staging 執行，由負責人本人操作並簽核。**

| 項目 | 內容 | 依據 |
|---|---|---|
| 網址 | `https://console-staging.oday-plus.com.tw`（staging 唯一的 Web 網域，經 `oday-staging-web-lb`） | `product_ops/deployment/staging_lifecycle.py:175-190` |
| 何時可用 | staging verify 的 11 個 stage 全部通過之後。`web_custom_domain` stage 把這個 release 接到 LB；`live_e2e_gate` stage 以密碼登入證明可用 | `staging_lifecycle.py:2868-2886`；`deploy-dev.yml:2315-2351` |
| 何時消失 | release 預設 TTL 24 小時；延長需記錄 owner 與原因，上限 168 小時。production 上線後，`staging_closeout` 在 watch receipt 驗證通過時才清理；receipt 目前無法產生（`L-6`），所以實際上會保留到人工 cleanup | `staging_lifecycle.py:68-69`；`deploy-dev.yml:2688-2723` |
| 在流程中的位置 | `deployment-plan.md` §2 步驟 5：staging 通過之後、registry 推進到 production 之前 | 決定 A |
| 資料 | 每個 release 有自己的資料庫與**一個** tenant（`staging_tenant_id`）。外部來源全部關閉 | `staging_lifecycle.py:60-66`、`:172-174`；`infra/terraform/modules/ephemeral_staging/outputs.tf:172` |
| 登入方式 | 只有密碼登入（staging 沒有 Google OAuth，D15） | `infra/terraform/modules/ephemeral_staging/main.tf:668-678` |

2026-10-24 版說「staging 沒有密碼帳號路徑」，**已過時**：PR #1737（ODP-STAGING-PASSWORD-JOURNEY-261121，2026-11-22 合併）之後，每個 staging release 都有自己的 bootstrap `platform_admin` 與密碼 secret，見 §3.1。

## 3. 帳號

### 3.1 起點：這個 release 的 bootstrap `platform_admin`

| 項目 | 內容 | 依據 |
|---|---|---|
| 帳號 | `staging-admin`（只存在於這個 release 的資料庫，隨 release 銷毀；不寄信） | `infra/terraform/modules/ephemeral_staging/main.tf:126-134` |
| 建立方式 | release 的 admin bootstrap job 以產品自己的 `python -m shared.identity.bootstrap` 建立 | 同檔 `:668-678`；`staging_lifecycle.py:194-204` |
| 兩個 secret | 一次性 bootstrap secret（`…-admin-initial`）與登入密碼（`…-admin-sign-in`）。值都是每個 release 隨機產生；Terraform output `staging_admin_account` 只給名稱與版本，不給值 | 同檔 `:679-745`；`infra/terraform/modules/ephemeral_staging/outputs.tf:101-110` |
| verify 之後的狀態 | `live_e2e_gate` 已完成首次登入換密碼，目前的密碼就是 `…-admin-sign-in` 的值 | `staging_lifecycle.py:4222-4235`；`main.tf:668-678` |
| bootstrap 期限 | 一次性 secret 最多 72 小時，而且不超過 release TTL | `main.tf:130-134` |
| 誰能讀登入密碼 | 沒有 workload 掛載它；只有具 staging 專案 Secret Manager 讀取權的身分（verify 用的 deployer）能讀 | `main.tf:716-717`；`:706-708` |

### 3.2 建立 UAT 帳號（Claude 執行，決定 B）

1. Claude 讀取這個 release 的 `…-admin-sign-in` secret。**密碼只留在記憶體，不寫入 repo、log、收據或對話。** Claude 以哪個身分讀這個 secret（需要 staging 專案的 `secretmanager.secretAccessor`）**仍待決**；唯讀讀回用的身分不一定有這個權限。
2. Claude 以 `staging-admin` 登入 `https://console-staging.oday-plus.com.tw`。
3. 經既有的邀請 API 建立帳號：在 `/operator?view=admin` 的使用者管理畫面操作，或直接呼叫同一個端點 `POST /api/v1/operator/users/invite`（`apps/web/features/operator/UserRoleManagementController.tsx:269`；`apps/api/app/routes/operator_modules/users_roles.py:511-547`）。
   - body：`username`、`email`、`name`、`roles`、`scope`、`lifetime_seconds`（最多 259200 秒，即 72 小時）、`reason`（`users_roles.py:79-90`）。
   - 角色必須是 `Role` enum 的值（`shared/identity/invitation_service.py:179-181`）；tenant 一律取自邀請者（`invitation_service.py:206-220`）。
   - 回應包含一次性的邀請 token（`users_roles.py:539-540`）。
4. Claude 把邀請連結交給負責人（以對話傳遞，不寫入 repo）。負責人在 `/auth/invitations` 接受邀請並設定密碼（`apps/web/src/app/auth/invitations/route.ts:177`；`apps/api/app/routes/identity_invitations.py:42-60`）。
5. 負責人首次登入時必須換密碼；換之前 API 回 403 `PASSWORD_CHANGE_REQUIRED`（`apps/api/oday_api/security/dependencies.py:306-332`）。這本身就是 UAT-CO-01。
6. Claude 記錄建立了哪些帳號（帳號名稱、角色、建立時間），**不記錄**密碼或 token。

### 3.3 帳號清單

角色以 `shared/auth/identity.py:21-43` 的 `Role` enum 為準。既有 UAT 計畫的帳號名稱（`UAT_ACCEPTANCE_PLAN.md:27`）與 enum 對不上，這裡不沿用。

| 帳號用途 | canonical 角色 | 為何需要 | 建立方式 |
|---|---|---|---|
| 管理員 | `platform_admin` | 由 bootstrap 建立（§3.1）。**沒有業務資料可見性**（`shared/auth/rbac.py:75-81`） | 既有 `staging-admin`；負責人也可另收一個 `platform_admin` 邀請 |
| **營運主管（必要，不能是 platform_admin）** | `operations_manager` | 主要營運路徑：營運主控台 VIEW／UPDATE、intervention、audit VIEW（`rbac.py:134-143`）。它也有 `forecastops`，在 core-ops 下應被拒絕 | §3.2 邀請 |
| 區域主管 | `regional_supervisor` | 範圍較窄的營運角色（`rbac.py:144-149`） | §3.2 邀請 |
| 稽核 | `auditor` | 稽核 VIEW／EXPORT（`rbac.py:189-197`） | §3.2 邀請 |
| 唯讀 | `operator_viewer` | 唯讀負向案例；必須綁定 tenant（`rbac.py:88-91`；`identity.py:42-43`） | §3.2 邀請 |
| 無業務權限帳號 | 只有登入、沒有業務角色 | 越權拒絕測試 | §3.2 邀請；邀請 API 要求至少一個角色（`users_roles.py:86`），用哪個最小角色**待確認** |
| 第二租戶的營運帳號 | `operations_manager`，tenant 不同 | 租戶隔離測試 | **staging 做不到**：每個 release 只有一個 tenant，邀請的 tenant 取自邀請者（`invitation_service.py:206-220`）。UAT-CO-11 的處置見 §4 |

## 4. 測試案例

**「對應自動化」欄的注意事項：**

- 列出的是 repo 內既有的測試，作為參照。多半在 deterministic 或本機環境跑，**不能代替** UAT。
- canonical Playwright spec 用的是 header-trust 測試身分，不是真實登入；檔內寫明「Neither composition is real role UAT」（`tests/e2e/operator-network-assisted-intake.spec.ts:15`）。

| ID | 角色 | 步驟摘要 | 預期結果 | 對應自動化（參照） | 等級 |
|---|---|---|---|---|---|
| UAT-CO-01 | 各角色 | 以邀請帳號首次登入 | 被要求換密碼；換密碼前 API 回 403 `PASSWORD_CHANGE_REQUIRED` | `apps/api/oday_api/security/dependencies.py:306-332`；`tests/identity/test_identity_bootstrap.py:87` | P0 |
| UAT-CO-02 | 各角色 | 換密碼後重新登入 | 成功登入，其他 session 被撤銷 | `tests/identity/test_session_lifecycle.py:225` | P0 |
| UAT-CO-03 | 各角色 | 登出 | session cookie 被清除，導回 `/login` | `tests/identity/test_session_lifecycle.py:270`；`apps/web/src/lib/auth/__tests__/logout.test.ts:18` | P1 |
| UAT-CO-04 | 各角色 | 閒置超時 | session 失效 | `tests/identity/test_session_lifecycle.py:106` | P1 |
| UAT-CO-05 | 匿名 | 未登入直接開 `/operator` | 導向 `/login?...returnTo=`；匿名呼叫 API 回 401 或 403 | `docs/evidence/ODP_LIVE_E2E_GATE.md:52-62` | P0 |
| UAT-CO-06 | 匿名 | 連續輸錯密碼 | 觸發登入節流 | `tests/security/test_login_throttle_wiring.py` | P1 |
| UAT-CO-07 | operations_manager、regional_supervisor、auditor | 登入後看導覽與 Today | 導覽依角色顯示，不顯示無權限的項目 | `tests/e2e/operator-shell-today.spec.ts:45`、`:70` | P1 |
| UAT-CO-08 | 無業務權限帳號、operator_viewer | 嘗試越權動作，例如核可或修改 | 被拒絕（403），UI 顯示 forbidden，不是空白 | `tests/e2e/shell-resource-binding.spec.ts:65`；`tests/security/test_rbac_abac.py:61`；`tests/e2e/operator-network-listings.spec.ts:355`；`tests/e2e/operator-network-assisted-intake.spec.ts:648` | P0 |
| UAT-CO-09 | platform_admin | 進入營運主控台讀業務資料 | 讀不到業務資料；只能管理使用者、角色、feature flag | `shared/auth/rbac.py:75-81`。repo 內**未找到**對應的 E2E | P0 |
| UAT-CO-10 | platform_admin | 邀請帳號、指派與變更角色 | 成功並留下稽核紀錄 | `tests/security/test_user_role_management.py:568`、`:580` | P1 |
| UAT-CO-11 | 第二租戶帳號 | 讀取第一租戶的資料 | 被拒絕，並以 `operator.tenant_isolation` 寫入稽核 | `tests/e2e/test_password_first_security_e2e.py:271`；`tests/security/test_tenant_isolation_guard.py:284`；`tests/e2e/test_live_business_journeys.py:1404` | P0 |
| UAT-CO-12 | 無業務權限帳號 | 越權被拒絕之後查稽核 | 有一筆安全稽核事件 | `tests/security/test_rbac_abac.py:180` | P0 |
| UAT-CO-13 | auditor | 匯出 Evidence Package | 產生匯出紀錄與稽核事件 | `tests/e2e/operator-governance.spec.ts:123` | P1 |
| UAT-CO-14 | operations_manager | Governance 退回但理由不足 | 被阻擋 | `tests/e2e/operator-governance.spec.ts:77` | P1 |
| UAT-CO-15 | operations_manager | Store Ops issue 生命週期：寫入後重新載入 | 狀態持久化 | `tests/e2e/operator-store-ops.spec.ts:224`。同檔 `:197` 的四燈屬於 ForecastOps，不在範圍內 | P1 |
| UAT-CO-16 | governance reviewer、無關角色 | 開啟 assisted listing intake | reviewer 看到遮罩後的唯讀畫面，無關角色 fail closed | `tests/e2e/operator-assisted-listing-intake.spec.ts:1146`。同檔 `:882` 會產生 SiteScore receipt，在 core-ops 下預期被拒絕 | P1 |
| UAT-CO-17 | 各角色 | 開啟模型相依功能，例如 SiteScore、AVM、ForecastOps、HeatZone | 如實顯示不可用，**沒有**替代預測或假分數 | design `:26-43`、`:240-245` | P0 |
| UAT-CO-18 | platform_admin | 開啟 `/operator?view=admin` | 顯示 core-ops 範圍通知與不可用的模型功能清單 | `apps/web/features/operator/OperatorReleaseNotice.tsx:20-35` | P2 |
| UAT-CO-19 | 負責人 | 讀 `/api/v1/platform/release-identity`（`runbook-core-ops.md` §2.3 路徑 A） | `release_profile=core-ops`、`release_profile_valid=true`、SHA 與 digest 相符；Web 與 API 兩組欄位一致 | `runbook-core-ops.md` §2 | P0 |

**UAT-CO-11 在 staging 的限制：** staging release 只有一個 tenant，沒辦法建立第二租戶帳號（§3.3）。處置**仍待決**：(a) 以自動化測試（上表參照）與 staging live gate 的租戶檢查代替，並在簽核中寫明；(b) 另行準備第二個 tenant 的方式；(c) 移到 production 上線後、對外公告前執行。

## 5. P0／P1 缺陷準則

repo 內沒有 UAT 專用的 P0／P1 定義。以下沿用事故等級（`docs/runbooks/incident-management.md:11-12`），並依 core-ops 調整。這是 DRAFT，需要負責人核准。

- **P0**：權限繞過（含跨租戶讀寫、`platform_admin` 讀到業務資料）；稽核遺失或無法寫入；資料損毀；模型功能回傳替代預測或假結果；部署的 SHA 或 profile 不符；無法登入。
- **P1**：核心營運流程無法使用，例如 Store Ops、Governance、登出、session 管理。
- **出場條件**：
  - P0／P1 未結數為 0（`docs/uat/UAT_ACCEPTANCE_PLAN.md:64`；registry gate-5 check `RELEASE_GATE_REGISTRY.json:713`）。
  - 附條件接受的項目必須有 owner 與期限。
  - P0／P1 未結時不得開始 UAT，除非 Product Owner 與 QA Lead 接受（`UAT_ACCEPTANCE_PLAN.md:29`）。

## 6. 簽核

依決定 B，UAT 由負責人本人簽核。既有計畫要求的簽核人是 Product Owner、QA Lead、Security Owner、SRE Owner、Business Owner（`UAT_ACCEPTANCE_PLAN.md:66`）；負責人一人簽核是否涵蓋這五個角色，請在簽核時寫明。

carryforward 義務 `ODP-SIGN-OFF-UAT` 要求「live staging 上，Operator、Manager、Franchisee 三種 persona 零 P0／P1 的簽核報告」，目前是 `PENDING_HUMAN_AUTHORITY`（`docs/evidence/completion/ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001/obligation_matrix.json:749`）。Franchisee 是否納入仍待決（§1），會影響這項義務是否能滿足。

格式沿用 `docs/uat/UAT_SIGNOFF_TEMPLATE.yaml:1-27`。所有欄位留白：

```yaml
uat_session_id:
module: core-ops
role:
tester_name:
tester_department:
date:
environment:
build_version:
git_commit:
data_snapshot:
model_versions: not-applicable (core-ops; models refused)
feature_flags:
test_cases:
  - id:
    result:
    defects:
    evidence_ids:
decision:
  accepted:
  accepted_with_conditions:
conditions:
  - owner:
    due_date:
    condition:
sign_off_by:
signature_date:
audit_export_ref:
```

| 簽核人角色 | 姓名 | 日期 | 決定 |
|---|---|---|---|
| 負責人（決定 B） | | | □ 接受 □ 附條件 □ 拒絕 |
| 是否另需 Product Owner／QA Lead／Security／SRE／Business Owner 分別簽核 | | | □ 是 □ 否（由負責人一人代表） |
