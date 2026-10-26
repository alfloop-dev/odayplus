# ODP-DEV-ADMIN-RELEASE-READINESS-001 evidence

Scope: source and offline verification only. This task made **no** cloud
write. It dispatched no build or deploy, ran no migration, trained or promoted
no model, activated no source, and made no change to IAM or credentials. It
did not edit the canonical release registry or manifest. Nothing here is live
evidence. A live dev run is the coordinator's next step under the existing
candidate task.

Design and handoff: `docs/design/ODP_DEV_ADMIN_RELEASE_PROFILE.md`.

## Targeted verification

These run once, at the final task head, after the last commit. Their exit
codes and that head SHA are published in the task `note` that accompanies
review submission. This file cannot embed them without moving the head it
describes.

```
uv run --frozen --python 3.12 pytest -o addopts="" -q -p no:cacheprovider tests/identity tests/security/test_user_role_management.py tests/release/test_release_profile.py tests/e2e/test_live_e2e_gate_dev_admin.py tests/e2e/test_live_e2e_gate.py tests/e2e/test_password_first_security_e2e.py
uv run --frozen --python 3.12 pytest -o addopts="" -q -p no:cacheprovider tests/ops tests/contract tests/security tests/e2e tests/release tests/integration/test_operator_live_domain_modules.py
(cd apps/web && npx vitest run && npx tsc --noEmit)
uv run --frozen --python 3.12 ruff check <changed python files>
uv run --frozen python delivery_toolchain/security/secret_scan.py
```

The first command holds the new behaviour: the profile and gate, the identity
bootstrap and identity-backed user administration against a real PostgreSQL 16
(`INTAKE_TEST_DATABASE_URL` or the bundled `pgserver`), and the password-first
boundary. The second covers the release, admission, workflow, contract and
security suites this change touches. The third covers the Operator Web
(Logout, admin and password views). Required PR CI runs as usual on the PR
head.

PostgreSQL rows created by those tests are test inputs in throwaway databases,
not live evidence.

## Review-6 continuation: pure-admin release notice

Pi preserved source head `c28dabe88db76a4d04df67b6ae32ae8d66645c68` and all
closed review findings. The bounded correction adds the missing operator-facing
notice to `/operator?view=admin`; see design §5.4. The server projects existing
API readiness/version after durable-session resolution, with exact admitted
profile/environment/candidate binding. It does not add a route, business grant,
model fallback or deployment input. PostgreSQL/auth/release source is unchanged;
previous green broad suites are not rerun for counts.

Focused verification commands for this correction (offline inputs only):

```
(cd apps/web && npx vitest run features/operator/__tests__/OperatorAdminAccess.test.tsx src/lib/auth/__tests__/operatorReleaseStatus.test.ts src/lib/auth/__tests__/operatorReleasePage.test.ts)
(cd apps/web && npx tsc --noEmit)
uv run --frozen python delivery_toolchain/governance/check_code_boundaries.py
```

The initial anchor `ba83c96d91fd` run exited 1 (56 passed, one new UI test
failed with an incomplete production-mode fetch fixture); TypeScript exited 2
(the new test environment lacked typed NODE_ENV). At `1313f42c1332`, TypeScript
and boundary checks exited 0, but that UI fixture still reused an already-consumed
Response (58 passed, one failed, exit 1). The fixture now returns a fresh Response
per call. These test setup defects were corrected, not skipped. The final focused receipts, exact head and subsequent required CI
are recorded through canonical task notes; no failed attempt is green evidence.
The new tests cover visible dev-only/missing-model messaging, ready full/admin
behavior, page wiring, malformed/mismatched bindings, persistence/session and
transport/read failures, and secret-free props. They are not live browser or
new PostgreSQL proof. The previously successful exact-head CI on `c28dabe8`
remains historical only; the notice head requires its own normal CI/review.

## Previously local-only failures

`tests/e2e/test_release_gate_registry.py::test_product_gate_accepts_expected_sha`
and `::test_dev_merge_gate_accepts_valid_registry_and_require_go_checks_packet`
failed on the first worker run with `Cannot find module '@playwright/test'`
because `node_modules` was absent. The locked Node dependencies are now
installed on the worker (`npm ci`) and both run in the second command above.
