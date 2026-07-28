# ODP-DEPLOY-PREFLIGHT-CONFIG-001 — Dev deployment preflight configuration repair

Owner: Claude2 · Reviewer: Antigravity4 · Date: 2026-07-28

## Failure being repaired

Deploy Dev run [30316313748](https://github.com/alfloop-dev/odayplus/actions/runs/30316313748)
(head `b607d216144869014b5eca50ab552c5ba7f6bb41`, job `deploy`) failed in the
fail-closed preflight with four distinct error classes:

```
- config:ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS: missing or placeholder
- secret-reference:ODP_AUTH_PRINCIPAL_MAP_SECRET: missing or placeholder
- runtime:ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS: must be a finite number between 0.05 and 10 seconds
- repository:provider_registry_import: cannot import provider registry: ModuleNotFoundError: No module named 'httpx'
- repository:operator_bootstrap_data_source: invalid: live-required OperatorStateService still exposes seed data
```

The first four are configuration/dependency defects owned by this task. The
last (`operator_bootstrap_data_source`) is the separate OperatorStateService
live-data gate owned by ODP-LIVE-RUNTIME-DEV-COMPOSE-001 and is intentionally
**not** touched here.

## Repairs

### 1. Governed finite provider probe timeout

The runtime probe clamp lives in
`modules/external_data/connectors/provider_connectivity.py`:
`DEFAULT_PROBE_TIMEOUT_SECONDS = 3.0`, band `[0.05, 10.0]` — the same band the
preflight enforces (`MIN/MAX_PROVIDER_PROBE_TIMEOUT_SECONDS`). The governed dev
value is therefore the runtime connector's own default:

- GitHub `dev` environment variable
  `ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS = 3.0` (set 2026-07-28, verified
  via `gh api repos/alfloop-dev/odayplus/environments/dev/variables/...`).
- New test `test_provider_probe_timeout_band_matches_runtime_connector` pins
  preflight band == connector band and that the connector default passes the
  preflight check, so the two sides cannot drift silently.

### 2. Principal-map Secret Manager secret, reference-only in GitHub

No principal-map secret existed in project `alfaloop-data-project`. Created:

- Secret: `oday-plus-dev-auth-principal-map` (version 1, automatic
  replication).
- Value (role mapping, no credentials): JSON keyed by the operator smoke
  principal, derived from the already-governed dev variables
  `ODP_OPERATOR_SMOKE_SUBJECT = 110296401444439097904` and
  `ODP_AUTH_SUBJECT_ROLE_BINDINGS = {"110296401444439097904": ["operations_manager"]}`;
  the subject was independently re-verified against
  `gcloud iam service-accounts describe oday-dev-smoke-operator@… --format='value(uniqueId)'`.
  Both the numeric subject and the service-account email map to
  `{"roles": ["operations_manager"]}`, matching
  `modules/opsboard/auth/config.py::_parse_principal_mappings` and the
  boundary's subject-then-email lookup.
- IAM: `roles/secretmanager.secretAccessor` granted to
  `gke-oday-dev-runtime@alfaloop-data-project.iam.gserviceaccount.com`,
  matching the per-secret binding pattern of the other `oday-plus-dev-*`
  secrets.
- GitHub `dev` environment variable
  `ODP_AUTH_PRINCIPAL_MAP_SECRET = oday-plus-dev-auth-principal-map:latest` —
  the reference only; the secret value exists solely in Secret Manager, which
  is exactly what `scripts/deploy_cloud_run_waji.sh` expects
  (`ODP_AUTH_PRINCIPAL_MAP=${ODP_AUTH_PRINCIPAL_MAP_SECRET}` secret binding).

### 3. Locked dependency bootstrap before preflight

`.github/workflows/deploy-dev.yml` ran the preflight on the runner's bare
`python3`, where `repository_capability_checks` cannot import the real
provider registry (`httpx`). The job already installs uv + Python 3.12; it now
runs `uv sync --frozen` before the preflight and invokes it with
`uv run --frozen python …`, so the preflight executes against the locked
project environment with no check weakened. Guarded by
`test_dev_workflow_bootstraps_locked_dependencies_before_preflight`.

## Verification

- `uv run --frozen pytest tests/ops/test_cloud_run_live_deployment.py` →
  **37 passed** (includes the two new tests).
- `uv run --frozen ruff check tests/ops/test_cloud_run_live_deployment.py .github`
  → clean.
- Local preflight replay with the exact env of the failing run (values lifted
  from the run 30316313748 job log) plus the two newly configured variables:

  ```
  Cloud Run live deployment preflight failed (fail-closed):
  - repository:operator_bootstrap_data_source: invalid: live-required OperatorStateService still exposes seed data
  ```

  68 checks evaluated; every configuration, secret-reference, runtime and
  dependency check passes and the preflight now reaches the separate
  OperatorStateService live-data gate as the single remaining (out-of-scope)
  blocker. Full redacted report: `preflight-local-replay.json`
  (`secret_values_redacted: true`).

## Scope guarantees

- No change to `apps/api`, `apps/web`, `modules/opsboard`,
  OperatorStateService, operator seed behavior, or any Package 10 visual
  archive path.
- Repo diff is limited to `.github/workflows/deploy-dev.yml`,
  `tests/ops/test_cloud_run_live_deployment.py`, and this evidence directory;
  the remaining acceptance items are GCP/GitHub configuration recorded above.
