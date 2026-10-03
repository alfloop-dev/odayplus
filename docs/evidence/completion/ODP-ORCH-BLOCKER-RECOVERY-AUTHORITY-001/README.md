# Blocker recovery authority correction

Task: ODP-ORCH-BLOCKER-RECOVERY-AUTHORITY-001 · Owner Pi · Reviewer Codex2

## Reproduction and authority

The isolated production dispatcher reproduces the reported failure without
reading the global activity history or touching XR-EXT-OSS-FINAL-AUDIT-001.
The real canonical `note` mutation replaces `next`; canonical `blockers[]`
retains the external-data/dataset gate. Before the fix, the real recovery and
persistence paths change blocked → todo and resolve the external blocker.

`baseline-red.json` records exit 1 for the declared focused selection against
base 1b14b276447a7778f1dad1045ced1a5bd6abe01e plus the regression subsequently
anchored as 3056e116e49e. Its output shows the actual incorrect transition,
not a harness/collection error. Earlier harness setup attempts were not proof
of the defect and remain separate unsuccessful receipts in the local store.

## Source boundary

- `dispatch_engine.py` passes its existing canonical board snapshot to the
  mainline assignment guard. No additional per-tick file/history scan.
- `supervisor.py` requires a versioned board with canonical blockers, derives
  dependencies from that same board, and checks existing structured gate
  semantics. Open human/external/unknown blockers override routing prose;
  ordinary notes are never evidence that these gates were released.
- Recoverable routing blockers and completed dependency-only gates still
  recover. Resolved history and unrelated tasks' blockers do not block them.
- `worker_failure_policy.py` persists the authorizing snapshot instead of
  reloading and blanket-resolving newly added blockers. Existing locked CAS
  rejects a concurrent canonical revision. A copy prevents rejected persistence
  from mutating the dispatch pass's task objects.

The brief's `worker_reassignment.py` does not exist at origin/dev; the two
actual extracted wiring files above are the minimal necessary implementation
contacts. This was recorded in canonical task progress, not inferred from the
seeded local board.

## Focused verification

Declared commands (each measured with its real exit status and duration):

- `git diff --check`
- `uv run --frozen --python 3.12 pytest -q .orchestrator/test_blocker_recovery_authority.py .orchestrator/test_task_dependency_gate.py`

`anchor-green.json` binds both selection and successful pytest exit 0 to
2088318b00c5 (the snapshot fix anchor). Final publication additionally requires
new receipts at the exact submitted head in `.orchestrator/evidence`, enforced
by `task_finalize.sh`; anchor evidence is not a substitute for that check.

Coverage includes ordinary/owned_paths notes, structured gates, mixed completed
dependency + independent gate, open unknown blockers, resolved history,
released routing and dependency gates with actual enqueue, missing snapshot /
revision, inconsistent task/maps, and human blocker insertion both before
persistence and immediately before locked CAS. No real worker launch occurs.
The existing AutomaticRecovery tests were updated to supply canonical snapshots;
the authorized focused selection does not run the wider supervisor suite.

Inventory is regenerated only with the existing boundary generator. Changed
Python sources are checked by the existing finalization lint preflight.

## Review correction and base composition

Reviewer Codex2 rejected head `0b588a943dbbd` because the general task-note
path sanitizer erased `External-data/dataset` from an open canonical blocker.
The remaining provider/handoff words incorrectly authorized recovery.

The task branch composed `origin/dev` `9a5ef53de695` using a normal merge
(`520798d7be6a`), preserving all original task commits. That base includes the
verification-command-identity correction; no runtime rollout was performed.

`mixed-prose-red.json` binds the declared selection to regression anchor
`fb6548154cdc`, exit 1 (4.091s). Both reviewer counterexample variants
(with/without a completed dependency) incorrectly changed blocked → todo and
open → resolved. Unknown business gate + routing prose also failed.

Recovery now preserves bare slash compounds in canonical gate prose before
broad path filtering. Only explicit code references (backticks, key=value,
absolute/dot-prefixed paths and filenames) are removed; ambiguous extensionless
slash prose remains fail-closed rather than being assumed to be a path.
Dependency IDs are masked first. An unclassified `gate` in an open blocker
cannot be released by routing words; resolver-proven dependency-gate wording
is exempt only when no independent gate remains.

`mixed-prose-green.json` binds exit 0 (3.534s) to fix anchor `3f757620f2a3`.
Added production-entry coverage includes the exact mixed-prose counterexample,
Human/Ops and unknown gate prose, a dependency + independent business gate in
one blocker, and retained `blocked_reason` evidence. Positive recovery still
enqueues with dependency IDs, filenames, quoted extensionless paths, absolute
paths and code identifiers present. Existing resolved-history, missing snapshot
and parallel-insertion/CAS coverage remains unchanged. Each assertion prevents
real launches and checks board preservation, enqueue absence and false
admission audit absence for negative cases.

These tracked receipts describe intermediate anchors; final submission must
also pass both declared commands at the exact new head, using the existing
receipt gate. No broader suite or host scan was needed.

## Fourth review correction (Claude, after churn reassignment)

Codex2 rejected head `26b684ee7ca5` because canonical blocker sanitization
still deleted every `key=value` pair and snake_case label before hard/unknown
classification. Open blockers `pending_human; provider handoff pending` and
`approval=pending; provider handoff pending` lost their human-gate evidence and
the remaining routing words authorized recovery.

Base composed first: `origin/dev` `357ebdb32a02` merged normally (tree equals
`git merge-tree --write-tree`), no task history rewritten.

Fix (`blocked_task_prose_context`, canonical mode only): `=` and `_` are split
into word boundaries instead of deleting the whole token, so `pending_human`
reads as `pending human` and `approval=pending` as `approval pending`; hard
markers and unknown-token fail-closed both see them. Explicit code references
(backticks, absolute/dot-relative paths, filenames with extensions) are still
removed first. Ordinary note context (non-canonical mode) is unchanged. The
only allowlist addition is `ref`/`refs`/`see`, the label left behind once an
explicit path value such as `refs=docs/dataset.json` is removed; it carries no
gate meaning. Chinese/non-ASCII unknown prose stays fail-closed.

Added production-entry cases (with and without completed dependency):
`pending_human`, `approval=pending`, `gate_status=pending_human_signoff`, the
same labels inside a dependency blocker, and both labels as task
`blocked_reason`. Against the previous head, 10 of these fail (blocked → todo);
on the fix all pass, along with the existing references/routing/dependency
positives, resolved-history, missing-snapshot and CAS cases. Exact-head
receipts come from the declared verification gate at submission.

## Runtime rollout and merge follow-up

**Source-only correction: no supervisor restart, runtime switch, XR/gate/IAM or
worker-process change was performed.** An unchanged loaded runtime continues
using its old authority behavior even after this source PR merges.

After independent Codex2 approval and merge, the owner must record the actual
merged SHA in the canonical closeout note (currently unavailable pre-review).
Coordinator/Human/Ops must batch the controlled supervisor runtime rollout
with this round's verification-command-identity correction, then confirm the
loaded runtime provenance matches the merged release. Keep the existing XR
non_dispatchable hold; this task does not authorize releasing it or admitting
missing datasets/live evidence. No new workflow, registry or approval is added.
