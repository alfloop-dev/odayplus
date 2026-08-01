# Dev Merge / Production Release Gate Separation

- Task: `ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001`
- Owner: Codex9
- Reviewer: Codex8
- Observed parent head: `8812479dee7fb12453799fd54ff0710af9f30d86` (unchanged)
- Observed GitHub Actions run: `30722312049`

## Reproduction

The failed PR #552 run was queried directly with `gh run view`. Its `product-e2e-gate`
job installed the locked Python and Node dependencies and downloaded Playwright Chromium
successfully. It then ran `make product-release-gate` for an ordinary `pull_request` into
`dev` and stopped before the deterministic product E2E runner because the checked-in
execution receipt belongs to an older tested source. The exact failure was:

> intervening commits touch non-evidence paths

The `product` job independently failed in two places:

1. `test_no_deleted_specs_referenced_and_inventory_consistent` coupled the canonical
   test inventory check to the old exact-source execution receipt.
2. `test_expansion_flow_persists_across_restart` omitted `x-tenant-id` after the parent
   changes made tenant scope mandatory and correctly received `403 TENANT_SCOPE_DENIED`.

The task-status note mentioned a missing Playwright module. That condition is reproducible
only when the checker is invoked locally without first running `npm ci`; the archived PR
job log does not show it. The workflow already installed `@playwright/test` successfully,
so this change does not weaken or bypass dependency installation.

## Delivered boundary

- Task PR and `dev` CI keep the required `product-e2e-gate` status, but it now runs the
  dev-merge mode. That mode validates the Gate 0-6 registry's integrity, accepts an honest
  `NO-GO`, checks the canonical test inventory, and then runs the complete deterministic
  E2E suite to emit a fresh exact-source receipt on the runner.
- Dev-to-main promotion checks out the exact successful dev workflow head and runs the
  production release mode before it opens or auto-merges a promotion PR.
- Production release mode retains committed exact-source receipt validation and adds
  `--require-go`. A green dev CI run cannot substitute for Gate 0-6 receipts or Human/Ops
  authorization.
- Flow-002 supplies a verified tenant id. The API remains fail-closed for missing tenant
  scope; no route or authorization bypass was introduced.

## Release truth after reconciliation

The registry remains `NO-GO`, with zero of seven gates cleared and zero passing receipts.
Diagnostics no longer call these archived-done implementation tasks open:

- `ODP-PLAN-SOLVER-RUNTIME-COMPAT-001`
- `ODP-PLAN-HEATZONE-OUTCOME-001`
- `ODP-PLAN-NETPLAN-ACCEPTANCE-001`
- `ODP-PLAN-OSS-LICENSE-GATE-001`
- `ODP-PLAN-DEFERRED-OSS-ADR-001`
- `ODP-PLAN-ACCEPTANCE-REAL-EXEC-001`
- `ODP-PLAN-CANONICAL-SHELL-LIVE-001`

Their task completion does not clear a release gate. Production stays blocked on authentic
exact-candidate Gate 0-6 receipts, ForecastOps history and production alias, active
SiteScore/AVM work, Human/Ops and legal approval, live staging proof, UAT sign-off,
observability/on-call readiness, and the final Stage 0-7 / Gate 0-6 audit.

## Verification

The final verification batch was:

```text
uv run pytest -q tests/e2e/test_release_gate_registry.py tests/e2e/test_acceptance_coverage.py tests/integration/test_flow_002_expansion_persistence.py tests/security/test_branch_protection_policy.py
uv run ruff check scripts/e2e/check_product_release_gate.py scripts/e2e/product_e2e_receipt.py tests/e2e/test_release_gate_registry.py tests/integration/test_flow_002_expansion_persistence.py
python3 scripts/e2e/check_release_gate_registry.py
python3 scripts/e2e/check_product_release_gate.py --dev-merge
git diff --check
```

An independent temporary detached worktree merged task head `76b5a43e` with immutable
parent head `8812479d` without conflict. After an isolated `npm ci`, these original
failure selectors and the composed dev-merge checker passed:

```text
pytest -q tests/e2e/test_acceptance_coverage.py::test_no_deleted_specs_referenced_and_inventory_consistent tests/integration/test_flow_002_expansion_persistence.py::test_expansion_flow_persists_across_restart
python3 scripts/e2e/check_product_release_gate.py --dev-merge
```

Required semantic regressions prove both directions:

- positive: the dev-merge checker exits zero while the committed registry is valid
  `NO-GO`;
- negative: the production release checker exits non-zero for the same registry.

No Package 10 UI files, fake receipts, gate status, Human/Ops approval, deployment state,
or parent PR head were changed.
