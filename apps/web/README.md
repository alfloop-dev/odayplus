# Web App

ODay Plus frontend application built with Next.js and the shared design system.

## Authentication modes

The Web app supports two authentication modes controlled by `ODP_AUTH_MODE`:

- **`local`** (default): Password-only authentication with no OIDC dependency.
  The deployment does not require any OIDC provider, client ID, or client secret.
- **`oidc`**: OIDC authorization-code + PKCE. The browser receives only an
  encrypted `HttpOnly`, `Secure`, `SameSite=Lax` session cookie. Calls to
  `/api/v1/**` and `/avm/**` go through the same-origin Next BFF. The BFF
  forwards the end-user access token in `Authorization` and obtains its Cloud
  Run service identity from the Google metadata server for
  `X-Serverless-Authorization`. Browser-supplied identity, service-identity,
  `X-Subject-Id`, `X-Tenant-Id`, and `X-Roles` headers are not forwarded.

Required environment (all modes):

| Variable | Purpose |
|---|---|
| `ODP_WEB_SESSION_SECRET` | Server-only session encryption secret, at least 32 bytes |
| `ODP_WEB_BASE_URL` | Canonical HTTPS web origin |
| `ODP_API_BASE_URL` | Server-side API origin used by the BFF and server components |
| `ODP_API_SERVICE_AUDIENCE` | Cloud Run API audience used to mint the BFF service identity token |
| `ODP_AUTH_MODE` | `local` (default) or `oidc` |
| `ODP_AUTH_LOCAL_ISSUER` | Issuer for Web-minted local access tokens; defaults to `urn:odp:identity:local` |
| `ODP_AUTH_AUDIENCES` | API audience for Web-minted local access tokens |

In production, `ODP_IDENTITY_TOKEN_SIGNING_KEY` is injected from Secret
Manager. The API and Web services must use the same pinned secret version; the
API trust resolver registers its plain value as the `local-default` HS256 key.

### OIDC mode (`ODP_AUTH_MODE=oidc`)

When OIDC is enabled, the following additional variables are required:

| Variable | Purpose |
|---|---|
| `ODP_WEB_OIDC_ISSUER` | Exact OIDC issuer |
| `ODP_WEB_OIDC_CLIENT_ID` | Registered web client ID |
| `ODP_WEB_OIDC_CLIENT_SECRET` | Web OIDC client secret (bound via Secret Manager `ODP_WEB_OIDC_CLIENT_SECRET_SECRET` in deployment) |

Optional OIDC environment:

| Variable | Purpose |
|---|---|
| `ODP_WEB_OIDC_REDIRECT_URI` | Override callback URI; defaults to `<ODP_WEB_BASE_URL>/auth/callback` |
| `ODP_WEB_OIDC_SCOPES` | Defaults to `openid profile email` |
| `ODP_WEB_OIDC_ALLOWED_ALGS` | Comma-separated ID-token algorithms; defaults to `RS256` |
| `ODP_WEB_OIDC_AUTHORIZATION_ENDPOINT` | Explicit endpoint when discovery is not used |
| `ODP_WEB_OIDC_TOKEN_ENDPOINT` | Explicit endpoint when discovery is not used |
| `ODP_WEB_OIDC_JWKS_URI` | Explicit JWKS endpoint when discovery is not used |
| `ODP_WEB_OIDC_END_SESSION_ENDPOINT` | Optional provider logout endpoint |

The provider must register the callback URI and, when supported, the
post-logout URI `<ODP_WEB_BASE_URL>/login`.

General optional environment:

| Variable | Purpose |
|---|---|
| `ODP_WEB_SESSION_TTL_SECONDS` | Session cap, no more than eight hours |
| `ODP_WEB_ALLOW_LEGACY_TRUSTED_HEADERS` | Local/test compatibility only; ignored in production |
| `ODP_WEB_LOGIN_THROTTLE_PEPPER` | Overrides the login throttle digest pepper; defaults to `ODP_WEB_SESSION_SECRET`. In production one of the two must be set, or `/login` fails closed with 503 |
| `ODP_WEB_TRUSTED_PROXY_HOPS` | Trusted proxies in front of the service; defaults to `1` |

## 登入節流（Login throttle）

`POST /login` 在驗證任何憑證之前就先節流。計數器存放在
`identity.login_attempts`，因此所有 Cloud Run instance 共用同一份狀態：

- 單一帳號十五分鐘內失敗五次即鎖定十五分鐘，之後每多一輪鎖定就加倍，上限
  六十分鐘；
- 單一來源 IP 十五分鐘內失敗五十次，後續嘗試一律拒絕；
- 登入成功會清除該帳號的計數器，並歸還先前記在來源 IP 上的那次嘗試。

嘗試在驗證前就被計入，且只有成功才歸還，因此一個還沒得出結論就中斷的請求
仍然算數。

### 回應碼與帳號存在性

節流閘門位於憑證驗證之前，所以它的拒絕只描述「嘗試速率」，不描述帳號：

- 帳號維度與 IP 維度的節流拒絕**一律**回 `429 AUTH_RATE_LIMITED`，兩者對外
  無法區分，也永遠不會回 `423`；
- `423 AUTH_ACCOUNT_LOCKED` 只有在密碼已被驗證正確、而該帳號處於 locked
  狀態時才會出現，並且**不會建立 session**、不會發出 cookie；
- 其餘所有失敗——帳號不存在、密碼錯誤、帳號為 disabled 或 invited、身分儲存
  層故障——一律收斂成同一個 `401 AUTH_INVALID_CREDENTIALS`，且 summary 文字
  固定。

因此持有錯誤密碼的攻擊者無法分辨帳號是否存在、是否被鎖定或停用：唯一能觸發
`423` 的前提，是他已經握有正確的密碼。節流的帳號金鑰也是由「送出的使用者
名稱」而非解析後的帳號推導，未知帳號與真實帳號的鎖定行為完全一致。

### 金鑰推導與 pepper

`attempt_key` 儲存 HMAC-SHA256 摘要，絕不寫入明文的來源 IP 或使用者名稱。
pepper 取自 `ODP_WEB_LOGIN_THROTTLE_PEPPER`，未設定時退回
`ODP_WEB_SESSION_SECRET`；輪替該 secret 會重新編碼整張表，並清掉進行中的
鎖定。

摘要的兩種輸入都可離線窮舉（IPv4 空間為 2^32，使用者名稱來自字典），所以
未加 pepper 的 raw SHA-256 等同於一份可還原的「誰在何處嘗試登入」紀錄。
**production 一旦設定了資料庫卻沒有任何 pepper，`getDefaultLoginThrottle`
會回傳 null，`/login` 直接回 `503 WEB_AUTH_NOT_CONFIGURED`**，而不是改用
未加 pepper 的摘要。未加 pepper 的路徑僅在本機與測試 runtime 可達。

### 來源位址與失效處置

用戶端位址取自 `X-Forwarded-For` 的最後一段，該段由平台附加、用戶端無法偽造。
若前方另有受信任的 proxy，以 `ODP_WEB_TRUSTED_PROXY_HOPS` 設定要略過的層數。

production 的節流一律 fail closed：沒有資料庫 URL 時 `/login` 回
`503 WEB_AUTH_NOT_CONFIGURED`，而不是提供未節流的登入表單；儲存層無法連線時
回 `503 WEB_AUTH_UNAVAILABLE`。資料庫出狀況不得讓這道控制被關掉。

The Web runtime service account must have permission to invoke the API Cloud
Run service. Production requests fail closed before contacting the API when
the service audience is missing or the metadata server cannot issue an
identity token. The service token remains server-side and is never copied to a
browser response.
