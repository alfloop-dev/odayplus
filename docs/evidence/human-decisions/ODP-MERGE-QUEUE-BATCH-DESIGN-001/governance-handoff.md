# Governance Handoff — Merge Queue Batch Requirement

- **Task**: ODP-MERGE-QUEUE-BATCH-DESIGN-001
- **Date**: 2026-09-08
- **From**: Antigravity3 (A-stage engineering preparation)
- **To**: WP-90 integrator (governance registry update)

## 1. Purpose

This document provides the governance update fragment for the merge queue
batch requirement, per D21 of the execution plan. It is a scoped fragment
for WP-90 to incorporate into the shared governance manifest; this task
does not directly modify shared manifests or open-decisions files to avoid
multi-worker write conflicts.

## 2. D21 Disposition Update

### 2.1 Previous State

- **Item 19** in `docs/plans/ODP_OPEN_DECISIONS_2026-09-03.md`: "已裁決不做"
  (decided not to implement)
- **Audit result** (`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`): `BLOCKED_BY_EVIDENCE`
  — no authoritative ruling existed; the "decision" was an AI-authored
  configuration default

### 2.2 New State (per D21)

- **User decision**: B — retain as formal implementation requirement (implementation direction selected)
- **Current disposition**: `OPEN` (pending specific evidence: H08 human parameter confirmation and B-stage execution assignment; A-stage design and measurement evidence package delivered)
  - *Distinction between implementation direction and B-stage readiness*: D21 resolves the prior "decided not to implement" misunderstanding. However, per pinned execution plan `be04fe7:81` and `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md:56`, advancing to `IMPLEMENTATION_READY` requires locked requirements/acceptance (including H08 batch parameter confirmation), a concrete delivery batch, and an assigned implementation owner/task (rather than an abstract work package ID like `WP-35`).
- **Scope**: `dev` branch merge queue batching on `alfloop-dev/odayplus`
- **Parameters**: Proposed via measurement in [configuration-options.md](configuration-options.md); pending H08 human confirmation
- **Next action**: Human H08 parameter confirmation, followed by assigning a concrete B-stage engineering task (`ODP-MERGE-QUEUE-BATCH-IMPL-001` / WP-35B) to transition the requirement state to `IMPLEMENTATION_READY`

### 2.3 Governance Registry Entry (for WP-90)

The following is the recommended update to item 19 in the open decisions register for WP-90 to apply in the shared manifest, reflecting D21 implementation selection and A-stage completion while awaiting H08 parameter confirmation:

```
| 19 | merge queue 批次 | OPEN | D21: user selected B (implementation direction); A-stage delivered in ODP-MERGE-QUEUE-BATCH-DESIGN-001; pending H08 parameter selection & B-stage task assignment |
```

If a `set_valued_requirements.json` member is created for this item (which the disposition audit recommended against for a configuration concern), the current truthful entry is:

```json
{
  "member_id": "MERGE_QUEUE_BATCH",
  "status": "absent",
  "disposition": {
    "state": "OPEN",
    "target_phase": "A-stage delivered; B-stage pending H08",
    "note": "D21: user selected implementation direction. A-stage evidence package delivered in ODP-MERGE-QUEUE-BATCH-DESIGN-001. Pending H08 parameter confirmation and B-stage task assignment."
  }
}
```

#### Conditional IMPLEMENTATION_READY Fragment (Upon B-Stage Entry Conditions Fulfilled)

Once H08 parameters are confirmed, a concrete delivery batch is scheduled, and a real execution owner/task (e.g. `ODP-MERGE-QUEUE-BATCH-IMPL-001`) is assigned per `implementation-handoff.md`, WP-90 may advance the disposition to `IMPLEMENTATION_READY`:

```json
{
  "member_id": "MERGE_QUEUE_BATCH",
  "status": "absent",
  "disposition": {
    "state": "IMPLEMENTATION_READY",
    "assigned_to": "<assigned-task-id-e.g.-ODP-MERGE-QUEUE-BATCH-IMPL-001>",
    "target_phase": "B-stage",
    "note": "D21: implementation selected; H08 parameters confirmed; B-stage implementation assigned."
  }
}
```

This task does **not** create or modify this entry; it is provided as a recommendation for WP-90.

## 3. What WP-90 Should Update

1. **Item 19 in `ODP_OPEN_DECISIONS_2026-09-03.md`** (or its successor):
   Change from `BLOCKED_BY_EVIDENCE` to `OPEN` with D21 reference and A-stage completion record (pending H08 parameters and B-stage task assignment).
2. **Governance registry** (if merge queue batch becomes a tracked member):
   Add the `OPEN` entry above, advancing to `IMPLEMENTATION_READY` only when B-stage entry conditions are met and a real execution task is assigned.
3. **Cross-reference**: Link to this task's evidence package at
   `docs/evidence/human-decisions/ODP-MERGE-QUEUE-BATCH-DESIGN-001/`

## 4. What This Task Does NOT Update

- `docs/plans/ODP_OPEN_DECISIONS_2026-09-03.md` (shared manifest, WP-90 owned)
- `set_valued_requirements.json` (shared manifest, WP-90 owned)
- `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md` (shared governance, WP-90 owned)
- Any live queue configuration
- Any required check or review gate

## 5. Relation to Disposition Audit

The disposition audit (`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`) submitted an
unsigned disposition envelope to Human/Ops for ruling. D21 resolves that
handback: the user chose implementation, not waiver. The unsigned envelope's
"maintain no-batch" path is therefore superseded by D21's implementation
direction.

The audit's gate improvements (note-as-amendment detection,
`WAIVER_SIGNAL_FIELDS` narrowing, handback reference requirements) remain
valid and are unaffected by D21.
