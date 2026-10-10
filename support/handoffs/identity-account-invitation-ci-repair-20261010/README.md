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
