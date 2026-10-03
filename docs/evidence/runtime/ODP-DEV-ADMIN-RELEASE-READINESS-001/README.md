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
uv run --frozen --python 3.12 pytest -q -p no:cacheprovider tests/release/test_release_profile.py tests/e2e/test_live_e2e_gate_dev_admin.py tests/e2e/test_live_e2e_gate.py
uv run --frozen --python 3.12 pytest -q -p no:cacheprovider tests/release/test_release_manifest.py tests/release/test_runtime_admission.py tests/release/test_build_release_handoff.py tests/ops/test_deploy_workflow_contract.py tests/reliability/test_health_endpoints.py tests/reliability/test_live_data_fail_closed.py tests/ops/test_cloud_run_live_deployment.py
uv run --frozen --python 3.12 ruff check delivery_toolchain/e2e/check_live_e2e_gate.py delivery_toolchain/release apps/api/oday_api/main.py apps/api/oday_api/runtime_mode.py tests/release/test_release_profile.py tests/e2e/test_live_e2e_gate_dev_admin.py
```

The first command holds the new behaviour. The second covers the release,
admission, workflow and readiness contracts this change touches. Required PR CI
runs as usual on the PR head.

## Known local-only failures (not caused by this change)

`tests/e2e/test_release_gate_registry.py::test_product_gate_accepts_expected_sha`
and `::test_dev_merge_gate_accepts_valid_registry_and_require_go_checks_packet`
fail on this worker host with `Cannot find module '@playwright/test'`.
`node_modules` is not installed here. Those tests list the Playwright suite and
touch nothing this change modifies. CI installs the Node dependencies.
