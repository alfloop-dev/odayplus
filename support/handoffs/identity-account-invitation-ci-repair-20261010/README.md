# ODP-IDENTITY-ACCOUNT-INVITATION-001 — CI repair

Owner: Pi · Reviewer: Codex2 · PR: [#1447](https://github.com/alfloop-dev/odayplus/pull/1447)

**Current checkpoint (2026-10-10):** base `d99de4a05ef5` is composed with
preserved history; the lifecycle repair is implemented on source anchor
`c29f9c46524c`. Focused verification passes, including actual Web/Pg/API auth.
Submit the new head for independent Codex2 review and required remote CI;
**no merge, live provisioning, or deployment acceptance is claimed.** The older
"not ready" paragraphs below describe prior increments, not this checkpoint.
The optional broader release-binding run hit a missing local PyNaCl dependency;
its failure and unavailable project runner are disclosed in the final section.

## Failure and bounded repair

[CI run 38029501236](https://github.com/alfloop-dev/odayplus/actions/runs/38029501236)
on submitted implementation head `3f9c8617863d1c4725e8ab61b3a123ffd4d6d330`
failed the API contract freshness gate. The product unit job's terminal receipt
was **1 failed, 7129 passed, 23 skipped** (exit 1); the only failing test was
`tests/contract/test_openapi_artifact_and_client.py::test_artifact_is_checked_in_and_matches_the_live_app`.
The aggregate product job failed because of these failures. This was artifact
drift, not a transient infrastructure failure; no blind CI retry was used.

Anchor `55fd757c99ad` regenerates `packages/openapi-client/openapi.json` and
`packages/openapi-client/src/generated/types.ts` using the existing exporters.
The diff contains only `UserInvitePayload` and the additive POST operations
`/api/v1/operator/users/invite` and `/api/v1/operator/users/create`.
No invitation runtime/UI logic, existing account, credential binding, deployment,
bootstrap bypass, or dev-admin live E2E gate was changed in this repair.

## Local verification receipts

Executed on anchor `55fd757c99ad` on 2026-10-10; the following commands all
completed synchronously with terminal exit **0**:

```sh
.venv/bin/python delivery_toolchain/openapi/check_drift.py --base-ref origin/dev
.venv/bin/python -m pytest -m 'not requires_live_env' \
  tests/contract/test_openapi_artifact_and_client.py \
  tests/identity/test_identity_user_role_management.py \
  tests/security/test_user_role_management.py
.venv/bin/python -m ruff check \
  apps/api/app/routes/operator_modules/users_roles.py \
  modules/opsboard/application/identity_user_role_management.py \
  modules/opsboard/application/user_role_management.py \
  tests/identity/test_identity_user_role_management.py \
  tests/security/test_user_role_management.py
.venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
```

- Contract gate: artifact/client fresh; **2 additive, 0 unapproved breaking**.
- Focused pytest: **58 passed**, 9 deprecation warnings, 88.67 seconds.
- Task Python lint: all checks passed.
- Boundary inventory: passed for 1211 files, no regeneration required.

The final evidence-only commit does not change the verified runtime or generated
artifacts. The full CI suite was not rerun locally; required remote CI and
Codex2 review must approve the new submitted head before merge/owner closeout.
No live account was provisioned and no deployment acceptance is claimed.

## Reopened review — base composition and UI increment (2026-10-10)

**This branch is not ready for review or merge.** Codex2 reopened `525feca41085`
for two P1 lifecycle findings and two P2 UI findings. The earlier passing CI and
58-test receipt do not resolve those findings.

- Merge `f15b840361d3` composes `origin/dev` `31785c571e06` without resetting,
  rebasing, discarding, or overwriting any task commit. No conflicts occurred.
- UI anchor `275c21af9ffa` hands off `user.username` (falling back to the
  submitted login name), never the UUID `subject_id`. Tests model a UUID account
  identifier distinct from the login name.
- Clipboard success is reported only after `writeText` resolves. Missing or
  rejected clipboard shows a static manual-copy warning and retains the
  one-time view; retry is supported. Pending writes cannot update the UI after
  close/unmount. Closing also clears the copy state, and the initial password
  form state is cleared after successful credential handoff.
- This increment does not change invitation issuance, account activation,
  passwords/authentication, API mounts, live users, or dev-admin gates.

### Increment verification

All commands below completed synchronously with terminal exit **0** on the
base-composed UI anchor plus the test-helper correction (the helper must fill
in the form's required audit reason):

```sh
npm test --workspace=@oday-plus/web -- features/operator/__tests__/UserRoleManagementController.test.tsx
npm run typecheck --workspace=@oday-plus/web
git diff --check
.venv/bin/python -m pytest -m 'not requires_live_env' \
  tests/contract/test_openapi_artifact_and_client.py \
  tests/identity/test_identity_user_role_management.py \
  tests/security/test_user_role_management.py
```

- Focused UI: **14 passed**, including username/UUID separation, delayed success,
  clipboard rejection/retry, unavailable clipboard, and late completion after close.
- Web typecheck and whitespace checks passed.
- Existing focused Python/contract suite: **58 passed**, 9 deprecation warnings,
  79.85 seconds. This is base-composition regression evidence, **not** real
  invitation acceptance/auth-path evidence for the unresolved lifecycle.
- The first UI run exited 1 (**5 failed, 9 passed**) because the new helper left
  the required audit reason empty. Correcting that helper yielded the above
  terminal passing receipt. `pnpm` was unavailable on PATH (exit 127); the
  repository's existing npm workspace scripts were used, without dependency or
  lockfile changes.

### Remaining required composition

PR [#1448](https://github.com/alfloop-dev/odayplus/pull/1448), task
`ODP-DEV-SMOKE-ACCOUNT-PROVISIONING-001`, owns the shared invitation service,
acceptance budget/migration, API/Web acceptance adapters, and overlapping users
router/service/OpenAPI files. Its head `7c6e083a8489` is in review, not approved
or merged as of this increment. No files from that unreviewed branch were copied
here, and no overlapping leased files were modified in this increment.

After its independent review and merge, compose its supported lifecycle from
`origin/dev`, explicitly extend it for general-role/scope invitations, and
remove this branch's password-reuse/immediate-active implementation. Preserve
existing users/router contracts and bounded pure-admin provisioning semantics;
use one acceptance authority and no conflicting route mounts. Required work:

1. Independent CSPRNG capability, hash-only storage, pending TTL/revocation;
   do not persist SHA256(password), or create usable credentials before acceptance.
2. Atomic, single-use acceptance with Argon2id, roles/scope, durable audit and
   appropriate `PASSWORD_CHANGE_REQUIRED` enforcement for general invitations.
3. Actual acceptance/router/PostgreSQL/auth-path expiry, revocation, replay,
   duplicate, tenant, permission, and rollback regression tests; regenerate
   OpenAPI artifacts for the exact composed contract.
4. Exact integrated-head CI and independent Codex2 review, submitted through
   `task_finalize.sh` only when the P1 findings are genuinely repaired.

Do not infer lifecycle safety from this UI increment or green CI. No live
credential custody, role mutations, provisioning, or deployment acceptance is
part of this worker's execution.

## Independently merged base and lifecycle repair (2026-10-10)

GitHub confirmed PR #1448 merged at `2026-10-10T14:00:26Z`; `origin/dev` is
`d99de4a05ef58340247470ab213089b8706543c7`. Merge anchor `d891af7c7749`
has parents `81b1b05599ce` and that base. Router conflicts retain both the fixed
release invitation endpoint and this task's general endpoint; generated contract
conflicts were resolved by exporting the composed schema. No task commit was
reset, rebased, discarded or overwritten. Anchors `40850238dc1a`, `5966b2200eca`
and `c29f9c46524c` make the following repair durable:

- Removed both immediate-account/password-as-token implementations, including
  the document-store fake credential fallback. Issuance creates only a pending
  `identity.invitations` row, never an account, password credential or session.
- `InvitationService.issue_account` extends the same PostgreSQL transaction,
  durable audit and acceptance budget authority with a versioned, validated
  username/display-name/roles/scope preset. Tenant comes from the verified
  issuer, whose persisted admin role, active account, rotated password and
  unrevoked/unexpired session are rechecked under the administration lock.
- Token is independent `secrets.token_urlsafe(32)` entropy, returned once with
  ID/expiry and persisted only as its hash. No issuance password is accepted.
  Pending username/email collisions are case-insensitive. A pure release invite
  cannot consume a pending general invitation's reserved username.
- Existing capability acceptance checks expiry, revocation and single use under
  the write lock, including after expensive hashing. General acceptance binds
  the issuer-selected username, roles and full scope; creates Argon2id credentials
  with `must_change=true`; and consumes capability plus audit in one transaction.
  Pure-admin release presets remain unchanged (`must_change=false`); legacy or
  malformed presets still fail closed rather than silently dropping grants.
- POST `/api/v1/operator/users/invite` (and `/create` alias) now returns **201**
  with `{status, invitation_id, tenant_id, expires_at, audit_event_id, token}`.
  It uses bounded manual validation/static errors and `cache-control: no-store`.
  Caller actor fields, passwords and unknown fields are refused without echo.
  These endpoints are additive against `origin/dev`, not an approved breaking
  change to mainline. Regenerated OpenAPI and client match this exact contract.
- Admin UI hands off the submitted login name separately from the invitation ID,
  token and expiry, never a manufactured active user or password. Clipboard
  completion/failure/manual-copy/late-completion behavior is preserved. The
  token lives only in component memory and is cleared on close/unmount.
- GET `/auth/invitations` adds manual private entry on the existing bounded Web
  adapter. No query prefill, browser storage, session creation or token URL is
  used. It has no-store/no-referrer/CSP; success clears the form. POST remains the
  existing capability adapter; after acceptance, use the existing login and
  password-change flow, not a second authentication authority.

### Verified source and terminal receipts

The following completed synchronously on source `c29f9c46524c` with **exit 0**:

```sh
.venv/bin/python -m pytest -m 'not requires_live_env' \
  tests/contract/test_openapi_artifact_and_client.py \
  tests/identity/test_identity_user_role_management.py \
  tests/security/test_user_role_management.py \
  tests/security/test_dev_smoke_invitation.py
npm test --workspace=@oday-plus/web -- \
  features/operator/__tests__/UserRoleManagementController.test.tsx \
  src/app/auth/invitations/__tests__/route.test.ts \
  src/lib/auth/__tests__/password.test.ts \
  src/lib/auth/__tests__/login.test.ts \
  src/lib/auth/__tests__/localAuth.test.ts
npm run typecheck --workspace=@oday-plus/web
.venv/bin/python -m ruff check \
  apps/api/app/routes/operator_modules/users_roles.py \
  modules/opsboard/application/identity_user_role_management.py \
  modules/opsboard/application/user_role_management.py \
  shared/identity/invitation_service.py \
  tests/identity/test_identity_user_role_management.py \
  tests/security/test_user_role_management.py \
  tests/security/test_dev_smoke_invitation.py
.venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
.venv/bin/python delivery_toolchain/openapi/check_drift.py --base-ref origin/dev
git diff --check
```

- Focused Python/contract: **108 passed**, 8 deprecation warnings, 160.51 seconds.
- Web: **81 passed, 1 skipped**; the skipped case requires the disposable Pg
  fixture and is executed (not skipped) by the Python acceptance regression.
- That regression invokes the existing password-route Vitest fixture with real
  PostgreSQL identity, session and throttle stores, using finite subprocess
  timeouts and asserting every terminal exit. It proves expired/revoked
  capabilities cannot log in/reset; actual API acceptance creates the account;
  `/login` verifies the accepted password and persists a real Web bearer;
  the existing API boundary returns `PASSWORD_CHANGE_REQUIRED`; actual
  `/auth/password` verifies and rotates the credential/sessions; the old Web
  bearer is refused and the rotated bearer has only its authorized business
  role (no user administration). There is no manually minted recipient JWT or
  direct SQL password rotation standing in for the tested path.
- General tests cover hash-only custody, full role/scope persistence, issuer name
  binding, malformed/foreign presets, pending/active duplicates, pure-release
  isolation, expiry after hashing, replay and atomic audit-failure rollback.
- Lint, Web typecheck, boundary inventory (1220 files) and contract freshness pass;
  drift is **2 additive, 0 unapproved breaking**.

The first auth-fixture runs failed on happy-dom's forbidden Origin header and
constructor-time cookie parsing (403, then 401). Using the existing route-test
patterns (explicit Origin and request.cookies.set) repaired the fixture; no
production auth, CSRF, session or gate policy was weakened. The dedicated real
Web/Pg/API regression then completed with exit 0, before the integrated rerun.

### Broader optional release-binding verification limitation

The combined focused command above **plus**
`tests/integration/test_dev_smoke_provisioning.py` completed with **exit 1**:
**437 passed, 59 setup errors**, 432.92 seconds. All setup errors were missing
`nacl` in the inherited local `.venv` at the encrypted-binding fixture. PyNaCl is
already declared by the independently merged base's `pyproject.toml`/`uv.lock`.
No release-binding assertion failure was observed, but this run is **not a
passing integrated-release receipt**. No tests were skipped or weakened to hide
it. The isolated task-focused command was subsequently rerun to establish the
passing terminal receipt above, not to count tests.

The permitted project recovery runner is unavailable here: `uv run pytest
--version` exited 127; `python3 -m uv --version` and `.venv/bin/python -m uv
--version` exited 1 (module absent). No host-wide tool search, ad-hoc dependency
installation, dependency/lockfile edit, or blind CI retry was attempted.
Required remote CI must exercise the exact submitted head with declared
dependencies; Codex2 must independently approve before any merge or `done`.
The next commit records evidence only, not another source change. This worker
performed no live identity mutations, credential binding, release admission or
deployment changes and did not alter the dev-admin gate relative to the base.
