# ODP-PROD-RUNTIME-RELEASE-PATH-001: Production Runtime Release Path Evidence

Task: 建立 production 的 Runtime Release 部署路徑
Status: Path structurally correct — blocked on ops variable provisioning
Verified at: 2026-09-15T06:05:00Z
Verified by: Antigravity6

## Summary

Runtime Release (`.github/workflows/deploy-dev.yml`) is the sole deployment
entrypoint for all environments.  The workflow already routes production
correctly at the code level: environment choice, concurrency groups, job
bindings, conditional gates, and fail-closed URL validation are all in place.

**However**, the GitHub `production` and `production-build` environments are
both missing two required variables (`ODP_CLOUD_RUN_VPC_CONNECTOR` and
`ODP_CLOUD_RUN_VPC_EGRESS`).  Until those are provisioned, both the build
and deploy binding gates will fail closed, making the production path
**currently not executable**.  A blocker has been raised for Human/Ops.

No workflow code change is required — the path structure is already correct.

---

## AC-1: Environment choice contains `production` and concurrency group is independent

**Status: ✅ Satisfied**

### Evidence

`.github/workflows/deploy-dev.yml` line 57–61:

```yaml
environment:
  description: "Release environment"
  required: true
  type: choice
  options: [dev, staging, production]
```

Concurrency group at line 149–151:

```yaml
concurrency:
  group: runtime-release-${{ inputs.environment }}-${{ inputs.phase }}
  cancel-in-progress: false
```

When `inputs.environment = production`, the concurrency key becomes
`runtime-release-production-build` or `runtime-release-production-deploy`,
which is distinct from both the `dev` and `staging` groups.

### Contract test

`test_environment_inputs_support_dev_staging_production` (tests/ops/test_deploy_workflow_contract.py line 1134)
asserts `set(env_input["options"]) == {"dev", "staging", "production"}`.

---

## AC-2: URL resolution — production resolves to `vars.ODP_PROD_DEPLOY_URL` with fail-closed guard

**Status: ✅ Satisfied**

### Disclosure: AC-2 references a removed artifact

AC-2 originally referenced "第 94 行 url 的二元三元式" — a ternary expression
in the workflow's `environment:` block `url:` key.  **That `url:` key was
removed by commit `c35ffe05`** (`ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001`),
which replaced the staging-in-workflow URL derivation with Terraform-based
ephemeral staging.  The current line 94 is a `web_image` input definition.

The defect AC-2 targeted (production silently falling back to the dev URL) is
**no longer possible** because the URL resolution now occurs inside the deploy
script with explicit fail-closed guards, not in a ternary expression that
could silently default.

### Current implementation

The live E2E URL resolution in `product_ops/deployment/deploy_cloud_run_waji.sh`
lines 985–991 is a **two-branch if/else** (not a three-branch ternary):

```bash
if [ "${ODP_DEPLOY_ENV}" = "production" ]; then
  LIVE_E2E_API_URL="${ODP_PROD_API_URL}"
  LIVE_E2E_WEB_URL="${ODP_PROD_DEPLOY_URL}"
else
  LIVE_E2E_API_URL="$(service_snapshot_url "${API_CANDIDATE_DESCRIPTION}")"
  LIVE_E2E_WEB_URL="$(service_snapshot_url "${WEB_CANDIDATE_DESCRIPTION}")"
fi
```

The three effective paths are:

- **production** → `ODP_PROD_DEPLOY_URL` / `ODP_PROD_API_URL` (enters the `if` branch)
- **dev** → Cloud Run default service URL (enters the `else` branch)
- **staging** → never reaches `deploy_cloud_run_waji.sh` at all, because the
  step is gated on `if: ${{ inputs.environment != 'staging' }}` at line 1200.
  Staging derives URLs from Terraform outputs via `staging_lifecycle.py` at
  workflow lines 1287–1288.

This constitutes three **effective** URL paths, but the if/else itself is a
two-branch structure.  The previous ternary was a literal three-branch
expression; this is a two-branch shell conditional plus a workflow-level gate.

### Fail-closed guard

The deploy script fails closed when production URLs are missing or non-HTTPS
(lines 43–51):

```bash
if [ "${ODP_DEPLOY_ENV}" = "production" ]; then
  : "${ODP_PROD_DEPLOY_URL:?Error: ODP_PROD_DEPLOY_URL is required for production live E2E.}"
  : "${ODP_PROD_API_URL:?Error: ODP_PROD_API_URL is required for production live E2E.}"
  for production_url in "${ODP_PROD_DEPLOY_URL}" "${ODP_PROD_API_URL}"; do
    if [[ ! "${production_url}" =~ ^https://[^[:space:]]+$ ]]; then
      echo "Error: production live E2E URLs must be HTTPS custom domains." >&2
      exit 1
    fi
  done
fi
```

### How `ODP_PROD_DEPLOY_URL` reaches the script

The deploy job passes it from the production GitHub environment at workflow
line 1080:

```yaml
ODP_PROD_DEPLOY_URL: ${{ vars.ODP_PROD_DEPLOY_URL }}
```

Because the deploy job binds `environment: ${{ inputs.environment }}` (line 996),
selecting `production` makes `vars.ODP_PROD_DEPLOY_URL` resolve from the
GitHub `production` environment where the value
`https://console.oday-plus.com.tw` is configured.

### Why fallback to dev URL is impossible

1. The `if` clause in `deploy_cloud_run_waji.sh` explicitly checks
   `"${ODP_DEPLOY_ENV}" = "production"`.  The deploy job sets
   `ODP_DEPLOY_ENV: ${{ inputs.environment }}` (line 1063), so selecting
   `production` always enters the production branch.
2. The deploy script requires `ODP_PROD_DEPLOY_URL` to be non-empty and HTTPS
   before any Cloud Run mutation (fail-closed).
3. Even if `vars.ODP_PROD_DEPLOY_URL` resolved to empty (misconfigured
   environment), the `:?` parameter expansion at line 44 would abort the
   script with exit code 1 before any deploy.

---

## AC-3: Audit of every `inputs.environment == staging` condition

**Status: ✅ Satisfied — all conditions audited below**

Every condition in the deploy job that branches on `staging` falls into three
categories:

### Category A: `!= 'staging'` — runs for both dev and production

These are the common non-staging operations that production correctly inherits:

| Line | Step Name | Guard | Production path | Rationale |
|------|-----------|-------|-----------------|-----------|
| 1120 | 確認 deploy 階段已綁定 environment 且變數齊備 | `!= 'staging'` | **Fires** — runs `--scope deploy` against the `production` environment | Production needs the same core deploy binding variables as dev |
| 1177 | Run the live runtime preflight | `!= 'staging'` | **Fires** — validates Cloud Run live deployment readiness | Production runs the same live preflight as dev |
| 1200 | Deploy Cloud Run by immutable digest | `!= 'staging'` | **Fires** — `deploy_cloud_run_waji.sh` reads `ODP_DEPLOY_ENV=production` | Production uses the static deploy script with production URL branch |

### Category B: `== 'staging'` — staging-only operations, correctly skipped for production

These are ephemeral staging lifecycle operations that production must not execute:

| Line | Step Name | Guard | Production path | Rationale |
|------|-----------|-------|-----------------|-----------|
| 1153 | 確認 staging foundation 已綁定 environment 且變數齊備 | `== 'staging'` | **Skipped** | Staging foundation variables (VPC, KMS, Terraform) are staging-only IaC |
| 1204 | Prepare release-scoped staging handoff paths | `== 'staging'` | **Skipped** | Ephemeral staging Terraform paths are not used in production |
| 1227 | Execute ephemeral staging lifecycle create | `== 'staging'` | **Skipped** | Production deploys via `deploy_cloud_run_waji.sh`, not `staging_lifecycle.py` |
| 1253 | Persist staging recovery bundle to protected recovery storage | `success() && == 'staging'` | **Skipped** | Staging recovery bundles are staging-only |
| 1263 | Execute ephemeral staging rehearsal verification | `== 'staging'` | **Skipped** | Staging rehearsal is an ephemeral staging concept |
| 1284 | Verify release-scoped staging authority endpoint | `== 'staging'` | **Skipped** | Staging remote proof uses staging Terraform outputs |
| 1296 | Hold ephemeral staging resources on failure | `failure() && == 'staging'` | **Skipped** | Production failure recovery is handled by `deploy_cloud_run_waji.sh`'s built-in rollback |
| 1315 | Persist staging hold state to protected recovery storage | `always() && == 'staging'` | **Skipped** | Staging hold state is staging-only |
| 1327 | Upload staging lifecycle receipts | `always() && == 'staging'` | **Skipped** | These receipts are only produced by staging lifecycle |

### Category C: `== 'production'` — production-only operations

| Line | Step Name | Guard | Production path | Rationale |
|------|-----------|-------|-----------------|-----------|
| 1338 | Verify production blue-green deployment state | `== 'production'` | **Fires** — captures and validates blue-green traffic state | Only production has blue-green traffic management |
| 1397 | staging_closeout job | `always() && == 'production' && deploy.success` | **Fires** — runs as a separate job to clean up staging after production deploy | Uses staging credentials to read watch receipt and clean up staging resources |

### Why no condition defaults production to a wrong path

Every `!= 'staging'` guard correctly implies "dev or production". The deploy
script's internal branching on `ODP_DEPLOY_ENV` ensures production-specific
logic (URLs, blue-green) triggers correctly. No condition uses a fallback
pattern like `staging ? X : Y` where production would silently receive Y.

---

## AC-4: Deploy job binds the production environment — `vars.*` resolve correctly

**Status: ⚠️ Structurally correct but NOT YET EXECUTABLE — missing VPC variables**

### Environment binding

`.github/workflows/deploy-dev.yml` deploy job, lines 995–996:

```yaml
environment:
  name: ${{ inputs.environment }}
```

When `inputs.environment = production`, the job binds to the GitHub `production`
environment.  All `vars.*` expressions in the deploy job's `env:` block
(lines 999–1088) resolve from that environment.

### Binding gate

The deploy job runs `check_release_environment.py --scope deploy` before any
Google Cloud authentication (line 1120, gated on `inputs.environment != 'staging'`).
This step verifies all required variables resolved to non-empty values:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_AR_REPO`
- `ODP_CLOUD_RUN_API_SERVICE`
- `ODP_CLOUD_RUN_WEB_SERVICE`
- `ODP_CLOUD_RUN_MIGRATION_JOB`
- `ODP_CLOUD_RUN_WORKER_JOB`
- `ODP_CLOUD_RUN_SCHEDULER_JOB`
- `ODP_CLOUD_RUN_VPC_CONNECTOR`
- `ODP_CLOUD_RUN_VPC_EGRESS`

If any variable is empty (unbound environment), the gate fails closed with a
zh-TW receipt naming the missing variables.

### Variable provisioning gap

**Measured fact**: the GitHub `production` environment currently has 42 variables,
but `ODP_CLOUD_RUN_VPC_CONNECTOR` and `ODP_CLOUD_RUN_VPC_EGRESS` are both absent.
The `production-build` environment has 9 variables and also lacks these two.

For comparison, `dev-build` has 12 variables including both VPC variables,
confirming the gap is production-specific and not a repository-wide omission.

**Consequence**: a `workflow_dispatch` with `environment=production` and
`phase=deploy` will trigger the binding gate at line 1120, which calls
`check_release_environment.py --scope deploy`.  The `deploy` scope requires
both `ODP_CLOUD_RUN_VPC_CONNECTOR` and `ODP_CLOUD_RUN_VPC_EGRESS`
(`delivery_toolchain/release/check_release_environment.py` lines 103–104).
Both will resolve to empty strings, so the gate will **fail closed** and abort
the run before any Google Cloud authentication or deploy mutation.

Similarly, a `phase=build` run against `production-build` will hit the build
binding gate at line 317, which requires both VPC variables in the `build`
scope (lines 82–83).  This also fails closed.

**This is by design**: the binding gate exists precisely to prevent silent
empty-string resolution from producing broken deploys.  The gap is an **ops
variable provisioning** issue, not a workflow code defect.

A blocker has been raised for Human/Ops to provision these two variables in
both `production` and `production-build`.

### Contract test

`test_the_binding_gate_exposes_exactly_the_variables_its_scope_requires`
(tests/ops/test_deploy_workflow_contract.py line 1586) ensures the step's `env:`
block contains exactly the variables `check_release_environment.py` checks for
the `deploy` scope.

### Admission and build also bind correctly

- **admission**: binds `${{ inputs.environment }}` (line 671) and runs
  `--scope admission` check — production triggers admission with production
  lease variables.
- **build**: binds `${{ inputs.environment }}-build` (line 275) — requires a
  `production-build` GitHub environment with the same GCP variables and no
  deployment approval.

---

## AC-5: No actual production deployment or resource mutation

**Status: ✅ Satisfied**

This task delivers only the **path** — the workflow structure and evidence
proving it routes correctly. No `workflow_dispatch` was triggered for
`environment=production`. The actual rollout is reserved for
ODP-PROD-BLUEGREEN-ROLLOUT-001.

---

## AC-6: Not relaxed by billing or PROD-OPS-05

No acceptance criterion has been relaxed based on billing status or
PROD-OPS-05 operational parameters. The variable provisioning gap is recorded
as a measured fact with an ops blocker, not waived.

---

## Existing Contract Tests

The following existing tests prove the production path is structurally
sound at the time this task was reviewed:

| Test | File | Line | What it proves |
|------|------|------|----------------|
| `test_environment_inputs_support_dev_staging_production` | test_deploy_workflow_contract.py | 1134 | Environment choice includes production |
| `test_each_phase_binds_to_its_own_authority_environment` | test_deploy_workflow_contract.py | 1567 | Deploy binds `${{ inputs.environment }}` |
| `test_the_binding_gate_exposes_exactly_the_variables_its_scope_requires` | test_deploy_workflow_contract.py | 1586 | Binding gate checks exactly the right variables |
| `test_the_binding_gate_runs_before_the_job_touches_google_cloud` | test_deploy_workflow_contract.py | 1619 | Gate fires before WIF auth |
| `test_every_job_that_reads_environment_variables_binds_an_environment` | test_deploy_workflow_contract.py | 1542 | No unbound job reads `vars.*` |
| `test_production_bluegreen_verification_gated_on_production_environment` | test_deploy_workflow_contract.py | 1289 | Blue-green fires for production only |
| `test_staging_lifecycle_invocations_gated_on_staging_environment` | test_deploy_workflow_contract.py | 848 | Staging lifecycle skipped for non-staging |
| `test_staging_skips_static_preflight_and_uses_foundation_binding_scope` | test_deploy_workflow_contract.py | 974 | Static deploy script gates on `!= staging` |
| `test_deploy_script_rejects_partial_or_invalid_vpc_config_before_cloud_run` | test_deploy_workflow_contract.py | 1254 | VPC config validated before mutation |
| `test_deploy_script_uses_digest_refs_for_every_cloud_run_target` | test_deploy_workflow_contract.py | 1223 | Deploy-by-digest enforced |

---

## Blocker: Production VPC Variable Provisioning

**Blocker target**: Human/Ops
**Required action**: Add the following variables to the GitHub `production`
and `production-build` environments:

| Variable | Example value (from dev-build) | Environment |
|----------|-------------------------------|-------------|
| `ODP_CLOUD_RUN_VPC_CONNECTOR` | (production VPC connector name) | production, production-build |
| `ODP_CLOUD_RUN_VPC_EGRESS` | (production VPC egress setting) | production, production-build |

Until these are provisioned, `ODP-PROD-BLUEGREEN-ROLLOUT-001` cannot execute
its first dispatch — both the build and deploy binding gates will fail closed.

---

## Prerequisites for ODP-PROD-BLUEGREEN-ROLLOUT-001

With this path established, the following must be verified before the first
production dispatch:

1. **`production-build` GitHub environment** has all 11 build-scope variables
   (currently 9; missing `ODP_CLOUD_RUN_VPC_CONNECTOR` and
   `ODP_CLOUD_RUN_VPC_EGRESS`). **⚠️ NOT YET MET.**
2. **`production` GitHub environment** has all deploy-scope variables.
   Currently has 42 but is missing the two VPC variables required by the
   deploy binding gate. **⚠️ NOT YET MET.**
3. **WIF** is configured for production's GCP project.
4. **Supervisor lease issuer** is configured for the production environment.

Items 1–2 are tracked by the blocker above.  Items 3–4 are not measurable by
this task and remain as operational prerequisites.
