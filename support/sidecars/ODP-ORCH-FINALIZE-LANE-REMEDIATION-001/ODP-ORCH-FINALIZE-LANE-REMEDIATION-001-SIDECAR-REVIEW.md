# Sidecar Review Packet: ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW

- **Task ID**: `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001`
- **Helper Kind**: `review_packet`
- **Owner**: `Claude2`
- **Reviewer**: `Claude3`
- **Status**: `review`
- **Packet Revision**: round 6 (2026-08-06) — second base advance + round-5 note N5 closed
- **Target Artifact**: `support/sidecars/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW.md`

### Parent Pin (read this before trusting any number below)

| Field | Value |
|---|---|
| Parent approved head | `f16593c7` |
| Parent merge-base with `dev` at that head | `c879004a` |
| Landed on `dev` as | `bc7366d3` — *[ReviewBus] ODP-ORCH-FINALIZE-LANE-REMEDIATION-001 … (#622)* |
| `scripts/orchestrator/` at `f16593c7` vs `bc7366d3` | identical (`git diff f16593c7 bc7366d3 -- scripts/orchestrator/` is empty) |
| `scripts/orchestrator/` at `f16593c7` vs this branch head | identical (re-checked at round 6 after the `85d60609` base advance; round 5 checked it at `a7fde1a8`) |
| Deliverable surface | 4 files, 1043 lines (see §2) |

> [!IMPORTANT]
> Every claim in this packet is derived from parent commit **`f16593c7`**, which
> is byte-identical to what landed on `dev` at `bc7366d3`. If the parent advances
> past `f16593c7`, re-derive §1–§4 before reusing this packet — earlier revisions
> of this document went stale exactly that way (rounds 1–3, recorded in
> `…-SIDECAR-REVIEW-FINDINGS.md`).

---

## Core Notice & Scope Boundary

> [!NOTE]
> This sidecar task is support-only. It creates only support artifacts
> (`support/sidecars/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001/*`) without modifying
> canonical L1 documents, core runtime contracts, or primary orchestrator
> implementations.

---

## 1. Defect Analysis & Root Cause

### Background & Problem Statement

In the Pantheon Orchestrator architecture, the Supervisor's finalize step acts
purely as an observer. When inspecting tasks in `review_approved` (or agents in
`finalize` status), if the CI status probe returns failure or non-green status:

- The supervisor writes a log line (`supervisor.py:10919` →
  `"resolve failing checks before finalization."`; `supervisor.py:10825/10939` →
  `"finalize dispatch suppressed"`) and `continue`s.
- There is no code path in `.orchestrator` that re-triggers CI workflows
  (`gh run rerun` / `workflow_dispatch`) or advances a stale task branch onto
  `dev`.
- A related failure source is the ReviewBus detached-HEAD bug
  (`github_bus.py:782/810` → `state="skipped_unpublished_branch"`), which leaves
  a pushed branch with no PR at all.

Consequently, tasks that are finished **and reviewed** can sit in
`review_approved` indefinitely. `finalize_lane_doctor.py`'s module docstring
records nine such tasks on 2026-08-04.

### Two Tools, Two Taxonomies — Do Not Merge Them

The parent ships **two independent diagnostics with different category
vocabularies**. They are not aliases of one another, and neither is a superset of
the other. Earlier revisions of this packet presented a single merged five-item
list with slash-aliases; that list was wrong for both tools and is retracted.

| Concern | `finalize_lane_doctor.py` | `diagnose_finalize_lane_remediation.py` |
|---|---|---|
| Vantage point | PR / check-centric — talks to `gh` and `git` | Board / owner-centric — reads `ai-status.json` + `.orchestrator/config.json` only |
| Category constant | `SEVERITY` (7 entries, worst-first) | `ALL_CATEGORIES` (5 entries) |
| Scan scope | `FINALIZE_STATUSES = ("review_approved",)`, **skips** task ids containing `SIDECAR` | `review_approved` **plus** `ready_dispatch.finalize_statuses` from config, **plus** any task whose owner is an agent in `finalize` status; does **not** skip sidecar ids |
| Network access | yes (`gh pr view`, `git ls-remote`) | none |

#### 1a. `finalize_lane_doctor.py` — 7 causes, severity-ordered

`SEVERITY` orders findings worst-first; the report sorts by
`SEVERITY.index(...)`.

| # | Cause | Condition | Remedy emitted by `--emit-commands` |
|---|---|---|---|
| 1 | `ALREADY_MERGED` | Branch is already an ancestor of `origin/dev`; the work landed but the board was never updated. | Close the task out directly — **not** `gh pr create`, which would fail with "No commits between dev and task/…". |
| 2 | `NO_PR` | Branch pushed but no PR exists (usually the ReviewBus detached-HEAD bug). Sub-case: no remote branch at all → "the work was never pushed", and no `gh pr create` is emitted. | `gh pr create --base dev --head <branch>` |
| 3 | `MISSING_REQUIRED_CHECK` | A PR exists and a required check never reported. Defaults: `orchestrator`, `product`, `product-e2e-gate`, `task-review-gate`. In `classify()` the `missing` test runs *before* every verdict branch, so this cause **preempts the rollup verdict entirely** — a PR with a missing required check is reported here even when the reported checks are failing, stale, or still pending, not only when they are green. | Register the task on the board so the gate fires. |
| 4 | `CI_STALE` | Rollup verdict is `failure` **and** the branch is behind `origin/dev` — the red verdict may be phantom. | `git merge --no-edit origin/dev && git push`, then rerun checks. |
| 5 | `CI_FAILED` | Rollup verdict is `failure` on a branch that is **not** behind base — a genuine failure. | Same branch-advance block, then inspect logs and push a fix. |
| 6 | `CI_PENDING` | A check has not concluded yet. | — (terminal, non-stranded) |
| 7 | `READY` | All required checks green. | — (terminal, non-stranded) |

Causes 1–5 count as *stranded*; `CI_PENDING` and `READY` do not. That split is
exactly what drives the exit code (§2).

The table's row order is `SEVERITY`, which is a *report* ordering. The actual
decision order inside `classify()` is: already-merged → no PR → missing required
check → `failure` (split `CI_STALE` / `CI_FAILED` by `branch_is_behind`) →
`pending` → `READY`. The two orders agree here, but only because
`MISSING_REQUIRED_CHECK` is checked first in both — do not read `SEVERITY` as
the evaluation order in general (contrast §1b, where the two orders diverge).

#### 1b. `diagnose_finalize_lane_remediation.py` — 5 causes

`ALL_CATEGORIES = [CI_UNRESOLVED, CI_FAILED, STALE_BASE, MISSING_PR,
OWNER_UNAVAILABLE]`. Note the **evaluation order in `classify_stranded_task()`
differs from the declaration order** — the first match wins:

| Eval order | Category | Condition (matched against task `next` + owner agent `next`, lowercased) |
|---|---|---|
| 1 | `OWNER_UNAVAILABLE` | No owner assigned; **or** the note contains `sidecar-only` / `auto-reassigned`; **or** the owner agent's status is `blocked`, `paused`, or `quota_terminal`. |
| 2 | `STALE_BASE` | Note contains `behind`, `rebase`, `conflict`, `base advance`, or `head_liveness`. |
| 3 | `CI_UNRESOLVED` | Note contains `unresolved`, `unknown`, `pending`, `in_progress`, or `conclusive`. |
| 4 | `CI_FAILED` | Note contains `failed`, `failure`, `cancelled`, `timed_out`, or `action_required`. |
| 5 | `MISSING_PR` | Task carries no `pr` number. |
| — | `CI_UNRESOLVED` (fallback) | Nothing above matched. |

`OWNER_UNAVAILABLE` has **no counterpart in the doctor's taxonomy**: it is the
only category that diagnoses the *agent* rather than the PR. `ALREADY_MERGED` and
`MISSING_REQUIRED_CHECK` are likewise doctor-only. `CI_FAILED` is the one name
shared by both tools; `NO_PR`/`MISSING_PR` and `CI_STALE`/`STALE_BASE` are
near-equivalents under different names, reached by different evidence (live `gh`
probe vs. board note text).

---

## 2. Parent Implementation Assessment

Parent task `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001` delivers **two** diagnostic
modules and **two** test suites in `scripts/orchestrator/`.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│              Finalize Lane Remediation — delivered surface @ f16593c7        │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  gh / git  ──►  finalize_lane_doctor.py  ──►  ALREADY_MERGED                 │
│  (live PR)                                    NO_PR                          │
│  ai-status.json ──┐                           MISSING_REQUIRED_CHECK         │
│                   │                           CI_STALE / CI_FAILED           │
│                   │                           CI_PENDING / READY (terminal)  │
│                   │                                                          │
│                   └─►  diagnose_finalize_lane_remediation.py                 │
│  .orchestrator/          (offline, board+owner)  ──►  OWNER_UNAVAILABLE      │
│  config.json  ──────────►                             STALE_BASE            │
│                                                       CI_UNRESOLVED          │
│                                                       CI_FAILED              │
│                                                       MISSING_PR             │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Delivered Modules

**1. `scripts/orchestrator/finalize_lane_doctor.py`** (316 lines)

- Classifies stranded finalize tasks into the 7 `SEVERITY` causes of §1a and
  sorts findings worst-first.
- CLI surface:

  | Flag | Behaviour |
  |---|---|
  | `--status` | Path to `ai-status.json`. Default `$ODP_SUPERVISOR_STATUS_FILE`; the parser **errors out** if neither is supplied. |
  | `--repo` | Repo root for the `gh`/`git` subprocesses (default `.`). |
  | `--base` | Base branch (default `dev`). |
  | `--emit-commands` | `store_true`; prints the exact remedy lines per finding. |
  | `--required-check` | `append`; **replaces** `DEFAULT_REQUIRED_CHECKS = ("orchestrator", "product", "product-e2e-gate", "task-review-gate")` when supplied — it does not filter or add to them. |

- Exit code: `main()` computes
  `stuck = sum(v for k, v in counts.items() if k not in (READY, CI_PENDING))` and
  returns `1` when non-zero, else `0`. There is no `--fail-on-stranded` flag on
  this tool; the exit code is unconditional.
- Read-only: `--emit-commands` prints remediation strings; the module contains no
  mutation path, by design ("creating PRs and moving branches are governed
  actions that belong to an owner, not to a diagnostic").

**2. `scripts/orchestrator/diagnose_finalize_lane_remediation.py`** (343 lines)

- Classifies finalize-lane tasks into the 5 `ALL_CATEGORIES` causes of §1b and
  attaches a `remediation` summary plus a concrete `remediation_cmd` per task.
- CLI surface:

  | Flag | Behaviour |
  |---|---|
  | `--status` / `-s` | Path to `ai-status.json`. Default `$ODP_SUPERVISOR_STATUS_FILE`, falling back to `ai-status.json`. |
  | `--config` / `-c` | Path to `.orchestrator/config.json`. Default `$ODP_SUPERVISOR_CONFIG_FILE`, falling back to `.orchestrator/config.json`. Supplies `ready_dispatch.finalize_statuses`. |
  | `--task` / `-t` | `append`; restrict diagnosis to specific task ID(s). Case-insensitive. |
  | `--category` | `choices=ALL_CATEGORIES`; filter output to one root cause. |
  | `--json` | Machine-readable report (`json.dumps(..., indent=2, sort_keys=True)`). |
  | `--remediate` | Include the explicit `Command:` line in human-readable output. |
  | `--fail-on-stranded` | Exit `1` when `stranded_count > 0`. Without it, a successful diagnosis always exits `0`. |

- Exit `1` is also returned on `FinalizeDiagnosisError` (missing or non-object
  status/config JSON), printed as `FAIL: …` on stderr.
- Read-only: no subprocess, no network, no writes.

### Delivered Test Suites

**3. `scripts/orchestrator/test_finalize_lane_doctor.py`** (196 lines, **17 tests**)

Covers rollup verdicts (success / failure listing failing checks / pending /
cancelled-counts-as-failing), `NO_PR` with and without a remote branch, missing
required checks and their precedence over a green verdict, `CI_STALE` vs. real
failure, `READY`, per-cause remediation text, `ALREADY_MERGED` outranking `NO_PR`,
`ALREADY_MERGED` closing the task rather than opening a PR, unmerged branches
still classifying normally, scan scope restricted to `review_approved`, and the
exit-code signal.

**4. `scripts/orchestrator/test_diagnose_finalize_lane_remediation.py`** (188 lines, **9 tests**)

One test per category (`test_ci_unresolved_category`, `test_ci_failed_category`,
`test_stale_base_category`, `test_missing_pr_category`,
`test_owner_unavailable_category`), plus `test_task_id_filter`,
`test_category_filter`, `test_main_json_and_fail_on_stranded`, and
`test_missing_file_raises_error`.

> [!NOTE]
> **Parent branch scope & generated state mirrors.** At parent approved head
> `f16593c7`, `git diff --stat $(git merge-base origin/dev f16593c7) f16593c7`
> reports **8 files / 2,383 insertions**. **4** are generated state mirrors
> (`ai-status.json`, `current-work.md`, `docs-site/ai-status.json`,
> `docs-site/current-work.md`); the **4** core deliverable files are
> `diagnose_finalize_lane_remediation.py` (343) +
> `test_diagnose_finalize_lane_remediation.py` (188) +
> `finalize_lane_doctor.py` (316) + `test_finalize_lane_doctor.py` (196)
> = **1,043 lines**. The same 8-file / 2,383-insertion shape is what landed on
> `dev` at `bc7366d3`.

---

## 3. Acceptance Verification Matrix

Ref IDs **A1–A5** cover `finalize_lane_doctor.py`; **A6–A10** cover
`diagnose_finalize_lane_remediation.py`; **A11** is cross-cutting.

| Ref | Module | Summary | Verification Method |
|---|---|---|---|
| **A1** | doctor | `SEVERITY` defines and worst-first orders the 7 causes of §1a; findings sort by `SEVERITY.index(...)`. | `test_finalize_lane_doctor.py` (`test_missing_check_outranks_green_verdict`, `test_already_merged_outranks_no_pr`) |
| **A2** | doctor | `ALREADY_MERGED` outranks `NO_PR` and its remedy closes the task instead of issuing an invalid `gh pr create`. | `test_already_merged_outranks_no_pr`, `test_already_merged_remediation_closes_task_not_opens_pr`, `test_unmerged_branch_still_classified_normally` |
| **A3** | doctor | CLI parses `ai-status.json`, scans only `review_approved` (skipping `SIDECAR` ids), and signals stranded work via exit code. | `test_only_review_approved_tasks_are_scanned`, `test_exit_code_signals_stuck_tasks` |
| **A4** | doctor | Non-destructive: `--emit-commands` prints per-cause remedies without mutating state; unpushed branches get no `pr create` command. | `test_remediation_differs_per_cause`, `test_unpushed_branch_gets_no_pr_create_command`, source inspection |
| **A5** | doctor | `CI_STALE` is distinguished from a genuine `CI_FAILED`, so phantom red runs are not treated as defects. | `test_stale_ci_distinguished_from_real_failure`, `test_ready_when_all_required_green` |
| **A6** | diagnose | All 5 `ALL_CATEGORIES` root causes are produced from board state. | `test_ci_unresolved_category`, `test_ci_failed_category`, `test_stale_base_category`, `test_missing_pr_category`, `test_owner_unavailable_category` |
| **A7** | diagnose | `OWNER_UNAVAILABLE` catches unassigned owners, sidecar-only / auto-reassigned owners, and `blocked`/`paused`/`quota_terminal` agents. | `test_owner_unavailable_category`, source read `classify_stranded_task()` L118–143 |
| **A8** | diagnose | `--task` and `--category` narrow the report correctly. | `test_task_id_filter`, `test_category_filter` |
| **A9** | diagnose | `--json` emits a machine-readable report and `--fail-on-stranded` drives exit code 1. | `test_main_json_and_fail_on_stranded` |
| **A10** | diagnose | Missing / malformed status file raises `FinalizeDiagnosisError` and exits 1 with a `FAIL:` message rather than a traceback. | `test_missing_file_raises_error`, source read `main()` L305–307 |
| **A11** | both | Clean lint and zero syntax errors across the delivered surface. | `ruff check scripts/orchestrator/`, `py_compile` on both modules |

---

## 4. Verification Suite Commands

Run from the repo root at parent commit `f16593c7` (or on `dev` at `bc7366d3` —
the `scripts/orchestrator/` trees are identical).

```bash
# 1. Full parent test surface — 26 tests (17 doctor + 9 diagnose)
python3 -m pytest -q \
  scripts/orchestrator/test_finalize_lane_doctor.py \
  scripts/orchestrator/test_diagnose_finalize_lane_remediation.py

# 1a/1b. Same suites individually, if you want the split
python3 -m pytest -q scripts/orchestrator/test_finalize_lane_doctor.py             # 17 passed
python3 -m pytest -q scripts/orchestrator/test_diagnose_finalize_lane_remediation.py  # 9 passed

# 2. Syntax compilation — both modules
python3 -m py_compile \
  scripts/orchestrator/finalize_lane_doctor.py \
  scripts/orchestrator/diagnose_finalize_lane_remediation.py

# 3. Lint
python3 -m ruff check scripts/orchestrator/

# 4. Whitespace / diff cleanliness of the sidecar branch
git diff --check origin/dev...HEAD
```

### Recorded results (owner re-run, round 6, 2026-08-06, this worktree at `dev`=`85d60609`)

| Command | Result |
|---|---|
| combined pytest (1) | **26 passed** |
| `test_diagnose_finalize_lane_remediation.py` (1b) | **9 passed** |
| `test_finalize_lane_doctor.py` (1a) | **17 passed** |
| `py_compile` both modules (2) | clean |
| `ruff check scripts/orchestrator/` (3) | **All checks passed!** |
| `git diff --check origin/dev...HEAD` (4) | clean; diff is 2 files under `support/sidecars/…` only |
| `git diff --stat f16593c7 HEAD -- scripts/orchestrator/` | empty — the pin still describes the tree under test |
| `wc -l` on the 4 deliverables at `bc7366d3` | 316 + 343 + 196 + 188 = **1043** |

The round-4 run at `dev`=`bc7366d3` and the round-5 run at `dev`=`a7fde1a8`
produced the same results; every row above was re-executed after the `85d60609`
base advance rather than carried forward.

---

## 5. Handoff Note & Reviewer Transition

This sidecar review packet is revised to round 6 and ready for re-review.

- **Owner**: `Claude2`
- **Assigned Reviewer**: `Claude3`
- **Why round 6 exists**: round 5 was approved at `a77753b5`, but PR #659 then went
  `BEHIND` again as `dev` advanced `a7fde1a8 → 85d60609`. `dev` requires strict
  status checks, so the branch had to compose the new base, and `command_done`
  compares the head against `approved_head` for exact equality — a base advance is
  therefore a re-review event by construction, not a mechanical refresh. This is
  the same mechanism that produced round 5; it is a lane-throughput artifact, not
  a defect in the packet.
- **Round-6 base advance is inert for §1–§4**: the incoming range is
  `a7fde1a8..85d60609` — `a7fde1a8` is this branch's merge-base with `dev`, not an
  arbitrary lane commit. That range is 13 commits, all from
  `ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001-SIDECAR-REVIEW` and
  `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001-SIDECAR-ACCEPTANCE`, and its
  whole content diff is two new files under those two sidecars' own `support/`
  trees. Nothing under `scripts/orchestrator/` moved:
  `git diff --stat f16593c7 HEAD -- scripts/orchestrator/` is still empty, and all
  §4 rows were re-executed at the new base.
- **Round-5 note N5 closed** (citation precision): round 5's §5 described its
  incoming range as `d94bc547..a7fde1a8`. `d94bc547` was the detached-HEAD lane's
  first content commit, not this branch's merge-base, so that literal range
  enumerates 21 commits including ones already in the branch. The correct round-5
  endpoint was the merge-base `bc7366d3`, at which the claim is exactly true:
  9 commits, all `ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001`, touching only
  `.orchestrator/github_bus.py` and its test. The round-5 text is replaced by this
  block, and the round-6 range above is quoted merge-base-first for the same
  reason. The substantive round-5 conclusion is unchanged — it never depended on
  the endpoint.
- **Round-4 note N4 closed**: §1a row 3 previously said `MISSING_REQUIRED_CHECK`
  applies when "reported checks are green". In `classify()` the `missing` test
  returns before any verdict branch, so the cause preempts `failure`, `pending`
  and green alike. The row is corrected and §1a now states the evaluation order
  explicitly alongside the `SEVERITY` report order.
- **Reviewer diff shortcut**: `git diff a77753b5 HEAD` — the whole delta is this
  §5 block, the round-6 rows in the header, the pin table and §4, plus the
  base-advance merge of `origin/dev`. `git diff a77753b5 HEAD --
  scripts/orchestrator/` is empty, and the reviewer's own findings file is
  untouched by this round.
- **Round-3 blockers addressed**: **G1** — §2 now lists both modules and both
  test suites with real CLI surfaces; **G2** — §1 is split into the two delivered
  taxonomies with no slash-aliases, and `CI_UNRESOLVED` / `OWNER_UNAVAILABLE` are
  documented; **G3** — the §2 `[!NOTE]` is re-derived at `f16593c7` (8 files /
  2,383 insertions / 4 mirrors / 4 deliverables / 1,043 lines); **G4** — §3 adds
  A6–A10 backed by the 9 diagnose tests and §4 now runs all 26; **G5 (pin)** —
  the parent commit is pinned in the header block.
- **Round-2 non-blocking notes folded in**: `--required-check` is described as
  *replacing* the default set (N2); the doctor's `review_approved`-only scan and
  `SIDECAR` skip are documented in §1 and A3 (N3).
- **Open observation for the parent owner** (not a sidecar defect): the parent
  ships two overlapping finalize-lane diagnostics with divergent vocabularies and
  scan rules. This packet documents both without asserting precedence; parent
  owner `Antigravity` / parent reviewer `Antigravity2` should record which one is
  authoritative for operators.
- **Next Action**: hand off `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW`
  to reviewer `Claude3`. On approval, the parent owner may absorb this packet into
  `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001`.
