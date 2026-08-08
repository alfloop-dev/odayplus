# Review Packet: ODP-CAP-UNOWNED-SCOPE-DECISION-001

- Sidecar task: `ODP-CAP-UNOWNED-SCOPE-DECISION-001-SIDECAR-REVIEW`
- Parent task: `ODP-CAP-UNOWNED-SCOPE-DECISION-001`
- Sidecar owner: `Antigravity4`
- Sidecar reviewer: `Claude`
- Canonical parent owner: `Claude`
- Canonical parent reviewer: `Antigravity2`
- Evidence captured: `2026-08-07` UTC
- Target / Parent branch: `origin/dev` / `task/ODP-CAP-UNOWNED-SCOPE-DECISION-001`
- Key parent commit: `dda55b919ca2a2d7c2a2dbbb624197819e01786e` (= parent `approved_head`, PR #646 head)
- Parent task status: `review_approved`
- Scope: Review packet and evidence summary only; no L1 canonical truth or runtime code modified in this sidecar.

---

## 1. Executive Summary & Background

Parent task `ODP-CAP-UNOWNED-SCOPE-DECISION-001` records the governance decision for five unowned MUST capabilities (U-1 through U-5) identified during the Package 10 canonical frontend convergence review.

### Operational Context & Root Cause

1. **Unowned MUST Capabilities Risk**:
   While Package 10 intentionally converged the canonical frontend into 3 routes (`/operator`, `/intake/[intakeId]`, `/franchisee`) and 5 workspaces, a comparison against `ODP-SA-06` (functional requirements) and `ODP-UX-03` (screen specifications) revealed 5 MUST capabilities that were not in canonical runtime, not in the 26-task governance ledger, and had no approved deviation or ADR. Leaving them in a "neither implemented nor formally excluded" state presented a major governance risk for RTM auditing (`ODP-PLAN-FINAL-GATE-AUDIT-001`).

2. **Formal Governance Decision (Option A)**:
   On 2026-08-04, Human/Ops formally decided **Option A** ("Schedule into next release") for all five capabilities. None took a deviation (Option B) or scope change / ADR (Option C). Therefore, the original MUST scope in `ODP-SA-06` and `ODP-UX-03` stands unchanged and all five items must be implemented to spec depth.

3. **Parent Task Deliverable**:
   Parent commits `72262e2d` and `dda55b91` updated `docs/design/ODAY_PLUS_UNOWNED_CAPABILITY_SCOPE_DECISION_2026-08-03.md` to:
   - Formally record Option A decisions in §6.
   - Snapshot the landing of follow-up execution task packets in §6.1.
   - Explicitly define acceptance criteria (§7) for each capability to prevent superficial "UI-only" closeouts.
   - Record the impact on RTM and final gate audit (§8).

**Bottom line for the reviewer**: The decision record on parent HEAD `dda55b91` is complete, accurate, verified against the status root/task archive, and currently in `review_approved` status awaiting parent closeout.

---

## 2. Reviewed Change Surface

Parent task commit `dda55b91` (and `72262e2d`) modifies a single governance decision document without altering L1 canonical architecture documents or core runtime contracts:

| File | Subsystem Role | Implementation Summary |
| --- | --- | --- |
| `docs/design/ODAY_PLUS_UNOWNED_CAPABILITY_SCOPE_DECISION_2026-08-03.md` | Governance Decision Record | Formally records Option A decisions for U-1 ~ U-5, maps follow-up task packets (§6, §6.1), specifies spec-depth acceptance criteria (§7), and defines RTM/Final Gate impact (§8). |

### Summary of Document Updates in Parent Head (`dda55b91`)

1. **Section 6 (Decision Record)**:
   - Records Option A decision by Human/Ops on 2026-08-04 for all 5 capabilities.
   - Maps follow-up tasks in order of §4 priority:
     1. U-3 (Feature Flag UI) -> `ODP-CAP-FEATURE-FLAG-UI-001`
     2. U-4 (Notification Delivery) -> `ODP-CAP-NOTIFICATION-DELIVERY-001`
     3. U-5 (Task Attachments) -> `ODP-CAP-TASK-ATTACHMENTS-001`
     4. U-1 (Model Release Controller UI) -> `ODP-CAP-MODEL-RELEASE-UI-001`
     5. U-2 (User & Role Management UI) -> `ODP-CAP-USER-ROLE-UI-001`

2. **Section 6.1 (Execution Task Landing Snapshot)**:
   - Verifies that all 5 follow-up execution task packets have been formally created and assigned on the task board.
   - Clarifies that closing/archiving an execution task does not automatically mark the underlying Functional Requirement (FR) as verified until `ODP-PLAN-FINAL-GATE-AUDIT-001` re-runs the RTM.

3. **Section 7 (Acceptance Criteria per Capability)**:
   - **U-3 (`ODP-CAP-FEATURE-FLAG-UI-001`)**: Enforces `FR-SHARED-004` ("UI, API, and Job must all be gated when flag is disabled") and `FR-GOV-009` runtime kill-switch requirements without redeployment.
   - **U-4 (`ODP-CAP-NOTIFICATION-DELIVERY-001`)**: Restores delivery adapters for Email, InApp, and Webhook across 5 spec triggers (task assign, timeout, approval, failure, rollback) per `FR-SHARED-006`.
   - **U-5 (`ODP-CAP-TASK-ATTACHMENTS-001`)**: Binds storage, scoped access, and `FR-SHARED-007` sensitivity masking for site-survey photos and lease scans per `FR-OPS-002`.
   - **U-1 (`ODP-CAP-MODEL-RELEASE-UI-001`)**: Connects operational UI over the 10 existing endpoints in `apps/api/app/routes/learninghub.py` for Backtest/Canary/Shadow/Rollback with audit trail per `FR-LH-003` & `FR-GOV-009`.
   - **U-2 (`ODP-CAP-USER-ROLE-UI-001`)**: Covers RBAC/ABAC CRUD operations and audit trailing per `FR-OPS-003`, guarding against unauthorized scope narrowing without an ADR (`ODP-00-04`).

4. **Section 8 (Impact on RTM & Final Gate)**:
   - Confirms all 5 capabilities transition from "unclassifiable gaps" to "scheduled but undelivered" for the 84-row RTM re-run.

---

## 3. Safety & Architectural Boundary Compliance

1. **Zero L1 Mutation**:
   No changes were made to L1 canonical architecture documents (`TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `PERSONA_RUNTIME_MODEL.md`, etc.), core runtime contracts, or database schemas.

2. **No Unapproved Deviations or Scope Narrowing**:
   Human/Ops explicitly selected Option A for all 5 capabilities. No scope was dropped or silently narrowed.

3. **Anti-Narrowing Guard**:
   Section 7 explicitly forbids closing execution tasks with superficial "UI-only" mockups or missing backend wiring, protecting contract integrity.

4. **Sidecar Scope Boundary**:
   This sidecar (`ODP-CAP-UNOWNED-SCOPE-DECISION-001-SIDECAR-REVIEW`) creates only support artifacts under `support/sidecars/ODP-CAP-UNOWNED-SCOPE-DECISION-001/` and does not edit canonical repository truth.

---

## 4. Verification & Evidence Summary

### 1. Parent Commit & Document Inspection
```bash
git show dda55b91:docs/design/ODAY_PLUS_UNOWNED_CAPABILITY_SCOPE_DECISION_2026-08-03.md
```
**Result**: Confirmed Section 6, 6.1, 7, and 8 contain complete decision records, task mappings, acceptance criteria, and gate audit rules.

### 2. Follow-Up Task Board Verification
Cross-checked live status root (`ai-status.json`) and task archive (`ai-task-archive/tasks/`):

| Capability | Task ID | Owner / Reviewer | Status (as of 2026-08-07) | Archive / Live Location |
| --- | --- | --- | --- | --- |
| U-3 Feature Flag UI | `ODP-CAP-FEATURE-FLAG-UI-001` | Antigravity / Antigravity2 | `review_approved` | Live (`ai-status.json`) |
| U-4 Notification Delivery | `ODP-CAP-NOTIFICATION-DELIVERY-001` | Claude / Claude3 | `done` | Archived (`ai-task-archive/tasks/`) |
| U-5 Task Attachments | `ODP-CAP-TASK-ATTACHMENTS-001` | Antigravity / Antigravity6 | `review_approved` | Live (`ai-status.json`) |
| U-1 Model Release UI | `ODP-CAP-MODEL-RELEASE-UI-001` | Antigravity4 / Antigravity6 | `done` | Archived (`ai-task-archive/tasks/`) |
| U-2 User & Role UI | `ODP-CAP-USER-ROLE-UI-001` | Antigravity / CodexCoordinator | `review` | Live (`ai-status.json`) |

### 3. Sidecar Scope Isolation
```bash
git diff --stat origin/dev...HEAD
```
**Result**: Only support sidecar artifacts within `support/sidecars/ODP-CAP-UNOWNED-SCOPE-DECISION-001/` are modified/added by this sidecar lane.

---

## 5. Sidecar Boundary & Reviewer Handoff

- **Deliverable Scope**: This review packet (`support/sidecars/ODP-CAP-UNOWNED-SCOPE-DECISION-001/ODP-CAP-UNOWNED-SCOPE-DECISION-001-SIDECAR-REVIEW.md`) is the sole artifact created by `ODP-CAP-UNOWNED-SCOPE-DECISION-001-SIDECAR-REVIEW`.
- **L1 Canonical Safety**: Confirmed zero L1 canonical document or runtime source modification.
- **Handoff Action**: Transitioning task status to `review` via canonical status tool `ai-status.sh handoff`, assigning sidecar review to `Claude`.
