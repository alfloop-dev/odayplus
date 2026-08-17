# ODP-ORCH-FINALIZE-STALE-WORKTREE-001 review packet

Prepared by: Codex2  
Sidecar reviewer / handoff target: Claude  
Parent task reviewer: Antigravity3  
Evidence snapshot: 2026-08-11 UTC

## Scope and conclusion

This packet reviews parent PR [#812](https://github.com/alfloop-dev/odayplus/pull/812) at the immutable review head `02b77ae472e0deacf54ef5b20fd36e07c8f72696`. It is support evidence only and does not change the parent implementation or canonical truth.

No blocking finding was identified in the reviewed diff. The implementation keeps the new stale-checkout path narrow: a checkout must be a proven ancestor of the approved head, and final delivery must still be proven by a merged PR whose head is exactly that approved SHA. Diverged heads, duplicate live claims at the approved head, moved-head PRs, and unmerged PRs remain fail closed.

At the evidence snapshot, PR #812 was open. The `orchestrator`, `performance-gate`, and `task-review-gate` checks passed; `product` and `product-e2e-gate` were still pending. This packet therefore does not claim that the PR was merged or that every CI check was complete.

## Exact-head change map

The reviewed commit changes only:

- `scripts/ai_status.py`: 130 lines changed.
- `scripts/test_ai_status.py`: 345 lines changed.
- Total diff: 468 insertions and 7 deletions.

The runtime change has four relevant parts:

1. `resolve_task_delivery_checkout(..., approved_head=...)` selects the single checkout at the approved head only when every other same-task claimant is a strict historical ancestor. A diverged claimant or two claimants at the approved head remains ambiguous.
2. `is_stale_task_checkout(...)` requires the approved commit object to exist and the checkout head to be its ancestor. Missing objects, unreadable paths, equal heads, empty heads, and divergence do not enter the stale path.
3. `collect_done_delivery_metadata(...)` records stale-checkout provenance as `merged_pr_with_stale_task_checkout`, preserves the stale head and dirty-entry count, and uses the approved head for commit metadata and immutable delivery verification.
4. `enforce_delivery_merged_gate(...)` remains the final authority: the PR must be `MERGED`, use the exact task branch and approved head, target the configured base, expose a merge commit, and have that merge commit on the target ref.

The ancillary `git_command_succeeds(...)` change converts `OSError` into `False`, preserving the existing “not proven” fail-closed contract when Git cannot run. Moving `unittest.main()` to the end makes the newly appended test classes visible in direct script execution.

## Acceptance coverage

| Parent acceptance | Evidence in exact head |
| --- | --- |
| A stale-behind same-branch worktree may finalize after the exact approved head has merged | `test_a_behind_checkout_finalizes_from_the_exact_merged_approved_head` and `test_done_accepts_the_delivery_a_behind_checkout_produced` |
| Wrong head and unmerged PR remain fail closed | Diverged-checkout, unmerged-PR, moved-head-PR, missing-approved-object, and merged-gate-disabled tests |
| Immutable finalize does not reset, rebase, or rewrite the branch | `test_the_superseded_tree_is_read_but_never_rewritten` records issued Git commands and rejects mutating verbs |
| Existing exact-head and advanced-checkout behavior remains valid | Focused rerun included `HistoricalClosemergeProvenanceTests` and `EvidenceOnlyAdvanceTests` alongside the new stale-checkout class |

## Independent verification

The following was run against a clean `git archive` snapshot of exact head `02b77ae472e0deacf54ef5b20fd36e07c8f72696`, not against the sidecar branch:

```text
python3 -m unittest \
  scripts.test_ai_status.StaleTaskCheckoutFinalizeTests \
  scripts.test_ai_status.EvidenceOnlyAdvanceTests \
  scripts.test_ai_status.HistoricalClosemergeProvenanceTests
```

Result: `Ran 47 tests in 0.371s — OK`.

The parent commit also records these broader author-run checks; they were inspected but not independently rerun by this sidecar:

- `python3 -m pytest -m "not requires_live_env" .orchestrator scripts` — 1093 passed, 287 subtests.
- `python3 scripts/test_ai_status.py` — 172 tests.
- Real-Git probes for behind, ahead, missing-object, unreadable-cwd, and two-claimant selection cases.

## Reviewer focus and residual risk

- Confirm the policy choice to skip the clean-tree gate for a stale checkout. The code records `git_clean_evaluated=false`, the skip reason, and a stale dirty-entry count; its safety argument is that the exact merged PR head, rather than the historical worktree, is the delivery.
- Confirm that `superseded_task_checkouts` is evidence only. No reset, rebase, checkout, worktree removal, branch move, or push is introduced by the new selection path.
- Keep the finalization gate closed until PR #812 is actually merged and its remaining CI checks complete. The unit tests prove the merged-PR path with fixtures; the live PR was not yet merged at this snapshot.
- If the parent head moves away from `02b77ae472e0deacf54ef5b20fd36e07c8f72696`, this packet is stale and the new head requires review.

## Handoff

Claude can use this packet as the sidecar evidence summary for the parent task. The recommended disposition for the reviewed exact head is **no blocking finding**, subject to the parent task's assigned reviewer and the live PR/CI merge gates. This packet does not approve, merge, or finalize the parent task.
