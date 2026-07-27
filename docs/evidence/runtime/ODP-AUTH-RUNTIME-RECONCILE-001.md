# ODP-AUTH-RUNTIME-RECONCILE-001 acceptance evidence

- Integration base: `c7c6e925ebdc5a5026b25ca2c3319ca9139ec7e7`
- Auth source audited: `b0116794cb0d93f219427e094cbad2df2e6d44e8`
- WIF source audited: `d139aa1210df129ce8b193d6f51905e6c57e2b45`
- Live-gate source retained: `11439d92de4eb43e223f44e2a3b6081c4f62d4f9`
- Composition anchor: `4958da6a`
- Refreshed `origin/dev`: `1c7dd935411ea8e9a85f4635337c846e64cd9523`
- Post-refresh validation tree: `72009c6a2803a0952079be384d725f0647017f60`

## Delivered contract

- Deployment workflows require Workload Identity Federation and contain no
  `GCP_SA_KEY` fallback.
- The WIF deployer impersonates the dedicated smoke service account to mint an
  ID token at runtime. The token is masked, exported only to the deploy process,
  unset during cleanup, and never read from a repository secret.
- The API receives its principal map through a Secret Manager binding.
  Subject mappings, or verified-email mappings, are authoritative for roles and
  tenant/scope. Unknown, invalid, or incomplete mappings never fall back to
  token-supplied platform authorization.
- The live non-mock E2E gate remains after traffic promotion and before
  `DEPLOYMENT_COMMITTED=true`.
- The diff from the integration base contains no Package 10 visual, canonical
  operator E2E, intake, or archived-design path.

## Verification

- `uv run pytest -q tests/security/test_opsboard_auth_boundary.py tests/ops/test_cloud_run_live_deployment.py tests/e2e/test_live_e2e_gate.py`
  - exit 0; 144 passed; one Starlette deprecation warning.
- `python3 infra/terraform/validate_contract.py`
  - exit 0; 14 Terraform files checked.
- `bash -n scripts/deploy_cloud_run_waji.sh`
  - exit 0.
- `git diff --check`
  - exit 0.
- Negative search for `GCP_SA_KEY` and static
  `secrets.ODP_OPERATOR_SMOKE_BEARER_TOKEN` in both deployment workflows and
  the deploy script
  - exit 0; no matches.
- Forbidden-path diff from `c7c6e925`
  - empty.

The focused Web OIDC Vitest could not start in this worktree: the pnpm attempt
treated internal workspace packages as registry dependencies, and the npm
fallback lacked locally installed `vitest/config` and `@vitejs/plugin-react`.
The changed behavior is the narrow, previously audited ID-token selection from
`b0116794`; independent exact-head CI must run the repository's normal
JavaScript dependency bootstrap and Web auth suite.

## Latest-dev refresh

PR #442 was refreshed after `dev` advanced. Merging
`1c7dd935411ea8e9a85f4635337c846e64cd9523` produced validation tree
`72009c6a2803a0952079be384d725f0647017f60` without conflicts or changes to
the task-owned auth and deployment contract.

- `uv run pytest -q tests/security/test_opsboard_auth_boundary.py tests/ops/test_cloud_run_live_deployment.py tests/e2e/test_live_e2e_gate.py`
  - exit 0; 144 passed; one Starlette deprecation warning.
- `python3 infra/terraform/validate_contract.py`
  - exit 0; 14 Terraform files checked.
- `bash -n scripts/deploy_cloud_run_waji.sh`
  - exit 0.
- `git diff --check`
  - exit 0.
- Negative search for `GCP_SA_KEY` and static
  `secrets.ODP_OPERATOR_SMOKE_BEARER_TOKEN`
  - exit 0; no matches.

The evidence-only commit following this validated tree does not change runtime
code. GitHub CI and both independent reviewers must nevertheless bind their
results to the newly pushed PR head; no result for stale head `a50e69b0` is
reusable.

## Web OIDC test reconciliation with the ID-token bearer contract

CI run 30306838576 on head `8c8a6b0f` failed only in the product job's Node
workspace checks: `src/lib/auth/__tests__/oidc.test.ts` still asserted that
`session.accessToken` equals the opaque OAuth `access_token`, while the
audited implementation (`b0116794`, carried in `4958da6a`) deliberately binds
the API bearer to the verified `id_token` so the backend JWT boundary can
validate signatures. The corrected assertion (bearer must equal the verified
`id_token` and must not be the opaque `access_token`) was restored from the
supervisor worktree backup
`odp-auth-runtime-reconcile-001-claude-20260727T220107Z-369f45f2.patch` and is
now committed; 232 of 233 web tests already passed on the prior head, so this
is the only Node-side delta.

Revalidation on the resulting tree:

- `uv run pytest tests/security/test_opsboard_auth_boundary.py tests/ops/test_cloud_run_live_deployment.py tests/e2e/test_live_e2e_gate.py`
  - exit 0; 144 passed; one Starlette deprecation warning.
- Web Vitest still cannot bootstrap in this worktree (workspace-package
  registry resolution); the assertion change is validated by the repository CI
  product job on the pushed head.
