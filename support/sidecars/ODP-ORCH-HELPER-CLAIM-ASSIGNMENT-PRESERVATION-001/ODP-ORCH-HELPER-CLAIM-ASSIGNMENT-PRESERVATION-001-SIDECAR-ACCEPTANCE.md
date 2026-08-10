# Helper-claim assignment preservation acceptance packet

- Sidecar task: `ODP-ORCH-HELPER-CLAIM-ASSIGNMENT-PRESERVATION-001-SIDECAR-ACCEPTANCE`
- Parent task: `ODP-ORCH-HELPER-CLAIM-ASSIGNMENT-PRESERVATION-001`
- Helper kind: `acceptance_packet`
- Current sidecar owner: Codex2
- Assigned sidecar reviewer: Claude
- Initially prepared: `2026-08-02`
- Rebound to merged implementation: `2026-08-10`

## Scope boundary

This is a support-only acceptance checklist, dependency map, and retrospective
evidence packet. It does not change Supervisor behavior, status-writer
behavior, task truth, canonical documents, runtime configuration, registry
state, or governance contracts. Only this sidecar artifact is changed.

The parent implementation is no longer pending. It was merged into `dev` by
PR #619 on `2026-08-04`, and the live status writer now reports the parent task
as unknown (archived rather than active). This packet therefore documents the
shipped result for sidecar reviewer Claude; it neither reopens the parent nor
grants authority to approve or close it.

## Implementation provenance and phase boundary

### Historical pre-implementation snapshot

When this sidecar first entered `in_progress` on `2026-08-02`, the observed
base and parent branch HEAD were
`475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f`. The focused
`-k 'helper_claim'` selection passed 13 tests. At that point the packet was a
pre-implementation contract and correctly did not claim the preservation
change existed.

That snapshot is historical only. It must not be used as the current parent
acceptance target.

### Shipped parent implementation

This revision binds acceptance to the parent result that is already in this
branch:

- implementation commit:
  `943422cc1158944b5c009447c11b7c31bf47b254`;
- test cleanup commit:
  `dd2ddb627d46f15ef42b5792d66be4a3e9fcf880`;
- merged parent task tip:
  `67b4f39ecd7410224b5e34dcc3646305260aed4e`;
- `dev` integration commit / PR #619:
  `777cceed2cca8a1f538be3dce793f329145fbae1`.

Both parent commits and the PR merge commit are ancestors of the current
sidecar HEAD. The binding target for shipped behavior is the PR #619 merge
commit `777cceed2cca8a1f538be3dce793f329145fbae1`, with
`943422cc1158944b5c009447c11b7c31bf47b254` identifying the exact implementation
delta.

The helper-claim selection increased from 13 to 15 because the parent
implementation added exactly these two regressions:

1. `test_dispatcher_helper_claim_preserves_designated_reviewer_when_target_is_not_reviewer`;
2. `test_dispatcher_helper_claim_replaces_reviewer_when_target_is_reviewer`.

The same parent commit also changed reviewer expectations in four existing
routes: idle ready work, disabled-owner-lane sidecar work, explicitly allowed
idle sidecar work, and paused-owner `in_progress` work. The `13 -> 15` delta is
therefore direct parent-change evidence, not unrelated base-advance noise.

## Shipped invariant and explicit fallback

For every selected helper candidate, current `.orchestrator/supervisor.py`
computes:

```text
new_reviewer = designated reviewer
    when it is present and differs from the helper target;
otherwise new_reviewer = previous task owner
```

The ordinary case preserves the existing distinct reviewer while ownership
moves to the helper. There is one deliberate exception that differs from the
original packet's proposed contract: a helper candidate equal to the
designated reviewer is **not rejected**. The claim proceeds, and
`.orchestrator/supervisor.py:12192-12196` falls back to
`new_reviewer = task_owner`. This prevents owner self-review while allowing the
reviewer to become the helper owner. The idle-claim message explicitly says
that the previous owner becomes reviewer in this case.

## Dependency map

| Authority or input | Shipped consumer | Observed result | Evidence or boundary |
| --- | --- | --- | --- |
| Task snapshot `owner` and `reviewer` | `dispatch_ready_tasks` helper-candidate construction | Carries both identities into selection | Existing dependency, active/pending duplicate, workload-cap, failure-loop, status, and sidecar guards still run before a claim |
| Existing designated reviewer and helper target | `new_reviewer` calculation | Preserves the reviewer when distinct from target | Dedicated distinct-target regression plus four updated route assertions |
| Helper target equals designated reviewer | `new_reviewer` calculation | Does not reject target; falls back to previous owner | Dedicated reviewer-target regression expects owner `Codex`, reviewer `Copilot` |
| Computed owner/reviewer pair | `persist_task_reassignment` | Persists helper owner and computed reviewer together | A false persistence result reaches `continue`, before dispatch/event/activity code |
| Canonical status reload | Post-persistence authority check | Uses the reloaded task only when owner and reviewer both match the computed pair | Persisted-authority regression verifies the matching path; on mismatch current code retains the computed pair and refreshes `last_update` rather than failing closed |
| Post-write in-memory task | `build_dispatch_event` | Signs the event with the post-write owner/reviewer pair | Persisted-authority regression verifies owner/reviewer in the event key |
| Reassignment metadata | status call, task object, activity, and operator message | Uses the same computed reviewer; distinct-target idle wording says reviewer preserved, reviewer-target wording says previous owner becomes reviewer | Activity is emitted only after event queueing succeeds |
| Owner/reviewer separation | dispatcher guard and status lifecycle | Avoids dispatching an owner-self-review assignment | Focused owner-self-review regression remains green |

### Actual composition flow

```text
task owner/reviewer snapshot
        |
        v
helper eligibility (reviewer is not excluded here)
        |
        v
reviewer distinct from helper? -- yes --> preserve designated reviewer
        | no
        v
fall back reviewer to previous owner
        |
        v
persist owner + computed reviewer
        |
        v
reload matching canonical task when available
        |
        v
sign event and activity with post-write assignment
```

The shipped production delta is narrow to the Supervisor helper-claim reviewer
calculation. Its parent tests live in `.orchestrator/test_supervisor.py`; the
parent did not change `scripts/ai_status.py`, helper-sidecar generation, task
schemas, configuration, registry/runtime code, or canonical architecture
documents.

## Settled acceptance checklist

Checked items below are confirmed against the shipped implementation and the
current focused replay. Notes explicitly distinguish code inspection from a
dedicated regression.

### Assignment behavior

- [x] A helper claim preserves a present designated reviewer when that reviewer
  differs from the helper target.
- [x] Idle ready-work claims use preserved-reviewer wording in the ordinary
  distinct-target case.
- [x] Paused-owner `in_progress` claims preserve a distinct designated
  reviewer.
- [x] Allowed sidecar claims preserve a distinct designated reviewer.
- [x] A helper target equal to the designated reviewer is accepted and the
  previous owner becomes reviewer; it is not rejected as the original packet
  proposed.
- [x] Repeated claims apply the same conditional rule at each transfer:
  preserve a reviewer distinct from the new helper, otherwise fall back to the
  immediately previous owner. No separate multi-claim regression was added.

### Separation and failure handling

- [x] The dedicated reviewer-target regression proves the fallback produces
  distinct owner/reviewer identities for that route.
- [x] The existing dispatcher owner-self-review regression remains green.
- [x] Missing/empty reviewer input follows the same previous-owner fallback by
  code inspection. The parent did not add a dedicated missing-reviewer test.
- [x] A false `persist_task_reassignment` result exits the claim path before
  delivery-event queueing or success activity by code inspection. The parent
  did not add a dedicated persistence-failure test.
- [x] A canonical reload match must contain both the helper owner and computed
  reviewer before the reloaded task is adopted.
- [x] A reload mismatch is not fail-closed in the shipped code: it retains the
  computed post-write assignment and refreshes `last_update` before event
  construction. The earlier packet's stronger fail-closed statement is
  withdrawn.

### Persisted authority and audit consistency

- [x] The matching canonical reload path supplies both owner and reviewer to
  the dispatch-event key.
- [x] `persist_task_reassignment(new_reviewer=...)`, the in-memory task, and
  `task_helper_claimed.new_reviewer` share the computed value.
- [x] Operator wording distinguishes reviewer preservation from the
  reviewer-target fallback for idle claims.
- [x] Existing dependency, workload-cap, failure-loop, sidecar eligibility,
  paused-lane, and owner-priority guards remain represented in the 15-test
  focused selection.

### Scope conformance

- [x] Parent production work is narrow to Supervisor helper-claim assignment
  behavior, with regression changes in `.orchestrator/test_supervisor.py`.
- [x] Legacy expectations were updated in the affected routes, and two focused
  regressions describe both branches of the reviewer calculation.
- [x] `scripts/ai_status.py` was not changed by the parent or this sidecar.
- [x] This sidecar revision changes only the designated support artifact and no
  L1 canonical truth, runtime registry, governance policy, Supervisor code, or
  live state file.

## Reviewer replay matrix

For this rebound packet, replay:

```bash
python3 -m pytest .orchestrator/test_supervisor.py -q -k 'helper_claim'
python3 -m pytest .orchestrator/test_supervisor.py -q \
  -k 'owner_self_review or helper_claim_uses_persisted_authority_for_event_key'
git diff --check origin/dev...HEAD
```

The review should also confirm:

1. `777cceed2cca8a1f538be3dce793f329145fbae1` is an ancestor of the reviewed
   sidecar HEAD;
2. the sidecar diff contains only this packet;
3. lines 12192-12196 retain the reviewer-target fallback described above;
4. the packet does not claim that the archived parent still awaits
   implementation or composition.

## Re-verification record (2026-08-10)

The current rebound run produced:

```text
python3 -m pytest .orchestrator/test_supervisor.py -q -k 'helper_claim'
15 passed

python3 -m pytest .orchestrator/test_supervisor.py -q \
  -k 'owner_self_review or helper_claim_uses_persisted_authority_for_event_key'
2 passed
```

Both commands exited successfully with the counts shown above. `git diff
--check` was also clean. Because this sidecar changes only Markdown, the parent
runtime and test files remain those already integrated by PR #619.

## Handoff disposition

This packet is rebound to the shipped parent merge, explains the parent-owned
`13 -> 15` regression delta, settles the checklist against actual behavior,
and records the reviewer-as-helper fallback rather than the superseded
rejection proposal. It is handed off to assigned reviewer `Claude` for sidecar
review. No parent implementation or parent-owner composition action remains.
