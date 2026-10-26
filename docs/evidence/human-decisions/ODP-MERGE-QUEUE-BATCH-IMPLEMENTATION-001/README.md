# ODP-MERGE-QUEUE-BATCH-IMPLEMENTATION-001 — Evidence Package

- **Task ID**: `ODP-MERGE-QUEUE-BATCH-IMPLEMENTATION-001`
- **Phase**: WP-35B (Merge queue batch engineering defaults, runbook & contract verification)
- **Owner**: Claude (reassigned from Antigravity7 after two reviewer reopens; the policy and runbook work below originated in that earlier run)
- **Reviewer**: Codex
- **Date**: 2026-09-09
- **Decision reference**: D21 in [ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md](https://github.com/alfloop-dev/odayplus/blob/be04fe7954d3414f024901e034caacd95ae81538/docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md)
- **Prerequisite A-Stage Artifacts**: [ODP-MERGE-QUEUE-BATCH-DESIGN-001](https://github.com/alfloop-dev/odayplus/tree/dev/docs/evidence/human-decisions/ODP-MERGE-QUEUE-BATCH-DESIGN-001)

---

## 1. Executive Summary

This task delivers the **WP-35B** engineering implementation for the merge queue batch requirement (confirmed in D21):

1. **Repository Policy Configuration**: `.github/branch-protection/policy.json` adopts Option B (`min_entries_to_merge = 2`, `min_entries_to_merge_wait_minutes = 10`) as an adjustable engineering default for `dev` (adopted by Codex per current user instructions to complete the deliverable, implementing the batch requirement confirmed under D21 without itemized H08 sign-off or live activation approval).
2. **Toolchain Alignment**: `delivery_toolchain/github/apply_branch_protection.py` fallback defaults match the new batch parameters.
3. **Runbook Documentation**: `docs/runbooks/dev-merge-queue.md` documents parameter definitions, bounded accumulation wait semantics, precise provenance, WP-35B/C stage boundaries, and the exact conditions under which each required check runs.
4. **Automated Verification**: `tests/contract/test_merge_queue_batch_policy.py` implements the 6 verification scenarios from handoff §3.2. Scenarios 3 and 4 execute the real shell body of `merge-queue-review-gate.yml` against an offline `gh` stub, so fail-closed behaviour is observed rather than pattern-matched.

---

## 2. Policy Configuration & Protection Invariants

The configuration in `.github/branch-protection/policy.json` specifies:

| Setting | Value | Rationale / Contract |
|---|---|---|
| `merge_method` | `MERGE` | Preserves standard merge commit history and `LLM-Agent` / `Task-ID` / `Reviewer` trailers. |
| `grouping_strategy` | `ALLGREEN` | Strictly retained. Every entry's own merge commit must be green, isolating failed PRs. |
| `min_entries_to_merge` | `2` | Option B default: accumulate 2 PRs into a batch when available, reducing `dev` merge commit churn. |
| `min_entries_to_merge_wait_minutes` | `10` | Bounded accumulation ceiling for a solo PR. Official GitHub documentation notes merge limits affect merges after build checks pass; offline tests verify the configured ceiling only. GitHub's timer start point and any resulting hold duration are **not** verified here and are WP-35C items. |
| `max_entries_to_merge` | `5` | Maximum PRs permitted in a single merged batch group. |
| `max_entries_to_build` | `5` | Maximum speculative merge group builds executing concurrently. |
| `check_response_timeout_minutes` | `60` | Maximum check response timeout before candidate ejection. |
| `required_status_checks` | `["orchestrator", "product", "product-e2e-gate", "task-review-gate"]` | All 4 required checks preserved; `enforce_admins` is `true`. |
| `branches.dev.strict` | `false` | Off on `dev` so the queue owns base composition; kept `true` on `main`. |

Only `min_entries_to_merge` (1→2) and `min_entries_to_merge_wait_minutes` (5→10) changed. Every other value above is unchanged from the pre-task baseline.

---

## 3. Automated Verification Matrix (Handoff §3.2)

The 6 scenarios are covered by `tests/contract/test_merge_queue_batch_policy.py` (23 tests). This table states what each scenario's tests **prove** and what they explicitly **do not**, so the claim matches the coverage.

| # | Scenario | What the tests actually assert | What is *not* proven here |
|---|---|---|---|
| 1 | Multiple qualified PRs form batch | Checked-in queue parameters (`min=2`, `max_merge=5`, `max_build=5`) and the ruleset payload the apply script would send | Live batch formation on GitHub (WP-35C #1) |
| 2 | Solo-PR bounded wait timeout | The configured ceiling is present, positive, and well inside the 60-minute check timeout, in both policy and generated payload; runbook documents it | GitHub's accumulation-timer start point and any real solo-PR hold duration (WP-35C #2) |
| 3 | Unapproved / failing PR exclusion | All 4 required contexts and `enforce_admins` survive into the branch-protection payload; **and** the real review-gate shell, executed against a stubbed `gh`, exits non-zero and posts *no* success status for a head whose gate is missing, failing, pending, errored, or green only on other contexts, and for an unparsable queue ref (which is refused before any PR lookup). A positive control confirms an approved head *is* stamped, on the merge group SHA | Live pre-admission denial and live candidate ejection (WP-35C #3a/#3b) |
| 4 | Head change re-validation | The gate resolves the PR's *current* `headRefOid` and queries that SHA's status: an approval left on a superseded head is refused, and the superseded SHA is never consulted. A control shows re-approving the new head clears it. `ci.yml`'s `pull_request` trigger still covers `synchronize`, so a new head re-runs CI | Live invalidation of an approval by GitHub itself (WP-35C #4) |
| 5 | Failure isolation under ALLGREEN | `grouping_strategy == "ALLGREEN"` in both policy and generated ruleset payload | Live ejection and rebuild behaviour (WP-35C #5) |
| 6 | Actual required checks on group SHA | `merge_group: [checks_requested]` on both workflows; required jobs exist under their required check names (no `name:` override that would rename the check); no required job or step is gated on `github.event*`; the review gate checks out and stamps `merge_group.head_sha` with `context=task-review-gate` and `statuses: write`; `change-scope` resolves the merge group diff | That the four checks actually reported on a real group SHA (WP-35C #6) |

### 3.1 Counter-example (mutation) evidence

Text assertions cannot see control flow, so the strengthened tests were measured against the specific counter-examples raised in review. Each mutation was applied to the working tree, the suite was run, and the file was restored with `git checkout --`. Tested at head `4094443376af8cfed92ff69e926e6bcb85d32577`.

| Mutation | Failing tests | Suite exit |
|---|---|---|
| Delete `exit 1` from `fail()` in `merge-queue-review-gate.yml` | 7 | 1 |
| Narrow `ci.yml` `pull_request` to `types: [opened]` | 1 | 1 |
| Gate `orchestrator` on `github.event_name == 'pull_request'` | 3 | 1 |
| Change review gate `HEAD_SHA` to `merge_group.base_sha` | 1 | 1 |
| Resolve the PR head from the queue ref instead of `gh pr view` | 3 | 1 |

Unmutated, the same selection is 23 passed / exit 0. These are offline mutations of repository files; no GitHub Actions run, merge group, or live status was created for any of them.

### 3.2 Scope of the `product` job skip (pre-existing)

`product` and `product-e2e-gate` carry `if: ${{ needs.change-scope.outputs.scope != 'development_tooling' }}` (`ci.yml:145`, `ci.yml:337`). This predates this task and is **not** a defect introduced here; it is recorded because `product` is the only job that runs `tests/contract`.

Measured with `delivery_toolchain/governance/classify_change_review_scope.py` against `config/change-review-scopes.json`:

- This PR's changed paths classify as `product_or_mixed`, so `product` runs and these contract tests execute in CI for this change.
- The task's own deliverables (`policy.json`, the runbook, this test file) are each non-tooling paths, so a future change to the queue policy cannot skip its own tests. Asserted by `test_scenario_6_queue_policy_changes_keep_the_product_job_on`.
- **Limitation, recorded rather than fixed**: `.github/workflows/` is a tooling prefix in the shared manifest, so a change touching *only* `ci.yml` or `merge-queue-review-gate.yml` classifies as `development_tooling`, skips `product`, and therefore would not run these contract tests. The always-on `orchestrator` job still runs but does not execute `tests/contract`. Narrowing that prefix would change CI scheduling for every lane and is outside this task's authorized scope, so it is asserted as a measured boundary (`test_scenario_6_workflow_only_changes_are_tooling_scoped`) instead of being altered here.

---

## 4. Verification Receipts

Measured at tested head `4094443376af8cfed92ff69e926e6bcb85d32577` (`git status --porcelain` empty at run time). Each command was run so its own exit code survived: no pipe, no `|| true`, no trailing `; echo`, no backgrounding.

| Command | Exit | Duration | Selection |
|---|---|---|---|
| `git diff --check` | `0` | 0.017 s | whole worktree, no output |
| `uv run --frozen pytest tests/contract/test_merge_queue_batch_policy.py -q` | `0` | 8.793 s | 23 tests collected, 23 passed |
| `uv run --frozen pytest tests/security/test_branch_protection_policy.py -q` | `0` | 7.044 s | 15 tests collected, 15 passed |
| `uv run --frozen ruff check tests/contract/test_merge_queue_batch_policy.py delivery_toolchain/github/apply_branch_protection.py` | `0` | 0.141 s | 2 files, "All checks passed!" |
| `uv run --frozen python delivery_toolchain/governance/check_code_boundaries.py` | `0` | 10.069 s | full boundary inventory |

Selection counts were read with `pytest --collect-only -q`, not by re-running a suite for statistics.

**Tested head vs submitted head.** `4094443376af` is the last commit that changes code under test. The submitted head adds only this evidence README and the runbook prose; it does not touch `policy.json`, either workflow, or the test file, so the receipts above remain the measurement of record for the code being reviewed. The canonical receipts for the exact submitted head — with head SHA, argv, real exit code, duration, and selection — are produced by `delivery_toolchain/git/task_verification.py` for the task's two declared verification commands (`git diff --check`, `uv run pytest tests/contract/test_merge_queue_batch_policy.py -q`) and stored under `.orchestrator/evidence/`; that store is the source of truth consulted by `task_finalize.sh`.

`uv run --frozen` is used rather than a bare `python3 -m pytest`, which has no pytest on this host and would fail on replay.

---

## 5. Scope Boundaries & Live Activation Separation (WP-35C)

1. **WP-35B boundary**: this deliverable updates repository policy definitions, the toolchain fallback, the runbook, and automated contract tests.
2. **WP-35C separation**: live application via `python3 delivery_toolchain/github/apply_branch_protection.py`, readback, and the behavioural acceptance matrix in handoff §4.2 are reserved for WP-35C under explicit operator authorization. No live ruleset PATCH/PUT, apply, or readback was executed by this task.
3. **No automatic apply linkage**: merging `policy.json` does not trigger `apply_branch_protection.py`. Checked by searching `.github/` for any reference to the script (none) and every `*.py`/`*.yml`/`*.yaml`/`*.sh` reference repo-wide: the only non-test hits are prose comments in `.orchestrator/common.py` and `.orchestrator/github_bus.py` that name the script while describing the shared `gh`-resolution rule, not invocations of it. The parameter change therefore lands as reviewable configuration only, and live activation stays a deliberate WP-35C step.
4. **Simulation vs live claims**: the offline tests verify repository contracts, payload builders, and the review gate's own control flow. They are not evidence of live GitHub batching, timer behaviour, or ejection. The `gh` stub serves only the three calls the workflow makes and applies the workflow's real `--jq` filters; it does not model GitHub semantics beyond those responses.
5. **H08**: `2` / `10` remain an adjustable engineering default. Nothing in this package records itemized H08 human sign-off or operator activation approval.
