---
doc_id: ODP-SLO-DR-RATIFY-001
title: Reliability SLO and DR Target Ratification (ODP-PGAP-RELIABILITY-001)
version: 0.1.0
status: ratified
owner: Human/Ops
ratified_by: Human/Ops (ops decision 2026-07-16, option 1)
related_task: ODP-PGAP-RELIABILITY-001
supersedes_absence_of:
  - docs/evidence/PRODUCT_PLATFORM_GAP_AUDIT_2026-07-13.md
  - docs/design/PRODUCT_PLATFORM_P1_FLEET_EXECUTION_TASKS_2026-07-15.md
updated_at: 2026-07-16
---

# Reliability SLO and DR Target Ratification

## 1. Purpose

ODP-PGAP-RELIABILITY-001's AC1 (load/soak at *declared* concurrency and volume)
and AC6 (DR RPO/RTO) could not be closed because the two source documents meant to
declare those targets never existed in the repo or its history. This is the
explicit Human/Ops ratification of those targets (ops decision 2026-07-16, option
1), so the reviewer can verify the delivered implementation against declared — not
self-selected — numbers. It ratifies TARGETS only; section 4 states exactly what
the current evidence does and does not prove.

## 2. Ratified AC1 Performance Targets (API + queue + DB path)

| Target | Ratified value | Current measured evidence |
|---|---|---|
| Concurrency levels exercised | 10, 20, 50 | met (`load_soak_performance_report.json`) |
| Request volume per run | 150 | met (150/150 success) |
| p95 latency budget | <= 3.0 s | met (measured p95 2.72 s) |
| Failure count under target load | 0 | met |

**Scope boundary (ratified):** covers the API, async queue, and persistence path
only. Browser, batch, and solver performance are explicitly OUT of scope for this
milestone and deferred to a named follow-up (ODP-PGAP-RELIABILITY-002, to be
created); they are NOT closed by this task.

## 3. Ratified AC6 Disaster-Recovery Targets

| Target | Ratified value |
|---|---|
| RPO (max data loss) | <= 60 minutes |
| RTO (max recovery time) | <= 240 minutes |

## 4. Honest evidence scope (what is and is NOT proven)

- The current DR drill (`dr_drill_records.json`) is a single-node, local
  backup/restore verification (`shutil.copy`-based). It validates the recovery
  mechanism, metadata, and the corrupt-backup fail path. Its sub-second measured
  RPO/RTO are an artifact of the local-copy method and are NOT a demonstration
  that the ratified 60-min / 240-min targets hold under a real distributed or
  production failure.
- A production-representative DR drill (remote store, realistic volume, network
  partition/restore) is a named follow-up required before any production-go
  decision. It is not claimed here.
- AC1 evidence is deterministic and local; sustained-soak and production-volume
  validation are follow-ups.

## 5. Decision

Per Human/Ops option 1: the targets in sections 2-3 are ratified as the official
acceptance targets for ODP-PGAP-RELIABILITY-001, subject to the scope and evidence
limitations in section 4. Follow-ups (full-surface performance, real DR drill) are
tracked separately and gate production readiness.
