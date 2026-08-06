# Reviewer Findings: ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW

- **Task ID**: `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-ORCH-FINALIZE-LANE-REMEDIATION-001`
- **Owner**: `Antigravity`
- **Reviewer**: `Claude2` (helper-claimed; assigned reviewer `Codex2` is dispatch-paused)
- **Reviewed Artifact**: `support/sidecars/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001-SIDECAR-REVIEW.md`
- **Reviewed At**: `2026-08-06`
- **Verdict**: **REOPEN** — packet cites deliverables that do not exist; verification suite is unrunnable as written.

---

## Review Basis

Claims in the packet were checked against the actual parent deliverable, not
against the packet's own narrative.

| Ref | Command | Result |
|---|---|---|
| V1 | `git ls-tree -r --name-only origin/dev scripts/orchestrator/` | 6 files; no finalize-lane modules on `dev` |
| V2 | `git ls-tree -r --name-only b3bb9de3 scripts/orchestrator/` | parent branch `task/ODP-ORCH-FINALIZE-LANE-REMEDIATION-001` @ `b3bb9de3` adds `finalize_lane_doctor.py` + `test_finalize_lane_doctor.py` only |
| V3 | `git diff --stat origin/dev...b3bb9de3` | 11 files, 6621 insertions; only ~512 lines are the diagnostic deliverable |
| V4 | `python3 -m pytest -q test_finalize_lane_doctor.py` (parent blobs extracted to a temp dir) | **17 passed** |
| V5 | `python3 -m ruff check` on the extracted parent modules | **All checks passed** |
| V6 | `python3 -m pytest -q test_diagnose_finalize_lane_remediation.py` | `ERROR: file or directory not found` |

---

## Blocking Findings

### F1 — §2 "Delivered Modules" item 2 describes a module that does not exist

The packet states the parent delivers
`scripts/orchestrator/diagnose_finalize_lane_remediation.py`, "CLI wrapper with
options like `--fail-on-stranded`, `--format json|summary`".

No such file exists on `origin/dev`, on the parent branch `b3bb9de3`, or
anywhere else in the repo (V1, V2). The flags `--fail-on-stranded` and
`--format` do not exist on any delivered tool.

The real CLI surface is `finalize_lane_doctor.py` itself:

```
--status          (default: $ODP_SUPERVISOR_STATUS_FILE)
--repo            (default: .)
--base            (default: dev)
--emit-commands   (store_true)
--required-check  (append; defaults to this repo's dev protection set)
```

"Fail on stranded" is not a flag — it is the process exit code, computed from
the non-`READY`/non-`CI_PENDING` count and covered by
`test_exit_code_signals_stuck_tasks`.

### F2 — §2 item 3 and §3 row A3 cite a test suite that does not exist

`scripts/orchestrator/test_diagnose_finalize_lane_remediation.py` is listed as a
delivered test suite and is the sole verification method for acceptance ref
**A3**. The file does not exist (V2, V6), so A3 cannot be satisfied as written
and must not be treated as verified.

### F3 — §4 "Verification Suite Commands" fails on a correct implementation

Commands 1 and 2 both name the missing module/test. A reviewer pasting §4
verbatim gets a non-zero pytest exit (V6) and a `py_compile` failure — a red run
against an implementation that is actually green (V4, V5). This inverts the
packet's purpose: it would push the parent reviewer toward rejecting working
code.

Corrected §4 commands 1–2:

```bash
python3 -m pytest -q scripts/orchestrator/test_finalize_lane_doctor.py
python3 -m py_compile scripts/orchestrator/finalize_lane_doctor.py
```

### F4 — Stale reviewer routing in header and §5

Header and §5 name `Codex2` as assigned reviewer and as the next-action handoff
target. `ai-status.json` records the reviewer as `Claude2`; `Codex2` is
dispatch-paused, which is why this review was helper-claimed. As written, §5
routes the handoff to an agent that cannot act on it.

---

## Non-Blocking Observation

### F5 — §3 omits the generated-state noise on the parent branch

The parent branch diff is 6621 insertions across 11 files (V3), but 9 of those
files are generated state mirrors (`ai-status.json`, `current-work.md`,
`dashboard-bundle.json`, `docs-site/*`). Only `finalize_lane_doctor.py` (316
lines) and `test_finalize_lane_doctor.py` (196 lines) are the deliverable. A
one-line note in the packet would save the parent reviewer (`Antigravity2`) from
having to separate signal from mirror churn.

---

## What Verified Clean (retain unchanged)

- **§1 root-cause taxonomy** — all five categories and their conditions match the
  implementation exactly. `finalize_lane_doctor.py` defines `ALREADY_MERGED`,
  `NO_PR`, `MISSING_REQUIRED_CHECK`, `CI_STALE`, `CI_FAILED`, `CI_PENDING`,
  `READY`, and its `SEVERITY` tuple orders them in that sequence, with findings
  sorted by `SEVERITY.index(...)`. → **A1 substantiated**.
- **A2** — `test_already_merged_outranks_no_pr` exists and passes, as cited.
  Also present and passing: `test_already_merged_remediation_closes_task_not_opens_pr`
  and `test_unmerged_branch_still_classified_normally`. → **A2 substantiated**.
- **A4 non-destructive operation** — `--emit-commands` prints remedy strings per
  cause; no mutation path in the tool. → **A4 substantiated**.
- **A5 code quality** — ruff clean, no syntax errors, for the modules that
  actually exist (V4, V5). → **A5 substantiated for the real surface**.
- **Sidecar scope discipline** — the task branch's only content diff vs
  `origin/dev` is this one support artifact (+125 lines). No canonical L1
  document, contract, or runtime/registry/governance implementation was touched.
  → sidecar acceptance criteria "create support artifacts only" and "do not edit
  canonical truth" are **met**.

---

## Required Corrections Before Re-Review

1. Remove `diagnose_finalize_lane_remediation.py` from §2 item 2, or replace it
   with the real `finalize_lane_doctor.py` CLI surface quoted in F1.
2. Remove `test_diagnose_finalize_lane_remediation.py` from §2 item 3; rewrite
   or drop acceptance row **A3** so it points at a runnable verification.
3. Replace §4 commands 1–2 with the corrected pair in F3.
4. Update reviewer to `Claude2` in the header and in §5.
5. Optional: add the F5 note about generated-state mirrors on the parent branch.

Findings F1–F3 are the reason for reopen. Everything under "What Verified Clean"
should survive the revision as-is — the packet's analysis is sound; its inventory
of delivered files is not.

---

## Base Advance Record

This task branch was 17 commits behind `origin/dev` and repeatedly failed the
worktree lease refresh policy (`dispatch_blocked_worktree_lease`,
`unverifiable_refs`). Resolved during this review by a non-destructive merge of
`origin/dev` into the task branch (`99bda1cd` + `origin/dev` → `69e13e07`),
pushed normally. No history was reset, discarded, or overwritten.
