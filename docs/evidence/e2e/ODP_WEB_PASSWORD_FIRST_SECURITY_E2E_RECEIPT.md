---
receipt_id: ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002
title: 帳密預設與可選 OIDC 的安全及端對端驗收 receipt
task: ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002
contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001
contract_matrix: Password login, throttle, T27, T28, T29, T30
owner: Codex
reviewer: Codex2
verdict: pass
secret_values_redacted: true
---

# 帳密預設與可選 OIDC 的安全及端對端驗收 receipt

## 1. 驗收範圍

本任務在 login throttle remediation 已合併至 `dev` 後，驗收正式 TypeScript
`POST /login`、password-first 預設、可選 OIDC、server-side session、API RBAC
tenant isolation，以及 secret redaction。瀏覽器端旅程在
`apps/web/tests/login-route.test.ts`；跨 runtime 的部署、API boundary 與 audit
旅程在 `tests/e2e/test_password_first_security_e2e.py`。

## 2. 證據矩陣

| 控制 | 證據 | 預期結果 |
|---|---|---|
| 帳密登入成功／失敗 | Web route suite | 200 建立 opaque session；無效憑證固定 401，無 cookie |
| account / IP threshold | Web route suite + `tests/security/test_login_throttle_wiring.py` | 429 `AUTH_RATE_LIMITED`；gate 在 credential verification 前；狀態持久於 `identity.login_attempts` |
| production fail closed | Web route suite | 無 durable throttle 或 store unavailable 時回 503，不降級登入 |
| local default / OIDC disabled | route suite + Python E2E | 無 OIDC 變數可通過 preflight；OIDC token 與 OIDC route fail closed |
| complete OIDC optional path | route suite + Python E2E | local 與 OIDC 解析到同一 authoritative principal；帳密不回歸 |
| RBAC tenant isolation | Python E2E | 跨 tenant API request 回 403，且寫入 `operator.tenant_isolation` deny audit event |
| secret exclusion | route responses + this receipt + rollout checklist | 不含 password、bearer、session secret、OIDC client secret 或 DSN credential |

## 3. Secret handling

所有部署 secret 只以變數名稱或 Secret Manager reference 出現；payload 一律
`<REDACTED>`。此 receipt 不記錄 password、bearer token、session id、database
URL、OIDC client secret、PHC hash 或其可逆形式。Web browser 只收到 opaque
session reference，API bearer 留在 server-side session store。

## 4. Verification record

The final layered verification completed on 2026-09-01 UTC after the task
source and artifacts were complete. The base was first composed from
`origin/dev` at `d0c81635df8e...` in merge commit `f3095a0e`; the task history
was preserved. No secret-bearing output was retained.

| Layer | Command | Result |
|---|---|---|
| Web tests | `npm --prefix apps/web run test` | 53 files, 474 tests passed |
| Web typecheck | `npm --prefix apps/web run typecheck` | pass |
| Web lint | `npm --prefix apps/web run lint` | pass; no warnings/errors |
| Python auth/security/identity/ops | `uv run --python 3.12 pytest tests/e2e/test_password_first_security_e2e.py tests/security/test_login_throttle_wiring.py tests/ops/test_conditional_oidc_deployment.py tests/identity` | 151 passed, 22 skipped |
| Python lint | `uv run --python 3.12 ruff check shared tests/identity tests/security/test_login_throttle_wiring.py tests/ops/test_conditional_oidc_deployment.py tests/e2e/test_password_first_security_e2e.py` | pass |
| Terraform contract | `python3 infra/terraform/validate_contract.py` | pass; 14 files checked |
| Terraform unit tests | `python3 -m unittest discover -s infra/terraform/tests -p 'test_*.py'` | 32 tests OK |

Commands recorded for reproduction:

```text
npm --prefix apps/web run test
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
uv run --python 3.12 pytest tests/e2e/test_password_first_security_e2e.py tests/security/test_login_throttle_wiring.py tests/ops/test_conditional_oidc_deployment.py tests/identity
uv run --python 3.12 ruff check shared tests/identity tests/security/test_login_throttle_wiring.py tests/ops/test_conditional_oidc_deployment.py tests/e2e/test_password_first_security_e2e.py
python3 infra/terraform/validate_contract.py
python3 -m unittest discover -s infra/terraform/tests -p 'test_*.py'
```

## 5. Limitations

- No live GCP or external OIDC provider is contacted by this receipt.
- The Web route and Python API boundary are exercised in their respective
  runtimes; the tests do not claim a deployed-browser HTTP run.
- Argon2id hashing parameters remain covered by the identity-core test suite;
  this task verifies the login route and authorization seams.
