# core-ops UAT 計畫（DRAFT · 未簽署）

行號以 `origin/dev` @ `5d761526` 為準。本計畫補充既有的 `docs/uat/UAT_ACCEPTANCE_PLAN.md`（`version 0.1.0`、`status: draft`，見 `:4-6`），範圍只限 core-ops。

**既有 UAT 計畫的角色腳本不適用 core-ops：**

- `UAT_ACCEPTANCE_PLAN.md:33-45` 的角色腳本大多以模型功能為主，例如 HeatZone、SiteScore、AVM、NetPlan、Model Card。
- 該計畫沒有登入、密碼、租戶隔離的 UAT 腳本。

因此本文件另訂 core-ops 的測試案例，ID 以 `UAT-CO-` 開頭。

## 1. 範圍

**在範圍內（core-ops 宣稱上線的功能，依 `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:8`、`:24`）：**

- 登入、session、登出、首次登入換密碼、登入節流
- RBAC：依角色顯示導覽、拒絕越權動作、`platform_admin` 看不到業務資料
- 租戶隔離
- 稽核：越權拒絕會寫入稽核、證據匯出會產生稽核事件、hash chain 完整
- 不依賴模型的營運流程：Today 與 shell、Store Ops 生命週期、Governance 核可、assisted listing intake 的權限邊界
- 模型功能「如實拒絕」：確認沒有替代預測，並確認提示文字

**不在範圍內（core-ops 不宣稱）：**

- HeatZone、SiteScore、AVM、ForecastOps 四燈、NetPlan、Learning Hub 與模型卡（design `:160-170`）
- 對應的既有腳本：`UAT-EXP`、`UAT-SITE`、`UAT-OPS`（四燈部分）、`UAT-AVM`、`UAT-NET`、`UAT-ML`

**需人工決定是否納入：**

- PriceOps（`UAT-PRICE`）、AdLift（`UAT-AD`）、Growth workspace、Franchisee portal（`UAT-FRAN`）
- repo 內沒有把這些功能分類為「模型相依」或「非模型相依」的權威文件。

## 2. 環境與資料（**需人工決定**）

| 選項 | 限制 | 依據 |
|---|---|---|
| ephemeral staging | 每個 release 建立獨立的 DB 與 tenant；成功後會等 production watch closeout 才清理，失敗則 hold 24 小時、最多 168 小時。staging 的 smoke 身分是 release 自己的 service account，**沒有密碼帳號**。模組只有三個 secret：database URL、cursor key、web session。設計文件指出，若要用密碼帳號，還得走 Web 登入、換密碼，並需要新的 signing key secret | `docs/deployment/ENVIRONMENTS.md:122-123`；`product_ops/deployment/staging_lifecycle.py:62`；design `:256-262` |
| production 上線前（切換後、對外公告前） | 會在 production 資料庫建立真實帳號與資料 | `deploy_cloud_run_waji.sh:1192-1198`（切換後才有公開流量） |
| dev | dev 自動部署的 profile 是 `dev-admin`，不是 core-ops | `docs/deployment/ENVIRONMENTS.md:48-49` |

**未驗證：** ephemeral staging 能否在不新增 secret 的情況下，讓人類 tester 以密碼帳號登入。依 design `:262` 的描述，目前看起來不能。這會直接影響 UAT 能在哪個環境執行，請 Human/Ops 先決定。

## 3. 角色與帳號

角色清單以 `shared/auth/identity.py:21-43` 的 `Role` enum 為準。既有 UAT 計畫的帳號名稱（`UAT_ACCEPTANCE_PLAN.md:27`）與 enum 對不上，這裡不沿用。

| 帳號用途 | canonical 角色 | 為何需要 | 姓名／帳號（留白） |
|---|---|---|---|
| 管理員 | `platform_admin` | 由 bootstrap 建立，`must_change=true`（`shared/identity/bootstrap.py:6-9`）。負責邀請其他帳號（`shared/identity/invitation_service.py:15-18`）。**沒有業務資料可見性**（`shared/auth/rbac.py:75-81`） | |
| **營運主管（必要，不能是 platform_admin）** | `operations_manager` | 主要營運路徑：營運主控台 VIEW／UPDATE、intervention、audit VIEW（`rbac.py:134-143`）。注意它也有 `forecastops`，在 core-ops 下應被拒絕 | |
| 區域主管 | `regional_supervisor` | 範圍較窄的營運角色（`rbac.py:144-149`） | |
| 稽核 | `auditor` | 稽核 VIEW／EXPORT（`rbac.py:189-197`） | |
| 唯讀 | `operator_viewer` | 唯讀負向案例；必須綁定 tenant（`rbac.py:88-91`；`shared/auth/identity.py:42-43`） | |
| 第二租戶的營運帳號 | `operations_manager`，tenant 不同 | 租戶隔離測試 | |
| 無業務權限帳號 | 只有登入、沒有業務角色 | 越權拒絕測試 | |

## 4. 測試案例

**「對應自動化」欄的注意事項：**

- 列出的是 repo 內既有的測試，作為參照。這些測試多半在 deterministic 或本機環境跑，**不能代替** UAT。
- 18 個 canonical Playwright spec 用的是 header-trust 測試身分（例如 `x-roles`），不是真實登入。檔內明文寫著「this is not a production login or real role UAT」（`tests/e2e/operator-network-assisted-intake.spec.ts:13`）。

| ID | 角色 | 步驟摘要 | 預期結果 | 對應自動化（參照） | 等級 |
|---|---|---|---|---|---|
| UAT-CO-01 | 各角色 | 以邀請帳號首次登入 | 被要求換密碼；換密碼前 API 回 403 `PASSWORD_CHANGE_REQUIRED` | `apps/api/oday_api/security/dependencies.py:280-310`；`tests/identity/test_identity_bootstrap.py:87` | P0 |
| UAT-CO-02 | 各角色 | 換密碼後重新登入 | 成功登入，其他 session 被撤銷 | `tests/identity/test_session_lifecycle.py:225` | P0 |
| UAT-CO-03 | 各角色 | 登出 | session cookie 被清除，導回 `/login` | `tests/identity/test_session_lifecycle.py:270`；`apps/web/src/lib/auth/__tests__/logout.test.ts:18` | P1 |
| UAT-CO-04 | 各角色 | 閒置超時 | session 失效 | `tests/identity/test_session_lifecycle.py:106` | P1 |
| UAT-CO-05 | 匿名 | 未登入直接開 `/operator` | 導向 `/login?...returnTo=`；匿名呼叫 API 回 401 或 403 | `docs/evidence/ODP_LIVE_E2E_GATE.md:52-62` | P0 |
| UAT-CO-06 | 匿名 | 連續輸錯密碼 | 觸發登入節流 | `tests/security/test_login_throttle_wiring.py` | P1 |
| UAT-CO-07 | operations_manager、regional_supervisor、auditor | 登入後看導覽與 Today | 導覽依角色顯示，不顯示無權限的項目 | `tests/e2e/operator-shell-today.spec.ts:45`、`:70` | P1 |
| UAT-CO-08 | 無權限帳號、operator_viewer | 嘗試越權動作，例如核可或修改 | 被拒絕（403），UI 顯示 forbidden，不是空白 | `tests/e2e/shell-resource-binding.spec.ts:65`；`tests/security/test_rbac_abac.py:61`；`tests/e2e/operator-network-listings.spec.ts:342`；`tests/e2e/operator-network-assisted-intake.spec.ts:613` | P0 |
| UAT-CO-09 | platform_admin | 進入營運主控台讀業務資料 | 讀不到業務資料；只能管理使用者、角色、feature flag | `shared/auth/rbac.py:75-81`。repo 內**未找到**對應的 E2E | P0 |
| UAT-CO-10 | platform_admin | 邀請帳號、指派與變更角色 | 成功並留下稽核紀錄 | `tests/security/test_user_role_management.py` | P1 |
| UAT-CO-11 | 第二租戶帳號 | 讀取第一租戶的資料 | 被拒絕，並以 `operator.tenant_isolation` 寫入稽核 | `tests/e2e/test_password_first_security_e2e.py:271`；`tests/security/test_tenant_isolation_guard.py:282`；`tests/e2e/test_live_business_journeys.py:1306` | P0 |
| UAT-CO-12 | 無權限帳號 | 越權被拒絕之後查稽核 | 有一筆安全稽核事件 | `tests/security/test_rbac_abac.py:180` | P0 |
| UAT-CO-13 | auditor | 匯出 Evidence Package | 產生匯出紀錄與稽核事件 | `tests/e2e/operator-governance.spec.ts:123` | P1 |
| UAT-CO-14 | operations_manager | Governance 退回但理由不足 | 被阻擋 | `tests/e2e/operator-governance.spec.ts:77` | P1 |
| UAT-CO-15 | operations_manager | Store Ops issue 生命週期：寫入後重新載入 | 狀態持久化 | `tests/e2e/operator-store-ops.spec.ts:223`。同檔 `:197` 的四燈屬於 ForecastOps，不在範圍內 | P1 |
| UAT-CO-16 | governance reviewer、無關角色 | 開啟 assisted listing intake | reviewer 看到遮罩後的唯讀畫面，無關角色 fail closed | `tests/e2e/operator-assisted-listing-intake.spec.ts:550`。同檔 `:366` 會產生 SiteScore receipt，在 core-ops 下預期被拒絕 | P1 |
| UAT-CO-17 | 各角色 | 開啟模型相依功能，例如 SiteScore、AVM、ForecastOps、HeatZone | 如實顯示不可用，**沒有**替代預測或假分數 | design `:31-36`、`:207-211` | P0 |
| UAT-CO-18 | platform_admin | 開啟 `/operator?view=admin` | 顯示 core-ops 範圍通知與不可用的模型功能清單 | `apps/web/features/operator/OperatorReleaseNotice.tsx:20-35` | P2 |
| UAT-CO-19 | Release owner | 讀 `/platform/release-identity` | `release_profile=core-ops`、`release_profile_valid=true`、SHA 與 digest 相符 | `runbook-core-ops.md` §2 | P0 |

## 5. P0／P1 缺陷準則

repo 內沒有 UAT 專用的 P0／P1 定義。以下沿用事故等級（`docs/runbooks/incident-management.md:11-12`），並依 core-ops 調整。這是 DRAFT，需要 Product Owner 與 QA Lead 核准。

- **P0**：
  - 權限繞過，包括跨租戶讀寫，或 `platform_admin` 讀到業務資料；
  - 稽核遺失或無法寫入；
  - 資料損毀；
  - 模型功能回傳替代預測或假結果；
  - 部署的 SHA 或 profile 不符；
  - 無法登入。
- **P1**：核心營運流程無法使用，例如 Store Ops、Governance、登出、session 管理。
- **出場條件**：
  - P0／P1 未結數為 0（`docs/uat/UAT_ACCEPTANCE_PLAN.md:52`；registry gate-5 check `RELEASE_GATE_REGISTRY.json:411`）。
  - 附條件接受的項目必須有 owner 與期限。
  - 依既有計畫，P0／P1 未結時不得開始 UAT，除非 Product Owner 與 QA Lead 接受（`UAT_ACCEPTANCE_PLAN.md:29`）。

## 6. 簽核

格式沿用 `docs/uat/UAT_SIGNOFF_TEMPLATE.yaml:1-27`。所有欄位留白，每個角色一份：

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

既有計畫要求的簽核人是 Product Owner、QA Lead、Security Owner、SRE Owner、Business Owner（`UAT_ACCEPTANCE_PLAN.md:54`）。carryforward 義務 `ODP-SIGN-OFF-UAT` 要求的是「live staging 上，Operator、Manager、Franchisee 三種 persona 零 P0／P1 的簽核報告」，目前是 `PENDING_HUMAN_AUTHORITY`（`docs/evidence/completion/ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001/obligation_matrix.json:749`）。

| 簽核人角色 | 姓名 | 日期 | 決定 |
|---|---|---|---|
| Product Owner | | | □ 接受 □ 附條件 □ 拒絕 |
| QA Lead | | | |
| Security Owner | | | |
| SRE Owner | | | |
| Business Owner | | | |
