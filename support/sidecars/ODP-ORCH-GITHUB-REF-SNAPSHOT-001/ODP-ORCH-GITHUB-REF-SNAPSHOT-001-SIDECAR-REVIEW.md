# Review Packet: ODP-ORCH-GITHUB-REF-SNAPSHOT-001

- Sidecar task: `ODP-ORCH-GITHUB-REF-SNAPSHOT-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-GITHUB-REF-SNAPSHOT-001`
- Sidecar owner: `Claude2`
- Sidecar reviewer: `Claude`
- Parent owner: `Claude` / parent reviewer: `Antigravity`
- Evidence captured: `2026-08-09` UTC
- Parent branch: `origin/task/ODP-ORCH-GITHUB-REF-SNAPSHOT-001` (PR #744)
- Exact reviewed parent HEAD: `209636e9903e363c0f67571c2caceb94f6880d4c`
- Parent `approved_head` / `review_gate_sha`: `209636e9903e363c0f67571c2caceb94f6880d4c` (matches reviewed HEAD)
- Base at review time: `origin/dev`
- Scope: review packet and evidence summary only; no parent implementation, runtime, or canonical truth modified.

## Executive disposition

Parent task `ODP-ORCH-GITHUB-REF-SNAPSHOT-001` replaces per-task `git ls-remote --heads origin <branch>` probing in `.orchestrator/github_bus.py` with a single short-TTL snapshot of all remote heads, so an unreachable GitHub remote costs the supervisor **one** bounded probe per remote per TTL instead of one per inspected task branch.

The parent reviewer (`Antigravity`) already approved at `209636e9`; `task-review-gate` is green. This packet is an independent re-verification at that exact SHA plus a residual-risk summary for parent-owner closeout.

**Disposition: the approval holds.** 65/65 `test_github_bus.py` tests pass at `209636e9`, the failure semantics are correct and fail-closed on first probe, and the diff stays inside the orchestrator control plane. One residual behavior (§ Reviewer attention point 1) is a **follow-up candidate, not a merge blocker**.

## Reviewed change surface

Against `origin/dev`, the parent branch touches three orchestrator files (`181 insertions(+), 7 deletions(-)`):

| File | Module role | Review observation |
| --- | --- | --- |
| `.orchestrator/github_bus.py` | GitHub ReviewBus coordinator | Adds `_REMOTE_BRANCH_SNAPSHOTS` (module-global, `remote -> (expires_at, refs, last_successful_probe_at)` on `time.monotonic`), `remote_branch_names()`, `parse_remote_head_names()`, `clear_remote_branch_snapshot_cache()`, and the two TTL config accessors. `remote_branch_exists()` becomes exact set membership over the snapshot. |
| `.orchestrator/test_github_bus.py` | Unit test suite | +103 lines: snapshot reuse, malformed/non-head ref parsing, timeout and non-zero-exit failure paths, max-stale expiry, first-probe fail-closed, and a no-config-reload-on-cache-hit assertion. |
| `.orchestrator/config.example.json` | Bus config template | Documents `remote_ref_snapshot_ttl_seconds` (30) and `remote_ref_snapshot_max_stale_seconds` (300) under `github_bus`. |

No L1 canonical documents, contract truth, DB schema, or product application files are touched.

## What the two commits do

| Commit | Author lane | Contribution |
| --- | --- | --- |
| `c99ffc69` | `CodexCoordinator` | Introduces the snapshot: one `ls-remote --heads origin` per TTL, membership test replaces per-branch probe. |
| `209636e9` | `Claude` (owner) | Corrects the failure semantics of `c99ffc69`, which cached a **failed** probe as an empty ref set. Adds the bounded stale-serve window, caches by expiry instead of re-reading TTL per call, and extracts `parse_remote_head_names()`. |

`209636e9` is the substantive review target: without it, a single 8s `ls-remote` stall would have poisoned the snapshot for the whole TTL and marked every published task branch unpublished.

## Feature & contract verification matrix

| Boundary | Pre-change behavior | Post-change behavior | Verification |
| --- | --- | --- | --- |
| **Probe fan-out** | One `ls-remote --heads origin <branch>` per inspected task branch | One `ls-remote --heads origin` per remote per TTL; all branches answered from the snapshot | `test_remote_branch_snapshot_reuses_one_probe_for_multiple_branches` (3 lookups → `probe.assert_called_once`) |
| **Ref matching** | `ls-remote` pattern match; git matches on the ref *tail*, so a pattern could be satisfied by a differently-namespaced ref | Exact membership against `refs/heads/` names parsed from the listing | `test_parse_remote_head_names_ignores_malformed_and_non_head_refs` (tags and malformed lines dropped) |
| **Probe timeout** | Returned `False` (branch reads as unpublished) | Serves last good refs inside the stale window; still bounded by `git_network_timeout_seconds` (default 8s) | `test_remote_branch_probe_times_out_without_blocking_the_bus`, `test_failed_probe_serves_last_good_snapshot_instead_of_empty` |
| **Non-zero exit** | Returned `False` | Same stale-serve path as timeout | `test_failed_probe_with_nonzero_exit_serves_last_good_snapshot` |
| **Prolonged outage** | Every call returned `False` | Stale serving stops after `remote_ref_snapshot_max_stale_seconds` (300s), then returns the empty, fail-closed answer | `test_failed_probe_past_max_stale_window_fails_closed` (elapsed 500s → `False`) |
| **Cold start failure** | Returned `False` | Returns `False`; `last_success` seeds to `-inf`, so `now - last_success` is `inf` and never satisfies the stale window | `test_first_probe_failure_without_snapshot_fails_closed` |
| **Config read cost** | `load_config()` twice per task branch (JSON file read, not memoized) | Zero config reads on a snapshot hit; TTL read once per probe | `test_snapshot_within_ttl_is_served_without_reloading_config` (`ttl.assert_called_once`) |

## Delay-bound claim, quantified

`remote_branch_exists()` is reached once per review-state task from `upsert_review_pr` (`github_bus.py:1003`), plus the `branch_exists()` fallback at `github_bus.py:267`. The live status root currently holds **9 tasks in `review`**.

| Remote unreachable | Before | After |
| --- | --- | --- |
| Worst-case stall per supervisor tick | 9 × 8s ≈ **72s** | 1 × 8s = **8s** |
| Growth | O(review tasks × timeout) | O(remotes × timeout), amortized over the 30s TTL |

`sync_github_bus` is imported in-process by `.orchestrator/supervisor.py:103`, so the module-global snapshot persists across ticks for the life of the supervisor process — the TTL is real wall-clock amortization, not merely intra-tick dedup.

## Why stale-positive is the correct failure direction

The two error directions are not symmetric at the call site:

- **False negative** (published branch reads as absent): `upsert_review_pr` writes `state: "skipped_unpublished_branch"` with `last_remote_branch_check_at`, and the `previous_unpublished` gate at `github_bus.py:1000` then suppresses re-evaluation for `unpublished_branch_recheck_seconds` (default 300s). A sub-second network blip is amplified into minutes of a missing review PR, and wrong task state is persisted.
- **False positive** (deleted branch still reads as present): control flow proceeds to `branch_has_diff` and the `gh` PR calls, which simply find nothing and skip. No wrong state is written.

Serving the last good ref set for a bounded window therefore trades a harmless transient for the avoidance of a persistent one. The parent's reasoning is sound and matches the code.

## Independent verification at exact parent HEAD

Run in a detached worktree pinned to `209636e9903e363c0f67571c2caceb94f6880d4c`:

```bash
git worktree add --detach /tmp/gh-ref-review 209636e9903e363c0f67571c2caceb94f6880d4c
cd /tmp/gh-ref-review/.orchestrator

python3 -m pytest test_github_bus.py -q
# 65 passed

python3 -m py_compile github_bus.py
# clean
```

Lint was **not** re-run locally — `ruff` is not installed in this worker environment. Lint coverage comes from the PR's `orchestrator` CI job, which passed (see below). The owner's own `Verified:` trailer on `209636e9` additionally records `pytest test_supervisor.py test_runtime_state.py (448 passed)` and `ruff check`.

## Parent PR #744 gate state at packet time

| Check | State |
| --- | --- |
| `orchestrator` | pass (1m13s) |
| `performance-gate` | pass (1m4s) |
| `product-e2e-gate` | pass (6m24s) |
| `product` | **pending** |
| `task-review-gate` | pass — "Approved by assigned reviewer Antigravity" |

PR state `OPEN`, `mergeable: MERGEABLE`, `mergeStateStatus: BLOCKED`, auto-merge already armed by `ajoe734` at 10:57Z.

`BLOCKED` here is attributable to the still-running `product` job, not to review or to a dirty base. No parent-owner action is required for it; the correct posture is to let auto-merge land the PR and only then run `done`.

## Reviewer attention points

1. **Residual: success-path staleness can still be amplified to 300s.** The stale-serve fix covers *failed* probes, but a *successful* snapshot is by construction up to `remote_ref_snapshot_ttl_seconds` (30s) old. A task branch pushed just after a snapshot is taken reads as absent for the remainder of that TTL; `upsert_review_pr` then persists `skipped_unpublished_branch`, and the `github_bus.py:1000` recheck gate holds that state for `unpublished_branch_recheck_seconds` (300s) because `head_sha` is unchanged across the retry. So a ≤30s ref-staleness window can still delay a review PR by up to ~300s — the same amplification shape the parent fixed on the failure path, on the path it did not change.

   This is a **bounded, self-healing delay, not a correctness fault**, and it is strictly better than the pre-`209636e9` state. Suggested follow-up (parent owner's call, out of this sidecar's scope): before writing `skipped_unpublished_branch`, fall back to a single targeted `ls-remote --heads origin <branch>` for that one branch, or invalidate the snapshot when the bus observes a task branch push. Either keeps the fan-out win while removing the false negative. No current test covers this interaction.

2. **Effective stale ceiling is `max_stale + ttl`, not `max_stale`.** The fast path (`if cached and now < cached[0]: return cached[1]`) returns without re-evaluating the stale window, which is only checked at re-probe time. During a sustained outage the last good refs can therefore be served for up to 330s rather than the documented 300s. Harmless given the stale-positive analysis above, but `config.example.json`'s `remote_ref_snapshot_max_stale_seconds` reads as a hard bound when it is a re-probe-boundary bound.

3. **Snapshot survives config changes.** `_REMOTE_BRANCH_SNAPSHOTS` is keyed by remote only; changing `remote_ref_snapshot_ttl_seconds` at runtime does not retroactively shorten an outstanding entry's expiry. Worth knowing during rollout tuning — the new TTL takes effect one entry later.

4. **Runtime rollout is part of acceptance.** The parent acceptance line reads "PR #744 全綠且已合併；runtime rollout 後 supervisor loop 無錯誤並量測延遲下降". Merge alone does not satisfy it: the runtime checkout must actually pick up `209636e9` before the latency claim is observable. Confirm with `git merge-base --is-ancestor 209636e9 HEAD` in the runtime root rather than relying on a freshness banner.

## Recommended reviewer disposition

- **RECOMMENDATION: the existing parent approval at `209636e9` stands.** The change is narrow, well-tested, correctly fail-closed, and confined to the orchestrator control plane.
- Attention point 1 is worth filing as a small follow-up task against `github_bus.py`; it should **not** reopen or re-review the parent.
- Parent owner `Claude` should wait for the `product` check and auto-merge to land PR #744, verify runtime pickup of `209636e9`, and only then run `done`.

## Sidecar boundary and handoff

This artifact (`support/sidecars/ODP-ORCH-GITHUB-REF-SNAPSHOT-001/ODP-ORCH-GITHUB-REF-SNAPSHOT-001-SIDECAR-REVIEW.md`) is a non-canonical support review packet produced by helper kind `review_packet`. It creates no runtime behavior, changes no contract, and does not alter the parent's approval state.

Handed off to sidecar reviewer `Claude`. Parent owner `Claude` decides whether any of it is absorbed into the mainline task record.
