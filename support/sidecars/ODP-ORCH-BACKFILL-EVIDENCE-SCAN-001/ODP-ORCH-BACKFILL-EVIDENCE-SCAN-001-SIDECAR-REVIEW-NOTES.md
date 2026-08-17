# Sidecar Review Notes — Round 1: REOPEN

- **Task ID**: `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001`
- **Owner**: `Antigravity4`
- **Reviewer**: `Claude3`
- **Reviewed Commit**: `9d5aa728` (branch `task/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW`)
- **Reviewed Artifact**: `support/sidecars/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001-SIDECAR-REVIEW.md`
- **Decision**: **REOPEN** — return to `in_progress` for a round-2 rewrite.
- **Reviewed At**: 2026-08-07

---

## Verdict

The packet is well-formed as a document, but it reviews the **wrong deliverable**.
It describes the `origin/dev` *baseline* of `backfill_task_archive_snapshots.py`
— the version that predates the parent task — and not the change the parent task
actually delivered. Every substantive section (§1 root cause, §2 assessment,
§3 acceptance matrix, §4 recorded results) is therefore about a different task's
work, so the packet cannot support an acceptance decision for
`ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001`.

Acceptance criteria "create support artifacts only" and "do not edit canonical
truth" are both satisfied — the branch adds exactly one file under
`support/sidecars/` (`git diff --name-status origin/dev...HEAD`: 1 added file,
134 lines). The failure is content correctness, not scope.

---

## Ground Truth (verified 2026-08-07)

Parent task record in the live canonical `ai-status.json`:

| Field | Value |
|---|---|
| Title | Stop the merge-evidence search from missing real deliveries |
| Owner / Reviewer | `Claude3` / `Antigravity2` |
| Status | `review_approved` |
| `approved_head` | `b81f43223dd7bf930c4209875cf6b3f87274c64d` |
| `review_gate_sha` | `b81f43223dd7bf930c4209875cf6b3f87274c64d` |
| PR | #682 — `task/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001` → `dev`, **OPEN**, `BLOCKED` |
| Delivering commits | `66fd4300`, `b81f4322` |

Delta of the parent deliverable against `origin/dev`:

```
git diff --stat origin/dev...origin/task/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001
 scripts/orchestrator/backfill_task_archive_snapshots.py      | 155 ++++++--
 scripts/orchestrator/test_backfill_task_archive_snapshots.py | 223 +++++++++++
 2 files changed, 360 insertions(+), 18 deletions(-)
```

`b81f4322` is **not** an ancestor of `origin/dev`
(`git merge-base --is-ancestor b81f4322 origin/dev` → false), so nothing the
parent produced is on `dev` yet.

---

## Findings

### R1 — §2 assesses the pre-parent baseline, not the parent deliverable (blocking)

The packet reports `backfill_task_archive_snapshots.py` at **249 lines**,
`test_backfill_task_archive_snapshots.py` at **189 lines**, **11 tests**,
"438 lines" total. Those are the exact figures for the file as it stands on
`origin/dev` — i.e. the state *before* the parent task ran:

```
git show origin/dev:scripts/orchestrator/backfill_task_archive_snapshots.py | wc -l       # 249
git show origin/dev:scripts/orchestrator/test_backfill_task_archive_snapshots.py | wc -l  # 189
```

At the parent's `approved_head` the test file has **28** tests, not 11, and the
implementation gained `subject_delivers()` (line 142). The function list in §2
(`find_merge_evidence`, `find_repo_artifacts`, `build_snapshot`, `plan`, `main`)
is the baseline's function list with the parent's new function missing.

The baseline was introduced by `de58b7a4` (#608,
`ODP-PLAN-PARALLEL-KICKOFF-20260803`) — the only commit touching this file on
`dev` — and its snapshots carry
`created_by: "ODP-RUNBOOK-TASK-DEPENDENCY-GRAPH-REPAIR"`, which the packet quotes
in §1 without noticing it names a different task.

**Fix**: re-derive §2 from `origin/task/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001`
at `b81f4322`, describing the delta over `dev`, not the file's absolute contents.

### R2 — Parent Pin table is wrong in all three rows (blocking)

| Row | Packet says | Actual |
|---|---|---|
| Parent approved head | `46e64a53` | `b81f4322` |
| Parent merge-base with `dev` | `46e64a53` | parent branch merge-base is `dev` tip; `b81f4322` is **not** on `dev` |
| Landed on `dev` as | `de58b7a4` (#608, `ODP-PLAN-PARALLEL-KICKOFF-20260803`) | nothing has landed; PR #682 is still OPEN |

`46e64a53` is the merge commit of **PR #681**, which belongs to an unrelated
sidecar task (`ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001-SIDECAR-REVIEW`). It is the
`HEAD~1` of this very branch, which is the likely source of the mistake — the
pin was read from local git history instead of from the parent's `approved_head`.

**Fix**: pin from the parent task record in the live canonical `ai-status.json`
(`approved_head` / `review_gate_sha`), and state PR #682's real state
(OPEN / BLOCKED, not merged).

### R3 — §3 acceptance matrix covers none of the parent's changes (blocking)

A1–A11 name eleven tests that all exist verbatim on `origin/dev` and all predate
the parent task. The matrix has **zero** rows for what the parent actually
changed:

- `subject_delivers()` and the two accepted delivery forms
  (merge commit whose branch tail *equals* `task/<id>`; squash subject that
  starts with the id) — including the `reviewbus-subject` form;
- the merge-form boundary fix in `b81f4322` (`==` on the branch tail rather than
  the unbounded `f"task/{task_id}" in subject` substring), which `66fd4300` left
  open;
- multi-candidate scanning replacing the single-candidate-then-give-up search
  that produced the false negatives;
- the corpus measurement cited in `b81f4322` (origin/dev, 2204 commits, 307 ids
  with a real delivery → 0 false negatives, 11 misattributions removed,
  e.g. `ODP-CI-FLAKE-REMEDIATION-001` → #679 instead of its own #678, and
  `ODP-EXT` → #229 `task/ODP-EXT-003`);
- the negative case `66fd4300` calls out explicitly — a commit that merely
  *mentions* another task id must not archive it.

A12 ("code compiles, `ruff check` passes") is real but not acceptance evidence
for a behaviour fix.

**Fix**: build the matrix from the 17 tests added between `dev` and
`b81f4322`, and add at least one row per bullet above.

### R4 — §1 states the wrong root cause (blocking)

§1 describes the Control Pack 3.1 problem — nine tasks merged on 2026-07-28 with
no archive snapshot — which is the problem the *baseline tool* was built to
solve. The parent's root cause, per `66fd4300`, is different: `git log --grep`
matches the whole commit message, so the newest hit for a task id is frequently
an unrelated commit that merely cites it; the search took one candidate per
pattern and gave up, turning a citation into a false negative. Concrete misses:
`ODP-PLAN-AVM-OUTCOME-001` at `90bfcf6a` (#587) and `ODP-PLAN-GATE-REGISTRY-001`
at `a74aceb0` (#520).

The "Candidate Tasks Addressed (9 total)" list belongs to the baseline task and
should not appear in this packet at all.

### R5 — §4 recorded results measure the wrong run (blocking)

"11 passed in 0.56s" is the baseline suite. The parent's own reviewer
(`Antigravity2`) recorded **73 passed** for the approved change, and the parent
task summary records the live effect as the dependency graph going from **33
failures to 2**. The packet's live dry-run line ("9 tasks checked, 9 already
present, 0 written") is a no-op against the canonical archive and demonstrates
nothing about the parent's fix — a meaningful run must compare evidence
discovery *before and after* `subject_delivers()`.

### R6 — §5 handoff note restates status instead of framing a decision (non-blocking)

"Status Transition: Handoff … to `review` state" is bookkeeping the status tool
already records. What the parent owner needs from a `review_packet` sidecar is
the absorb/reject recommendation and any residual risk. Worth adding once R1–R5
are fixed.

---

## Required For Round 2

1. Re-pin against parent `approved_head` `b81f4322` and PR #682's real state.
2. Rewrite §1 to the parent's actual root cause (`git log --grep` whole-message
   matching → false negatives; unbounded merge-form match → misattribution).
3. Rewrite §2 as the delta `origin/dev...b81f4322` (+360 / −18, 2 files).
4. Rebuild §3 from the 17 tests the parent added; cover `subject_delivers()`,
   the branch-tail equality boundary, multi-candidate scanning, delivery-form
   return, and the mention-is-not-delivery negative case.
5. Re-record §4 from the parent's verification (73 passed) and, if a live run is
   included, make it a before/after comparison.
6. Add an explicit absorb recommendation and residual-risk note in §5.
7. Keep the change support-only under
   `support/sidecars/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001/`.

---

## Reviewer Verification Commands

```bash
# parent task record: approved_head, reviewer, status
python3 -c "import json;d=json.load(open('$PANTHEON_STATUS_ROOT/ai-status.json'));\
print([t for t in d['tasks'] if t['id']=='ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001'])"

# parent PR state
gh pr view 682 --json number,state,headRefName,mergeStateStatus,mergedAt

# parent deliverable is not on dev
git merge-base --is-ancestor b81f4322 origin/dev; echo $?     # -> 1

# baseline figures the packet reported
git show origin/dev:scripts/orchestrator/backfill_task_archive_snapshots.py | wc -l        # 249
git show origin/dev:scripts/orchestrator/test_backfill_task_archive_snapshots.py | wc -l   # 189
git show origin/dev:scripts/orchestrator/test_backfill_task_archive_snapshots.py \
  | grep -c '^def test'                                                                    # 11

# parent deliverable figures
git diff --stat origin/dev...origin/task/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001               # +360 -18
git show origin/task/ODP-ORCH-BACKFILL-EVIDENCE-SCAN-001:\
scripts/orchestrator/test_backfill_task_archive_snapshots.py | grep -c '^def test'         # 28

# sidecar scope check
git diff --name-status origin/dev...HEAD    # 1 added file under support/sidecars/
```
