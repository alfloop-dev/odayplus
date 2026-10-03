# Blocker recovery authority correction

Task: ODP-ORCH-BLOCKER-RECOVERY-AUTHORITY-001 · Owner Pi · Reviewer Codex2

Review-round sections below record historical implementations, not cumulative
sanitization permissions. The latest reopen-8 section describes current slash
handling; finalization receipts must still bind the exact submitted head.

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

## Review round 5: Markdown spans are not gate-release authority

Codex2 rejected head `32894673805c` because canonical blocker sanitization
still deleted every backtick span before hard/unknown classification. Open
blockers ``waiting for `human approval`; provider handoff pending``,
``` `仍欠業主准許`；provider handoff pending ``` and
``` `pending_human`; provider handoff pending ``` lost their gate evidence and
the remaining routing words authorized recovery (with and without a completed
dependency).

Fix (`blocked_task_prose_context`, canonical mode only): a quoted span (inline
or fenced) is removed only when `_is_explicit_code_path_reference` proves it is
a path: whitespace-free and either absolute/dot-relative, ending in a known
file extension, or anchored at a top-level root of this repository
(`docs/…`, `scripts/…`, `.orchestrator/…`). Every other span is unwrapped and
classified as prose, so quoted human conditions, Chinese requirements,
`pending_human`, `approval=pending`, `Human/Ops` and `External-data/dataset`
remain hard/unknown evidence. Ordinary-note (non-canonical) mode is unchanged.

Deliberate tightening: a quoted structured-gate field name such as
`` `external_data_gate` `` is a canonical gate label in the same sense as
`pending_human` (both are `ai_status` gate schema values), and shape alone
cannot tell them apart, so it now fails closed. The explicit code/path
positive keeps `scripts/deployment.py`, `refs=docs/dataset.json`,
`` `docs/dataset` ``, `/tmp/dataset`, `./docs/dataset` and adds quoted
`` `scripts/ai_status.py` ``, `` `/tmp/dataset` `` and
`` `.orchestrator/supervisor.py` ``; it still recovers and enqueues.

Added production-entry negatives (each with and without completed dependency):
the three reviewer messages, `` `approval=pending` ``, `` `external_data_gate` ``,
`` `Human/Ops` ``, `` `External-data/dataset` ``, a fenced `awaiting client
consent` block, and `` `pending_human` `` inside a dependency blocker. Against
head `32894673805c`, 17 of these fail (blocked → todo); on the fix all pass,
together with the existing routing/dependency/references positives,
resolved-history, missing-snapshot, Chinese failclosed and CAS cases.
Exact-head receipts come from the declared verification gate at submission.

## Reopen 6: job-condition evidence (review of `afa3469931cf`)

Codex2 showed that step 5 of `blocked_task_prose_context` erased every
`<word> job` span in canonical blocker prose as well, so `deployment job
pending; provider handoff pending` lost its `deployment`/`deploy` hard-gate
word and the residual routing words released the blocker. Job-reference
erasure is now limited to ordinary notes; canonical blocker and task gate
prose keep it, so a pending deployment/production job fails closed.

Added production-entry negatives (with and without completed dependency):
`deployment job pending; provider handoff pending`, `waiting for deploy job;
provider handoff pending`, `production 相關 job pending; provider handoff
pending`, and a dependency blocker carrying `deployment job pending`; plus the
two reviewer messages as task `blocked_reason`. On the pre-fix supervisor 9 of
these fail (blocked → todo, open → resolved); on the fix all pass together with
the routing/dependency/references positives, resolved history, missing
snapshot, quoted/snake/kv/Chinese failclosed and CAS cases, and
`AutomaticRecoveryTests`. Exact-head receipts come from the declared
verification gate at submission.

## Reopen 7: path/filename conditions (review of `c8fc0c0ed881`)

Codex2 showed that canonical blocker prose still erased filenames and paths
before hard/unknown classification, so `waiting for dataset.csv`, `waiting
for docs/approval.json` or `waiting for attestation.json` left only
allowlisted waiting/provider/handoff words and released the blocker. A path
can name the missing deliverable itself; path syntax is not release
authority.

Canonical blocker and task gate prose now keep every path and filename by
default. Only two bounded reference grammars remove one, each ending at a
clause boundary: a whole `refs=`/`ref:`/`see` clause (`refs=docs/dataset.json`,
``see `scripts/ai_status.py` ``) and a failure location (`provider failure in
scripts/deployment.py`). Any remaining absolute, relative or home path —
including an extensionless `/provider/worktree` whose slash-split parts are
all allowlisted — or dotted filename fails closed, also in the
completed-dependency residual checks. Ordinary-note sanitization is unchanged.

Added production-entry negatives (with and without completed dependency): the
three reviewer messages, `waiting for payload.csv`, `required manifest.json`,
backtick, curly-quote and `「」` quoted filenames, `/srv/inbox/payload`,
`./out/payload`, `../handoff/payload`, `~/provider/handoff`,
`/provider/worktree` (bare, quoted and alone), standalone `` `docs/dataset` ``
and `/tmp/dataset` clauses, and dependency blockers carrying a filename or
extensionless absolute path. On the pre-fix supervisor 32 of these fail
(blocked → todo); on the fix all pass. The references positive now uses only
the explicit reference grammars and still recovers with a completed
dependency, alongside routing/dependency/resolved positives, missing
snapshot, CAS and `AutomaticRecoveryTests`. Exact-head receipts come from the
declared verification gate at submission.

## Reopen 8: extensionless relative artifacts (Pi)

Codex2 rejected `c6943d253de9`: relative `auth/credentials`,
`runtime/credentials` and `provider/worktree` lacked both a leading path marker
and a file extension. The classifier split them into allowlisted routing words,
releasing required artifacts despite an ordinary note carrying no release
authority.

Regression anchor `87f6bcf08a96` first adds the exact production-entry messages
(with and without done dependencies), bare/quoted routing-named paths,
demanded paths followed by `failure`, dependency residuals and retained task
`blocked_reason`. The real note/load/recovery/persist/CAS harness reproduces
incorrect transitions; the declared two-file selection exits 1 (5.571s), receipt
`712e5e23c3f4c55c`, copied here as `relative-path-red.json`. It is not a
collection failure. The existing AutomaticRecoveryTests selection stays green.

Fix anchor `72cffd788d8c` treats **every remaining slash as an atomic ambiguous
artifact condition**, before either ordinary routing tokenization or completed
dependency residual cleanup. This covers all relative/absolute extensionless
forms without a filename/root allowlist. The only slash-compound exception is
an entire, boundary-anchored provider/quota/worktree failure clause, e.g.
`provider quota/worktree failure` or `stale provider/worktree failure`.
`waiting for provider/worktree`, `required provider quota/worktree failure`
and bare quoted paths do not match this grammar. No allowed-token list was
expanded, and no assertion was removed.

Explicit bounded `refs=`/`ref:`/`see` and failure-location clauses remain
incidental source references, now positively tested with extensionless
`auth/credentials`, `runtime/credentials` and `provider/worktree` references.
Pure routing still resolves/enqueues; dependency-only recovery, resolved
history, Chinese unknown prose, missing snapshots and parallel human-blocker
CAS protection remain covered. No cross-file snapshot scans or persistence
changes were introduced by this correction.

Fix-anchor receipts copied here:

- `relative-path-green.json`: declared two-file selection, exit 0, 4.574s,
  receipt `71433ea90bba1a2e`.
- `relative-path-routing-green.json`: declared AutomaticRecoveryTests,
  exit 0, 1.846s, receipt `03ef5618cc579bcd`.
- `git diff --check`: exit 0, 0.016s, receipt `e9db5831d03d47bd` in the
  existing receipt store.

The existing generator recalculates the boundary inventory. Publication runs
all three declarations on the final head after this evidence commit; anchor
receipts are not substitutes for exact-head proof. No wider supervisor suite
was run. Base `origin/dev` `e126cba49b93` was verified as an ancestor of the
task head; no merge or history rewrite was necessary this dispatch.

## Reopen 9: reference-label and identity-token boundaries (Pi)

Codex2 rejected `06f32fbea4bc`: a missing label delimiter parsed bare
`seed/dataset` and `reference.json` as `see`/`ref` clauses. Substring identity
masking also erased `Pi` inside `API`, turning an independent API release
requirement into allowed routing prose.

Production-entry regression anchor `191a525e9c09` runs the real ordinary-note
mutation, canonical load, eligibility, persistence and CAS, with boards in
scratch and launch/external boundaries mocked. Both owner Codex and owner Pi
are tested with and without a completed dependency. The declared two-file
selection exits **1** (5.357s), receipt `6663402df54b9826`, copied here as
`token-boundary-red.json`: ten false-release cases, not a collection error.
The original six review counterexamples are included. API release with Codex
and `./seed/dataset` remain negative controls; delimited `refs=`, `ref:`,
`see ` and `refs `, explicit dependency IDs and complete agent names are
positive controls. AutomaticRecoveryTests exits 0 (1.855s) at the red anchor.

Fix anchor `ac90e0b90cde` requires a colon/equal separator or actual whitespace
after a reference label. Structural identity removal now uses escaped,
complete tokens with Unicode word, identifier and path punctuation boundaries;
it cannot remove an agent name inside `API`, `Pi.provider`, `provider.Pi` or
path components. Additional production-entry filename negatives and an explicit
`refs=Pi.provider` positive cover artifact preservation versus code references.
No routing allowlist was expanded; no assertion was removed. Snapshot/CAS,
Chinese/hard/unknown gates, dependency resolution and legitimate routing
recovery retain their existing coverage and implementation.

The boundary inventory is recalculated only by the existing generator. All
three declared verification commands must pass once at the final submitted
head, with exact command/head/exit/duration/selection receipts in the existing
`.orchestrator/evidence` store before publication. The tracked red receipt is
not substituted for exact-head green proof. No broader suite, live gate
mutation, worker launch or runtime rollout is authorized by these tests.

## Reopen 10: prerequisites are not routing noise (Pi)

Canonical state confirmed Pi ownership and Human/Ops continuation consumption;
prior review findings/counts and Codex2 independence remain unchanged.
`origin/dev` was fetched and inspected (base `e126cba49b93`); no base merge,
history rewrite, live XR/gate change or runtime action was needed.

Regression anchor `58bd73013c63` adds production-dispatch negatives for
`waiting for dependencies`, `waiting for upstream dependency` and
`waiting for dependency` without a colon/kind, plus prerequisite/upstream
variants. Each is exercised as a canonical open blocker after a real ordinary
note, as retained `blocked_reason`, and as task `next`, with no declared
resolver authority. Assertions require unchanged board, zero enqueue/launch
and no admission claim. Positive controls require resolver-completed
dependencies to resolve/enqueue both with and without routing prose, and retain
the legitimate auto-reassigned/sidecar-only routing case.

At that anchor the declared two-file verification exits **1**, 6.508s, receipt
`8ac5be0da6bc92ec`, copied as `prerequisite-authority-red.json`. It exposes false
release negatives and rejected dependency-only positives, not collection
failure. The existing AutomaticRecoveryTests selection exits **0**, 1.833s,
receipt `dfbf1d2a2571d4dc`; diff exits 0, 0.015s, `3bc8dbc086415eee`.

The fix removes prerequisite words from the routing token allowance. A shared
word-boundary grammar recognizes them regardless of punctuation; only a
nonempty canonical `depends_on` satisfied by the resolver permits removing
them. A dependency-only residual need not contain a routing marker, but all
remaining words must still pass the existing fail-closed classifier. Independent
hard/unknown requirements, artifacts and unknown dependency IDs remain blocking.
No permissive token was added and no existing assertion was removed.

Unicode-aware token splitting remains intact (no ASCII-only extraction).
Production-entry coverage retains the exact `仍欠業主准許；provider handoff pending`
blocker with an ordinary routing note, and adds Chinese prerequisite, German
and Arabic unknown conditions and mixed prerequisite/independent requirements.
Existing structured gates, resolved history, missing snapshots, canonical
snapshot/dependency wiring, CAS/concurrent-blocker protections and lawful
routing recovery remain unchanged. The boundary generator was run; its tracked
inventory is unchanged because no code file was added.

Before publication, run the three declared commands via
`delivery_toolchain/git/task_verification.py run` on the final committed head.
The receipt store records exact head/command/exit/duration/selection; the tracked
red receipt does not substitute for final-head green proof. No wider supervisor
suite or already-measured head/selection rerun is required.

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
