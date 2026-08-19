# Review Packet: ODP-ORCH-DEPENDENCY-GRAPH-MIGRATION-001

- Sidecar task: `ODP-ORCH-DEPENDENCY-GRAPH-MIGRATION-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-DEPENDENCY-GRAPH-MIGRATION-001`
- Helper kind: `review_packet` (support material only; `mutates_canonical: false`)
- Sidecar owner: `Claude2`
- Sidecar reviewer: `Antigravity`
- Parent owner: `Antigravity` · Parent reviewer: `Codex2` (helper-claimed)
- Evidence captured: `2026-08-10` UTC
- Target / parent branch: `origin/dev` / `origin/task/ODP-ORCH-DEPENDENCY-GRAPH-MIGRATION-001`
- Key parent commit: `3a3717cdc56ef69dfef6125bab9ea74cb77fc736` (= parent `approved_head` = `review_gate_sha` = PR [#761](https://github.com/alfloop-dev/odayplus/pull/761) head)
- Parent status: `review_approved`, dispatched `owned_finalize_dispatch` — **not yet finalizable**, see § Parent PR Gate State.
- Scope of this sidecar: this packet file only. No L1 canonical truth, runtime, registry, or governance file touched.

---

## Executive Summary

Parent task `ODP-ORCH-DEPENDENCY-GRAPH-MIGRATION-001` delivers
`scripts/orchestrator/migrate_task_dependency_graph.py` plus its unit test module.
The script rewrites dangling `depends_on` ids in `ai-status.json` to their canonical
replacement, validates the **candidate** graph with the existing
`check_task_dependency_resolvability.py` **before** any persistence, and refuses to
write unless the supplied `--status` path is the canonical status file.

**Bottom line for the reviewer:** the approved head is independently reproducible —
86 orchestrator dependency tests pass, Ruff is clean, the live canonical graph reports
`0 failures`, and the one migrated edge resolves to a genuinely Human/Ops-gated task.
The three acceptance criteria are met. Both of `Codex2`'s prior blocking findings (F1,
F2) are genuinely closed in the diff, not merely asserted.

Two things the parent owner still has to handle: PR #761 is **green but not merged**
(it is sitting in the `dev` merge queue), and there are four residual risks below that
this sidecar found and that no prior review round recorded — none of them blocking the
approval, but R1 and R3 are worth a follow-up task.

---

## Reviewed Change Surface

Diff of `3a3717cd` against its merge base with `origin/dev`: **2 files, 370 insertions,
0 deletions.** No file outside `scripts/orchestrator/` is touched.

| File | Role | Summary |
| --- | --- | --- |
| `scripts/orchestrator/migrate_task_dependency_graph.py` | New migration tool (199 lines) | Explicit `DEPENDENCY_MIGRATION_MAP`, in-memory rewrite + dedupe, candidate-graph validation before persistence, canonical-status-path guard, sync through `ai_status.sync_all`. |
| `scripts/orchestrator/test_migrate_task_dependency_graph.py` | New test module (171 lines) | 6 tests: map rewrite/dedupe, dry-run no-write, status-root mismatch, invalid replacement, sync failure, successful dangling repair. |

The task's `artifacts` list also names `ai-status.json` and
`check_task_dependency_resolvability.py`. Neither is modified by the commit:
`check_task_dependency_resolvability.py` already exists on `dev` and is *consumed*, and
`ai-status.json` was changed by *running* the tool against the live board, not by the
PR. That is the correct split — the deliverable is a repeatable tool, and the data
change lives in the canonical status root.

Three commits on the branch: `eaf51254` (anchor migration script) → `f92f9c06`
(fix lint imports) → `3a3717cd` (validate candidate graph, the review-response commit).

---

## Acceptance Criteria — Verdict

| # | Criterion | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | Resolvability failures reduced to only validly-gated blocked tasks | **MET** | Live checker: `Task dependency resolvability: OK (55 task(s) scanned)`, exit `0`. Zero failures remain. |
| 2 | Every migration has traceable replacement or archive provenance | **MET, weakly** | Provenance exists but is an inline code comment, not an auditable record — see R3. |
| 3 | No incomplete work marked `done`; no gate removed | **MET** | See below. |

### On criterion 1 — attribution

The criterion's baseline is *49 failures*. The delivered `DEPENDENCY_MIGRATION_MAP`
contains exactly **one** entry. This sidecar could not find a `49 failures` record in
the live activity log, and the earliest in-log measurement for this task already reads
`55 tasks, 0 failures`. The honest reading: the 49-failure baseline predates this task
and the bulk of it was retired by other lanes (e.g. the dependency-remediation lane);
this task closed the **last remaining dangling edge** and, more importantly, delivered
the durable, tested tool and the pre-mutation guard that stop the class from
reappearing. The *outcome* the criterion demands is achieved; the *delta* attributable
to this commit is one edge plus the tooling. A reviewer should not read "49 fixed" into
this PR.

### On criterion 3 — no fake completion, no gate removal

Verified directly against the live board rather than taken on trust. The single migrated
edge points at `ODP-PLAN-OSS-LEGAL-POLICY-001`, whose live record is:

```
status: blocked
owner:  Human/Ops
```

So the rewrite moved a dependency from a non-existent id onto a task that is *still
blocked behind a real human gate*. Nothing was marked `done`, and the gate was not
dissolved — the dependent tasks stay correctly blocked, they are just now blocked for a
reason the graph can express. This is exactly what the acceptance language protects
against.

---

## Prior Review Findings — Closure Check

`Codex2` rejected `f92f9c06` with two blocking findings, then approved `3a3717cd`. This
sidecar re-checked both against the code rather than against the approval note.

**F1 — arbitrary `--status` could replace the canonical task list. CLOSED.**
`run_migration` now resolves both paths and refuses on mismatch before any write
(`migrate_task_dependency_graph.py:130-141`):

```python
canonical_status_path = ai_status.STATUS_FILE.resolve()
status_path_resolved = status_path.resolve()
if status_path_resolved != canonical_status_path:
    print("ERROR: status path mismatch: ...", file=sys.stderr)
    return 1
```

Covered by `test_run_migration_status_root_mismatch`, which also asserts the fixture file
is byte-identical afterwards. Confirmed working: a fixture path is rejected with
exit `1` and no write. Note the residual half in R2.

**F2 — write-before-validate plus raw-write fail-open. CLOSED.**
The resolvability check now runs on the candidate graph *before* the
`if not dry_run and count > 0:` block, and returns `1` on any failure. The
`except Exception` branch around `ai_status.sync_all` now returns `1` instead of falling
back to a raw `status_path.write_text`. `test_run_migration_sync_failure` asserts the
file is unchanged when `sync_all` raises. `test_run_migration_invalid_replacement`
asserts a bad map target is caught by the candidate check.

**F2 sub-point — `test_run_migration_fixes_dangling_graph` never called `run_migration`.
CLOSED.** The rewritten test calls `run_migration(..., dry_run=False)` under patched
`ai_status` and asserts `check()` returns `[]` on the re-read file.

---

## Verification Evidence (independently reproduced by this sidecar)

All commands run against a clean export of the approved head, not the owner's transcript:

```bash
git archive 3a3717cdc56ef69dfef6125bab9ea74cb77fc736 | tar -x -C /tmp/dgm-parent
```

### 1. New test module

```bash
cd /tmp/dgm-parent && python3 -m pytest scripts/orchestrator/test_migrate_task_dependency_graph.py -q
# -> 6 passed
```

### 2. Focused migration + checker suites

```bash
python3 -m pytest scripts/orchestrator/test_migrate_task_dependency_graph.py \
                  scripts/orchestrator/test_check_task_dependency_resolvability.py -q
# -> 18 passed
```

### 3. Full orchestrator dependency suite (the parent's `Verified:` trailer claim)

```bash
python3 -m pytest scripts/orchestrator/test_*.py -q
# -> 86 passed
```

Matches the commit's `Verified: ... (86 passed)` trailer exactly.

### 4. Lint

```bash
python3 -m ruff check scripts/orchestrator/migrate_task_dependency_graph.py \
                      scripts/orchestrator/test_migrate_task_dependency_graph.py
# -> All checks passed!
```

### 5. Live canonical graph (read-only, against `$PANTHEON_STATUS_ROOT`)

```bash
python3 scripts/orchestrator/check_task_dependency_resolvability.py \
  --status "$PANTHEON_STATUS_ROOT/ai-status.json" \
  --archive-dir "$PANTHEON_STATUS_ROOT/ai-task-archive/tasks"
# -> Task dependency resolvability: OK (55 task(s) scanned)   exit 0
```

### 6. Live migration dry-run (read-only; `--dry-run` never imports or calls `ai_status`)

```bash
python3 scripts/orchestrator/migrate_task_dependency_graph.py \
  --status "$PANTHEON_STATUS_ROOT/ai-status.json" \
  --archive-dir "$PANTHEON_STATUS_ROOT/ai-task-archive/tasks" --dry-run
# -> No dangling dependency migrations needed.
# -> Post-migration resolvability check: OK (55 tasks scanned, 0 failures)   exit 0
```

The migration is **idempotent and now a no-op** on the live board: the legacy id
`ODP-PLAN-OSS-LICENSE-GATE-001` no longer appears in any `depends_on`, confirming the
mapping was already applied to canonical data.

### 7. Whitespace

```bash
git diff --check $(git merge-base origin/dev 3a3717cd) 3a3717cd
# -> migrate_task_dependency_graph.py:199: new blank line at EOF.
# -> test_migrate_task_dependency_graph.py:171: new blank line at EOF.
```

Reproduces `Codex2`'s non-blocking hygiene note exactly. Cosmetic; Ruff does not flag it.

### Note on a counting discrepancy

An earlier run in this same session reported `56 task(s) scanned`, a later one `55`. This
is live-board churn between the two reads (a task reached a terminal state in between),
not a disagreement between the checker and the migration tool — both report `55` when run
back to back. Recorded here so the reviewer is not misled by the differing numbers in the
parent's own history.

---

## Residual Risks (found by this sidecar; none blocks the approval)

### R1 — Lost-update window when the migration actually writes  *(highest value follow-up)*

`run_migration` reads the task list from `status_path` at the top of the function, mutates
that snapshot, and then — much later, after the resolvability check — calls
`ai_status.load_state()` and does `state["tasks"] = tasks`, overwriting the freshly loaded
task list with the **earlier snapshot**. Any task transition written by another agent
between the two reads is silently discarded.

This matters because `scripts/ai_status.py` has **no locking**: `load_state()` is a plain
`json.loads(STATUS_FILE.read_text())` and `save_state()` is an atomic `os.replace` of a
whole-file serialization. Atomic replace prevents a torn file; it does not prevent a lost
update. On a board written continuously by the whole fleet, the window is real.

Mitigating factors: the write path only runs when `count > 0`, and after this migration
`count` is `0` on the live board, so the risky branch is currently unreachable in practice.
Suggested fix for a follow-up: re-read inside the same critical section — load state
*first*, migrate `state["tasks"]` in place, validate, then sync — so there is exactly one
read of canonical truth.

### R2 — The canonical-path guard binds data, not code

F1 is closed for the *data* target, but note precisely what the guard compares.
`ai_status.STATUS_FILE` derives from `ORCH_STATUS_ROOT` / `PANTHEON_STATUS_ROOT`
(`ai_status.py:29-50, 79`), i.e. from the **environment**, while the `ai_status` module
itself is imported from the script's own checkout (`ROOT/scripts`). Consequence: running
this script from a *stale worktree* passes the guard — `--status` and `STATUS_FILE` both
resolve to the live data root — and then writes live canonical state using that stale
checkout's `sync_all`. This is the same hazard the worker wakeup prompts warn about
("do not run `scripts/ai_status.py` from an isolated worktree").

Not a defect introduced by this PR, and not something the PR claimed to solve. Operational
guidance for whoever runs the tool: **run it from the live canonical checkout only.**
Worth one line in an operator note.

### R3 — Migration provenance is a code comment, not an auditable record

Acceptance criterion 2 requires traceable provenance. What exists is the comment above
`DEPENDENCY_MIGRATION_MAP`:

> `ODP-PLAN-OSS-LICENSE-GATE-001 -> ODP-PLAN-OSS-LEGAL-POLICY-001`: Legacy OSS license
> gate task was merged/restructured into canonical legal policy task
> `ODP-PLAN-OSS-LEGAL-POLICY-001`.

This sidecar checked for a stronger record and did not find one. `ODP-PLAN-OSS-LICENSE-GATE-001`
has **no snapshot** in `ai-task-archive/tasks/` (the only OSS-adjacent archive entry is
`ODP-PLAN-DEFERRED-OSS-ADR-001.json`), and — because the rewrite has already been applied
— the legacy id now survives on the live board only inside one checkpoint message. So the
entire audit trail for that rewrite is: this code comment, plus one activity-log line.

The claim is plausible and the target is correct, but a future auditor asking "who decided
these two tasks are the same task?" has no record to follow. Recommend the parent lane
either add an archive stub with `terminal_status` provenance for the retired id, or cite an
ADR / decision record in the comment.

### R4 — Unhandled crash on a malformed task entry

`migrate_dependencies` calls `task.get("depends_on")` without a type guard, and
`run_migration` builds its board with `t.get("id")` likewise. The checker it delegates to
is defensive here (`load_board` skips `if not isinstance(entry, dict)`), so the two
disagree. Reproduced:

```bash
# tasks: ["NOT-A-DICT", {"id": "TASK_A", "depends_on": []}]
migrate_task_dependency_graph.py ... --dry-run
# -> AttributeError: 'str' object has no attribute 'get'   (traceback, exit 1)
check_task_dependency_resolvability.py ...
# -> Task dependency resolvability: OK (1 task(s) scanned)  exit 0
```

Low severity — the canonical file is schema-controlled and this fails closed with a
non-zero exit before any write. Worth an `isinstance` guard for parity with the checker.

### R5 — `--status` default degrades silently when the env is unset

```python
default=os.environ.get("ODP_SUPERVISOR_STATUS_FILE")
        or os.environ.get("PANTHEON_STATUS_ROOT", "") + "/ai-status.json"
```

With neither variable set this becomes the literal path `/ai-status.json`. It fails closed
(`ERROR: status file not found`), so the behaviour is safe, just cryptic. Cosmetic.

---

## Safety & Boundary Compliance

1. **No L1 canonical mutation by the parent PR.** The diff is confined to two new files
   under `scripts/orchestrator/`. No architecture doc, contract, governance rule, registry,
   or supervisor file is touched.
2. **Fail-closed persistence.** After `3a3717cd` there is exactly one write path
   (`ai_status.sync_all`), reached only when the candidate graph validates *and* the target
   is the canonical status file. Every failure branch returns `1` without mutating disk —
   asserted by three separate tests.
3. **Gate integrity preserved.** The migration rewrites dependency *identity*, never
   dependency *satisfaction*. The checker still requires an archived dependency to carry
   `terminal_status == "done"`, so a rewrite cannot be used to unblock a task.
4. **This sidecar mutated nothing.** All live-board interaction was read-only: the checker,
   and the migration under `--dry-run` (which returns before the `import ai_status` inside
   the write branch). Test execution happened in `/tmp/dgm-parent`, a `git archive` export,
   never in a checkout wired to the live status root.

---

## Parent PR Gate State (blocking finalization)

PR [#761](https://github.com/alfloop-dev/odayplus/pull/761) — head `3a3717cd`, base `dev`,
not draft, **all five required checks green**:

| Check | Conclusion |
| --- | --- |
| `orchestrator` | SUCCESS |
| `product` | SUCCESS |
| `performance-gate` | SUCCESS |
| `product-e2e-gate` | SUCCESS |
| `task-review-gate` | SUCCESS |

The PR is nonetheless **still open**, and:

```bash
git merge-base --is-ancestor 3a3717cd origin/dev   # -> NOT yet in dev
```

The reason is the merge queue, not a defect. Auto-merge was armed (activity log:
"Auto-merge is enabled on PR #761 into `dev`"), which **enqueues** the PR — after which
`autoMergeRequest` reads back as `null` and `mergeStateStatus` flickers to `UNKNOWN`. The
queue ref is live on origin:

```
refs/heads/gh-readonly-queue/dev/pr-761-532bec33b8185369e27afc5033ffddf89bbaf360
```

and it is stacked on top of `refs/heads/gh-readonly-queue/dev/pr-732-...`, so #761 merges
after #732 clears.

**Do not** read `autoMergeRequest: null` as "auto-merge fell off", and **do not**
base-advance the branch — `approved_head`, `last_approved_head`, and the PR head are all
`3a3717cd`, the freeze is intact, and advancing the base would invalidate it. Per
`.orchestrator/skills/task-closeout-finalization.md`, an open PR — even fully green and
enqueued — is not sufficient for `done`. The parent stays `review_approved` until the
queue lands it on `dev`.

---

## Reviewer Handoff

- **Sole artifact:** `support/sidecars/ODP-ORCH-DEPENDENCY-GRAPH-MIGRATION-001/ODP-ORCH-DEPENDENCY-GRAPH-MIGRATION-001-SIDECAR-REVIEW.md`.
- **Recommendation:** the parent approval at `3a3717cd` is sound and this sidecar
  reproduces every claim in it. No re-review of the parent is warranted.
- **Handoff:** to sidecar reviewer `Antigravity` via the canonical status utility.

### Open items for the parent owner (`Antigravity`) — outside this sidecar's authority

1. Wait for the merge queue to land #761 on `dev`, then run `done`. Nothing to fix; the
   queue is simply working through #732 first.
2. Consider a small follow-up task for **R1** (single-read critical section) and **R3**
   (durable provenance record for the retired id). R1 is currently unreachable because the
   map is a no-op on the live board, which makes this a cheap fix to land before the map
   ever grows a second entry.
3. **R2** is an operating constraint rather than a code fix: run
   `migrate_task_dependency_graph.py` from the live canonical checkout only, never from a
   worker worktree.
