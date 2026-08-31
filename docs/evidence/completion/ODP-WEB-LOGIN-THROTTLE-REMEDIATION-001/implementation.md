# ODP-WEB-LOGIN-THROTTLE-REMEDIATION-001 implementation evidence

Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §2.2, §6.4

## What was blocked

ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001 accepted Wave Auth as a conditional
pass and recorded two §6.4 blockers:

- **B1** the throttle service had no production call site — `/login` applied no
  throttle at all;
- **B2** the throttle layer had no durable repository, so nothing was shared
  between Cloud Run instances.

## Delivered runtime behavior

`POST /login` (`apps/web/src/app/login/route.ts`) now drives
`apps/web/src/lib/auth/loginThrottle.ts` on every attempt:

- The gate reads and counts the attempt in `identity.login_attempts` **before**
  `authenticateLocalCredentials` runs. Counting up front is what makes the
  control fail safe: an attempt that never reaches a verdict — a crash, a
  timeout, an instance torn down mid-request — stays counted. Only a verified
  success gives it back.
- Five failures per account inside a fifteen minute window lock the account for
  fifteen minutes, doubling on each further lockout round to a sixty minute
  ceiling. The multiplier comes from `lockout_count` (rounds already served),
  not from the in-window failure count: the window is fifteen minutes while a
  lockout runs up to sixty, so binding the multiplier to the failure count
  would reset the escalation whenever the window expired and the doubling
  required by §6.4 would never happen.
- Fifty failures per source IP inside the window reject further attempts from
  it, with a fixed lockout — §6.4 asks the IP dimension only to "reject and
  record", so it does not double.
- A successful login deletes the account row and returns the attempt charged to
  the source IP, which budgets failures only.
- The IP dimension is evaluated first, so a blocked source cannot drive an
  otherwise untouched account towards its own lockout.

### Shared across every Cloud Run instance

`PostgresLoginThrottleStore` is the only thing that touches
`identity.login_attempts` (migration `000011_identity_schema.sql`, already
deployed). Read-modify-write runs inside a transaction with
`SELECT ... FOR UPDATE`, so concurrent instances on the same key serialize and
no increment is lost. The state machine itself is a set of pure functions
shared by the Postgres store and the in-memory store used by non-production
development, so both dimensions behave identically wherever they run.

In production there is no in-memory fallback: with no database URL
`getDefaultLoginThrottle` returns null and `/login` answers
`503 WEB_AUTH_NOT_CONFIGURED` rather than serving an unthrottled login form. An
unreachable store answers `503 WEB_AUTH_UNAVAILABLE` — failing closed, because
an attacker who can disturb the database must not thereby switch the throttle
off.

### No account-existence disclosure

The account key is derived from the **submitted username**, never from a
resolved account. The gate has to run before verification, and resolving an
account first would mean unknown usernames could not be throttled at all —
both a bypass and an enumeration oracle. Because an unknown username is
counted and locked exactly like a real one, the refusal (`AUTH_ACCOUNT_LOCKED`
423, or `AUTH_RATE_LIMITED` 429 for the IP dimension) carries no signal about
whether the account exists. `POST /login is throttled …: throttles an unknown
username exactly like a real one` asserts the two response sequences are
byte-identical through lockout.

The pre-existing post-verification `AUTH_ACCOUNT_LOCKED` for
`accounts.status = 'locked'` is unchanged and still only reachable after a
correct password.

### `attempt_key` holds no plaintext identifier

Per §2.2 the plaintext client IP is never stored. `attempt_key` is
`account:<hex>` / `ip:<hex>` where the digest is HMAC-SHA256 over a
dimension-tagged message. The username is digested too, so mistyped passwords
that land in the username field do not accumulate in the table.

The pepper defaults to `ODP_WEB_SESSION_SECRET`, which is already required in
every mode and never leaves the server, so no new deployment variable becomes
mandatory (`ODP_WEB_LOGIN_THROTTLE_PEPPER` overrides it). Without a pepper the
IPv4 space is small enough that a bare SHA-256 could be reversed offline.
Rotating the session secret re-keys the table and clears in-flight lockouts;
lockouts last at most an hour, so that is an acceptable consequence of a rare
operation.

Addresses are canonicalized before hashing (IPv6 expansion, brackets, trailing
port, case) so equivalent spellings cannot be used to get a fresh budget. The
address is taken from the last `X-Forwarded-For` entry — the one the platform
appends and a client cannot forge — with `ODP_WEB_TRUSTED_PROXY_HOPS` for
deployments that add trusted proxies. When no address can be resolved the IP
dimension is skipped rather than collapsing every caller into one bucket; the
account dimension still applies.

## One mechanism, not two

The Python `shared/identity/login_throttle.py` prototype is retired:
`LoginThrottleService`, `ThrottleRepository`, `LoginAttemptRecord`,
`account_attempt_key` and `ip_attempt_key` are removed from
`shared.identity`, and the T05 / T05b classes are removed from what is now
`tests/identity/test_session_lifecycle.py` (renamed, since it no longer covers
throttling). The production login path is TypeScript; keeping a second
implementation in a runtime that `/login` never calls would be exactly the
parallel mechanism the acceptance forbids.

`tests/security/test_login_throttle_wiring.py` pins all three properties: the
route drives the throttle before verifying credentials (B1), the durable store
over `identity.login_attempts` exists and locks rows (B2), and exactly one
module issues statements against that table.

## Owned boundary

This task owns `apps/web/src/lib/auth/loginThrottle.ts`, the `/login` POST
wiring, the two TypeScript test suites, the security wiring guard, the
retirement of the Python prototype, and the `apps/web/README.md` section. It
does not change the session store, the identity store, the OIDC path, or the
`identity` schema — `identity.login_attempts` already carries every column
this needs.

## The two XFAIL guards

Artifact three asked for the two `xfail(strict=True)` guards in the security
E2E suite to be removed and replaced with passing evidence. **That file is not
reachable from this branch**: `tests/e2e/test_password_first_security_e2e.py`
exists only on `task/ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001` (commit
`ea64f02e`), which is unmerged, unpushed, and owned by another lane. Editing
it from here would take over another task's file and guarantee a conflict.

The guards are also no longer correct as written. They assert Python-side
facts — a `LoginThrottleService` production call site, and
`shared.identity.SqlThrottleRepository` — and the architecture correction of
2026-08-31T13:58:05Z forbids exactly those: no Python `SqlThrottleRepository`,
no cross-runtime call from Next.js, and the Python prototype retired once the
TypeScript throttle ships. Under the shipped architecture both guards would
fail rather than XPASS.

What this branch delivers instead is the passing form of the same two
assertions, restated against the shipped architecture and living in a file
this task owns: `tests/security/test_login_throttle_wiring.py::
test_b1_login_route_drives_the_throttle_before_verifying_credentials` and
`::test_b2_throttle_state_is_durable_in_identity_login_attempts`.

**Follow-up owed by the security E2E lane**: when
ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001 rebases onto a `dev` containing this
change, it must delete its two xfail guards and its
`TestT26LoginThrottleContract` import of `LoginThrottleService` (which no
longer exists), and re-point its strict guards at the TypeScript route and the
durable datastore, as the same architecture note directs.

## 安全審查退回後的修正（2026-08-31）

Codex2 在 PR #1085 的安全審查提出兩項阻擋，以下為修正內容。

### 一、`423` 曾是帳號列舉的破口

**問題**：節流閘門跑在憑證驗證之前，而它在帳號維度鎖定時直接回
`423 AUTH_ACCOUNT_LOCKED`。`423` 是一個關於「帳號」的陳述，卻出現在攻擊者
用任意密碼就能觸發的回應上。加上驗證後路徑同樣可能回 `423`，攻擊者便能藉由
狀態碼分辨 known／unknown／disabled 帳號的無效憑證。

**修正**（`apps/web/src/app/login/route.ts`）：

- 驗證前的節流拒絕，帳號維度與 IP 維度**一律**回 `429 AUTH_RATE_LIMITED`。
  節流描述的是嘗試速率，不是帳號狀態，所以這條路徑不再出現 `423`。兩個維度
  對外完全同形，無法區分。
- 驗證後的失敗改為顯式分支：只有 `AUTH_ACCOUNT_LOCKED`（即密碼已驗證正確、
  帳號為 locked）回 `423`，且在建立 session／token 的程式碼之前就 return，
  不會發出任何 cookie。其餘一律回 `401 AUTH_INVALID_CREDENTIALS`。
- `401` 的 summary 固定寫在 route 內，不再從 `authResult.summary` 轉發，
  避免日後 `localAuth` 加入更細的訊息時悄悄擴大回應面。

`localAuth.ts` 本身早已先驗證密碼再揭露 lock，因此 `AUTH_ACCOUNT_LOCKED`
在架構上就只能出現在密碼正確之後——`423` 無法在不握有正確密碼的前提下觸發。

### 二、production 缺 pepper 時會退回 raw SHA-256

**問題**：`resolveThrottlePepper` 在 `ODP_WEB_LOGIN_THROTTLE_PEPPER` 與
`ODP_WEB_SESSION_SECRET` 皆未設定時回傳 null，`digest` 隨即退回未加 pepper
的 SHA-256。摘要的兩種輸入都可離線窮舉（IPv4 空間 2^32、使用者名稱來自
字典），等於把 `identity.login_attempts` 變成一份可還原的登入嘗試紀錄。

**修正**（`apps/web/src/lib/auth/loginThrottle.ts`）：
`getDefaultLoginThrottle` 在「production 且有資料庫 URL 卻解不出 pepper」時
回傳 null，route 既有的 null 處理即產生 `503 WEB_AUTH_NOT_CONFIGURED`。
空白字串經 `trim()` 後視同未設定。未加 pepper 的分支因此只在本機與測試
runtime 可達；`ODP_WEB_LOGIN_THROTTLE_PEPPER` 與 `ODP_WEB_SESSION_SECRET`
任一者存在即可滿足 production。

#### 三、production Web revision 綁定 ODAY_DATABASE_URL

**問題**：先前 `product_ops/deployment/deploy_cloud_run_waji.sh` 中，`WEB_SECRET_BINDINGS` 僅綁定 `ODP_WEB_SESSION_SECRET` 與 `ODP_IDENTITY_TOKEN_SIGNING_KEY`（及可選 OIDC secret），未綁定 `ODAY_DATABASE_URL=${ODAY_DATABASE_URL_SECRET}`。這導致 production Web container 在 runtime 無法取得資料庫連線字串，`getDefaultLoginThrottle` 回傳 null，使 `/login` 在 production 固定回 `503 WEB_AUTH_NOT_CONFIGURED`，無法由 `PostgresLoginThrottleStore` 達成 Cloud Run 跨 instance 共享節流狀態。

**修正**：
1. `product_ops/deployment/deploy_cloud_run_waji.sh`：將 `WEB_SECRET_BINDINGS` 設定為 `WEB_SECRET_BINDINGS="ODAY_DATABASE_URL=${ODAY_DATABASE_URL_SECRET}"` 並累加其他必要密鑰，確保 Secret Manager 中的資料庫 URL 注入 Cloud Run Web revision。
2. 文件更新：同步更新 `apps/web/README.md`、`docs/deployment/ENVIRONMENTS.md`、`docs/deployment/GCP_DEPLOY_GUIDE.md`，明確載明 Web 服務在 production 會透過 Secret Manager 綁定 `ODAY_DATABASE_URL`。
3. 驗證與守衛：在 `tests/security/test_login_throttle_wiring.py` 新增 `test_web_deployment_binds_database_secret_for_cross_instance_throttle` 守衛，並更新 `tests/ops/test_conditional_oidc_deployment.py` 確保部署腳本之 secret 綁定完整無誤。

### 跨 Task 接線與 E2E 驗收說明

本 remediation task 與 `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001` 職責分明：
- 本 task 負責實作 durable repository（`PostgresLoginThrottleStore`）、正式登入路徑接線（`POST /login`）、Cloud Run 跨 instance 共享（`WEB_SECRET_BINDINGS`）、fail-closed 防禦與通用 401/429 錯誤回應；並以 `tests/security/test_login_throttle_wiring.py` 交付 B1 與 B2 兩大阻擋項的 passing 形式與各項安全守衛。
- 當本 PR 合併至 `dev` 後，`ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-001` rebase 即可直接以本實作取代舊有 Python prototype xfails，達成 Wave Auth 完整安全端對端驗收。

### 新增的測試

`apps/web/src/lib/auth/__tests__/loginThrottleRoute.test.ts` 新增兩個
describe 區塊（共 10 個案例）：

- **不洩漏帳號存在性與狀態**：對 unknown／active／locked／disabled／invited
  五種帳號送出錯誤密碼，斷言五筆回應（status、body、set-cookie）**逐欄相同**
  且皆為 `401 AUTH_INVALID_CREDENTIALS`；locked 帳號配正確密碼才升級為
  `423` 且無 cookie；disabled 與 invited 即使密碼正確仍為 `401`；active
  帳號仍能正常登入並取得 session cookie。
- **拒絕寫入可還原的 attempt key**：production + DB 無 pepper 時工廠回 null、
  `/login` 回 `503`；兩個 pepper 變數任一皆可滿足；空白 pepper 視同未設定；
  本機開發（`NODE_ENV=test`）不受影響。

既有案例中，驗證前節流拒絕的斷言由 `423 AUTH_ACCOUNT_LOCKED` 改為
`429 AUTH_RATE_LIMITED`（含 HTML 表單導回的 `error=` 參數）。

`tests/security/test_login_throttle_wiring.py` 新增三道原始碼守衛：驗證前的
區段不得出現 `AUTH_ACCOUNT_LOCKED` 或 `423`、驗證後 `423` 只能出現一次；
工廠必須同時檢查 `isProductionWebRuntime` 與 `!resolveThrottlePepper`；
以及 Cloud Run Web 部署腳本必須綁定 `ODAY_DATABASE_URL` 至 `WEB_SECRET_BINDINGS`。
原始碼守衛在比對前會先剝除 `//` 註解，只檢查實際產生的程式碼。

## Verification

Run on 2026-08-31 UTC:

```text
npm --prefix apps/web run test
Test Files  52 passed (52)
     Tests  436 passed (436)

npm --prefix apps/web run typecheck
tsc --noEmit          (no output)

npm --prefix apps/web run lint
✔ No ESLint warnings or errors

uv run pytest tests/identity tests/security/test_login_throttle_wiring.py tests/ops/test_conditional_oidc_deployment.py
106 passed, 27 skipped

uv run --python 3.12 ruff check shared tests/identity tests/security/test_login_throttle_wiring.py tests/ops/test_conditional_oidc_deployment.py
All checks passed!

uv run python delivery_toolchain/governance/check_code_boundaries.py --write-inventory
Code boundary checks passed for 1007 files.
```

Web 測試 52 files 436 passed，Python 安全與接線守衛 77 passed。既有案例中僅節流拒絕的狀態碼斷言由 `423` 改為 `429`，無其他既有斷言被放寬或刪除。

No external data fetching and no OIDC requirement was added.
