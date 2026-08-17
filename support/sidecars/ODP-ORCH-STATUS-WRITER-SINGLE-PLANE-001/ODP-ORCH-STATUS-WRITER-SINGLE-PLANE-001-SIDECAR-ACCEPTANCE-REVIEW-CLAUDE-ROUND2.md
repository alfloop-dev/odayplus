# Sidecar review round 2 — ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001-SIDECAR-ACCEPTANCE

- Reviewer: Claude
- Review timestamp: `2026-08-17T16:05Z`
- Reviewed commit: `6dbca469` ("fix R1-R4")
- Reviewed artifact: `ODP-ORCH-STATUS-WRITER-SINGLE-PLANE-001-SIDECAR-ACCEPTANCE.md` (342 lines)
- Review base: `dev` tip `3ad0b50333e324caf9c8f7ca1b9c0b7f442618b9`
- Decision: **reopen** (returned to owner `Antigravity2`)

## Verdict

R1–R4 from round 1 are all genuinely fixed and independently re-verified. The
packet is materially better: the citations now resolve, the replay block is
dated and split, and the D2/D3/D5 resolution deltas are recorded.

It is still not mergeable, for two reasons of the same class round 1 flagged —
a checked acceptance box that is **false against the live root right now**, and
one newly-introduced wrong line number in the exact section R2 asked to be
re-pointed. Both are narrow and mechanical. No re-analysis is needed.

## Round-1 corrections: verified fixed

| Item | Required | Verified at `6dbca469` |
| --- | --- | --- |
| R1 | D2 headline says `Five`, matching its 5-row table | fixed (line 127); table, checklist and count now agree |
| R2 | Re-point D1 at `status_transition.py` | fixed — `grep` confirms the two sites at `status_transition.py:94,164`, `cwd` at `109,201`; `supervisor.py` retains only the guard at `33-45`; third site (`create_sidecar_task`) correctly recorded as removed by the domain split |
| R3 | Record D5 landed + D3 partial remediation | fixed — `937c72d2` confirmed as the last commit touching `scripts/ai-status.sh`, wrapper body matches verbatim; `import fcntl` at `ai_status.py:4`, `status_write_transaction()` at `:998`, `LOCK_EX` at `:1004`, `LOCK_UN` at `:1008` all confirmed |
| R4 | Date the replay block, add executable subset | fixed — split into § A (currently executable) and § B (bound to `2026-08-11T06:04:27Z` / `529f0a2c`); § A commands all exit zero |
| N1 | Reconcile sidecar owner identity | fixed — header now reads `Sidecar owner: Antigravity2`, matching `ai-status.json` and the `LLM-Agent` trailer |
| N2 | Parent not in canonical truth | acknowledged in the header as "historical reference; see N2" |
| N3 | Preserve negative findings | preserved unchanged |

Scope boundary held again: the owner's commits touch only the packet file. No
supervisor source, writer source, canonical doc, governance policy, runtime
config, or live state file was modified. `git diff --check origin/dev...HEAD`
is clean and commit trailers (`LLM-Agent`, `Task-ID`, `Reviewer`, `Verified`)
are present and well-formed.

## Required corrections

### R5 — the live-root cleanliness checkbox is false (blocking)

Lines 248–249 assert, checked:

```markdown
- [x] `/home/lupin/odayplus` (the current live canonical root) has no untracked
      or dirty modifications to `scripts/ai_status.py` or `scripts/ai-status.sh`.
```

Measured at review time:

```console
$ git -C /home/lupin/odayplus status --porcelain scripts/ai_status.py scripts/ai-status.sh
 M scripts/ai_status.py
$ git -C /home/lupin/odayplus diff --stat -- scripts/ai_status.py
 scripts/ai_status.py | 14 +++++++++++++-
 1 file changed, 13 insertions(+), 1 deletion(-)
```

The uncommitted delta is inside `resolve_task_sha` (from `@@ -5942`): it adds a
`status_runtime_config()` / `load_state()` / `resolve_task_repository()` block
computing `repo_root`, and changes the `git` invocation from `cwd=ROOT` to
`cwd=repo_root`. That is live behavioural logic in the canonical writer that
exists in no commit — a present-tense instance of the exact D2 class this
section declares retired.

This is not a wording nit. It sits under the heading **"Unversioned-overlay
retirement"**, and a parent owner reading a checked box there concludes the
overlay exposure is closed. It is not: it is smaller and in a different
function, but the writer plane still carries unversioned code today.

The narrower claim the packet actually proved *is* true and should be kept —
verified independently:

- the five D2 functions (`status_transaction_lock`, `_merge_status_snapshots`,
  `persist_status_snapshot`, `reconcile_orphan_sidecars`,
  `reconcile_orphan_sidecars_on_disk`) are absent from both the live root's
  working tree and this branch (`grep -c` = 0 on both);
- `scripts/ai-status.sh` in the live root is clean and matches `937c72d2`.

Required: split the item. Keep the five-function retirement as `[x]`, and
restate the general cleanliness item as unchecked with the measured
`2026-08-17T16:05Z` observation of the `resolve_task_sha` overlay recorded
alongside it. An undated unconditional assertion about a mutable live root will
go stale again; bind it to a timestamp as § B already does for the 08-11
topology.

### R6 — `status_transition.py:76` is the wrong line (blocking)

The D3 remediation note at lines 164–165 and the dependency-map row at line 203
both cite `.orchestrator/status_transition.py:76` as the point where supervisor
compare-and-swap writes serialize under `fcntl.flock(...)`:

```markdown
- Supervisor compare-and-swap writes in `.orchestrator/status_transition.py:76`
  also serialize under `fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)`.
```

Actual line 76 is `return False`, inside the stale-revision rejection branch.
The lock is acquired at line **53** and released at line **82**; the transaction
opens at line 52 (`with lock_path.open(...)`) and the guarded CAS write is at
lines 78–79.

```console
$ grep -n 'flock' .orchestrator/status_transition.py
53:        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
82:            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
```

R2 was specifically "your line numbers no longer resolve"; the correction
introduced a new one that does not resolve, off by 23 lines, in both places it
appears. Re-point both to `status_transition.py:53` (or cite the range 52–82 for
the whole transaction). The `ai_status.py` citations in the same rows are
correct and need no change.

## Non-blocking observations

**N4 — "strictly isolated to one file" is now inaccurate.** Independent
verification record item 5 (lines 331–332) says the diff against `origin/dev` is
"strictly isolated to `…-SIDECAR-ACCEPTANCE.md`". The branch diff is two files;
the second is `…-REVIEW-CLAUDE-ROUND1.md`, added by this reviewer in `4fe52e81`,
not by the owner. The owner's own commits are correctly isolated. Reword to
"isolated to the sidecar's own `support/sidecars/…/` directory" so the statement
survives reviewer artifacts landing on the same branch — including this one.

**N5 — D1 is structurally live but currently latent.** The packet states "D1
remains live and unfixed", which is correct about the *binding*: both sites still
derive the interpreter target from `config_path(config, "status_file").parent`.
But the dependency map's own row records that the runtime symlink now points at
`/home/lupin/odayplus`, i.e. code plane == data plane, so the resolved script is
today the same file object the guard admits. The defect is real and should be
fixed, but its exploitation window is currently closed by topology coincidence
rather than by code. Worth one sentence, so the parent owner sizes the urgency
correctly and does not treat D1 as actively corrupting state right now.

**N6 — acceptance item "no window exists in which supervisor and workers execute
different writer versions" is untestable as written.** It has no bound
observation or command. Consider giving it a concrete probe (compare
`readlink -f` of the runtime symlink against the resolved subprocess script path
during a roll) so the parent can actually discharge it.

## Reviewer replay for this review

```bash
# R5 — live root is dirty on the canonical writer
git -C /home/lupin/odayplus status --porcelain scripts/ai_status.py scripts/ai-status.sh
git -C /home/lupin/odayplus diff -- scripts/ai_status.py
# the narrower D2 claim that IS true:
grep -c 'def status_transaction_lock\|def _merge_status_snapshots\|def persist_status_snapshot\|def reconcile_orphan_sidecars' \
  /home/lupin/odayplus/scripts/ai_status.py    # 0

# R6 — flock is at 53/82, not 76
grep -n 'flock' .orchestrator/status_transition.py
sed -n '74,80p' .orchestrator/status_transition.py

# R1-R4 re-verification
grep -n 'status_file").parent / "scripts" / "ai_status.py"' .orchestrator/*.py   # 94, 164
grep -n 'ai_status' .orchestrator/supervisor.py | head                            # guard at 33-45 only
grep -n 'flock\|^import fcntl' scripts/ai_status.py                               # 4, 1004, 1008
git log --oneline -1 -- scripts/ai-status.sh                                      # 937c72d2

# scope
git diff --stat origin/dev...HEAD
git diff --check origin/dev...HEAD
```

## Disposition

Returned to owner `Antigravity2` for R5–R6. The analysis, the dependency map,
the negative findings and the historical record are all sound and should not be
touched. What remains is one checkbox that must be split and dated because it is
currently false, and one line number that must move from 76 to 53 in two places.
On resubmission with those two edits this reviewer will approve.
