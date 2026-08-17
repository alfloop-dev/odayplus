# ODP-ORCH-FINALIZE-STALE-WORKTREE-001 acceptance packet

Support-only packet for parent task `ODP-ORCH-FINALIZE-STALE-WORKTREE-001`.
It records review criteria and dependency boundaries; it does not change runtime,
registry, governance, or canonical truth.

## Decision boundary

Immutable finalization may disregard a task-named worktree only when that
worktree is demonstrably a stale-behind observation and independent merged-PR
provenance proves that the exact reviewer-approved head was delivered. A
different, divergent, or unmerged head remains a hard failure.

The safe distinction is:

- **stale observation:** checkout HEAD is an ancestor of `approved_head`; the
  exact task PR has `headRefOid == approved_head`, is merged to the configured
  base, has a merge commit on the target ref, and has green current checks;
- **conflicting truth:** checkout HEAD is ahead of or divergent from
  `approved_head`, the approved commit cannot be resolved, PR evidence is absent
  or ambiguous, or any exact-head/base/merge/CI predicate fails.

The first case may use PR provenance instead of the stale checkout. The second
must fail closed. No reset, rebase, checkout mutation, ref rewrite, cleanup, or
worktree removal belongs in this path.

## Current dependency map

| Layer | Current contact point | Evidence consumed | Required parent behavior |
| --- | --- | --- | --- |
| Task freeze | `command_done` | `status == review_approved`, owner, `approved_head` | Keep `approved_head` immutable and require the merged PR `headRefOid` to match it exactly. |
| Repository selection | `task_primary_repository_id`, `repository_local_path` | task artifacts and configured repository | Resolve within the configured repository only; retain the repository-slug match gate. |
| Worktree inventory | `resolve_task_delivery_checkout` | current branch and `git worktree list --porcelain` | Classify candidates using branch, checkout HEAD, and `approved_head`; do not let a name-only stale candidate become delivery truth. |
| Stale-behind proof | resolver/collector boundary | `merge-base --is-ancestor <checkout-head> <approved_head>` (or equivalently strict ancestry) | Only the behind relation can make a mismatched task checkout ignorable. Ahead, divergent, missing, or unreadable ancestry fails closed unless the established post-merge target-advance path applies. |
| Commit identity | `collect_done_delivery_metadata` | approved commit object, subject, author, required trailers | Read identity from `approved_head`, never from the stale checkout HEAD. Preserve task-id and trailer checks. |
| Local hygiene | `collect_done_delivery_metadata` | task checkout porcelain status | Evaluate cleanliness only for a checkout accepted as authoritative. If a stale checkout is bypassed, record cleanliness as unevaluated rather than clean; do not inspect the canonical writer as a substitute. |
| Remote provenance | `enforce_delivery_merged_gate` | exact task branch, exact PR head, configured base, merged time, merge commit, target ancestry | This is the authority that permits stale-checkout bypass; all predicates remain mandatory. |
| CI provenance | `normalized_green_pr_checks` | latest run per check/workflow and `task-review-gate` | Preserve empty/red/pending/unknown fail-closed behavior and stale-run supersession semantics. |
| Final record | `command_done` delivery payload | verified head, approved head, PR head and merge commit, provenance mode | Make the bypass explicit and auditable; do not claim that stale-checkout cleanliness or push status was evaluated. |

Expected control flow:

```text
review_approved + approved_head
              |
              v
configured repository + worktree inventory
              |
       +------+--------------------+
       |                           |
exact approved checkout       named checkout has other HEAD
       |                           |
normal clean/push gates       +---+----------------------+
       |                      |                          |
       v                  strictly behind          ahead/diverged/unknown
exact merged-PR gate           |                          |
       |                       v                          v
       +--------------> exact merged-PR gate         reject
                               |
                       +-------+-------+
                       |               |
                    proven          not proven
                       |               |
                PR-only finalize     reject
```

The existing, separately justified post-merge target-branch advance remains a
third accepted path only through `is_approved_head_satisfied`; it must not be
conflated with an arbitrary stale or moved task checkout.

## Acceptance matrix

| ID | Checkout / local state | PR and target evidence | Expected result | Required record or assertion |
| --- | --- | --- | --- | --- |
| A1 | Sole task-named checkout is exactly at `approved_head` and clean | Exact branch/head/base PR is merged; merge commit is on target; current checks green | Accept | Existing exact-head path remains unchanged. |
| A2 | Sole task-named checkout is strictly behind `approved_head` | Same complete exact merged-PR proof as A1 | Accept | Record a distinct stale-checkout/PR-provenance mode, `verified_head == approved_head`, stale checkout path and observed HEAD, and local clean/push gates as not evaluated if bypassed. |
| A3 | No task checkout survives | Same complete exact merged-PR proof as A1 and approved commit object exists | Accept | Preserve existing `merged_pr_without_task_checkout` behavior. |
| A4 | Checkout is a verified post-merge target advance | Exact approved-head PR merged and current HEAD lies on verified target lineage | Accept | Preserve `post_merge_checkout_advanced`; do not generalize it to arbitrary descendants. |
| F1 | Checkout is behind | PR is open, closed-unmerged, absent, or network lookup fails | Reject | Error identifies immutable approved-head PR provenance failure. |
| F2 | Checkout is behind | Merged PR head differs from `approved_head` | Reject | Earlier-head or moved-head PR cannot authorize finalization. |
| F3 | Checkout is behind | PR branch or configured base differs | Reject | Same-name/wrong-base ReviewBus PR is not delivery proof. |
| F4 | Checkout is behind | Merge time or merge commit missing, or merge commit is not on target | Reject | No topology shortcut from checkout ancestry alone. |
| F5 | Checkout is behind | Latest required check is red/pending/unknown, or rollup is empty | Reject | Preserve CI fail-closed behavior. |
| F6 | Checkout HEAD is ahead of or divergent from `approved_head` | Even if a PR exists | Reject | Stale bypass requires strict behind ancestry, not merely mismatch. |
| F7 | Checkout or `approved_head` cannot be read sufficiently to prove ancestry/identity | Any | Reject | Git/read failures are not treated as absence or staleness. |
| F8 | Multiple candidates remain authoritative or provenance is ambiguous | Any | Reject | Do not choose by recency, path order, or first match. |
| F9 | Accepted authoritative checkout is dirty outside exact worker seed files | Exact PR is merged | Reject | Existing git-clean gate still applies to an authoritative checkout. |
| F10 | Remote repository slug differs from configured repository | Exact-looking PR payload | Reject | Cross-repository evidence cannot authorize closeout. |
| N1 | Stale checkout is dirty | Exact merged-PR proof exists and checkout is strictly behind | Must be an explicit policy assertion | Recommended: bypass it as historical/non-authoritative and record cleanliness as unevaluated; never report it clean and never mutate it. Reviewer should reject an implementation that silently ignores this distinction. |

## Minimum test packet for the parent change

Add focused tests near the existing done-delivery provenance regressions. The
tests should exercise the public collector path, not only a new helper.

1. A `git worktree list --porcelain` fixture contains the configured writer plus
   one `refs/heads/task/<TASK-ID>` worktree whose HEAD is an ancestor of the
   exact `approved_head`.
2. `collect_done_delivery_metadata` succeeds only when the exact merged PR and
   target ancestry are present, returning auditable stale-checkout provenance.
3. Table-drive F1 through F7 so each missing predicate fails closed.
4. Preserve the existing exact-head, absent-checkout, post-merge-advance,
   dirty-checkout, wrong-repository, and ambiguous-provenance regressions.
5. Assert no mutating git operation is issued. In mocked command traces, reject
   `reset`, `rebase`, `checkout`, `switch`, `branch -f`, `update-ref`, `clean`,
   and worktree removal commands.

Suggested focused verification after the parent lands its tests:

```bash
python3 -m unittest \
  scripts.test_ai_status.DoneDeliveryProvenanceRegressionTests \
  scripts.test_ai_status.HistoricalClosemergeProvenanceTests \
  scripts.test_ai_status.StaleTaskWorktreeFinalizeTests
```

If the parent uses a different new class name, substitute that class while
retaining both existing regression classes. Then run the full status suite:

```bash
python3 -m unittest scripts.test_ai_status
```

## Reviewer checklist

- [ ] Diff is limited to `scripts/ai_status.py`, `scripts/test_ai_status.py`, and
      any task-owned evidence/support artifact explicitly declared by the parent.
- [ ] Resolver selection is evidence-based; it does not pick the first path or
      newest local timestamp.
- [ ] `approved_head` stays the commit/trailer and PR-head authority.
- [ ] Stale-behind is established with a fail-closed ancestry check.
- [ ] Exact branch, head, configured base, merged time, merge commit on target,
      repository slug, and green latest CI checks remain mandatory.
- [ ] No local mutation is introduced during immutable finalization.
- [ ] Bypassed local gates are recorded as unevaluated, not successful.
- [ ] Wrong/ahead/divergent/unreadable candidates and ambiguous evidence reject.
- [ ] Existing absent-checkout and post-merge-advance behavior still passes.
- [ ] Focused and full `scripts.test_ai_status` commands pass with exact output
      recorded by the parent.

## Handoff to parent owner and assigned reviewer

Parent owner Claude may absorb this packet as review guidance without copying
its wording into canonical documents. Assigned sidecar reviewer Claude should
verify that this file is support-only, that the matrix covers all four parent
acceptance statements, and that no claim here is treated as implementation
proof. The parent implementation and its test output remain the authoritative
delivery evidence.
