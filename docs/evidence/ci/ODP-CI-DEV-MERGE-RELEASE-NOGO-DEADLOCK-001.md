# Dev Merge / Production Release Gate Separation

- Task: `ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001`
- Current owner: Codex
- Current reviewer: Codex2
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
## TOCTOU and Candidate Ancestry Remediation

- **Immutable promotion SHA**: `promote-dev-to-main.yml` binds `PROMOTION_SHA` to `${{ github.event.workflow_run.head_sha }}` and passes `EXPECTED_SHA` into `make product-release-gate`.
- **PR-head drift failure**: `promote-dev-to-main.yml` asserts that `PR headRefOid` matches `PROMOTION_SHA`. If `dev` advances after CI validation, the workflow logs `::error::Promotion SHA drift detected!...` and aborts before status stamping or auto-merging.
- **Candidate ancestry policy**: `scripts/e2e/check_release_gate_registry.py` accepts `--expected-sha` and verifies that the registry's `release.candidate_sha` is either an exact match or an evidence-only ancestor (where intervening commits touch only `docs/evidence/`, `docs/release/`, `docs/runbooks/`, etc.). Intervening non-evidence (product or test code) commits fail closed.
- **First-parent merge delta semantics**: `scripts/e2e/check_release_gate_registry.py` uses `git log --first-parent -m` for commit traversal in `check_candidate_ancestry`. This evaluates merge commits against their first-parent (candidate) tree rather than all parents, preventing false-positive re-reporting of candidate product changes when merging evidence from stale task branches, while retaining fail-closed protection against non-evidence merge resolutions and product changes.
- **Negative dev-advance & merge regressions**: `tests/e2e/test_release_gate_registry.py` tests `check_candidate_ancestry` against simulated non-evidence commit diffs, merge-resolution product changes (failing closed), and evidence-only merges from stale second-parent branches (`test_cli_expected_sha_ancestry_stale_second_parent_evidence_merge_passes`).

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

## 2026-08-02 current-base composition

Owner dispatch resumed from published task head
`1a381de9037b712f34ab513e036b62ba7f5d4331`. The task branch was 92 commits
behind and five commits ahead of `origin/dev`. To preserve the already-pushed
task history and satisfy normal-push policy, current `origin/dev` head
`475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f` was merge-composed rather than
rebasing or force-pushing it.

The only conflict was the tenant header in
`tests/integration/test_flow_002_expansion_persistence.py`. The resolution keeps
the latest-base test-specific value `tenant-flow-002`; this preserves the task's
tenant-scope coverage while avoiding the older generic `tenant-a` identity. The
resolved, base-advanced implementation head is
`50baea9095ca7ccf62fca8c1789c0e4ecac00c56`, pushed normally to PR #562.

Verification on that exact implementation head:

```text
uv run pytest -q tests/e2e/test_release_gate_registry.py tests/e2e/test_acceptance_coverage.py tests/integration/test_flow_002_expansion_persistence.py tests/security/test_branch_protection_policy.py  # exit 0
uv run ruff check scripts/e2e/check_product_release_gate.py scripts/e2e/check_release_gate_registry.py scripts/e2e/product_e2e_receipt.py tests/e2e/test_release_gate_registry.py tests/integration/test_flow_002_expansion_persistence.py  # exit 0
python3 scripts/e2e/check_release_gate_registry.py  # exit 0; NO-GO registry valid, 0/7 gates cleared
python3 scripts/e2e/check_product_release_gate.py --dev-merge  # exit 0
python3 scripts/e2e/check_product_release_gate.py --require-go  # exit 1 as required; explicit NO-GO
git diff --check origin/dev...HEAD  # exit 0
python3 YAML safe-load of .github/workflows/ci.yml and .github/workflows/promote-dev-to-main.yml  # exit 0
```

The production negative assertion remains a required pass condition: this task
separates dev-merge execution from production authority; it does not change the
registry's authentic `NO-GO`, clear any Gate 0-6 blocker, or grant release GO.
