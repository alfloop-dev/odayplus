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
