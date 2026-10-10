# ODP-IDENTITY-ACCOUNT-INVITATION-001 — CI repair

Owner: Pi · Reviewer: Codex2 · PR: [#1447](https://github.com/alfloop-dev/odayplus/pull/1447)

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
