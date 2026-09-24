# ODP-ORCH-AUTONOMOUS-RECOVERY-001 — Completion evidence

**Task:** ODP-ORCH-AUTONOMOUS-RECOVERY-001 (驗證並正式交付 supervisor 自動恢復修復)
**Owner:** Claude · **Reviewer:** Codex2 · **PR:** #1362 · **Class:** remediation (tracked adoption of an already-running orchestrator fix)

## Purpose

Bring the interrupted-merge recovery fix (quota-fenced worker exits during a Git merge; the
supervisor preserves the exact merge state and lets only the sealed owner or an authorised
successor resume it) through owner validation, independent review and the normal merge path.
No product deployment is included.

## Provenance and adoption

- Implementation commit `43d2fe8ca8e04b35a5049802169a01a5bc958678` was authored by the
  interactive Codex session (`LLM-Agent: Codex`, `Reviewer: Claude`) and opened as draft PR #1362.
  It is adopted as-is: commit history, author and trailers are preserved; no file from that
  commit is modified by this task.
- Claude (this task's owner) inspected and probed the change independently; see
  `independent_assessment.md`. The prior green CI on `43d2fe8c` was not used as a substitute for
  that review, and no reviewer approval is claimed here.
- The existing clean task worktree discovered by the supervisor was reused. No other task
  worktree, no live supervisor and no worker was touched.

## Base advance (required by the dispatch)

| Item | Value |
|---|---|
| Merge commit | `31dc1a65e3d813d9174852925d6e95feb237cdf7` |
| Parents | `43d2fe8ca8e04b35a5049802169a01a5bc958678` (task) + `c4efabbbeba9e743fab8ee52125932137852c703` (`origin/dev`) |
| Resulting tree | `1f74062138f6d2dbb23db4cb8c63c39feadd37a9` = tree predicted by `git merge-tree --write-tree` before merging |
| Conflicts | none; `origin/dev` added no change under `.orchestrator/` or to the three deliverable files |
| History | plain merge, no rebase, no reset; the previously pushed tip `43d2fe8c` remains an ancestor |

The three deliverable blobs are byte-identical at `43d2fe8c`, at `31dc1a65` and at the head that
carries this document (blob ids in `independent_assessment.md`).

## Verification

Environment: project `uv` environment rebuilt on CPython 3.12 (the default 3.14 has no
`pgserver` wheel); pytest 9.1.1; git 2.43.0. All runs below completed before
`2026-09-24T07:25:26Z` (UTC, read from `date -u` after the last run) on the merged tree
`31dc1a65`, i.e. on source identical to the final head for every non-evidence file.

### Declared verification (receipt-bearing)

The three commands declared on the task board (`git diff --check`, `ruff check` on the two
Python files, and the five-suite pytest selection) are executed once through
`delivery_toolchain/git/task_verification.py run` at the final task head, immediately before
`task_finalize.sh`, so that each receipt binds the exact head SHA, command, exit code, duration
and selection. Because a receipt binds the head that contains this document, its values cannot
be copied into this document without invalidating it; the outcomes are posted to the task board
(`note`) and appear in the PR body. The finalize gate (`task_verification check`) refuses to
publish unless every declared command has a passing receipt at that head.

### Independent probes and the runbook's broader selection (this task's own validation)

| Run | Result | Exit | Duration (pytest) |
|---|---|---|---|
| Scratch probes on top of the `QuotaSiblingFencingDirtyHandoffTests` fixture: conflicted merge preserved and sealed (A); same-path content edit after the seal detected (B); stat-cache refresh keeps the seal (C); revert in progress stays blocked (D); fenced-sibling successor dispatch on a conflicted merge (E) | 29 passed, 1 skipped (cherry-pick sub-case of D: git leaves no `CHERRY_PICK_HEAD` for a conflicting `--no-commit` cherry-pick, so nothing to block), 4 subtests passed | 0 | 20.22 s |
| `test_supervisor.py -k 'preserve or handoff or review_churn'` on the merged branch `31dc1a65` | 55 passed, 6 skipped, 0 failed (74 JUnit cases incl. subtests) | 0 | 5.75 s |
| Same selection on an unmodified `git archive` copy of `origin/dev c4efabbb` (module origin verified to be the copy) | 55 passed, 6 skipped, 0 failed | 0 | 6.98 s |

The probe file lives in the worker scratch directory and is intentionally not shipped; its
scenarios are described in `independent_assessment.md` so a reviewer can reproduce them.

The runbook's 2026-09-23 statement that the broader selection fails with `common.ConfigError`
did not reproduce on either tree (finding F1). The runbook received an addendum recording this;
the original text is kept.

### Genuine regressions

None observed. No test was re-run for a count.

## Live observation (log level only; no runtime paths, PIDs or dumps published)

- The supervisor's runtime alias resolves to a runtime checkout at `43d2fe8c`; its
  `.orchestrator/worker_workspace.py` is blob `596bd281…`, identical to the deliverable.
- The canonical activity log shows the sealed-continuation path exercised after that rollout:
  `worker_worktree_preserved` (trigger `sibling_fenced`) at `2026-09-23T14:35:06Z`,
  `task_reassigned` Antigravity4 → Claude2 at `2026-09-23T14:35:15Z`, and
  `worker_worktree_refreshed` with `refresh_status=sealed_owner_continuation` for Claude2 at
  `2026-09-23T14:39:40Z` and again at `2026-09-23T15:27:51Z`, all for
  `DPF-OVERTURE-BOUNDED-RECORD-READER-001`. The activity log does not record whether a seal was
  `owner_dirty` or `interrupted_merge`, and the live state holds no open handoff block at the time
  of writing, so this is evidence that the continuation path ran, not a measurement of the merge
  branch specifically.

## Rollout record

The runtime already runs the exact original patch. After Codex2 approval, required CI and the
normal merge into `dev`, the merged source for the next controlled runtime rollout is the `dev`
merge commit of PR #1362. This task performs no rollout, no supervisor restart and no product
deployment.

## Files in this directory

- `README.md` — this record.
- `independent_assessment.md` — Claude's source-bound code assessment, acceptance-item mapping,
  findings F1–F4 (no code defect found).
