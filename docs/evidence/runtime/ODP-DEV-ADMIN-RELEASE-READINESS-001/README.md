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

## Previously local-only failures

`tests/e2e/test_release_gate_registry.py::test_product_gate_accepts_expected_sha`
and `::test_dev_merge_gate_accepts_valid_registry_and_require_go_checks_packet`
failed on the first worker run with `Cannot find module '@playwright/test'`
because `node_modules` was absent. The locked Node dependencies are now
installed on the worker (`npm ci`) and both run in the second command above.
