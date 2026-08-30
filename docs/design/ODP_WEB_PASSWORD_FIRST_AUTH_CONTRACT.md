---
doc_id: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001
title: 帳密預設、OIDC 可選的單一身份契約
version: 1.0.0
status: proposed
document_class: interface-contract
project: ODay Plus
language: zh-TW
owner: Claude2
reviewer: Antigravity4
related_task: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001
binds_tasks:
  - ODP-WEB-LOCAL-IDENTITY-CORE-001
  - ODP-WEB-PASSWORD-FIRST-LOGIN-001
  - ODP-WEB-LOCAL-AUTH-API-TRUST-001
  - ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001
  - ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001
source_documents:
  - apps/web/README.md
  - docs/deployment/GCP_DEPLOY_GUIDE.md
  - docs/deployment/ENVIRONMENTS.md
  - docs/adr/ADR-0003-user-role-principal-map-linkage.md
  - docs/design/ODAY_PLUS_R0_SCREEN_INVENTORY.md
amends:
  - docs/adr/ADR-0003-user-role-principal-map-linkage.md
updated_at: 2026-08-30
---

# 帳密預設、OIDC 可選的單一身份契約

## 0. 規範用語

本文件使用下列規範動詞，實作任務不得自行放寬：

- **必須 / 不得**：驗收條件，違反即為未通過。
- **應該**：預設做法，偏離時必須在 PR 說明中寫出理由與替代保證。
- **可以**：允許但非必要。

「單一機制原則」貫穿全文：本平台**只有一套**身份儲存、**一套** session 狀態、
**一個** server-side 驗證器、**一條** web-to-API 信任路徑。新增 provider 只能是
既有機制上的一個輸入來源，不得成為平行的登入或授權狀態。

---

## 1. 目的與適用範圍

### 1.1 目的

凍結 ODay Plus 的身份契約：**本地帳號密碼為預設且唯一必要的登入方式，OIDC 降為
可選 provider**。本文件是 Wave Auth 1 / Wave Auth 2 全部實作任務的規格來源；下游
任務依本契約實作，不得自行發明第二個身份來源、第二種 session 表述或第二條授權路徑。

### 1.2 現況（本契約要改變的事實）

| 事實 | 現況位置 | 問題 |
|---|---|---|
| Production 登入只有 OIDC authorization-code + PKCE | `apps/web/src/app/login/route.ts`、`apps/web/src/lib/auth/oidc.ts` | 沒有 Google OAuth client 就無法登入 |
| Web session 是把 OIDC access token 封進 cookie | `apps/web/src/lib/auth/session.ts` `WebSession.accessToken` | 無 server-side session 記錄，無法撤銷 |
| 角色與 scope 來自 `ODP_AUTH_PRINCIPAL_MAP` 靜態 secret | `modules/opsboard/auth/config.py`、`docs/adr/ADR-0003` | 無法自助管理，且與 `/operator/users` 的持久化狀態不同步 |
| 部署驗證硬性要求 OIDC 變數 | `product_ops/deployment/validate_cloud_run_live_deployment.py:89-101`、`infra/terraform/checks.tf:55-69` | production 無 Google client 即 fail-closed |
| 系統中沒有任何密碼雜湊實作 | 全庫無 argon2/bcrypt/scrypt 相依 | 本地帳密能力尚未存在 |

### 1.3 不在本契約範圍

以下明確排除，需要時另開任務，不得在 Wave Auth 內夾帶：MFA / TOTP、WebAuthn 與
passkey、SAML、SCIM 自動佈建、企業 IdP 角色同步、社群登入、API personal access
token、機器對機器的新憑證型別。

---

## 2. 唯一權威身份來源（Authoritative Identity Store）

### 2.1 指定

**唯一權威身份來源是 `ODAY_DATABASE_URL` 所指向的 PostgreSQL 資料庫中的 `identity`
schema。** 帳號是否存在、是否啟用、擁有哪些角色與資料 scope，一律以 `identity`
schema 為準。

- 任何其他位置（瀏覽器儲存、JWT claims、`ODP_AUTH_PRINCIPAL_MAP`、in-memory seed）
  **不得**被當成本地帳號的授權事實。
- `modules/opsboard/application/user_role_management.py` 的 `DEFAULT_SEED_USERS`
  **不得**在 `ODP_PRODUCT_MODE=production` 下生效。
- 身份資料與業務資料共用同一個資料庫與同一套 migration 管線（`infra/db/migrations`），
  **不得**另建身份專用資料庫或另一套 migration 工具。

### 2.2 Schema 契約

下表是欄位級契約。實作任務（ODP-WEB-LOCAL-IDENTITY-CORE-001）可增加欄位，
**不得**刪改下列語意。所有時間欄位為 `TIMESTAMPTZ`，所有識別碼為 `UUID`。

| 表 | 必要欄位 | 語意約束 |
|---|---|---|
| `identity.accounts` | `account_id` PK、`tenant_id`、`username`、`email`、`display_name`、`status`、`created_at`、`created_by`、`updated_at`、`disabled_at`、`disabled_reason` | `status ∈ {invited, active, disabled, locked}`；`UNIQUE (tenant_id, lower(username))`、`UNIQUE (tenant_id, lower(email))`；**不得**有全域無 tenant 的帳號 |
| `identity.password_credentials` | `account_id` PK/FK、`algorithm`、`phc_hash`、`params` JSONB、`must_change`、`last_rotated_at`、`updated_at` | `algorithm` 固定為 `argon2id`；`phc_hash` 為 PHC 編碼字串；**不得**儲存明文、可逆加密或密碼提示 |
| `identity.account_roles` | `account_id`、`role`、`granted_at`、`granted_by` | `role` 必須屬於 `shared.auth.Role` 列舉；未知角色在寫入時即拒絕（不是讀取時忽略） |
| `identity.account_scopes` | `account_id` PK/FK、`brand_ids`、`region_ids`、`store_ids`、`assigned_area_ids`、`heat_zone_ids`、`modules`、`clearance` | 對應 `shared.auth.Scope` 的軸；空集合表示「該軸不額外限制」；`clearance` 必須屬於 `DataClassification` |
| `identity.sessions` | `session_id` PK、`account_id`、`provider`、`created_at`、`last_seen_at`、`idle_expires_at`、`absolute_expires_at`、`revoked_at`、`revoked_reason`、`rotated_from` | 見 §5；`provider ∈ {local_password, oidc}` |
| `identity.invitations` | `invitation_id` PK、`tenant_id`、`email`、`token_hash`、`preset_roles`、`preset_scope`、`created_by`、`expires_at`、`accepted_at`、`revoked_at` | `token_hash` 為雜湊值；**不得**儲存邀請 token 明文 |
| `identity.federated_identities` | `account_id` FK、`issuer`、`subject`、`linked_at`、`linked_by` | `UNIQUE (issuer, subject)`；OIDC 登入只能經由此表對應到既有 `accounts` 列 |
| `identity.login_attempts` | `attempt_key`、`window_started_at`、`failure_count`、`locked_until` | 供 §6.4 節流使用；`attempt_key` 為帳號鍵或來源 IP 雜湊 |

### 2.3 `ODP_AUTH_PRINCIPAL_MAP` 的重新定位

`ODP_AUTH_PRINCIPAL_MAP`（ADR-0003）**保留但範圍縮小**：

- **仍然是**服務身份（GCP service account 簽發的 identity token，例如
  `ODP_OPERATOR_SMOKE_BEARER_TOKEN`）的角色與 scope 來源；部署驗證仍要求
  `ODP_AUTH_PRINCIPAL_MAP_SECRET`。
- **不再是**人類使用者的授權來源。本地帳號與 OIDC 使用者的角色/scope 一律取自
  `identity` schema。
- ODP-WEB-LOCAL-AUTH-API-TRUST-001 必須同步提交一份 ADR-0003 修訂註記，說明此範圍縮小；
  **不得**只改程式而讓 ADR 與實作互相矛盾。

---

## 3. Provider 模型

### 3.1 兩個 provider，一個身份

| Provider | 預設狀態 | 啟用條件 | 身份解析 |
|---|---|---|---|
| `local_password` | **啟用** | 無需任何額外變數 | `identity.accounts` 直接查找 |
| `oidc` | **停用** | `ODP_AUTH_OIDC_ENABLED=true` 且 §8.3 全部變數齊備 | 以 `(iss, sub)` 查 `identity.federated_identities`，再取得 `accounts` 列 |

### 3.2 規範

- Production 在**未設定任何 OIDC 變數**時必須可完整部署、登入、通過部署驗證。
- OIDC 未啟用時，`/login` **不得**顯示任何 OIDC 入口，`/auth/callback`
  必須以 `404`（不揭露設定狀態）或 `503 WEB_AUTH_PROVIDER_DISABLED` fail closed；
  **不得**回傳可推測設定內容的錯誤細節。
- `ODP_AUTH_OIDC_ENABLED=true` 但變數不完整時，**必須整體 fail closed**（沿用
  `AuthBoundaryConfig.has_live_inputs` 既有語意：宣告即視為意圖，殘缺設定一律拒絕），
  **不得**降級成「只用帳密」而讓部署者以為 OIDC 已生效。
- OIDC 登入**不得**自動建立帳號。`(iss, sub)` 在 `identity.federated_identities`
  查無對應時必須拒絕登入並記稽核事件 `security.authentication` / `outcome=failure` /
  `reason=federated_identity_not_linked`。此規則同時是 §7 invite-only 的守門機制。
- 兩個 provider 共用同一組 `subject_id`、`tenant_id`、roles、scope 與 RBAC policy。
  **不得**為 OIDC 使用者建立平行的角色表或平行的 session 表述。

---

## 4. Web-to-API 信任路徑（唯一）

### 4.1 路徑定義

```
瀏覽器
  │  只帶 __Host-oday_web_session（HttpOnly / Secure / SameSite=Lax，內容為不透明 session 參照）
  ▼
Next BFF（同源 /api/v1/** 與 /avm/**，apps/web/src/lib/auth/proxy.ts）
  │  Authorization: Bearer <local access token 或 OIDC access token>
  │  X-Serverless-Authorization: Bearer <Cloud Run 服務身份 token>
  ▼
API（modules/opsboard/auth/boundary.py 的單一 AuthenticationBoundary）
  │  驗簽 → 驗 iss/aud/exp → 組出 shared.auth.Principal
  ▼
shared.auth.AuthorizationEngine（RBAC + ABAC，維持不變）
```

### 4.2 規範

- 這是**唯一**的 web-to-API 信任路徑。**不得**新增第二個驗證器、第二個 middleware
  信任層，或讓 API 直接接受瀏覽器來源的身份宣告。
- BFF **不得**轉發瀏覽器提供的 `Authorization`、`X-Serverless-Authorization`、
  `X-Subject-Id`、`X-Tenant-Id`、`X-Roles`。現行
  `apps/web/src/lib/auth/proxy.ts` 的 `FORWARDED_REQUEST_HEADERS` 白名單語意必須保留。
- `ODP_WEB_ALLOW_LEGACY_TRUSTED_HEADERS` 在 production 必須維持永遠無效
  （`apps/web/src/lib/auth/runtime.ts` 現行行為），並在 §9 P5 階段移除。
- API 側的 `principal_from_headers` header-trust stub 只允許存在於
  `ODP_PRODUCT_MODE != production` 的本機與測試路徑，且在 §9 P5 階段移除。

### 4.3 Local access token 契約

本地帳密登入後，API 的 identity 服務以 session 為依據簽發短效 access token；
BFF 只把它存在 **server 端 session 記錄**中，**不得**寫入 cookie、response body
或任何瀏覽器可讀位置。

| 項目 | 契約值 |
|---|---|
| 型別 | Compact JWT，由既有 `modules/opsboard/auth/jwt.py` 驗證 |
| 簽發者 | server-side identity 服務；簽章金鑰來自 `ODP_IDENTITY_TOKEN_SIGNING_KEY`（Secret Manager 注入） |
| `iss` | `ODP_AUTH_LOCAL_ISSUER`，預設 `urn:odp:identity:local` |
| `aud` | 必須落在 `ODP_AUTH_AUDIENCES` 內 |
| 必要 claims | `sub`（= `account_id`）、`sid`（= `session_id`）、`iat`、`exp`、`tenant_id` |
| TTL | 預設 120 秒，上限 300 秒 |
| 角色/scope claims | **不得**放進 token。角色與 scope 一律由 API 在驗簽後從 `identity` schema 讀取 |
| 撤銷 | 見 §5.4 |

**驗證器必須支援多個受信任 issuer**：`AuthBoundaryConfig` 由單一 `issuer` 擴充為
issuer 集合，每個 issuer 綁定自己的金鑰來源（local issuer → 對稱/非對稱本地簽章金鑰；
OIDC issuer → JWKS；service issuer → Google JWKS）。這是**同一個驗證器的設定擴充**，
不是第二套授權狀態；**不得**為 local token 另寫一條驗證流程。

### 4.4 Principal 組裝優先序

| Issuer 類別 | 身份來源 | 角色 / scope 來源 | 用途 |
|---|---|---|---|
| local（`ODP_AUTH_LOCAL_ISSUER`） | `identity.accounts`（以 `sub`） | `identity.account_roles` / `identity.account_scopes` | 本地帳密使用者（預設） |
| oidc（`ODP_AUTH_OIDC_ISSUER`） | `identity.federated_identities` → `identity.accounts` | 同上（**不**取 token claims） | 可選的企業 IdP 使用者 |
| service（`ODP_AUTH_SERVICE_ISSUER`） | token `sub` / 已驗證 `email` | `ODP_AUTH_PRINCIPAL_MAP` | 部署 smoke、服務對服務 |

三類 issuer 全部走同一個 `AuthenticationBoundary.authenticate()`，產出同一個
`shared.auth.Principal`，交給同一個 `AuthorizationEngine`。查無對應帳號、帳號
`status != active`、或 issuer 未註冊，一律回傳 `ANONYMOUS` 並記稽核事件。

---

## 5. Session 契約

### 5.1 表述

- 瀏覽器 cookie `__Host-oday_web_session` 只承載**不透明的 session 參照**
  （封裝後的 `session_id` 加最小中繼資料）。
- **不得**把 access token、角色、tenant、scope、email 或任何授權事實放進 cookie、
  `localStorage`、`sessionStorage` 或前端狀態並據以判斷權限。前端顯示用資料一律
  來自 server 回應，且僅供 UI 呈現。
- 權威 session 狀態在 `identity.sessions`。

### 5.2 生命週期參數

| 參數 | 契約值 | 備註 |
|---|---|---|
| Idle timeout | 預設 30 分鐘（可設定 15–60） | 每次成功請求更新 `last_seen_at` |
| Absolute lifetime | ≤ 8 小時 | 沿用現行 `SESSION_COOKIE_MAX_AGE_SECONDS`，**不得**放寬 |
| Cookie 屬性 | `HttpOnly`、`Secure`、`SameSite=Lax`、`Path=/`、`__Host-` 前綴 | 沿用現行 `webSessionCookieOptions` |
| OIDC transaction cookie | `__Host-oday_oidc_transaction`、10 分鐘 | OIDC 啟用時才簽發 |

### 5.3 輪替（rotation）

必須在下列每一個時點輪替 `session_id`（新列 + `rotated_from` 指向舊列 + 舊列標記
`revoked_at`），且回應必須以新值覆寫 cookie：

1. 登入成功（含 OIDC callback 成功）——防 session fixation。
2. 密碼變更成功。
3. 角色或 scope 變更（權限提升或降級）。
4. 距上次輪替超過 15 分鐘的第一個請求。

### 5.4 撤銷（revocation）

- 撤銷來源：使用者登出、管理員停用帳號、密碼變更（撤銷該帳號**其他所有** session）、
  管理員手動撤銷單一 session、absolute/idle 到期。
- 撤銷必須在 `identity.sessions` 寫入 `revoked_at` 與 `revoked_reason`，並記稽核事件。
- **撤銷傳播上界**：一般讀取請求最遲在 local access token TTL（≤ 300 秒）內失效；
  **所有寫入與高風險動作**（`shared.audit.policy.HIGH_RISK_ACTIONS`）必須在授權前
  即時檢查 `sid` 對應 session 仍為有效，撤銷立即生效、無延遲窗。
- 撤銷後的請求必須回 `401`，**不得**回 `403`（避免把「已撤銷」誤讀為「權限不足」）。

### 5.5 相容既有 OIDC session（不可破壞）

- Cookie 名稱 `__Host-oday_web_session` 與 seal purpose 字串 `web-session`
  （`apps/web/src/lib/auth/session.ts`）**不得**變更。
- `readWebSession()` 必須同時接受兩種 payload：
  - **legacy**：`{kind, accessToken, tokenType, subject, issuedAt, expiresAt}`（現行格式，無 `sid`）
  - **new**：含 `sid` 的不透明參照格式
- 讀到 legacy payload 時，必須**就地升級**：以其 `subject` 與 `accessToken` 建立
  `identity.sessions` 記錄（`provider=oidc`），改寫 cookie 為新格式，並保留原
  `expiresAt` 不延長。使用者**不得**因為升級而被登出。
- 相容讀取路徑最早只能在 §9 P5 移除，且必須在 absolute lifetime（8 小時）×
  安全係數之後。

---

## 6. 密碼與憑證契約

### 6.1 Argon2id 參數

| 參數 | 契約值 | 說明 |
|---|---|---|
| 演算法 | Argon2id | **不得**使用 Argon2i / Argon2d / bcrypt / scrypt / PBKDF2 |
| memory cost | ≥ 65536 KiB（64 MiB） | |
| time cost（iterations） | ≥ 3 | |
| parallelism | 1 | Cloud Run 單請求可預測性 |
| salt | 每個憑證獨立，≥ 16 bytes CSPRNG | |
| 輸出長度 | 32 bytes | |
| 儲存格式 | PHC 編碼字串（`$argon2id$v=19$m=...,t=...,p=...$salt$hash`） | 參數必須可從雜湊字串還原 |

**可升級性**：參數集中在單一常數模組並帶版本號。登入驗證成功後，若儲存的參數低於
當前政策，必須以當次明文密碼重新雜湊並寫回（rehash-on-verify），**不得**要求使用者
重設密碼來完成參數升級。

### 6.2 密碼政策

- 長度 ≥ 12 且 ≤ 1024 字元；輸入先做 Unicode NFKC 正規化。
- **不得**強制組成規則（大小寫/符號/數字）或週期性強制更換（依 NIST 800-63B）。
- 必須比對弱密碼拒絕清單（離線清單即可），並拒絕與 username / email 高度相同者。
- **不得**設定密碼提示問答、密碼歷史明文保存或密碼寄送。

### 6.3 錯誤訊息與時序

- 帳號不存在、密碼錯誤、帳號停用一律回同一個回應：
  `401 { error: { code: "AUTH_INVALID_CREDENTIALS" } }`，**不得**洩漏帳號是否存在
  （對齊 `docs/design/ODAY_PLUS_R0_SCREEN_INVENTORY.md` §9）。
- 帳號不存在時必須仍執行一次等價成本的 Argon2id 驗證（dummy verify），使時序不可區分。
- 帳號被鎖定時可以另回 `423 AUTH_ACCOUNT_LOCKED`，但**僅在**憑證正確的前提下才揭露，
  避免成為帳號枚舉管道。

### 6.4 節流與鎖定

| 維度 | 門檻 | 動作 |
|---|---|---|
| 每帳號 | 15 分鐘內 5 次失敗 | 鎖定 15 分鐘，指數退避（每次再鎖定加倍，上限 60 分鐘） |
| 每來源 IP | 15 分鐘內 50 次失敗 | 拒絕並記錄 |
| 成功登入 | — | 清除該帳號計數 |

節流狀態必須持久化在 `identity.login_attempts`（Cloud Run 多實例共享），
**不得**只放行程內記憶體。

---

## 7. Invite-only 帳號生命週期

### 7.1 禁止公開註冊

- **不得**存在任何未經驗證即可建立帳號的路由、CLI 或 API。
- **不得**由 OIDC 首次登入自動佈建帳號（§3.2）。
- 建立帳號的唯一入口：具備 `Role.PLATFORM_ADMIN` 的既有帳號透過受 RBAC 保護的
  管理介面發出邀請。

### 7.2 Bootstrap

- 首個管理帳號只能由部署期一次性 bootstrap 程序建立：讀取一次性 secret
  （`ODP_IDENTITY_BOOTSTRAP_SECRET`），建立單一 `platform_admin` 帳號並標記
  `must_change=true`。
- Bootstrap 必須具冪等性：`identity.accounts` 已存在任一 `active` 帳號時直接 no-op。
- Bootstrap 必須寫稽核事件 `identity.account.bootstrap`，且**不得**把密碼或 secret
  寫進 log、receipt、workflow 輸出或 PR。

### 7.3 邀請、重設、停用

| 動作 | 契約 |
|---|---|
| 邀請 | 單次使用；只存 `token_hash`；TTL ≤ 72 小時；接受時才設定密碼並轉為 `active` |
| 密碼重設 | 與邀請共用單次使用 token 機制；重設成功必須撤銷該帳號所有 session |
| 停用 | `status=disabled`，立即撤銷所有 session；**不得**硬刪除帳號（保留稽核可追溯性） |
| 重新啟用 | 需 `platform_admin`，並記稽核事件 |

所有帳號生命週期動作必須寫入不可否認的稽核事件（§8.1），actor 一律取自
server 端已驗證身份，**不得**採信請求主體提供的 actor 欄位。

---

## 8. 授權、稽核與部署契約

### 8.1 RBAC 與稽核

- RBAC/ABAC 決策邏輯維持 `shared/auth/rbac.py`、`shared/auth/abac.py`、
  `shared/auth/engine.py` 不變。本契約只改變 `Principal` 的**來源**，不改變授權演算法。
- **allow 與 deny 都必須產生稽核事件**，經由 `shared.audit` 既有通道，欄位至少包含：
  `event_type`、`actor`（server 端解析的 subject）、`action`、`resource`、`outcome`、
  `correlation_id`、`tenant_id`、`reason`（deny 時）。
- 新增/沿用的事件型別：`security.authentication`（沿用）、`security.authorization`（沿用）、
  `security.session`（新增：create / rotate / revoke）、`identity.account.*`（新增：
  invite / accept / disable / enable / password_change / role_change）。
- Tenant 隔離：`Principal.scope.tenant_id` 為 null 時，除服務身份外一律拒絕跨 tenant 讀寫。
- 稽核事件**不得**包含密碼、token、session id 明文、邀請 token 或 secret 值；
  email 依 `shared.audit.policy.mask_email` 遮罩。

### 8.2 Production 必要變數（password-first 預設）

| 變數 | 來源 | 用途 |
|---|---|---|
| `ODAY_DATABASE_URL` | Secret Manager | 身份與業務資料庫（權威身份來源） |
| `ODP_WEB_SESSION_SECRET` | Secret Manager | Web session 參照封裝金鑰（≥ 32 bytes） |
| `ODP_WEB_BASE_URL` | 環境變數 | 正規 HTTPS 來源（cookie / CSRF / redirect 基準） |
| `ODP_API_BASE_URL` | 環境變數 | BFF 上游 API |
| `ODP_API_SERVICE_AUDIENCE` | 環境變數 | Cloud Run 服務身份 audience |
| `ODP_IDENTITY_TOKEN_SIGNING_KEY` | Secret Manager（新增） | Local access token 簽章金鑰 |
| `ODP_AUTH_AUDIENCES` | 環境變數 | API 接受的 audience 集合 |
| `ODP_AUTH_SERVICE_ISSUER` / `ODP_AUTH_SERVICE_JWKS_URI` | 環境變數 | 服務身份 / 部署 smoke token 驗證 |
| `ODP_AUTH_PRINCIPAL_MAP` | Secret Manager | **僅**服務身份的角色映射（§2.3） |

上表**不含任何 OIDC 變數**。這是「production 預設不需要任何 OIDC 變數或 Google
client」的具體定義。

### 8.3 OIDC 啟用時才必要的變數

`ODP_AUTH_OIDC_ENABLED=true` 時，下列全部必須齊備，缺一即 fail closed：

`ODP_AUTH_OIDC_ISSUER`、`ODP_AUTH_OIDC_JWKS_URI`、`ODP_AUTH_OIDC_AUDIENCES`、
`ODP_WEB_OIDC_ISSUER`、`ODP_WEB_OIDC_CLIENT_ID`、`ODP_WEB_OIDC_CLIENT_SECRET`
（部署時由 Secret Manager 注入）、`ODP_WEB_OIDC_REDIRECT_URI`（預設
`<ODP_WEB_BASE_URL>/auth/callback`）、`ODP_WEB_OIDC_ALLOWED_ALGS`（預設 `RS256`）。

### 8.4 相容別名與不受影響的既有機制

- 既有的 `ODP_AUTH_ISSUER` / `ODP_AUTH_JWKS_URI` 目前同時服務「使用者 OIDC」與
  「service account smoke token」兩種用途。遷移期間必須被視為
  `ODP_AUTH_SERVICE_ISSUER` / `ODP_AUTH_SERVICE_JWKS_URI` 的**別名**，使
  `ODP_OPERATOR_SMOKE_BEARER_TOKEN` 路徑（`product_ops/deployment/deploy_cloud_run_waji.sh`
  的 smoke 區段）零改動可用。
- **不受本契約影響**：GitHub Actions Workload Identity Federation、
  `X-Serverless-Authorization` 服務身份、Cloud Run IAM invoker 綁定、
  第三方 provider default-deny egress、映像簽章與 admission gate。

### 8.5 部署驗證與 Terraform 的必要改動

由 ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001 執行：

| 位置 | 現況 | 契約要求 |
|---|---|---|
| `product_ops/deployment/validate_cloud_run_live_deployment.py:89-101` | `ODP_WEB_OIDC_ISSUER`、`ODP_WEB_OIDC_CLIENT_ID`、`ODP_WEB_OIDC_CLIENT_SECRET_SECRET` 無條件必要 | 改為 `ODP_AUTH_OIDC_ENABLED=true` 時才必要；password-first 預設下驗證必須通過 |
| `product_ops/deployment/deploy_cloud_run_waji.sh:574-576` | Web env 無條件寫入 OIDC 變數 | 改為條件注入；未啟用時**不得**注入 |
| `product_ops/deployment/deploy_cloud_run_waji.sh:303-304` | Web 無條件綁定 `ODP_WEB_OIDC_CLIENT_SECRET` | 改為只在啟用 OIDC 時綁定；`ODP_WEB_SESSION_SECRET` 維持必要 |
| `infra/terraform/checks.tf:55-69, 202-234` | production 硬性要求完整 OIDC 設定與 pinned client secret | 改為條件式：未啟用 OIDC 時不要求；啟用時維持現行嚴格驗證（HTTPS issuer、HTTPS JWKS、非空 audience、pinned secret version） |
| `infra/terraform/cloud_run.tf:213-262` | 無條件掛載 web OIDC client secret | 改為條件掛載 |

**Cloud Run 只注入已啟用 provider 所需的 secrets**：未啟用 OIDC 時，Web 服務的
secret 綁定中**不得**出現任何 OIDC client secret。

---

## 9. 遷移與回退（Migration / Rollback）

採 expand–contract。每個階段可獨立部署、獨立回退，且回退**不得**登出既有使用者。

| 階段 | 內容 | 主責任務 | 回退方式 | 是否破壞既有 session |
|---|---|---|---|---|
| **P0** | 契約凍結（本文件） | 本任務 | 無需回退 | 否 |
| **P1** | `identity` schema expand migration（純新增）、憑證服務、session/revocation 核心；`ODP_AUTH_LOCAL_PASSWORD_ENABLED` 預設 `false` | ODP-WEB-LOCAL-IDENTITY-CORE-001 | 保留空表（additive，無讀路徑變更） | 否 |
| **P2** | Web `/login` 帳密表單、登出、變更密碼、server-side session、legacy cookie 相容讀取與就地升級 | ODP-WEB-PASSWORD-FIRST-LOGIN-001 | 關閉 `ODP_AUTH_LOCAL_PASSWORD_ENABLED`；相容雙讀確保舊 session 續存 | 否 |
| **P3** | 多 issuer 驗證器、local issuer、Principal 由 `identity` schema 組裝、RBAC 稽核 | ODP-WEB-LOCAL-AUTH-API-TRUST-001 | 移除 local issuer 設定即回到單一 OIDC/service issuer | 否 |
| **P4** | Terraform / Runtime Release / 部署驗證條件化 OIDC | ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001 | 重新設定 `ODP_AUTH_OIDC_ENABLED=true` 與原變數 | 否 |
| **P5**（contract） | 移除 legacy sealed-cookie 讀路徑、`ODP_WEB_ALLOW_LEGACY_TRUSTED_HEADERS`、header-trust stub | ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001 之後另開收縮任務 | 需 revert 部署；**唯一**具破壞性的階段 | 是（僅限尚未升級的舊 cookie） |

規範：

- P5 **不得**與 P1–P4 併入同一次發布，且必須在 P2 上線滿一個 absolute lifetime
  （8 小時）× 安全係數並確認 legacy 讀路徑命中率歸零後才執行。
- 每個階段的資料庫變更必須是 expand（新增表/欄位/索引），**不得**在 P1–P4 執行
  破壞性 down migration；回退一律以「保留新結構、關閉新路徑」達成
  （對齊 `docs/deployment/ENVIRONMENTS.md` 的 rollback 原則）。
- 每個階段必須先在 dev 部署並通過 live E2E gate，再進 ephemeral staging 九階段演練，
  最後才進 production blue-green。

---

## 10. 測試矩陣

| # | 層級 | 測項 | 通過條件 | 主責任務 |
|---|---|---|---|---|
| T01 | unit | Argon2id 參數符合 §6.1；PHC 往返 | 參數低於政策時 `needs_rehash` 為真 | IDENTITY-CORE |
| T02 | unit | rehash-on-verify 升級 | 舊參數雜湊在成功登入後被改寫 | IDENTITY-CORE |
| T03 | unit | 密碼政策（長度、NFKC、弱密碼、近似 username） | 全數拒絕並回統一錯誤碼 | IDENTITY-CORE |
| T04 | unit | 帳號不存在 vs 密碼錯誤的回應與時序 | 回應位元組相同；dummy verify 有被呼叫 | IDENTITY-CORE |
| T05 | integration | 節流與鎖定門檻（§6.4） | 跨程序共享；成功登入清零 | IDENTITY-CORE |
| T06 | integration | session 建立 / 輪替 / 撤銷（§5.3、§5.4） | 四個輪替時點皆換新 `session_id`，舊 id 立即失效 | IDENTITY-CORE |
| T07 | integration | 密碼變更撤銷其他所有 session | 其他 session 於下一次請求回 401 | IDENTITY-CORE |
| T08 | migration | `identity` schema expand migration 可重跑、可回退為 no-op | 重複執行不報錯，既有資料不變 | IDENTITY-CORE |
| T09 | unit(route) | `/login` 預設呈現帳密表單且不導向 OAuth | 回應不含任何 authorize endpoint 導向 | LOGIN |
| T10 | unit(route) | 登入/登出/變更密碼皆使用 `__Host-` HttpOnly Secure SameSite=Lax cookie | cookie 屬性斷言 | LOGIN |
| T11 | unit(route) | CSRF：`Origin` 不符或 token 不符即拒絕 | 回 403，且不改動 session | LOGIN |
| T12 | unit(route) | `returnTo` open-redirect 防護 | 沿用 `safeReturnTo` 既有斷言全綠 | LOGIN |
| T13 | unit | 前端不得持有角色/tenant/subject 作為安全依據 | 靜態掃描 + 回應內容斷言 | LOGIN |
| T14 | unit | OIDC 未啟用時 `/login` 無 OIDC 入口、callback fail closed | 無 provider 洩漏 | LOGIN |
| T15 | unit | legacy sealed cookie 相容讀取與就地升級 | 舊 payload 不被登出，升級後 `sid` 存在，`expiresAt` 未延長 | LOGIN |
| T16 | contract | API 拒絕瀏覽器自帶 `x-subject-id` / `x-roles` / `x-tenant-id` | production 模式下一律不採信 | API-TRUST |
| T17 | contract | 多 issuer 驗證器：local / oidc / service 三類 token 各自正確組裝 Principal | 角色 scope 來源符合 §4.4 表 | API-TRUST |
| T18 | contract | 未 link 的 OIDC `(iss, sub)` 一律拒絕 | 401 + `federated_identity_not_linked` 稽核事件 | API-TRUST |
| T19 | contract | provider 未設定或驗證失敗 fail closed | 殘缺設定不降級為 header trust | API-TRUST |
| T20 | contract | RBAC allow 與 deny 都產生稽核事件 | 兩種 outcome 都有事件與 `correlation_id` | API-TRUST |
| T21 | contract | 撤銷傳播：高風險動作即時檢查 `sid` | 撤銷後第一個寫入請求即 401 | API-TRUST |
| T22 | terraform | password-first 預設下 `terraform validate` / `checks` 通過且不要求 OIDC 變數 | 無 OIDC 變數即可 plan | DEPLOYMENT |
| T23 | terraform | `ODP_AUTH_OIDC_ENABLED=true` 時 OIDC 設定仍被嚴格驗證 | 殘缺設定必須失敗 | DEPLOYMENT |
| T24 | workflow | Cloud Run 只注入已啟用 provider 所需 secrets | 未啟用時 secret 綁定不含 OIDC client secret | DEPLOYMENT |
| T25 | workflow | 既有 WIF、`X-Serverless-Authorization`、smoke token 路徑不回歸 | smoke 以 `ODP_AUTH_SERVICE_ISSUER` 別名通過 | DEPLOYMENT |
| T26 | E2E | 帳密登入成功 / 失敗 / rate limit / session rotation / logout / revoke / 變更密碼 | 每項皆有 redacted receipt | SECURITY-E2E |
| T27 | E2E | 未設定 OIDC 時部署驗證通過且 OIDC 路由 fail closed | dev 環境實測 | SECURITY-E2E |
| T28 | E2E | 完整 OIDC 設定時可選登入不回歸 | OIDC 登入仍可用且共用同一 Principal | SECURITY-E2E |
| T29 | E2E | RBAC tenant isolation 與稽核事件 | 跨 tenant 讀寫被拒並留稽核 | SECURITY-E2E |
| T30 | E2E | 無 secret 值寫入 logs / receipts / PR | receipt 標記 `secret_values_redacted: true` | SECURITY-E2E |

測試分層規範：T01–T25 由各自任務在自己的 PR 內跑；**完整 suite 只在
ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001 跑一次**，不得為了單一任務重跑全庫測試，
也**不得**另建平行的 auth 測試 harness。

---

## 11. 驗收對照表

| 下游任務驗收條件 | 本契約對應條款 |
|---|---|
| 密碼只以可升級參數的 Argon2id hash 保存 | §6.1 |
| 沒有公開註冊路徑 | §7.1 |
| invite bootstrap reset 與停用有可稽核紀錄 | §7.2、§7.3、§8.1 |
| session 可輪替撤銷且不在 client 暴露 credential | §5.1、§5.3、§5.4 |
| 資料庫 migration 採 expand contract 並有測試 | §9、T08 |
| 預設登入畫面提供帳號密碼且不導向 OAuth | §3.1、T09 |
| CSRF / return-to / 錯誤訊息 fail closed | §6.3、T11、T12 |
| OIDC 僅在完整設定時顯示可選入口 | §3.2、§8.3 |
| API 不接受 browser 自帶 role tenant subject | §4.2、T16 |
| local 與 OIDC 共用同一 subject role tenant policy | §4.4、§3.2 |
| RBAC deny 與 allow 都產生可稽核事件 | §8.1、T20 |
| token session trust 僅由 server-side verifier 發出或驗證 | §4.3、§5.1 |
| provider 未設定或驗證失敗一律 fail closed | §3.2、§4.4、T19 |
| dev/staging/prod 在預設下不需 Google OAuth client | §8.2、T22 |
| Cloud Run 只注入已啟用 provider 所需 secrets | §8.5、T24 |
| 既有 GitHub Actions WIF 與 API service identity 不受影響 | §8.4、T25 |
| 指定唯一 authoritative identity store 與 web-to-API trust path | §2.1、§4.1 |
| 不破壞既有 OIDC session 的 migration rollback | §5.5、§9 |

---

## 12. 契約變更程序

- 本文件為下游五個任務的規格來源。實作中若發現契約有誤或不可行，**必須**先開
  contract 修訂任務並取得 reviewer 核准，**不得**在實作 PR 內默默偏離。
- 任何新增 provider、新增 session 表述、新增信任路徑的提案，預設拒絕；
  提案必須說明為何無法在既有單一機制上擴充。
- 版本規則：語意變更提升 minor；驗收條件變更提升 major；錯字與說明性補充提升 patch。
