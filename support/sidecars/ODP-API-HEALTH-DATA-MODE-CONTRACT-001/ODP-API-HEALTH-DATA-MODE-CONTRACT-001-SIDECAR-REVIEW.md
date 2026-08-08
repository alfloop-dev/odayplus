# Review Packet: ODP-API-HEALTH-DATA-MODE-CONTRACT-001

- Sidecar task: `ODP-API-HEALTH-DATA-MODE-CONTRACT-001-SIDECAR-REVIEW`
- Parent task: `ODP-API-HEALTH-DATA-MODE-CONTRACT-001`
- Sidecar owner: `Codex8`
- Assigned sidecar reviewer / parent owner: `Antigravity4`
- Parent reviewer: `Antigravity7`
- Evidence captured: `2026-08-02` UTC
- Parent branch: `origin/task/ODP-API-HEALTH-DATA-MODE-CONTRACT-001`
- Exact reviewed parent HEAD: `6b4d56e892b5d4886db932a4acaf20b192a23538`
- Parent PR: `#574` (`dev` <- `task/ODP-API-HEALTH-DATA-MODE-CONTRACT-001`)
- Scope: review and evidence only; no parent implementation or canonical truth changed

## Executive disposition

The focused health data-mode contract is supported by direct endpoint, validator, and real-app composition evidence at parent HEAD `6b4d56e8`. The focused tests and lint checks pass, and the diff retains the deployment smoke requirement that both health endpoints report `status == "ok"` and a resolved data mode of exactly `live`.

The parent PR is **not merge-ready as captured**. PR `#574` reports `mergeStateStatus=BLOCKED`: `product` and `product-e2e-gate` fail on release-evidence ancestry (`tested source is not an ancestor of evidence HEAD`), while `task-review-gate` remains pending. This packet therefore supports review of the contract slice but does not claim full CI acceptance, deployment success, review approval, or downstream unblock.

## Reviewed change surface

Compared with `origin/dev` at `475f6d5e`, the parent branch changes four files only:

| File | Contract role | Review observation |
| --- | --- | --- |
| `apps/api/oday_api/main.py` | Health/readiness producer | Adds top-level `data_mode` to `/platform/health` and `/readiness`; value is derived from the same `modes["data"]["mode"]` used by nested payloads. Unhealthy live-required states remain HTTP 503. |
| `scripts/deployment/validate_cloud_run_live_deployment.py` | Candidate smoke consumer | Resolves root `data_mode`/`dataMode`, nested `modes.data.mode`, nested `details.data.mode`, Operator `meta`, and legacy binding keys. Smoke acceptance still requires `status == "ok"` and resolved mode `live`. |
| `tests/ops/test_cloud_run_live_deployment.py` | Validator and composition evidence | Covers supported envelope shapes and boots real API apps for live, unavailable, and fixture cases. |
| `tests/reliability/test_health_endpoints.py` | Endpoint regression evidence | Asserts default fixture-mode responses expose truthful top-level `data_mode`; does not hard-code a non-live runtime as live. |

No L1 canonical document, registry, governance policy, or unrelated runtime surface is part of this sidecar change.

## Contract evidence matrix

| Scenario | Producer result | Validator / smoke expectation | Evidence |
| --- | --- | --- | --- |
| Live-configured runtime with production-capable persistence and healthy provider probe | `/platform/health` and `/readiness`: HTTP 200, `status=ok`, top-level and nested mode `live` | `_declared_data_mode(...) == "live"`; live-data smoke condition accepts | `test_real_app_platform_health_and_readiness_data_mode_contract` |
| Live data required but in-memory persistence supplied | Both endpoints: HTTP 503, `status=unhealthy`, top-level and nested mode `unavailable` | Resolved mode is `unavailable`; smoke condition rejects | Same real-app composition test |
| Development fixture/in-memory runtime | Both endpoints: HTTP 200, `status=ok`, top-level mode `fixture` | Resolved mode is `fixture`; live deployment smoke condition rejects | Same real-app composition test plus `test_health_endpoints.py` |
| Missing declaration | No supported mode key | Resolver returns empty string; live smoke condition rejects | `test_declared_data_mode_handles_all_envelope_shapes` |
| Health endpoint success path | Root `data_mode` mirrors nested runtime mode | Existing database, provider, and queue checks still run independently | Source inspection and focused suites |

## Independent verification at exact parent HEAD

The following checks were run in a temporary detached worktree at `6b4d56e892b5d4886db932a4acaf20b192a23538`:

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  tests/ops/test_cloud_run_live_deployment.py \
  -k 'declared_data_mode or real_app_platform_health_and_readiness_data_mode_contract'
# 2 passed

/home/lupin/oday-plus/.venv/bin/pytest -q \
  tests/reliability/test_health_endpoints.py
# 6 passed

/home/lupin/oday-plus/.venv/bin/ruff check \
  apps/api/oday_api/main.py \
  scripts/deployment/validate_cloud_run_live_deployment.py \
  tests/ops/test_cloud_run_live_deployment.py \
  tests/reliability/test_health_endpoints.py
# All checks passed!

git diff --check
# clean
```

Both pytest runs emitted only the existing FastAPI/TestClient deprecation warning.

## PR and status evidence

Captured from PR `#574` at remote head `6b4d56e8`:

| Check | Result | Relevant detail |
| --- | --- | --- |
| `orchestrator` | Success | Completed successfully. |
| `performance-gate` | Success | Completed successfully. |
| `product` | Failure | `1 failed, 2568 passed, 70 deselected`; sole failure is `tests/e2e/test_acceptance_coverage.py::test_no_deleted_specs_referenced_and_inventory_consistent`, reporting `tested source is not an ancestor of evidence HEAD`. |
| `product-e2e-gate` | Failure | Release registry remains truthful `NO-GO`; command fails on the same tested-source/evidence-HEAD ancestry condition. |
| `task-review-gate` | Pending | Parent reviewer stamp is not complete. |

The live task state records `last_approved_head=def980ae9c38c0e6e76722f30812ae072c1f436c`, while the pushed PR head is `6b4d56e8`. `def980ae` is not an ancestor of `6b4d56e8`, so the old stamp cannot approve the current head. A scoped tree comparison shows the four parent-owned files are identical between those two commits, but review must still stamp the exact pushed HEAD.

## Reviewer attention points

1. **Exact-head review is required.** Review and any approval should name `6b4d56e892b5d4886db932a4acaf20b192a23538`, not the pre-rebase `def980ae` hash.
2. **Do not treat focused green evidence as full PR green.** The two red jobs are release-evidence lineage failures outside the four-file contract diff, but branch protection remains correctly blocked until that lineage is repaired or refreshed by its owning lane.
3. **Envelope disagreement is not explicitly tested.** `_declared_data_mode` uses first non-empty candidate precedence, with root `data_mode` ahead of nested declarations. Current API producers derive both from the same runtime value, and the real-app tests prove agreement for live/unavailable cases. If the intended contract requires rejection whenever duplicate declarations disagree, the parent needs a separate consistency assertion and negative test; this packet does not silently assume that policy.
4. **A deploy rerun is still required.** These source-level checks address the prior missing-mode failure, but only an exact-SHA Cloud Run candidate smoke can prove the deployed payload and validator compose in the target environment.

## Recommended reviewer disposition

- The focused implementation can be evaluated on its own merits at `6b4d56e8`; no contract-local failing test was found in this review.
- Keep the parent task in review until the exact head is stamped and PR `#574` is no longer blocked by the evidence-ancestry checks.
- After merge, rerun the downstream exact-SHA deployment verification. Do not mark `ODP-P10-DEV-REDEPLOY-VERIFY-001` unblocked from this packet alone.

## Sidecar boundary and handoff

This artifact is the only repository output of `ODP-API-HEALTH-DATA-MODE-CONTRACT-001-SIDECAR-REVIEW`. It records evidence and reviewer questions only. It does not modify or redefine the health contract, runtime behavior, release gates, canonical documents, or parent task disposition.

Handoff target: `Antigravity4`. Parent owner should decide whether to incorporate these findings into the parent review exchange and coordinate the exact-head review with `Antigravity7`.
