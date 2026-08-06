# Sidecar Review Packet: ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001-SIDECAR-REVIEW

- **Task ID**: `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001`
- **Parent Title**: Resolve detached-HEAD branch resolution for ReviewBus
- **Parent Owner / Parent Reviewer**: `Antigravity` / `Antigravity6`
- **Helper Kind**: `review_packet`
- **Sidecar Owner**: `Claude3`
- **Sidecar Reviewer**: `Claude`
- **Phase**: Orchestrator reliability
- **Parent head reviewed by this packet**: `6968de59`
- **Last Updated**: 2026-08-06 (§1–§4 re-derived against `6968de59`; prior round preserved as §6)

---

## Executive Summary

This support sidecar is a review packet for parent task
`ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001`. It describes the parent diff as
it actually exists, records the verification commands that were run and their
observed output, and lists the residual risk the parent diff does not cover.

**Head under review: `6968de59`.** This is the parent's `approved_head` and
`review_gate_sha` in `ai-status.json`, and also the current tip of
`origin/task/ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001`. Earlier revisions of
this packet reviewed `d32a73d2`; commit `1a8783c8` landed after that and roughly
doubled the diff. Everything in §1–§4 below is re-derived against `6968de59`.
The superseded `d32a73d2` round is retained verbatim in §6 and its findings are
time-scoped to that head — they are **not** standing claims.

The parent diff is **two layers**: the source fix in `current_branch()`, plus a
defence-in-depth pass that makes five neighbouring functions fail closed on
`HEAD`-shaped names. Five regression tests cover it.

Sidecar scope: support artifacts only. No canonical L1 truth, contract, runtime,
registry, or governance file is modified by this task.

### Parent diff under review

```
$ git diff --stat origin/dev...6968de59
 .orchestrator/github_bus.py      | 30 ++++++++++++++++-----
 .orchestrator/test_github_bus.py | 58 ++++++++++++++++++++++++++++++++++++++++
 2 files changed, 81 insertions(+), 7 deletions(-)
```

Two substantive commits, both 2026-08-04; the remaining six are `dev` merges:

```
$ git log --oneline origin/dev..6968de59
6968de59 Merge remote-tracking branch 'origin/dev' into task/...
e310325c Merge remote-tracking branch 'origin/dev' into task/...
a5d71c10 Merge remote-tracking branch 'origin/dev' into task/...
1a8783c8 ODP-...-001: anchor reviewbus branch resolution detached head safety
d32a73d2 Merge branch 'dev' into task/...
0cd6c8d9 Merge branch 'dev' into task/...
775b60e2 Merge remote-tracking branch 'origin/dev' into task/...
d94bc547 ODP-...-001: stop reporting "HEAD" as a branch name
```

---

## 1. Defect Analysis & Root Cause

### The primitive

In a detached checkout, `git rev-parse --abbrev-ref HEAD` prints the literal
string `HEAD` and exits `0`. It reports a branch name for a state that has no
branch.

### Root cause chain (as it stood on `origin/dev`, pre-fix)

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

   `branch_exists()` on `origin/dev` tries `refs/heads/<branch>` first (fails),
   then `refs/remotes/origin/<branch>` — and `refs/remotes/origin/HEAD` is the
   remote's default-branch symref, which exists in any normally cloned repo.
   That second probe is what makes the false claim validate.

3. **Resulting exposure.** With `"HEAD"` accepted as a branch name, ReviewBus
   records a non-branch as a task's review branch and cannot match or create the
   real task PR. The live consequence is documented in
   `scripts/orchestrator/check_runtime_freshness.py`: on 2026-08-04 the
   supervisor checkout was detached, `current_branch()` reported `"HEAD"`,
   ReviewBus recorded it as a branch name and skipped PR creation, stranding
   finished tasks with no pull request.

Step 2 is reproducible against pre-fix source; see §4.4.

### Scope note on how wide the pre-fix hole actually was

In `origin/dev` the last fallback in `review_branch_for_task()` is:

```python
branch = current_branch()
if branch and branch != default_branch(config) and (not task_id or task_id_matches_branch(task_id, branch)) and branch_exists(branch):
    return branch
```

`task_id_matches_branch(<any real task id>, "HEAD")` is `False`, so for a task
with a non-empty id this fallback already rejected `"HEAD"` even before the
parent fix. The remaining pre-fix hole on that path was the `not task_id`
disjunct (empty/missing task id). The broader 2026-08-04 exposure predates the
`task_id_matches_branch` guard, which was introduced 2026-08-02 by
`d583b26a` (ODP-ORCH-TASK-PR-DISCOVERY-001).

This does not weaken the parent fix — `current_branch()` returning a string that
is not a branch is wrong at the source regardless of how many downstream guards
happen to catch it — but the packet states it so the reviewer is not left
believing the fix closed a currently-wide hole. It also explains why one of the
five new tests passes pre-fix (§2, test table note).

---

## 2. Parent Implementation Assessment

### Layer 1 — the source fix (`current_branch()`)

```diff
 def current_branch() -> str | None:
-    proc = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT)
+    # `rev-parse --abbrev-ref HEAD` answers the literal string "HEAD" on a
+    # detached checkout, and every downstream guard lets it through: ...
+    proc = run_command(["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=ROOT)
     if proc.returncode != 0:
         return None
     branch = (proc.stdout or "").strip()
-    return branch or None
+    if not branch or branch == "HEAD":
+        return None
+    return branch
```

`git symbolic-ref --short -q HEAD` exits non-zero on a detached HEAD, so the
existing `returncode != 0` arm already produces `None`. The explicit
`branch == "HEAD"` check is belt-and-braces: `symbolic-ref` cannot return the
literal `HEAD`, so this arm is unreachable via the real command and only fires
under mocking. Harmless, and it does document the invariant.

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
 return None      return None if stdout in ("", "HEAD") else stdout.strip()
```

### Layer 2 — defence in depth (added by `1a8783c8`)

Five further functions now reject `HEAD`-shaped names. This is the part the
`d32a73d2`-era packet described as absent; it is present at `6968de59`.

| Function | Guard added | Effect |
|---|---|---|
| `branch_exists` | `if not branch or branch == "HEAD" or branch.endswith("/HEAD"): return False` | Kills the `refs/remotes/origin/HEAD` probe path from §1 step 2 |
| `branch_head_sha` | same predicate → `return None` | No plausible-looking SHA for a non-branch |
| `remote_branch_exists` | same predicate → `return False` | |
| `branch_has_diff` | `if not base or not branch or base == "HEAD" or branch == "HEAD": return False` | Note: bare literal only, no `/HEAD` suffix arm — see R5 |
| `review_branch_for_task` | four inline `!= "HEAD"` checks: on `explicit`, on the agent-record `branch`, on each `candidate`, and on the `current_branch()` fallback | Rejects `"HEAD"` on every one of the four resolution paths, including the `not task_id` disjunct |

The `review_branch_for_task` guards are inline string comparisons repeated at
four sites rather than one shared predicate. That is a maintainability
observation, not a defect — see review observation 3.

### Test coverage added (`.orchestrator/test_github_bus.py`)

**Five** new tests, across **two** classes. Method count goes 30 on `origin/dev`
to 35 at `6968de59`.

| Test | Class | Style | Fails pre-fix? |
|---|---|---|---|
| `test_current_branch_returns_none_when_detached_head` | `TaskPRDiscoveryTests` | mock (`run_command` patched) | **yes** |
| `test_branch_exists_returns_false_for_head` | `TaskPRDiscoveryTests` | direct call, no mock | **yes** (asserts `"HEAD"` and `"origin/HEAD"`) |
| `test_review_branch_for_task_rejects_head_branch_name` | `TaskPRDiscoveryTests` | mock (`current_branch` patched) | no |
| `test_detached_head_yields_no_branch` | `DetachedHeadBranchResolutionTests` | real temp git repo, `ROOT` patched | **yes** |
| `test_named_branch_is_still_returned` | `DetachedHeadBranchResolutionTests` | real temp git repo, `ROOT` patched | no |

Three of five are mock-based, contrary to the `d32a73d2`-era claim that "no
mock-based tests were added".

Two tests pass on unfixed source, for different and both-legitimate reasons:

- `test_named_branch_is_still_returned` is the no-regression half of the pair —
  passing either way is the point.
- `test_review_branch_for_task_rejects_head_branch_name` passes pre-fix because
  it uses `task["id"] = "ODP-FOO-001"`, and `task_id_matches_branch` already
  rejects `"HEAD"` for any real task id (§1 scope note). It pins the *intended
  contract* but does not pin *this* diff. A variant with `task = {"id": ""}`
  would exercise the `not task_id` disjunct that the new guards actually close.
  Non-blocking; worth noting if the parent owner touches the file again.

### Review observations on the parent diff

1. **Regression value is confirmed, not assumed.** Reverting only
   `.orchestrator/github_bus.py` to `origin/dev` and rerunning the suite gives
   3 failures out of 35 (commands and output in §4.2). The tests genuinely pin
   the bug.

2. **Style nit — missing blank lines between classes (still open at
   `6968de59`).** `test_named_branch_is_still_returned` ends at
   `test_github_bus.py:793` and `class TaskPRBaseBranchTests` begins immediately
   at line 794 with no separating blank lines, unlike every other class boundary
   in the file (PEP 8 wants two). Cosmetic, non-blocking; worth a one-line
   fixup if the parent owner pushes again. `ruff` is not installed in this
   environment, so this was found by inspection, not by lint.

3. **Guard predicate is duplicated, not centralized.** `branch_exists`,
   `branch_head_sha`, and `remote_branch_exists` each carry an identical
   `not branch or branch == "HEAD" or branch.endswith("/HEAD")` line, and
   `review_branch_for_task` carries four more inline `!= "HEAD"` comparisons —
   eight guard sites in one file with no shared helper. `branch_has_diff` has
   already drifted from the other three (R5), which is exactly the failure mode
   duplication invites. A one-line `def _is_head_ref(name) -> bool` would make
   the invariant single-sourced. Non-blocking.

4. **Comment duplication.** The same explanation appears in the
   `current_branch()` comment and again in the `DetachedHeadBranchResolutionTests`
   docstring. Acceptable — the test docstring is the one a future reader hits
   first.

5. **Adjacent surfaces checked, not affected.** `scripts/ai_status.py:1851`
   (`task_delivery_checkout`) still uses `rev-parse --abbrev-ref HEAD`, but it
   only compares the result against an explicit `["task/<id>", "task-<id>"]`
   list, so `"HEAD"` cannot be mistaken for a task branch there; it falls
   through to `git worktree list`. `scripts/orchestrator/check_runtime_freshness.py:59`
   uses `--abbrev-ref` deliberately, because it needs to *detect* the `"HEAD"`
   sentinel. Neither needs to change with this parent diff.

---

## 3. Acceptance Matrix

All rows re-derived against `6968de59`.

### Implemented and covered

| Ref | Acceptance rule | Evidence | Result |
|---|---|---|---|
| **A1** | `current_branch()` returns `None` when HEAD is detached | `test_detached_head_yields_no_branch`, `test_current_branch_returns_none_when_detached_head`; plus live probe in a detached worktree (§4.3) | PASS |
| **A2** | Named-branch resolution is unchanged | `test_named_branch_is_still_returned` | PASS |
| **A3** | The new tests actually pin the defect | Pre-fix rerun: 3 failures / 35, incl. `AssertionError: 'HEAD' is not None` (§4.2) | PASS |
| **A4** | No collateral regression in the bus suite | `python3 -m unittest` at `6968de59` → `Ran 35 tests … OK` (§4.1) | PASS |
| **A5** | `branch_exists` / `branch_head_sha` / `remote_branch_exists` fail closed on `HEAD` and `*/HEAD` | `test_branch_exists_returns_false_for_head`; live probe (§4.3) returns `False`/`None` for both spellings | PASS |
| **A6** | `review_branch_for_task` rejects `"HEAD"` on all four resolution paths | Source inspection (§2 layer-2 table) + `test_review_branch_for_task_rejects_head_branch_name` | PASS (test does not isolate the `not task_id` path — §2 test-table note) |

### Residual risk

R1–R4 below were recorded as **Open** against `d32a73d2`. At `6968de59` they are
**closed by `1a8783c8`**, verified by live probe. They are kept here so the
reviewer can see the delta rather than having to diff two packet revisions.

| Ref | Residual risk (as filed at `d32a73d2`) | Status at `6968de59` | Evidence |
|---|---|---|---|
| **R1** | `branch_exists("HEAD")` returns `True` via the `refs/remotes/origin/HEAD` probe | **Closed** — returns `False`; `"origin/HEAD"` also `False` | §4.3 |
| **R2** | `branch_head_sha("HEAD")` returns the origin default-branch SHA instead of `None` | **Closed** — returns `None` | §4.3 |
| **R3** | `branch_has_diff()` and `remote_branch_exists()` have no `"HEAD"` guard | **Closed for the bare literal** — both return `False`. Partially superseded by R5 | §4.3 |
| **R4** | `review_branch_for_task()`'s `not task_id` disjunct would accept `"HEAD"` | **Closed** — explicit `!= "HEAD"` guard now precedes the disjunct on all four paths | §2 layer-2 table |

**Open at `6968de59`:**

| Ref | Residual risk | Observed | Disposition |
|---|---|---|---|
| **R5** | `branch_has_diff()` guards only the bare literal `"HEAD"`; it lacks the `.endswith("/HEAD")` arm that `branch_exists`, `branch_head_sha`, and `remote_branch_exists` all have | `branch_has_diff("dev", "HEAD") == False` but `branch_has_diff("dev", "origin/HEAD") == True` (§4.3) | Open, low severity. Its one in-repo caller (`github_bus.py:862`) passes a branch already validated by `branch_exists`, which rejects `*/HEAD` — so the inconsistency is not reachable today. It is a latent trap for the next caller. Fix is one clause, or the shared helper from §2 observation 3. |

**Recommendation to the parent owner and parent reviewer:** the R1/R2 follow-up
that the previous packet revision proposed is **already implemented** at
`6968de59` — do not re-open it. The only live item is R5, plus the two
non-blocking nits in §2 (blank lines at `test_github_bus.py:794`; duplicated
guard predicate). None of these blocks parent closeout; all three are one-line
fixups if the parent owner pushes to this branch again for another reason.

---

## 4. Verification — commands run and observed output

All commands below were run on 2026-08-06 by `Claude3`. Parent branch tests were
run in a throwaway worktree pinned at `6968de59`, created with
`git worktree add --detach /tmp/odp-parent-verify2 6968de59` and removed
afterwards. Nothing outside that worktree was modified for verification; the
worktree was confirmed clean (`git status --short` empty) after every
revert/restore cycle.

Note on the runner: `pytest -q` in this environment prints the progress dots but
**not** the trailing `N passed` summary line. The earlier packet revision's
"32 passed" was inferred from counting dots. Counts below come from
`python3 -m unittest`, which reports them explicitly.

### 4.1 Full bus suite at the approved head

```
$ git rev-parse HEAD
6968de59370a3ffe094492d55a01d80f90e76f80

$ python3 -m pytest -q .orchestrator/test_github_bus.py
...................................                                      [100%]

$ python3 -m unittest discover -s .orchestrator -p "test_github_bus.py"
Ran 35 tests in 1.748s

OK
```

35 tests, all passing — matching the parent approval note's "35/35".

Method count delta:

```
$ git show origin/dev:.orchestrator/test_github_bus.py | grep -c "    def test_"
30
$ grep -c "    def test_" .orchestrator/test_github_bus.py
35
```

### 4.2 Regression check — do the new tests fail without the fix?

```
$ git checkout origin/dev -- .orchestrator/github_bus.py    # revert only the fix

$ python3 -m unittest discover -s .orchestrator -p "test_github_bus.py"
Ran 35 tests in 1.823s

FAILED (failures=3)

$ python3 -m pytest -q .orchestrator/test_github_bus.py
>               self.assertIsNone(github_bus.current_branch())
E               AssertionError: 'HEAD' is not None
.orchestrator/test_github_bus.py:780: AssertionError
FAILED .orchestrator/test_github_bus.py::TaskPRDiscoveryTests::test_branch_exists_returns_false_for_head
FAILED .orchestrator/test_github_bus.py::TaskPRDiscoveryTests::test_current_branch_returns_none_when_detached_head
FAILED .orchestrator/test_github_bus.py::DetachedHeadBranchResolutionTests::test_detached_head_yields_no_branch

$ git checkout HEAD -- .orchestrator/github_bus.py          # restore
$ git status --short                                        # empty
```

`test_named_branch_is_still_returned` and
`test_review_branch_for_task_rejects_head_branch_name` pass either way; see the
§2 test-table note for why each does.

### 4.3 Live probe of the module, from a genuinely detached worktree

Run inside `/tmp/odp-parent-verify2`, which is itself on a detached HEAD, so this
exercises the real condition rather than a synthesized one.

**Fixed source at `6968de59`:**

```
$ python3 -c "import sys; sys.path.insert(0,'.orchestrator'); import github_bus; ..."
branch_exists("HEAD")            = False
branch_exists("origin/HEAD")     = False
branch_head_sha("HEAD")          = None
branch_head_sha("origin/HEAD")   = None
remote_branch_exists("HEAD")     = False
remote_branch_exists("origin/HEAD") = False
branch_has_diff("dev", "HEAD")   = False
branch_has_diff("dev", "origin/HEAD") = True      # <-- R5
current_branch()                 = None
```

**Same probe with `github_bus.py` reverted to `origin/dev`:**

```
branch_exists("HEAD")   = True
branch_head_sha("HEAD") = 84029065b42aac28c93aba47d0157e006852a265
current_branch()        = HEAD
```

The pairing is the most useful evidence in the packet: it reproduces the defect
on unfixed source and shows every probe failing closed on fixed source, with the
single `origin/HEAD` asymmetry (R5) visible in the same output.

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
  the parent diff touches `github_bus.py` only, and the changed functions' only
  in-repo callers are inside that same module.

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
- **This round (2026-08-06 reopen at `07d8507c`)**: not a base advance. The
  reviewer confirmed scope and hygiene were fine and blocked on **content**: the
  packet still reviewed `d32a73d2` while the parent had advanced to
  `6968de59` and reached `review_approved`. §1–§4 are now re-derived against
  `6968de59`; see "Changes in this revision" below.
- **Known loop, for the parent owner / orchestrator**: rounds 2 and 3 above were
  consecutive cases where an approved, fully green sidecar PR was overtaken by
  `dev` before it merged, costing a full re-review cycle each time. Two
  mitigations are worth a follow-up decision (neither is in this sidecar's
  scope): enable auto-merge on task PRs at approval time so the PR merges the
  moment it is green and up to date, or let ReviewBus run `update-branch` +
  merge as one step instead of leaving an approved PR to go stale. Note this
  round's reopen shows a second cost of the loop: each forced re-review is also
  a window in which the *parent* can move underneath the packet.
- **Sidecar reviewer**: `Claude` — please check §2 against
  `git diff origin/dev...6968de59 -- .orchestrator/github_bus.py .orchestrator/test_github_bus.py`
  and confirm §3 draws the implemented/residual line where you would draw it.
  R5 and the two §2 nits are the only claims that assert something is still
  open; everything else in §3 asserts closure.
- **Parent action**: the parent is already `review_approved` at `6968de59`, so
  this packet is a record and a residual-risk list, not a gate. On approval,
  parent owner `Antigravity` (parent reviewer `Antigravity6`) decides whether to
  absorb it into `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001` closeout, and
  whether R5 becomes a follow-up task or an accepted-risk note. **R1/R2 need no
  follow-up — they are already fixed at the approved head.**

### Changes in this revision (2026-08-06 reopen)

Every point in the reopen note is addressed:

- (1) Executive Summary now quotes the real stat at `6968de59` — 2 files, 81
  insertions, 7 deletions — with the commit list showing `1a8783c8`.
- (2) §2's "no other function is modified" claim is deleted. The layer-2 table
  documents all six functions that `1a8783c8` touched, with the exact guard
  predicate each one uses.
- (3) §2's test table now lists all five tests across both classes, marks which
  three are mock-based, records the 30→35 method-count delta, and explains why
  two of the five pass pre-fix.
- (4) §3's R1–R4 are marked **Closed at `6968de59`** with live-probe evidence,
  and the "open a follow-up for R1/R2" recommendation is withdrawn as already
  implemented. §4.3 now shows both the fixed and the pre-fix probe output. A new
  R5 records the one guard asymmetry that genuinely remains open.
- (5) Parent reviewer corrected to `Antigravity6` in the header and in §5, per
  `ai-status.json`.
- Kept as instructed: the §2 blank-line style nit (re-verified: still holds at
  `test_github_bus.py:794`), and the §5 base-advance history.
- The superseded `d32a73d2` round is preserved as §6, explicitly time-scoped.

---

## 6. Superseded round record — packet as reviewed at `d32a73d2`

**This section is a historical record, not a set of standing claims.** It
describes the parent branch as it stood at `d32a73d2`, before `1a8783c8` landed.
Every finding in it has been superseded by §2–§4 above. It is retained so the
parent owner can see what changed between rounds without diffing packet
revisions.

At `d32a73d2` the parent diff was:

```
 .orchestrator/github_bus.py      |  8 +++++++-
 .orchestrator/test_github_bus.py | 35 +++++++++++++++++++++++++++++++++++
 2 files changed, 42 insertions(+), 1 deletion(-)
```

— one behaviour change (the `current_branch()` probe swap) plus two
real-subprocess tests in `DetachedHeadBranchResolutionTests`
(`test_detached_head_yields_no_branch`, `test_named_branch_is_still_returned`).
The suite had 32 tests. Against **that** head the packet correctly recorded:

- No `"HEAD"` guards existed in `branch_exists`, `branch_head_sha`,
  `remote_branch_exists`, `branch_has_diff`, or `review_branch_for_task`, and no
  `branch == "HEAD"` check existed inside `current_branch()`.
- `branch_exists("HEAD") == True` and
  `branch_head_sha("HEAD") == 84029065b42aac28c93aba47d0157e006852a265`
  (filed as R1/R2, Open), with a recommendation to either accept them as out of
  scope or open a follow-up making both fail closed on `"HEAD"` and `*/HEAD`.

`1a8783c8` is that follow-up, implemented on the same branch: it added the
`*/HEAD` guards to `branch_exists`/`branch_head_sha`/`remote_branch_exists`, a
base/branch guard to `branch_has_diff`, four `!= "HEAD"` checks to
`review_branch_for_task`, the `branch == "HEAD"` check inside `current_branch()`,
and three further tests. The `d32a73d2`-era R1–R4 are therefore closed at
`6968de59`, as recorded in §3.
