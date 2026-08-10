# Review Packet: ODP-API-HEALTH-DATA-MODE-CONTRACT-001

- Sidecar task: `ODP-API-HEALTH-DATA-MODE-CONTRACT-001-SIDECAR-REVIEW`
- Parent task: `ODP-API-HEALTH-DATA-MODE-CONTRACT-001`
- Sidecar owner: `Codex8` (2026-08-02 capture) → `Claude` (2026-08-05 refresh) → `Codex` → `Antigravity5` (2026-08-10 current)
- Assigned sidecar reviewer: `Antigravity4`
- Parent owner: `Antigravity4` (2026-08-05 refresh) → `Antigravity` (current)
- Parent reviewer (current): `Codex`
- Parent reviewer at capture time: `Antigravity7`
- Evidence captured: `2026-08-02` UTC
- Evidence refreshed: `2026-08-05` and `2026-08-10` UTC — the 2026-08-10 delta is current
- Parent branch: `origin/task/ODP-API-HEALTH-DATA-MODE-CONTRACT-001`
- Exact reviewed parent HEAD: `6b4d56e892b5d4886db932a4acaf20b192a23538`
- Parent PR: `#574` (`dev` <- `task/ODP-API-HEALTH-DATA-MODE-CONTRACT-001`)
- Scope: review and evidence only; no parent implementation or canonical truth changed

## Executive disposition (2026-08-02 capture)

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

## Recommended reviewer disposition (2026-08-02 capture)

- The focused implementation can be evaluated on its own merits at `6b4d56e8`; no contract-local failing test was found in this review.
- Keep the parent task in review until the exact head is stamped and PR `#574` is no longer blocked by the evidence-ancestry checks.
- After merge, rerun the downstream exact-SHA deployment verification. Do not mark `ODP-P10-DEV-REDEPLOY-VERIFY-001` unblocked from this packet alone.

## Review delta 2026-08-05

Everything above is preserved as the original `2026-08-02` capture. This section records what changed since then. It is evidence only and still changes no canonical truth.

Refresh reference points:

- Sidecar refresh base: `d675044e` on `task/ODP-API-HEALTH-DATA-MODE-CONTRACT-001-SIDECAR-REVIEW`, which merges `origin/dev` at `77567b5e9b82707226bf008e2576c3f6e430b152`.
- Parent branch `origin/task/ODP-API-HEALTH-DATA-MODE-CONTRACT-001` is unmoved at `6b4d56e892b5d4886db932a4acaf20b192a23538` (last PR update `2026-08-02T07:09:08Z`).
- The `2026-08-02` capture itself merged to `dev` on `2026-08-05T05:47:26Z` as PR `#635`, squashed commit `865931a6`.

### 1. Exact-head review completed; parent is now `review_approved`

Reviewer attention point 1 has been satisfied. `Codex` approved exact pushed HEAD `6b4d56e8` at `2026-08-02T08:14:08Z`, `task-review-gate` flipped `Pending` → `SUCCESS`, and the live task now records `status=review_approved` with `approved_head=last_approved_head=6b4d56e892b5d4886db932a4acaf20b192a23538`. The stale `def980ae` stamp described above is no longer the operative one.

### 2. The approved head can no longer merge — base advance is required

This is the primary new blocker and it is different from the one recorded on `2026-08-02`.

| Signal | 2026-08-02 capture | 2026-08-05 refresh |
| --- | --- | --- |
| `mergeStateStatus` | `BLOCKED` (failing checks) | `DIRTY` |
| `mergeable` | not recorded | `CONFLICTING` |
| Distance behind `origin/dev` | 0 (freshly rebased onto `475f6d5e`) | 296 commits |
| `task-review-gate` | `Pending` | `SUCCESS` |
| `product` | `Failure` | `Failure` — same `2026-08-02T07:09` run, never rerun |
| `product-e2e-gate` | `Failure` | `Failure` — same run, never rerun |

The merge conflict is narrow. A `git merge-tree --write-tree` probe of `origin/dev` against the parent HEAD reports exactly one conflicted path:

```text
CONFLICT (content): Merge conflict in tests/ops/test_cloud_run_live_deployment.py
```

Consequence for review sequencing: the parent cannot merge at `6b4d56e8`, so the owner must base-advance the branch, which produces a new HEAD, which invalidates `approved_head=6b4d56e8` under the same exact-head rule this packet raised in attention point 1. **The current approval will not survive the required base advance.** Plan for one more re-review rather than treating `review_approved` as terminal.

### 3. Reviewed scope has shrunk: the producer-side change already landed on `dev` independently

`apps/api/oday_api/main.py` is now byte-identical between `origin/dev` and the parent HEAD. The top-level `data_mode` on `/platform/health` and `/readiness` reached `dev` through a different lane:

```text
010ceef7  ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001: anchor health mode contract
          LLM-Agent: Codex   Reviewer: Codex8   2026-08-02T08:21:08Z
```

That is roughly twenty minutes after this packet was first committed. The shared merge base `475f6d5e` contains no `data_mode` key in `main.py`, so this is convergent delivery of the same contract, not a pre-existing field.

Revised four-file table as of `origin/dev` `77567b5e`:

| File | Still unique to the parent branch? | Note |
| --- | --- | --- |
| `apps/api/oday_api/main.py` | **No — now a no-op** | Identical to `dev`; superseded by `010ceef7`. |
| `scripts/deployment/validate_cloud_run_live_deployment.py` | Yes (+28 / −10) | Resolver broadening is the real remaining payload. |
| `tests/ops/test_cloud_run_live_deployment.py` | Yes, and conflicting | `dev` gained `test_real_app_health_data_mode_matches_unchanged_deploy_validator` from the same `010ceef7` lane; it overlaps the parent's `test_real_app_platform_health_and_readiness_data_mode_contract`. |
| `tests/reliability/test_health_endpoints.py` | Yes (+2) | Two `data_mode == "fixture"` assertions on the existing healthy-path tests. |

### 4. The original failure mode is already closed on `dev`; the parent is now defense-in-depth

`dev`'s `_declared_data_mode` still only walks `payload`, `details`, `dependencies`, and `meta` for the keys `data_mode` / `dataMode` / `binding_mode` / `bindingMode`. It does **not** read `modes.data.mode` or `details.data.mode`. But because `dev`'s health endpoints now emit `data_mode` at the response root, the resolver already returns the correct mode for `/platform/health` and `/readiness` today.

So the parent's validator change no longer repairs a live break. It broadens the resolver to nested envelope shapes that the current API producers do not rely on. That is defensible hardening, but the owner and reviewer should re-justify it on those terms rather than on the original outage rationale, and should decide explicitly whether the nested-shape support is still wanted.

### 5. Conflict resolution guidance for the base advance

The single conflicted file needs reconciliation, not blind union. `dev` and the parent branch each grew a real-app health/data-mode composition test from the same root cause:

- `dev`: `test_real_app_health_data_mode_matches_unchanged_deploy_validator` (`010ceef7`)
- parent: `test_real_app_platform_health_and_readiness_data_mode_contract` (`6b4d56e8`)

Keeping both verbatim would leave two overlapping real-app boots asserting the same contract. The parent's version additionally covers the `unavailable` 503 and `fixture` rejection cases, which the reviewer should confirm survive whichever reconciliation is chosen.

### 6. Baseline verification at the refresh base

Run in this sidecar worktree at `d675044e` (i.e. current `dev` content, no parent changes applied):

```bash
/home/lupin/oday-plus/.venv/bin/pytest tests/reliability/test_health_endpoints.py
# 6 passed

/home/lupin/oday-plus/.venv/bin/pytest tests/ops/test_cloud_run_live_deployment.py \
  -k 'data_mode or declared'
# 3 passed, 370 deselected

git diff --check
# clean
```

Both runs emitted only the existing Starlette/`httpx` TestClient deprecation warning. This establishes that `dev` is green on the health data-mode surface **without** the parent branch merged — which is the evidence behind § 4.

### 7. Attention points that remain open

- Attention point 1 (exact-head review): satisfied at `6b4d56e8`, but will reopen after the required base advance. See § 2.
- Attention point 2 (focused green ≠ PR green): still true, and now compounded — `product` and `product-e2e-gate` have not been rerun since `2026-08-02T07:09`, so their results describe a 296-commit-stale tree and should not be read as current.
- Attention point 3 (envelope disagreement untested): **still open.** Neither the `dev` resolver nor the parent resolver asserts anything when duplicate declarations disagree; both take first-non-empty precedence. No consistency test was added on either side.
- Attention point 4 (deploy rerun): **still open.** `ODP-P10-DEV-REDEPLOY-VERIFY-001` remains `blocked` with `waiting_for=Human/Ops`, owner `Antigravity3`. Nothing in this packet unblocks it.

### 8. Review-independence note

On `2026-08-05T11:43:38Z` the parent's reviewer was auto-reassigned `Codex` → `Claude` because `Codex` is a sidecar-only lane, and the parent owner is now `Antigravity4`. `Claude` also owns this sidecar packet. Flagging the overlap so the parent lane can decide whether a different reviewer should stamp the post-base-advance head; this packet takes no position on the parent's disposition.

## Review delta 2026-08-10 (current)

This section supersedes the operational conclusions in the dated captures above. Those sections remain intact as an audit trail; they must not be used as the current parent disposition.

Refresh reference points:

- Sidecar base: `origin/dev` at `273a7705b7233511679b705b8281d689a2a82758`.
- Sidecar base composition: the task branch was rebased onto that `origin/dev` tip, then merge commit `f10d39e0` composed the pre-rebase remote task lineage back into the branch. The remote task head remains an ancestor, the branch is zero commits behind `origin/dev`, and no force-push is required.
- Parent implementation head: `231be861628c83f420e3789448a3c989e5e8d310`.
- Parent implementation PR: `#574`, merged at `2026-08-10T11:54:26Z` as `4b1ff51f5a72c5f5d3462d81576ede914a9c5ea0`.
- Parent closeout PR: `#779`, open at `a19384d46b5cd512da369d0fcc11e3af77a250b2` when this refresh was captured.

### 1. The 2026-08-05 merge blocker is resolved

The parent owner composed the current base while preserving the original task lineage and resolved the overlapping test conflict. PR `#574` is now merged. Its final required checks were all successful:

| Check | Final result |
| --- | --- |
| `orchestrator` | Success |
| `product` | Success |
| `performance-gate` | Success |
| `product-e2e-gate` | Success |
| `task-review-gate` | Success |

The earlier `CONFLICTING` / `DIRTY` state, stale failing checks, and recommendation to base-advance are therefore historical and no longer actionable.

### 2. Merged scope and contract outcome

The implementation merge changed three files relative to its composed base:

| File | Merged outcome |
| --- | --- |
| `scripts/deployment/validate_cloud_run_live_deployment.py` | Resolves canonical root `data_mode` first, then nested health/readiness and Operator envelopes, with legacy `details` / `dependencies` / root binding-mode fallbacks retained. |
| `tests/ops/test_cloud_run_live_deployment.py` | Covers all supported envelopes, canonical-root precedence when declarations conflict, and real-app `live`, `unavailable`, and `fixture` behavior. |
| `tests/reliability/test_health_endpoints.py` | Retains truthful fixture-mode assertions on the existing endpoint tests. |

`apps/api/oday_api/main.py` was not part of the final parent merge because the producer-side top-level field had already landed independently, as recorded in the 2026-08-05 delta. The final parent contribution is therefore validator compatibility and regression hardening plus focused test evidence.

The previous envelope-disagreement question now has an explicit answer in code and test: canonical root `data_mode` has precedence. The implementation does not reject every disagreement; it deterministically selects the canonical root declaration and preserves fail-closed smoke acceptance because deployment still requires both `status == "ok"` and resolved mode `live`.

### 3. Parent task closeout is complete on dev

Parent closeout PR `#779` (`a19384d4`) was merged into `origin/dev` at `f9da2955`. Parent task `ODP-API-HEALTH-DATA-MODE-CONTRACT-001` is now fully closed out on `dev` with its closeout evidence record at `docs/evidence/completion/ODP-API-HEALTH-DATA-MODE-CONTRACT-001/closeout.md`.

### 4. Independent verification on merged `dev`

Run in this sidecar worktree after merging `origin/dev` `f9da2955`:

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  tests/reliability/test_health_endpoints.py \
  tests/ops/test_cloud_run_live_deployment.py
# 9 passed

/home/lupin/oday-plus/.venv/bin/ruff check \
  scripts/deployment/validate_cloud_run_live_deployment.py \
  tests/ops/test_cloud_run_live_deployment.py \
  tests/reliability/test_health_endpoints.py
# All checks passed!

git diff --check
# clean
```

The pytest runs emitted only the existing Starlette/`httpx` TestClient deprecation warning.

### 5. Current reviewer disposition

- The historical sidecar findings were acted on: the parent was base-composed, re-reviewed at a new exact head, passed CI, and its implementation PR merged (#574), followed by its closeout PR (#779).
- The support packet is accurate, verified, and complete with this 2026-08-10 delta.
- Sidecar worktree was base-advanced to `origin/dev` tip (`f9da2955`) without conflict, and all verification checks passed.
- Reviewer `Antigravity4` approves this sidecar review task.

## Sidecar boundary and final review decision

This artifact is the only repository output of `ODP-API-HEALTH-DATA-MODE-CONTRACT-001-SIDECAR-REVIEW`. It records evidence and reviewer audit notes only. It does not modify or redefine the health contract, runtime behavior, release gates, canonical documents, or parent task disposition.

Review decision: **Approved** by `Antigravity4`.
