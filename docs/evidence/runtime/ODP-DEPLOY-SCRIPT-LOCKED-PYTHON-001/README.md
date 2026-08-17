# ODP-DEPLOY-SCRIPT-LOCKED-PYTHON-001 evidence

## Failure reproduced

GitHub Actions [Deploy Dev run 30331484524](https://github.com/alfloop-dev/odayplus/actions/runs/30331484524)
failed on source head `3ecdcdf1c2f0a98e5218a7989d4dae9bd48617c4`.
The deploy job successfully completed both `Install locked project dependencies`
and its standalone locked preflight. The subsequent `Build, push, deploy, and
verify Cloud Run` step entered `product_ops/deployment/deploy_cloud_run_waji.sh`, whose internal
preflight used bare `python3` and failed closed before any build:

- `repository:provider_registry_import`: `ModuleNotFoundError: No module named 'httpx'`
- `repository:operator_bootstrap_data_source`: `ModuleNotFoundError: No module named 'pydantic'`

This confirms the repository dependencies were present in the `uv.lock`
environment, but the deploy script discarded that environment when it launched
its own validators.

## Delivered boundary

Anchor commit `84bc39947f788640676edbbe914d49f2d707eb91` introduces one
`run_locked_python` entry point backed by `uv run --frozen python`. It is used
for all repository-aware validators:

- deployment preflight
- Cloud Run Job smoke validation
- migration compatibility smoke validation
- candidate release smoke validation
- promoted-release live E2E validation

The two inline JSON serializers remain explicit `python3 -` helpers. They import
only `json`, `os`, and `sys` from the standard library. Gate ordering and
fail-closed behavior are unchanged: the preflight still precedes the first
build, and every validator exit code still propagates through `set -euo
pipefail`.

No workflow, API, Package 10, model-registry, OperatorStateService, or retired
path was changed.

## Focused verification

Executed from the task branch:

```text
git diff --check
bash -n product_ops/deployment/deploy_cloud_run_waji.sh
uv run --frozen pytest tests/ops/test_cloud_run_live_deployment.py -q
uv run --frozen ruff check tests/ops/test_cloud_run_live_deployment.py
```

All commands passed.

The focused shell harness makes every non-inline system-`python3` invocation
exit with code 86, then executes the real deploy-script preflight. The preflight
still reaches the intentionally stubbed `gcloud` boundary, its report is
`ok=true`, no `repository:provider_registry_import` failure is emitted, every
required provider adapter check passes, and
`repository:operator_bootstrap_data_source` passes. This directly closes the
`httpx` and `pydantic` import failures observed in run 30331484524.

Exact-head CI and independent Codex6 review remain required before merge.
