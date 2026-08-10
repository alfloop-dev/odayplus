# ODP-ORCH-CLOSEOUT-CI-RECONCILIATION-001 acceptance packet

- Status: support packet prepared; reconciliation cohort enumerated and classified from live state
- Parent task: `ODP-ORCH-CLOSEOUT-CI-RECONCILIATION-001`
- Sidecar task: `ODP-ORCH-CLOSEOUT-CI-RECONCILIATION-001-SIDECAR-ACCEPTANCE`
- Prepared by: Claude2
- Assigned sidecar reviewer: Claude
- Parent owner: Claude
- Parent reviewer: Codex
- Snapshot taken: `2026-08-09T14:14:55Z`, against `origin/dev` = `9c95ecc3e1f2d0885bb4078070a116e852487f69`

## Scope boundary

This is a support-only acceptance checklist, dependency map, and fixture
proposal. It does **not** change `scripts/ai_status.py`,
`scripts/orchestrator/finalize_lane_doctor.py`, supervisor behavior, task truth,
canonical documents, registry/governance policy, or release state. It does not
move any task to `done` and does not relax any delivery gate.

Parent owner Claude and parent reviewer Codex decide whether and how to absorb
this into the mainline implementation. **Parent acceptance and parent closeout
readiness are explicitly NOT claimed.** Every classification below is a
reproducible observation, not an approval.

## Parent task snapshot

- Parent live status: `in_progress`; owner `Claude`, reviewer `Codex`.
- Parent artifacts: `scripts/ai_status.py`, `scripts/orchestrator/finalize_lane_doctor.py`.
- Parent acceptance criteria (from the board):
  1. Reviewer-approved historical tasks that have already merged can obtain
     verifiable PR + merge provenance and complete `done`.
  2. Missing unique provenance, or a head that does not match, must still fail
     closed.
  3. New regression tests cover the stale rollup and non-existent checkout cases.
- Helper-claim history: assigned to `Claude2` at `14:01:14Z`, helper-claimed by
  idle `Claude` at `14:03:54Z` with designated reviewer `Codex` preserved. This
  is the "requeue through an eligible helper slot" clause in the parent title;
  the claim preserved the reviewer, so no reviewer-identity remediation is owed.

## Reconciliation cohort (live evidence)

The live board holds **21 tasks in `review_approved`**. Of those, **7 have an
`approved_head` that is already an ancestor of `origin/dev`** — these are the
"historical merged closeouts" the parent task must reconcile. The remaining 14
have unmerged approved heads and are ordinary open-PR tasks, **out of scope**
for this reconciliation.

Every one of the 7 is currently unable to reach `done`, and they fail for
**five distinct reasons**. A fix that only addresses one class will leave the
rest stranded.

| # | Task | Merge provenance | Blocker class |
| --- | --- | --- | --- |
| 1 | `ODP-SEC-NPM-AUDIT-NANOID-001` | PR #693 MERGED→`dev`, head `e497a465` == approved, merge `7c21c070` on dev, rollup 9/9 green | **A — checkout absent** |
| 2 | `ODP-ORCH-AGENT-LOAD-BALANCE-001-SIDECAR-REVIEW` | PR #726 MERGED→`dev`, head `f97083d7` == approved, merge `79c6792c` on dev | **B — frozen non-green rollup** |
| 3 | `ODP-ORCH-GITHUB-REF-SNAPSHOT-001-SIDECAR-ACCEPTANCE` | PR #746 MERGED→`dev`, head `71fb1ef7` == approved, merge `ebfe128e` on dev | **B — frozen non-green rollup** |
| 4 | `ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001` | PR #575 MERGED→`dev` **and** PR #617 MERGED→`main`, both head `cc560e00` == approved | **C — resolver picks wrong-base PR** |
| 5 | `FIX-SUPERVISOR-THROUGHPUT-20260802` | PR #586 MERGED→`dev` at `bd50ac04` == approved, but head branch is `codex/fix-supervisor-throughput-20260802` | **D — provenance under alternate branch name** |
| 6 | `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW` | PR #685 MERGED→`dev` at head `bbece5be`; approved head `1c2c061a` is **not** an ancestor of that PR head | **E — approved head diverges** |
| 7 | `ODP-ORCH-REBASE-HEAD-LIVENESS-001` | PR #577 MERGED→`dev` at `cdc5e5b6`; approved head `d518d04c` is **59 files ahead** of that PR head | **E — approved head diverges** |

### Class A — task-owned checkout no longer exists

`ODP-SEC-NPM-AUDIT-NANOID-001` is the cleanest specimen in the cohort: PR #693
is MERGED into `dev`, `headRefOid` equals `approved_head` exactly, the merge
commit is an ancestor of `dev`, and all 9 rollup entries are green. Nothing
about its provenance is ambiguous.

It still cannot finalize, because `collect_done_delivery_metadata`
(`scripts/ai_status.py:2270`) calls `task_delivery_checkout` **before** it ever
reaches `enforce_delivery_merged_gate` (`scripts/ai_status.py:2452`).
`task_delivery_checkout` (`scripts/ai_status.py:1863`) requires exactly one
worktree on `task/<id>` and raises otherwise. No such worktree exists for this
task, so closeout dies at `found 0` while the durable evidence sits unread.

This is the ordering inversion at the heart of the parent task: **the most
ephemeral input (a local worktree) is consulted first, and the most durable one
(an immutable merged PR) is consulted last.** Worktrees get pruned and GitHub
auto-deletes merged task branches, so for any sufficiently old task the first
gate is the one guaranteed to fail.

### Class B — non-green rollup frozen on the PR head after a merge-queue merge

PRs #726 and #746 both merged into `dev`, yet both still carry
`performance-gate | CheckRun | COMPLETED | FAILURE` on the PR head. Because
`normalized_green_pr_checks` (`scripts/ai_status.py:1777`) rejects any CheckRun
whose conclusion is not `SUCCESS`/`NEUTRAL`/`SKIPPED`, these two fail closed
permanently — the rollup is immutable and will never turn green.

The merge itself was legitimate. `dev` merges through a merge queue, and the
authoritative verdict for a queued merge is the `merge_group` run computed on
*dev-tip + the PR*, not the rollup attached to the PR head. Recent `merge_group`
runs for the dev tips in this window (`9c95ecc3`, `b03144fb`, `e16d6989`) are
`success`. PR #746's entire diff is a single documentation file
(`support/sidecars/.../ODP-ORCH-GITHUB-REF-SNAPSHOT-001-SIDECAR-ACCEPTANCE.md`),
so a `performance-gate` failure on it cannot be attributable to the change.

**This is the "stale rollup" case named in parent acceptance criterion 3.** The
reconciliation needs a merge-queue-aware verdict source; simply widening the
accepted conclusion set would destroy the gate.

### Class C — MERGED-preference resolver ignores the base branch

`pull_request_status_for_branch` (`scripts/ai_status.py:1608`) exists to avoid
returning a CLOSED ReviewBus PR against `main` instead of the MERGED task PR
against `dev`. It does this by returning the primary `gh pr view <branch>`
result as soon as its state is `MERGED` (`scripts/ai_status.py:1639`), and only
falling back to `gh pr list --state merged` otherwise.

`ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001` has **two** MERGED PRs on the
same head `cc560e00`: #575 into `dev` and #617 into `main`. The primary lookup
returns #617, which is MERGED, so the function short-circuits and never
considers #575. `enforce_delivery_merged_gate` then requires
`pr_base == target_branch` (`scripts/ai_status.py:1976`), sees `main` instead of
`dev`, and fails closed — even though the correct, unique, in-target provenance
(#575) exists and is reachable.

The MERGED-preference short-circuit is one predicate short: it should prefer a
MERGED PR **whose base equals the delivery target branch**, and only then fall
back. Verified live: `gh pr view task/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001`
returns `PR#617 MERGED base=main`.

### Class D — provenance exists under an alternate head branch name

`FIX-SUPERVISOR-THROUGHPUT-20260802` has remote branch
`task/FIX-SUPERVISOR-THROUGHPUT-20260802` pointing at `bd50ac04`, which equals
its `approved_head` and is an ancestor of `dev`. But no PR was ever opened with
that head branch — `gh pr list --head task/FIX-...` returns empty. The actual
merge vehicle is **PR #586, head branch `codex/fix-supervisor-throughput-20260802`,
same SHA `bd50ac04`, merged into `dev` at `2026-08-02T15:40:16Z`.**

So provenance is verifiable by SHA but not addressable by the `task/<id>`
naming convention that both `branch_for` (`finalize_lane_doctor.py:102`) and
`enforce_delivery_merged_gate`'s `pr_head_name == branch` check
(`scripts/ai_status.py:1975`) assume. Note the failure is doubled: the lookup
finds nothing, and even if the reconciliation located #586 by SHA, the head-name
equality check would still reject it.

### Class E — approved head genuinely diverges from the merged PR head

These two must **not** be auto-reconciled, and they are the concrete evidence
that parent acceptance criterion 2 has real teeth:

- `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW`: PR #685 merged head
  `bbece5be`, but approved head `1c2c061a` is **not** an ancestor of it. The
  approved head reached `dev` by some other path. PR #685's rollup also still
  shows `task-review-gate | StatusContext | state=PENDING`, which
  `normalized_green_pr_checks` treats as pending and rejects.
- `ODP-ORCH-REBASE-HEAD-LIVENESS-001`: the merged PR is #577 at head
  `cdc5e5b6`; the approved head `d518d04c` is a **descendant** of it, differing
  by 59 files across `.orchestrator/supervisor.py`, `.github/workflows/`,
  `Makefile`, and app code. This is emphatically not an evidence-only advance,
  so the `is_approved_head_satisfied` evidence-only allowance
  (`scripts/ai_status.py:2091`) does not and should not rescue it. Its primary
  PR lookup also returns `PR#593 CLOSED base=main`.

For both, more than one candidate merge vehicle exists and none of them carries
the approved head as its PR head. Unique provenance is genuinely absent, so
fail-closed is the correct outcome. They should be adjudicated case by case,
not swept into an automated backfill.

## Dependency map

| Authority / input | Consumer and recorded output | Required pass condition | Fail-closed coverage that must survive |
| --- | --- | --- | --- |
| Task-owned checkout (`task_delivery_checkout`, `ai_status.py:1863`) | `repository_root`, `branch`, `verified_head` | exactly one worktree on `task/<id>` | zero or multiple checkouts are rejected today; any reconciliation must not silently fall back to central-writer HEAD, which is precisely the substitution this function was written to prevent |
| Gate ordering (`ai_status.py:2270` before `:2452`) | whether merged-PR provenance is ever consulted | durable provenance should be reachable when the ephemeral checkout is gone | reordering must not let a *live, dirty, or divergent* checkout skip the clean/head checks when one does exist |
| Immutable approved head | `delivery.approved_head`, commit subject/body/trailers | approved head resolvable in some repository the finalizer can read | absent approved head still aborts (`ai_status.py:2240`) |
| PR resolution (`pull_request_status_for_branch`, `:1608`) | `delivery.pull_request` | resolve the unique MERGED PR **whose base is the delivery target** | must not accept a MERGED PR into the wrong base, and must not accept an arbitrary pick when several equally-valid candidates exist |
| Merged-PR gate (`enforce_delivery_merged_gate`, `:1906`) | `merge_verified_via_pr`, merge commit, merged-at | state MERGED; `pr_head == approved_head`; `pr_head_name == branch`; `pr_base == target`; merge commit ancestor of target | Class E must keep failing; head-name equality is the check that Class D trips even after SHA-based discovery |
| CI rollup (`normalized_green_pr_checks`, `:1777`) | `ci_checks`, `ci_status` | every CheckRun `SUCCESS`/`NEUTRAL`/`SKIPPED`; every StatusContext `SUCCESS`; rollup non-empty | empty/malformed rollups must stay rejected; a merge-queue-aware source must be *additional* evidence, never a blanket downgrade of failures |
| Merge-queue verdict (`merge_group` runs) | proposed authoritative CI source for Class B | the queued `merge_group` run for the merge commit is `success` | a green `merge_group` for a *different* commit must not be accepted as this PR's verdict |
| Target topology | `merge_target_sha`, `head_merged_to_target` | approved head and merge commit are ancestors of `origin/<target>` | unavailable target ref still aborts (`:1933`) |
| Finalize lane doctor (`classify`, `finalize_lane_doctor.py:164`) | operator-facing cause + remediation | `ALREADY_MERGED` is detected before PR lookup | doctor is diagnostic only; it must not become a second closeout authority |
| Live canonical writer | status transition, archive, delivery record | owner runs the live-root wrapper; no hand-edited state | env/local delivery-gate relaxation cannot disable the mandatory `done` gates (`ai_status.py:2227`) |

## Acceptance checklist (for parent verification)

### Criterion 1 — merged historical tasks can finalize

- [ ] Class A: `ODP-SEC-NPM-AUDIT-NANOID-001` reaches `done` using PR #693
      provenance with no task-owned worktree present.
- [ ] Class B: `ODP-ORCH-AGENT-LOAD-BALANCE-001-SIDECAR-REVIEW` (#726) and
      `ODP-ORCH-GITHUB-REF-SNAPSHOT-001-SIDECAR-ACCEPTANCE` (#746) finalize on a
      merge-queue-aware verdict despite the frozen `performance-gate` FAILURE.
- [ ] Class C: `ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001` resolves PR
      #575 (base `dev`) rather than #617 (base `main`).
- [ ] Class D: `FIX-SUPERVISOR-THROUGHPUT-20260802` either resolves PR #586 by
      SHA with an explicit alternate-head-name allowance, or is documented as
      deliberately out of scope.

### Criterion 2 — fail-closed preserved

- [ ] Class E: `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW` and
      `ODP-ORCH-REBASE-HEAD-LIVENESS-001` still refuse to finalize, with a
      message naming the divergence rather than a generic head mismatch.
- [ ] A task with no MERGED PR into the target branch still fails closed.
- [ ] A task with two MERGED PRs into the *same* target base and different heads
      still fails closed as ambiguous.
- [ ] Absence of a task-owned checkout does **not** cause the finalizer to
      substitute central-writer HEAD or an unrelated worktree.
- [ ] Empty or malformed `statusCheckRollup` still fails closed.
- [ ] The 14 unmerged `review_approved` tasks are unaffected by the change.

### Criterion 3 — regression coverage

- [ ] Non-existent-checkout fixture, modeled on Class A, added to
      `scripts/test_ai_status.py`.
- [ ] Stale-rollup fixture, modeled on Class B, covering a MERGED PR whose head
      rollup holds a `FAILURE` CheckRun alongside a green merge-queue verdict.
- [ ] Negative fixtures for Class E divergence and for the ambiguous-PR case, so
      the reconciliation cannot regress into accepting them.

## Proposed regression fixtures

Existing coverage lives in `scripts/test_ai_status.py`. Suggested additions,
derived from the live specimens above so they encode real shapes:

1. `test_done_finalizes_merged_task_without_task_checkout` — worktree inventory
   returns zero matches for `task/<id>`; PR payload mirrors #693 (MERGED, head
   == approved, merge commit ancestor of target, 9 green entries). Expect
   success, and assert the delivery record marks that provenance came from the
   merged PR rather than a checkout.
2. `test_done_rejects_missing_checkout_when_pr_not_merged` — same missing
   checkout, PR state OPEN. Expect `SystemExit`. This is the guard that keeps
   fixture 1 from becoming a bypass.
3. `test_done_accepts_merge_queue_verdict_over_stale_pr_head_rollup` — rollup
   mirrors #746 (`performance-gate` COMPLETED/FAILURE, everything else green),
   with a `merge_group` run for the merge commit reporting success.
4. `test_done_rejects_failing_rollup_without_merge_queue_verdict` — same rollup,
   no green `merge_group` for that merge commit. Expect `SystemExit`.
5. `test_pr_resolution_prefers_merged_pr_into_delivery_target` — two MERGED PRs
   on one head, bases `main` and `dev`; assert the `dev` PR is chosen.
6. `test_done_rejects_approved_head_not_in_merged_pr` — approved head is a
   descendant of the merged PR head by non-evidence files (Class E, #577 shape).
   Expect `SystemExit`.

## Reviewer replay

All commands below are read-only and were run from
`/home/lupin/oday-plus-supervisor-live` against `origin/dev` `9c95ecc3`.

1. Enumerate the cohort — for each `review_approved` task with an
   `approved_head`, test `git merge-base --is-ancestor <approved_head> origin/dev`.
   Expect 21 approved tasks, 7 merged.
2. Class A — `git worktree list --porcelain | grep -i nanoid` shows only the
   `-SIDECAR-REVIEW` branch, never `task/ODP-SEC-NPM-AUDIT-NANOID-001`; then
   `gh pr view task/ODP-SEC-NPM-AUDIT-NANOID-001 --json state,headRefOid,baseRefName,mergeCommit`
   shows MERGED / `e497a465` / `dev` / `7c21c070`.
3. Class B — `gh pr view 746 --json statusCheckRollup` shows
   `performance-gate` `COMPLETED`/`FAILURE`; `gh pr diff 746 --name-only` shows a
   single `.md` file; `gh run list --event merge_group` shows `success` runs for
   the dev tips in that window.
4. Class C — `gh pr view task/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001`
   returns `PR#617 MERGED base=main`, while
   `gh pr list --head <same> --state all` also lists `PR#575 MERGED base=dev`.
5. Class D — `gh pr list --head task/FIX-SUPERVISOR-THROUGHPUT-20260802 --state all`
   is empty; `gh pr list --head codex/fix-supervisor-throughput-20260802 --state all`
   returns `PR#586 MERGED base=dev head=bd50ac04`.
6. Class E — `git merge-base --is-ancestor 1c2c061a bbece5be` fails; and
   `git diff --name-only cdc5e5b6 d518d04c | wc -l` reports 59.
7. Confirm this sidecar branch's diff against `origin/dev` contains only this
   support artifact.

## Independent verification record

- Every classification above was produced by direct read-only inspection of the
  live status root and GitHub at `2026-08-09T14:14:55Z`; none is inferred from
  another packet or from prior task history.
- No canonical file, task state, or gate was modified. No `done` transition was
  attempted for any cohort member.
- Sidecar diff is limited to
  `support/sidecars/ODP-ORCH-CLOSEOUT-CI-RECONCILIATION-001/ODP-ORCH-CLOSEOUT-CI-RECONCILIATION-001-SIDECAR-ACCEPTANCE.md`.
- Limitation: cohort membership is a snapshot. `origin/dev` advances, so a task
  listed here as unmerged may merge later; the parent owner should re-run step 1
  of the replay before acting on the list.
- Limitation: the merge-queue verdict in Class B was corroborated from recent
  `merge_group` runs at the dev tips in that window, not by pinning each merge
  commit to its own `merge_group` run. Confirming that per-PR mapping is
  implementation work owned by the parent, and fixture 4 above is what keeps the
  mapping honest.

## Handoff disposition

The support packet is prepared for parent task
`ODP-ORCH-CLOSEOUT-CI-RECONCILIATION-001`. Its main contribution is that the
reconciliation population is **not homogeneous**: seven merged-and-approved
tasks fail closeout for five different reasons, one of which (Class E) must keep
failing. A fix targeting only the missing-checkout case would leave five of the
seven stranded.

Parent closeout readiness and acceptance are explicitly NOT claimed. Handed off
to sidecar reviewer Claude; parent owner Claude and parent reviewer Codex decide
absorption into the mainline implementation.
