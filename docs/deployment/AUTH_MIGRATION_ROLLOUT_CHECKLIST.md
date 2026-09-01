# 身份遷移上線檢查表（Password-First / 可選 OIDC）

Task: `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`  
Contract: `docs/design/ODP_WEB_PASSWORD_FIRST_AUTH_CONTRACT.md` §§2–10  
Source: `apps/web/README.md`、`infra/terraform/README.md`、
`docs/deployment/GCP_DEPLOY_GUIDE.md`

本表是從 OIDC-only 遷移到 password-first、OIDC optional 的 release gate。每一項
都要有不含 secret payload 的部署或測試證據；任何 fail 都停止 promotion。

## 1. Preflight

- [ ] `identity` migrations 已套用，`identity.accounts`、`password_credentials`、
  `sessions` 與 `login_attempts` 版本一致。
- [ ] `ODAY_DATABASE_URL` 是 Web/API 共用的 authoritative identity database，
  且 Web revision 已由 Secret Manager reference 綁定。
- [ ] `ODP_WEB_SESSION_SECRET` 與 `ODP_IDENTITY_TOKEN_SIGNING_KEY` 已建立並由
  Secret Manager 注入；receipt 只記 reference，不記 payload。
- [ ] `ODP_WEB_BASE_URL` 是 canonical HTTPS origin；API audience、service issuer
  與 JWKS/verification inputs 已配置。
- [ ] migration/reconciliation job 已完成，API `/readiness` 回 HTTP 200。

## 2. Authentication modes

### Local default

- [ ] `ODP_AUTH_MODE` 未設定或為 `local`。
- [ ] 沒有 OIDC 變數時 deploy preflight 通過，Web `/login` 顯示 password form，
  不顯示 OIDC provider。
- [ ] `POST /login` 的 throttle gate 在 credential verification 前執行。
- [ ] account 15-minute threshold 與 source-IP threshold 都回 429
  `AUTH_RATE_LIMITED`，不以 423 洩漏帳號狀態。
- [ ] 423 `AUTH_ACCOUNT_LOCKED` 只在正確密碼已驗證後出現，且不建立 session。
- [ ] `identity.login_attempts` 使用 peppered derived key，production 沒有 DB 或
  pepper 時 fail closed；database outage 回 `WEB_AUTH_UNAVAILABLE`。

### Optional OIDC

- [ ] `ODP_AUTH_MODE=oidc` 時 issuer、client id、client secret 與 provider
  endpoints 完整；缺任一項即 503，不降級成 password 或匿名。
- [ ] 完整 OIDC 設定時 authorization-code + PKCE transaction cookie 啟用，
  password login 仍可用。
- [ ] 切回 local 後，leftover OIDC inputs 不會讓 API 接受 OIDC token。
- [ ] local password 與 linked OIDC 解析到同一 account、tenant、roles、scope；
  未 link 的 federated identity 拒絕。

## 3. Session, RBAC, and audit

- [ ] Browser cookie 是 `__Host-`、`HttpOnly`、`Secure`、`SameSite=Lax`，只含
  opaque session reference；bearer 只存在 `identity.sessions`。
- [ ] session tenant context 從 authoritative identity/session store 解析，不信任
  browser claims 或 client headers。
- [ ] 跨 tenant read/write 回 403，並寫入 `operator.tenant_isolation` deny audit
  event；same-tenant allow 也保留 audit evidence。
- [ ] audit event 不含 password、bearer token、session id、database URL 或任何
  secret payload。

## 4. Rollout and rollback

- [ ] 先在 dev 驗證 migration、`/healthz`、`/readiness`、login mode、throttle、
  RBAC/audit，再進 staging rehearsal。
- [ ] staging 驗證 migration compatibility、OIDC local/optional matrix、rollback
  traffic 與 receipt upload；未通過不得進 production。
- [ ] production 使用 immutable API/Web image digest、exact release SHA、private
  API invocation、Cloud SQL private path 與至少兩個 Web/API instances。
- [ ] rollback 保留新增 schema 與 session rows，關閉新路徑或回到已驗證 revision；
  不執行破壞性的 down migration，不把 secret payload 寫入 log 或 receipt。
- [ ] 變更後確認 active sessions、revocation、audit chain 與 alerting；所有結果
  連到本 task receipt。

## 5. Verification commands

The final layered suite is run once after implementation is complete:

```text
npm --prefix apps/web run test
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
uv run --python 3.12 pytest tests/e2e/test_password_first_security_e2e.py tests/security/test_login_throttle_wiring.py tests/ops/test_conditional_oidc_deployment.py tests/identity
uv run --python 3.12 ruff check shared tests/identity tests/security/test_login_throttle_wiring.py tests/ops/test_conditional_oidc_deployment.py tests/e2e/test_password_first_security_e2e.py
python3 infra/terraform/validate_contract.py
python3 -m unittest discover -s infra/terraform/tests -p 'test_*.py'
```

No command output may contain secret values. The final receipt records only command
names, pass/fail counts, commit SHA, and review metadata.

## 6. Task verification result

- [x] Web route suite: 53 files / 474 tests passed; typecheck and lint passed.
- [x] Python auth/security/identity/ops layers: 151 passed, 22 skipped.
- [x] Ruff: all checks passed.
- [x] Terraform production contract: pass; 14 files checked.
- [x] Terraform unit tests: 32 tests OK.
- [x] Receipt and this checklist contain no secret payload; `secret_values_redacted: true` is recorded in the receipt.

This result is repository-level acceptance evidence. Live GCP rollout, DNS,
Cloud SQL migration execution, and external OIDC provider availability remain
deployment-owner gates and are not claimed by this task.
