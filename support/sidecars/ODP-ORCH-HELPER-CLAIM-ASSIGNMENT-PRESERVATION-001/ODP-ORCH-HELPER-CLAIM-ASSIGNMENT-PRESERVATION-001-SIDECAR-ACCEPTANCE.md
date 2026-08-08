# Helper-claim assignment preservation acceptance packet

- Sidecar task: `ODP-ORCH-HELPER-CLAIM-ASSIGNMENT-PRESERVATION-001-SIDECAR-ACCEPTANCE`
- Parent task: `ODP-ORCH-HELPER-CLAIM-ASSIGNMENT-PRESERVATION-001`
- Helper kind: `acceptance_packet`
- Sidecar owner: Codex5
- Assigned sidecar reviewer and parent owner: Antigravity2
- Parent reviewer: Antigravity6
- Prepared: `2026-08-02`

## Scope boundary

This is a support-only acceptance checklist and dependency map. It does not
change Supervisor behavior, status-writer behavior, task truth, canonical
documents, runtime configuration, or governance contracts. Antigravity2 owns
the parent implementation and decides whether to compose this packet into the
parent review. This packet is neither approval of the parent task nor authority
to close it.

## Frozen parent baseline

Observed after the sidecar entered `in_progress` on `2026-08-02`:

- Parent live status: `in_progress`; owner Antigravity2; reviewer Antigravity6.
- Parent branch: `task/ODP-ORCH-HELPER-CLAIM-ASSIGNMENT-PRESERVATION-001`.
- Parent branch HEAD: `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f`, equal to the
  then-current sidecar base and `origin/dev`.
- Parent task worktree had no tracked task implementation diff. Its only
  untracked paths were supervisor-seeded task context files.
- The baseline helper-claim regression selection passed 13 tests, but those
  tests encode the behavior under repair: multiple cases expect the previous
  owner to replace the already designated reviewer.

This is a pre-implementation contract packet. It must not be read as evidence
that the parent change already exists. A later parent commit, changed task
assignment, or changed base requires the reviewer to bind acceptance to the new
exact parent HEAD and rerun the focused checks.

## Problem statement and invariant

The helper-claim path in `.orchestrator/supervisor.py` currently transfers
ownership to an eligible helper and computes the new reviewer from the previous
owner before persisting the reassignment. Repeated helper claims can therefore
rotate review authority even when the task already names a distinct designated
reviewer.

The parent change should enforce one narrow invariant:

> A helper claim may transfer execution ownership, but it must not silently
> replace an existing, distinct designated reviewer.

Preservation must remain compatible with the status invariant that owner and
reviewer are different identities. In particular, the designated reviewer must
not be selected as helper owner while that reviewer assignment is being
preserved. Missing or malformed legacy reviewer data must fail closed or use an
explicitly tested safe fallback; it must not create owner self-review.

## Dependency map

| Authority or input | Parent consumer | Required result | Fail-closed condition |
| --- | --- | --- | --- |
| Task snapshot `owner` and `reviewer` | `dispatch_ready_tasks` helper-candidate construction | Carry both identities unchanged into the claim decision | Missing task identity, active/pending duplicate, or unsatisfied dependencies still prevents a claim |
| Existing designated reviewer | `choose_helper_claim_agent` and helper target selection | Candidate helper must be distinct from the reviewer | A reviewer-as-helper candidate is rejected rather than producing identical owner/reviewer or rotating review authority |
| Eligible helper and previous owner | `persist_task_reassignment` call | Change only owner; preserve the valid reviewer | Persistence failure emits no dispatch event and no success activity |
| Canonical status writer reload | Persisted-task authority check | Reloaded owner equals helper and reviewer equals the preserved reviewer | Mismatched or unavailable persisted authority must not be represented as a successful reassignment |
| Persisted owner/reviewer pair | `ready_dispatch_signature` / `build_dispatch_event` | Event key and task payload contain the post-write owner and preserved reviewer | Stale pre-write authority must not sign the event |
| Reassignment metadata | handoff record, `task_helper_claimed` activity, and operator message | All surfaces name the same helper and preserved reviewer; wording does not claim that the prior owner became reviewer | Contradictory reviewer fields or obsolete operator text fails review |
| `scripts/ai_status.py` state validation and review handoff | Downstream `handoff`, `approve`, and `done` commands | Independent designated reviewer remains the only valid review target | Identical owner/reviewer continues to be rejected; helper claim must not weaken lifecycle validation |

### Intended composition boundary

```text
task owner/reviewer snapshot
        |
        v
helper eligibility ---- reject reviewer-as-helper
        |
        v
persist owner transfer + preserve reviewer
        |
        v
reload canonical task truth
        |
        v
sign dispatch event and activity with the same assignment
        |
        v
existing owner -> designated reviewer handoff/review lifecycle
```

The likely parent change surface is limited to
`.orchestrator/supervisor.py` and `.orchestrator/test_supervisor.py`. This
packet does not require a change to generic reassignment policy,
`scripts/ai_status.py`, helper-sidecar generation, task schemas, configuration,
or canonical architecture documents.

## Acceptance checklist

### Assignment preservation

- [ ] A `todo` task claimed while its owner handles higher-priority work keeps
  its pre-claim designated reviewer.
- [ ] An idle-work helper claim keeps the designated reviewer, and its human-
  readable message no longer states that the previous owner becomes reviewer.
- [ ] A paused-owner claim of an `in_progress` task keeps the designated
  reviewer.
- [ ] A helper claim of an allowed sidecar keeps the designated reviewer.
- [ ] Repeated claims do not rotate the reviewer through the chain of prior
  owners.

### Separation and fail-closed behavior

- [ ] A helper candidate equal to the designated reviewer is not used for the
  ownership transfer while preserving that reviewer.
- [ ] No successful claim can persist or dispatch identical owner and reviewer
  identities.
- [ ] Missing or invalid reviewer data has an explicit regression proving the
  chosen fail-closed behavior; it is not silently converted into self-review.
- [ ] A failed status persistence operation does not queue a delivery event or
  write a successful `task_helper_claimed` activity record.

### Persisted authority and audit consistency

- [ ] The post-persistence reload check expects both the helper owner and the
  preserved reviewer.
- [ ] The dispatch-event key and task payload are derived from the reloaded
  assignment, not the stale pre-claim snapshot.
- [ ] `persist_task_reassignment(new_reviewer=...)`, the in-memory task,
  handoff reconciliation, activity `new_reviewer`, and operator-facing message
  all agree.
- [ ] Existing dependency, workload-cap, failure-loop, sidecar eligibility,
  paused-lane, and owner-priority guards remain intact.

### Scope conformance

- [ ] Parent production changes remain narrow to Supervisor helper-claim
  assignment behavior.
- [ ] Regression updates replace legacy expectations that the prior owner
  always becomes reviewer; unrelated reassignment tests are not weakened.
- [ ] `scripts/ai_status.py` continues rejecting owner self-review.
- [ ] No L1 canonical truth, runtime registry, governance policy, or live state
  file is changed by this sidecar.

## Reviewer replay matrix

At the exact parent review HEAD, run at minimum:

```bash
python3 -m pytest .orchestrator/test_supervisor.py -q -k 'helper_claim'
python3 -m pytest .orchestrator/test_supervisor.py -q \
  -k 'owner_self_review or helper_claim_uses_persisted_authority_for_event_key'
git diff --check origin/dev...HEAD
```

Then audit the parent diff and confirm that updated tests cover these distinct
routes rather than only one happy path:

1. higher-priority owner load (`todo`);
2. idle claim (`todo`);
3. owner dispatch pause (`in_progress`);
4. allowed sidecar claim;
5. target helper equals designated reviewer;
6. canonical reload/event-signature authority;
7. repeated claim with a reviewer already inherited from the original task.

For the parent review, record the exact HEAD, test counts, and changed paths.
Any parent HEAD movement invalidates the result and requires `re_review`.

## Sidecar verification record

The preparer ran the baseline helper-claim selection before writing this
packet:

```text
python3 -m pytest .orchestrator/test_supervisor.py -q -k 'helper_claim'
13 passed
```

That result proves the pre-change test baseline is runnable; it does not prove
the preservation invariant. The parent reviewer must ensure legacy assertions
such as `new_reviewer == previous_owner` are replaced or supplemented with the
preservation and reviewer-as-helper rejection cases above.

## Handoff disposition

This packet is ready for Antigravity2 to review and use as the parent task's
acceptance contract. Parent implementation, exact-head verification, and
composition remain Antigravity2's responsibility, with independent parent
review authority held by Antigravity6.
