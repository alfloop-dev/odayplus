# ODP-DEPLOY-JOB-SECRET-BINDING-SELECTION-001 evidence

## The failure, from the exact receipts

Deploy Dev run [30376737123](https://github.com/alfloop-dev/odayplus/actions/runs/30376737123)
at `dda726155a399487474ae148b4dc1c3294ea9463` built and cosign-signed the API,
worker, and scheduler images. The immutable migration candidate
`oday-migration-r-dda726155a39` was deployed and **executed successfully** as
`oday-migration-r-dda726155a39-ndb4l`.

The deployment then aborted inside the migration gate on exactly one check:

```text
jobs-smoke:migration:secret_bindings
```

Rollback restored API revision `oday-api-00005-gin` and Web revision
`oday-web-00008-ws4`.

The check failed for a configuration that was correct. `cloud_run_job_checks`
proved secret bindings like this:

```python
required_secret_envs = (
    "oday_database_url",
    "odp_listing_provider_api_key",     # <-- always demanded
    "odp_poi_provider_api_key",
    "odp_geocode_provider_api_key",
    "odp_admin_boundary_provider_token",
)
all(name in description_text for name in required_secret_envs)
```

`description_text` is `json.dumps(job_description).lower()` — a substring scan
over the whole job description. The release selected

```text
ODP_PRODUCTION_PROVIDER_IDS=poi.commercial_api,geocode.primary_api,admin_boundary.official_dataset
```

`listing.partner_feed` is not in that set, so `scripts/deploy_cloud_run_waji.sh`
never adds `ODP_LISTING_PROVIDER_API_KEY` to `API_SECRET_BINDINGS`, and the
string `odp_listing_provider_api_key` never appears in the job description. The
gate demanded a secret for a provider the release deliberately does not deploy.

That is reproduced mechanically by
`test_job_smoke_reproduces_run_30376737123_secret_binding_failure`, which
asserts on the run's own receipt shape that

```python
assert "odp_listing_provider_api_key" not in json.dumps(job).lower()
```

and then requires every check to pass.

## Delivered boundary

The requirement is now derived, not hardcoded, and it is structural rather than
textual. `job_secret_binding_checks` does four things:

1. **Reads the job's own env, in either schema.** `_iter_job_containers` finds
   container mappings by shape, so both the Knative
   (`spec.template.spec.template.spec.containers`) and the v2
   (`template.template.containers`) layouts are supported. Migration, worker,
   and scheduler jobs all use the same reader.
2. **Reads the selection from the deployed job.** The plaintext
   `ODP_PRODUCTION_PROVIDER_IDS` env entry of the job under test is the
   authority for what that job actually selected.
3. **Derives the required secrets from the provider registry.**
   `required_job_secret_env_vars` returns `ODAY_DATABASE_URL` plus every
   `required_in_live` credential `env_var` of each selected provider, read from
   `modules.external_data.connectors.provider_registry`. Adding, renaming, or
   re-scoping a provider credential updates the deploy gate with no second copy
   of the mapping to drift.
4. **Proves each binding is a secret reference.** `_secret_binding_proof`
   accepts only `valueFrom.secretKeyRef.name` (Knative) or
   `valueSource.secretKeyRef.secret` (v2), and only when that reference is not a
   placeholder.

Substring scanning is gone for this check: a job that merely mentions
`ODAY_DATABASE_URL` in a label or an argument no longer satisfies it.

## Fail-closed matrix

| Job under test | Outcome |
| --- | --- |
| selection excludes `listing.partner_feed`, key not bound | **passes** (run 30376737123's case) |
| selection includes `listing.partner_feed`, key not bound | `secret_bindings` fails, naming `ODP_LISTING_PROVIDER_API_KEY` |
| selection includes `listing.partner_feed`, key bound | passes |
| `ODAY_DATABASE_URL` not bound | `secret_bindings` fails, for every selection |
| any single selected provider secret not bound | `secret_bindings` fails, naming it |
| selected provider secret set as a plaintext `value` | `secret_bindings` fails; the literal is never echoed into the detail or report |
| env entry with no `valueFrom`/`valueSource` | `binding declares no usable secretKeyRef` |
| empty `valueFrom` / `valueSource` / `secretKeyRef` | same |
| `secretKeyRef` naming a placeholder (`placeholder`, `changeme`, …) | same |
| no plaintext `ODP_PRODUCTION_PROVIDER_IDS` in the job | `provider_selection` **and** `secret_bindings` both fail: the selection is unprovable |
| `ODP_PRODUCTION_PROVIDER_IDS` supplied only as a secret reference | same — an unreadable selection proves nothing |
| selection names a provider the registry does not know | `provider_selection` and `secret_bindings` fail |
| provider registry cannot be imported | both fail with the import error |
| job selection ≠ release `ODP_PRODUCTION_PROVIDER_IDS` | `selected_provider_release_match` fails |
| release allowlist present but empty | `selected_provider_release_match` fails |
| a provider secret bound but not selected | passes; reported under `unselected_provider_secret_env_vars` |

The empty-`valueSource` rows are a deliberate tightening: the previous fixtures
used `{"name": "...", "valueSource": {}}`, which is not a binding gcloud emits
and which proves nothing about Secret Manager.

## Check and report surface

`jobs-smoke:<kind>:secret_bindings` keeps its name, so the deploy gate and any
existing triage against run 30376737123 still refer to the same check. Two
checks are added:

- `jobs-smoke:<kind>:provider_selection` — the job declares a readable,
  registry-known provider allowlist.
- `jobs-smoke:<kind>:selected_provider_release_match` — emitted only when the
  validating process has `ODP_PRODUCTION_PROVIDER_IDS` (the deploy script
  always exports it, since it writes the same value into the job env file); the
  deployed job's selection must equal the release's, compared as sets.

The report gains `selected_provider_ids`, `required_secret_env_vars`,
`secret_bound_env_vars`, `unselected_provider_secret_env_vars`, and (when
cross-checked) `release_provider_ids`. All are env-var and provider **names**;
`secret_values_redacted` remains `true` and
`test_job_smoke_rejects_plaintext_provider_secret` asserts that a plaintext key
placed in the job description never reaches the detail text or the report.

## Unchanged by this task

- `scripts/deploy_cloud_run_waji.sh`, both deploy workflows, and the job proof
  capture path (`capture_latest_execution`, `resolve-latest-execution`).
- The `jobs-smoke` CLI surface: same subcommand, same required arguments.
- `release_sha`, `entrypoint`, `execution`, and `execution_receipt` checks.
- Preflight, smoke, compatibility-smoke, traffic, and scheduler rollback logic.
- API, Package 10, model registry, and OperatorStateService scope.

## Focused verification

Executed from the task branch at commit `26aaa6cb`:

```text
uv run --frozen pytest tests/ops/test_cloud_run_live_deployment.py -q   # 84 passed
uv run --frozen pytest tests/ops -q                                     # 159 passed
uv run --frozen ruff check .                                            # All checks passed
uv run --frozen ruff format --check scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py
git diff --check
```

All commands passed. `uv` must be on `PATH`
(`export PATH="$HOME/.local/bin:$PATH"` on the worker image): the suite executes
the real deploy script through
`test_deploy_preflight_imports_runtime_dependencies_via_locked_python`, whose
`require_command` guard exits `1` without it.

Exact-head CI and an independent Codex6 review are required before merge. After
merge, ODP-P10-DEV-REDEPLOY-VERIFY-001 must re-run from the exact merged `dev`
SHA; that rerun is the live proof that run 30376737123's migration gate now
clears with the same provider selection.
