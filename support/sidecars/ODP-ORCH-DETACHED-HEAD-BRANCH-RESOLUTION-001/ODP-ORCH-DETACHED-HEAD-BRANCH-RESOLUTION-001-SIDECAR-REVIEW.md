# Sidecar Review Packet: ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001-SIDECAR-REVIEW

- **Task ID**: `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001`
- **Parent Title**: Resolve detached-HEAD branch resolution for ReviewBus
- **Parent Owner / Parent Reviewer**: `Antigravity` / `Antigravity2`
- **Helper Kind**: `review_packet`
- **Sidecar Owner**: `Claude3`
- **Sidecar Reviewer**: `Claude`
- **Phase**: Orchestrator reliability
- **Last Updated**: 2026-08-06 (third base advance; §1–§4 unchanged since approval at `a58cf5d0`)

---

## Executive Summary

This support sidecar is a review packet for parent task
`ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001`. It describes the parent diff as
it actually exists, records the verification commands that were run and their
observed output, and lists the residual risk the parent diff deliberately does
not cover.

The parent diff is **one line of behaviour change plus two regression tests**.
It is narrow on purpose. This packet does not claim it is broader than it is.

Sidecar scope: support artifacts only. No canonical L1 truth, contract, runtime,
registry, or governance file is modified by this task.

### Parent diff under review

```
$ git diff --stat origin/dev...origin/task/ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001
 .orchestrator/github_bus.py      |  8 +++++++-
 .orchestrator/test_github_bus.py | 35 +++++++++++++++++++++++++++++++++++
 2 files changed, 42 insertions(+), 1 deletion(-)
```

Branch head at review time: `d32a73d2` (three `dev` merges on top of the single
substantive commit `d94bc547`, "stop reporting \"HEAD\" as a branch name").

---

## 1. Defect Analysis & Root Cause

### The primitive

In a detached checkout, `git rev-parse --abbrev-ref HEAD` prints the literal
string `HEAD` and exits `0`. It reports a branch name for a state that has no
branch.

### Root cause chain (verified, this worktree, 2026-08-05)

1. **False branch claim.** `.orchestrator/github_bus.py::current_branch()`
   called `git rev-parse --abbrev-ref HEAD` and returned its stdout verbatim.
   On a detached worktree the return value was the string `"HEAD"` rather than
   `None`.

2. **The claim survives downstream validation.** `"HEAD"` is truthy and differs
   from the default branch, so the truthiness and default-branch guards in
   `review_branch_for_task()` do not reject it. `branch_exists("HEAD")` then
   returns `True` — but **not** via `refs/heads/HEAD`:

   ```
   $ git show-ref --verify refs/heads/HEAD
   fatal: 'refs/heads/HEAD' - not a valid ref      # rc=128

   $ git show-ref --verify refs/remotes/origin/HEAD
   84029065b42aac28c93aba47d0157e006852a265 refs/remotes/origin/HEAD   # rc=0
   ```

   `branch_exists()` tries `refs/heads/<branch>` first (fails), then
   `refs/remotes/origin/<branch>` — and `refs/remotes/origin/HEAD` is the
   remote's default-branch symref, which exists in any normally cloned repo.
   That second probe is what makes the false claim validate.

3. **Resulting exposure.** With `"HEAD"` accepted as a branch name, ReviewBus
   records a non-branch as a task's review branch and cannot match or create the
   real task PR. The live consequence is documented in
   `scripts/orchestrator/check_runtime_freshness.py`: on 2026-08-04 the
   supervisor checkout was detached, `current_branch()` reported `"HEAD"`,
   ReviewBus recorded it as a branch name and skipped PR creation, stranding
   finished tasks with no pull request.

### Scope note on the current exposure (reviewer should confirm)

In `origin/dev` today the last fallback in `review_branch_for_task()` is:

```python
branch = current_branch()
if branch and branch != default_branch(config) and (not task_id or task_id_matches_branch(task_id, branch)) and branch_exists(branch):
    return branch
```

`task_id_matches_branch(<any real task id>, "HEAD")` is `False`, so for a task
with a non-empty id this fallback already rejects `"HEAD"` even before the
parent fix. The remaining pre-fix hole on that path is the `not task_id`
disjunct (empty/missing task id). The broader 2026-08-04 exposure predates the
`task_id_matches_branch` guard, which was introduced 2026-08-02 by
`d583b26a` (ODP-ORCH-TASK-PR-DISCOVERY-001).

This does not weaken the parent fix — `current_branch()` returning a string that
is not a branch is wrong at the source regardless of how many downstream guards
happen to catch it — but the packet states it so the reviewer is not left
believing the fix closes a currently-wide hole.

---

## 2. Parent Implementation Assessment

### The only behaviour change (`.orchestrator/github_bus.py`)

`current_branch()` swaps its probe command; nothing else in the function
changes. The added comment block explains the failure mode.

```diff
 def current_branch() -> str | None:
-    proc = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT)
+    # `rev-parse --abbrev-ref HEAD` answers the literal string "HEAD" on a
+    # detached checkout, and every downstream guard lets it through: ...
+    proc = run_command(["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=ROOT)
     if proc.returncode != 0:
         return None
     branch = (proc.stdout or "").strip()
     return branch or None
```

`git symbolic-ref --short -q HEAD` exits non-zero on a detached HEAD, so the
existing `returncode != 0` arm already produces `None`. The fix reuses the
function's existing failure path rather than adding a new branch.

**No other function is modified.** In particular the parent diff does **not**
add `"HEAD"` guards to `branch_exists()`, `branch_head_sha()`,
`remote_branch_exists()`, `branch_has_diff()`, or `review_branch_for_task()`,
and does **not** add a `branch == "HEAD"` check inside `current_branch()`. Those
functions are unchanged from `origin/dev`. See §3 for what that leaves open.

```
current_branch()
        │
        ▼
git symbolic-ref --short -q HEAD
        │
   ┌────┴────────────────┐
 rc != 0                rc == 0
 (detached)          (on a branch)
   │                     │
   ▼                     ▼
 return None      return stdout.strip() or None
```

### Test coverage added (`.orchestrator/test_github_bus.py`)

One new class, `DetachedHeadBranchResolutionTests`, with exactly **two** tests.
Both build a real temporary git repo and patch `github_bus.ROOT` at it:

| Test | What it does |
|---|---|
| `test_detached_head_yields_no_branch` | `git init` + empty commit + `checkout --detach HEAD`, asserts `current_branch() is None` |
| `test_named_branch_is_still_returned` | `git init -b task/ODP-X-001` + empty commit, asserts `current_branch() == "task/ODP-X-001"` |

No mock-based tests were added. The tests are real-subprocess tests.

### Review observations on the parent diff

1. **Regression value is confirmed, not assumed.** Reverting only
   `.orchestrator/github_bus.py` to `origin/dev` and rerunning the new class
   fails with `AssertionError: 'HEAD' is not None` — the test genuinely pins the
   bug (command and output in §4).

2. **Style nit — missing blank lines between classes.** The new class body ends
   at `test_github_bus.py:770` and `class TaskPRBaseBranchTests` begins
   immediately at line 771 with no separating blank lines, unlike every other
   class boundary in the file (PEP 8 wants two). Cosmetic, non-blocking; worth a
   one-line fixup if the parent owner pushes again. `ruff` is not installed in
   this environment, so this was found by inspection, not by lint.

3. **Comment duplication.** The same explanation appears in the
   `current_branch()` comment and again in the test class docstring. Acceptable
   — the test docstring is the one a future reader hits first.

4. **Adjacent surfaces checked, not affected.** `scripts/ai_status.py:1851`
   (`task_delivery_checkout`) still uses `rev-parse --abbrev-ref HEAD`, but it
   only compares the result against an explicit `["task/<id>", "task-<id>"]`
   list, so `"HEAD"` cannot be mistaken for a task branch there; it falls
   through to `git worktree list`. `scripts/orchestrator/check_runtime_freshness.py:59`
   uses `--abbrev-ref` deliberately, because it needs to *detect* the `"HEAD"`
   sentinel. Neither needs to change with this parent diff.

---

## 3. Acceptance Matrix

Only behaviour the parent diff actually implements is asserted here. Everything
else is recorded as residual risk.

### Implemented and covered

| Ref | Acceptance rule | Evidence | Result |
|---|---|---|---|
| **A1** | `current_branch()` returns `None` when HEAD is detached | `test_detached_head_yields_no_branch`; plus live probe in a detached worktree (§4.3) | PASS |
| **A2** | Named-branch resolution is unchanged | `test_named_branch_is_still_returned` | PASS |
| **A3** | The new test actually pins the defect | Pre-fix rerun fails `AssertionError: 'HEAD' is not None` (§4.2) | PASS |
| **A4** | No collateral regression in the bus suite | `pytest -q .orchestrator/test_github_bus.py` → 32 passed (§4.1) | PASS |

### Residual risk — open, not addressed by this diff

| Ref | Residual risk | Observed | Disposition |
|---|---|---|---|
| **R1** | `branch_exists("HEAD")` still returns `True`, via the `refs/remotes/origin/HEAD` probe — `refs/heads/HEAD` does not resolve | `github_bus.branch_exists("HEAD") == True` (§4.3) | Open. Unreachable from `current_branch()` after the fix, but reachable from any other caller that passes a literal `"HEAD"` (e.g. an explicit `task["branch"]`/`github.head_branch` value, or an agent record whose `branch` field was written as `"HEAD"`). |
| **R2** | `branch_head_sha("HEAD")` returns the origin default-branch SHA instead of `None` | `github_bus.branch_head_sha("HEAD") == 84029065…` (§4.3) | Open. Same reachability as R1. A caller that gets a plausible SHA back will not notice the input was not a branch. |
| **R3** | `branch_has_diff()` and `remote_branch_exists()` have no `"HEAD"` guard | Source inspection of `origin/dev` (unchanged by parent diff) | Open, lower severity — both are downstream of an already-validated branch name in current call paths. |
| **R4** | `review_branch_for_task()`'s `not task_id` disjunct would still accept `"HEAD"` if `current_branch()` ever returned it | Source inspection | Closed in practice by the parent fix (`current_branch()` can no longer return `"HEAD"`), but the guard itself remains absent. |

**Recommendation to the parent owner and parent reviewer:** R1/R2 are the ones
worth a decision. Either accept them explicitly as out of scope for a
minimal-fix task, or open a small follow-up that makes `branch_exists()` and
`branch_head_sha()` fail closed on `"HEAD"` and on any `*/HEAD` ref. This
sidecar does not implement either; it only surfaces the choice.

---

## 4. Verification — commands run and observed output

All commands below were run on 2026-08-05 by `Claude3`. Parent branch tests were
run in a throwaway worktree pinned at
`origin/task/ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001` (`d32a73d2`), created
with `git worktree add --detach /tmp/odp-parent-verify` and removed afterwards.
Nothing outside that worktree was modified for verification.

### 4.1 Full bus suite on the parent branch

```
$ python3 -m pytest -q .orchestrator/test_github_bus.py
................................                                         [100%]
```

32 passed.

### 4.2 Regression check — does the new test fail without the fix?

```
$ python3 -m pytest -q .orchestrator/test_github_bus.py -k DetachedHeadBranchResolution
..                                                                       [100%]

$ git checkout origin/dev -- .orchestrator/github_bus.py    # revert only the fix
$ python3 -m pytest -q .orchestrator/test_github_bus.py -k DetachedHeadBranchResolution
>               self.assertIsNone(github_bus.current_branch())
E               AssertionError: 'HEAD' is not None
FAILED .orchestrator/test_github_bus.py::DetachedHeadBranchResolutionTests::test_detached_head_yields_no_branch

$ git checkout HEAD -- .orchestrator/github_bus.py          # restore
```

`test_named_branch_is_still_returned` passes either way, as expected — it is the
no-regression half of the pair.

### 4.3 Live probe of the fixed module, from a genuinely detached worktree

Run inside `/tmp/odp-parent-verify`, which is itself on a detached HEAD, so this
exercises the real condition rather than a synthesized one:

```
$ python3 -c "import sys; sys.path.insert(0,'.orchestrator'); import github_bus; \
    print('branch_exists(HEAD)  =', github_bus.branch_exists('HEAD')); \
    print('branch_head_sha(HEAD)=', github_bus.branch_head_sha('HEAD')); \
    print('current_branch()     =', github_bus.current_branch())"
branch_exists("HEAD")  = True
branch_head_sha("HEAD") = 84029065b42aac28c93aba47d0157e006852a265
current_branch()        = None
```

This is the single most useful line in the packet: `current_branch()` is fixed
(`None`), and R1/R2 are demonstrably still open.

### 4.4 Ref-resolution facts behind §1 step 2

```
$ git show-ref --verify refs/heads/HEAD            ; echo rc=$?
fatal: 'refs/heads/HEAD' - not a valid ref
rc=128

$ git show-ref --verify refs/remotes/origin/HEAD   ; echo rc=$?
84029065b42aac28c93aba47d0157e006852a265 refs/remotes/origin/HEAD
rc=0

$ git ls-remote --heads origin HEAD                ; echo rc=$?
rc=0                                                # empty output → no remote head named HEAD
```

### 4.5 Not run

- `ruff check .orchestrator/` — `ruff` is not installed in this environment
  (`ruff: command not found`). The §2 style observation comes from reading the
  file, not from lint.
- `.orchestrator/test_supervisor.py` — outside the parent diff's blast radius;
  the parent diff touches one function with one in-repo caller.

---

## 5. Handoff Note

- **This packet's scope**: support artifact only. The single file touched by
  this sidecar task is
  `support/sidecars/ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001/ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001-SIDECAR-REVIEW.md`.
- **Base advance**: performed three times, all by merge. No history was reset,
  force-pushed, or overwritten at any point.
  1. *2026-08-05, before editing* — branch was 1 ahead / 12 behind `origin/dev`;
     `origin/dev` merged in cleanly (`7129d3e0`), then the packet revision
     `a58cf5d0` was written on top and approved at that head.
  2. *2026-08-06, after approval* — `origin/dev` advanced again (branch 3 ahead /
     5 behind) and PR #639 went `BEHIND`. `dev` protection sets
     `required_status_checks.strict = true`, so a behind branch cannot merge and
     the approved head could not be delivered as-is. `origin/dev` merged in
     cleanly again (`37582d42`, no conflicts); this doc commit sits on top.
     Because the merge moves the branch head off the approved head `a58cf5d0`,
     and the `done` gate in `scripts/ai_status.py` compares checkout HEAD and PR
     head against `approved_head` for *exact equality*, the task returns to the
     reviewer via `re_review` rather than being finalized at a stale head. The
     packet's substance is unchanged; `git diff --stat origin/dev...HEAD` is
     still exactly this one file and nothing else.
  3. *2026-08-06, after the second approval* — the same loop repeated. The
     re-review approved `52ad4722`, every CI check on PR #639 finished green
     (`orchestrator`, `product`, `performance-gate`, `product-e2e-gate`,
     `task-review-gate` all `SUCCESS`), but before the PR could be merged
     `origin/dev` advanced by two more commits (`c879004a` #642, `bc7366d3`
     #622) and `mergeStateStatus` went back to `BEHIND`. PR #639 has no
     auto-merge request set (`autoMergeRequest: null`), so nothing merges it
     while it is behind. `origin/dev` was merged in cleanly a third time
     (`ec34a468`, no conflicts); this doc commit sits on top. Scope after the
     merge is still exactly one file: `git diff --name-only origin/dev...HEAD`
     returns only this packet, and the branch is 0 behind / 6 ahead.
     `is_evidence_only_advance()` in `scripts/ai_status.py:2010` carries an
     approval forward only when every changed path sits under `docs/evidence/`,
     so a base-advance merge that pulls in orchestrator source necessarily
     invalidates `approved_head` — `re_review` is the only correct route, and
     `restore_approved_head` still refuses a moved branch.
- **Known loop, for the parent owner / orchestrator**: this is now the second
  consecutive round where an approved, fully green sidecar PR was overtaken by
  `dev` before it merged, costing a full re-review cycle each time. The packet
  content has not changed since `a58cf5d0`; only base-advance bookkeeping has.
  Two mitigations are worth a follow-up decision (neither is in this sidecar's
  scope): enable auto-merge on task PRs at approval time so the PR merges the
  moment it is green and up to date, or let ReviewBus run `update-branch` +
  merge as one step instead of leaving an approved PR to go stale.
- **Sidecar reviewer**: `Claude` — please check §2 against
  `git diff origin/dev...origin/task/ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001`
  and confirm §3 draws the implemented/residual line where you would draw it.
- **Parent action**: on approval, parent owner `Antigravity` (parent reviewer
  `Antigravity2`) decides whether to absorb this into
  `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001` closeout, and whether R1/R2
  become a follow-up task or an accepted-risk note.

### Changes from the rejected revision (2026-08-05 reopen)

Every point in the reopen note is addressed:

- (a) §2 now documents only the real change — the `current_branch()` probe swap.
  The fabricated `"HEAD"` guards in `branch_exists` / `branch_head_sha` /
  `remote_branch_exists` / `branch_has_diff` / `review_branch_for_task`, and the
  fabricated `branch == "HEAD"` check inside `current_branch()`, are removed and
  explicitly called out as *not* in the diff.
- (b) The three nonexistent tests are deleted. Only the two real tests remain.
- (c) The acceptance matrix is rebuilt: implemented behaviour (A1–A4) is
  separated from open residual risk (R1–R4), and `branch_exists("HEAD") == True`
  is recorded as still open rather than as a passing assertion.
- (d) §1 step 2 is corrected: `refs/heads/HEAD` does not resolve (rc=128); the
  real path is `refs/remotes/origin/HEAD`.
- (e) Header metadata refreshed (parent title, both owner/reviewer pairs). No
  "fully verified" claim: §4 lists the commands that were run with their output,
  and §4.5 lists what was not run and why.
