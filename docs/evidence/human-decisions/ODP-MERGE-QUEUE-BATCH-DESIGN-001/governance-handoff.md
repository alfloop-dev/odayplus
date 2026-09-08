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

- **User decision**: B — retain as formal implementation requirement
- **Disposition**: `IMPLEMENTATION_READY` (not `DECIDED`; the user chose to
  implement, not to waive)
- **Scope**: `dev` branch merge queue batching on `alfloop-dev/odayplus`
- **Parameters**: To be determined by measurement and H08 human review
- **Next action**: WP-35B engineering implementation after H08 parameter
  confirmation

### 2.3 Governance Registry Entry (for WP-90)

The following is the recommended update to item 19 in the open decisions
register. WP-90 is responsible for the actual write:

```
| 19 | merge queue 批次 | IMPLEMENTATION_READY | D21: user selected B (retain as requirement); parameters pending H08 measurement-based proposal |
```

If a `set_valued_requirements.json` member is created for this item (which
the disposition audit recommended against for a configuration concern), it
should carry:

```json
{
  "member_id": "MERGE_QUEUE_BATCH",
  "status": "absent",
  "disposition": {
    "state": "IMPLEMENTATION_READY",
    "assigned_to": "WP-35",
    "target_phase": "B-stage",
    "note": "D21: user selected implementation. A-stage evidence package delivered in ODP-MERGE-QUEUE-BATCH-DESIGN-001. Parameters pending H08."
  }
}
```

This task does **not** create or modify this entry; it is provided as a
recommendation for WP-90.

## 3. What WP-90 Should Update

1. **Item 19 in `ODP_OPEN_DECISIONS_2026-09-03.md`** (or its successor):
   Change from `BLOCKED_BY_EVIDENCE` to `IMPLEMENTATION_READY` with D21
   reference
2. **Governance registry** (if merge queue batch becomes a tracked member):
   Add the entry above
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
