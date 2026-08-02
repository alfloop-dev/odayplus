# Sidecar Acceptance Packet & Dependency Map: ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001

- **Task ID**: `ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001-SIDECAR-ACCEPTANCE`
- **Parent Task ID**: `ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001`
- **Helper Kind**: `acceptance_packet`
- **Task Class**: `sidecar`
- **Owner**: `Antigravity5`
- **Reviewer / Parent Owner**: `Antigravity2`
- **Target Release Claim**: `no-go-until-final-gate-audit`
- **Phase**: `Orchestrator Control Plane`

---

## 1. Executive Summary & Scope Boundary

This document serves as the sidecar support packet and acceptance specification for parent task `ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001` ("Roll merged review-head freeze into live Supervisor").

### Scope Boundaries
- **Support Only**: This packet is a support artifact created under sidecar isolation. It does **not** mutate L1 canonical architecture documents, core runtime contracts, or production Supervisor binaries directly.
- **Purpose**: Establishes the authoritative acceptance checklist, dependency map, fail-closed verification rules, live probe isolation requirements, and handoff criteria required before parent task `ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001` can be submitted for canonical review and closeout.

---

## 2. Live Rollout & Controlled Publication Requirements

Parent implementation `ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001` deploys the control plane review-head freeze (PR #505 exact-head freeze) to the live Supervisor root while preserving active worker processes and dirty runtime state.

### Key Deployment Gates & Verification Criteria

| Deployment Stage | Required Operation | Verification / Receipt Proof |
| :--- | :--- | :--- |
| **1. Fleet Dispatch Documentation** | Create `docs/evidence/fleet_dispatch/ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001.md` before any live write. | Document committed with execution steps, preflight plan, and rollback procedures. |
| **2. Source SHA Hash Verification** | Validate exact source SHA256 hashes from PR #505 for `.orchestrator/supervisor.py` and `scripts/ai_status.py`. | `.orchestrator/supervisor.py` SHA: `3bb01341fee9b5d10f78591003d74aab299826a68c9b5fa9c1529175c90e6050`<br/>`scripts/ai_status.py` SHA: `bc1ba0c2f60e58d6038480e686c69abc064f08fd36c38c1b4b7ec33dc832856e` |
| **3. Preflight State Capture** | Log live Supervisor state prior to modification. | Record `ActiveState`, `SubState`, `MainPID`, `ExecMainStartTimestamp`, `NRestarts`, heartbeat, and active worker list. |
| **4. Backup & Rollback Provisioning** | Create byte-level backups and executable rollback scripts. | Backup binaries stored in `docs/evidence/runtime/ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001/backup/` with atomic revert commands. |
| **5. Atomic Publication** | Publish exact-head files using atomic `os.replace` operations across live target roots. | Zero partial writes or broken sibling imports; destination file hashes match source exactly. |
| **6. Controlled Single Restart** | Execute exactly one controlled Supervisor restart via SIGTERM / systemd driver. | New live `MainPID` verified; `SubState` running; `NRestarts` unchanged or incremented by exactly 1; fresh heartbeat recorded. |
| **7. Fail-Closed Live Probes** | Run B23, B24, N3 live probes using an isolated temporary `PANTHEON_STATUS_ROOT`. | Probes pass without mutating live status files (`ai-status.json`), task archive, or dashboard bundles. |
| **8. Preserved Disabled Agents** | Maintain `ready_dispatcher.disabled_agents` configuration. | `Claude`, `Claude2`, and `Claude3` remain in disabled agents list. |

---

## 3. Comprehensive Dependency Map

```mermaid
graph TD
    A["ODP-ORCH-REVIEW-HEAD-FREEZE-001<br/>(Upstream PR #505 Control Plane Fixes)"] --> B["ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001<br/>(Parent Live Rollout Task)"]
    C["PR #505 Source Blobs<br/>(supervisor.py & ai_status.py)"] --> B
    B --> D["support/sidecars/ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001/<br/>ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001-SIDECAR-ACCEPTANCE.md<br/>(This Acceptance Packet)"]
    B --> E["Live Supervisor Runtime<br/>(PID 262802 / systemd control plane)"]
    B --> F["ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001<br/>(Downstream Control Plane Rollout)"]
    B --> G["ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001<br/>(Downstream Live Rollout)"]
```

### Upstream Dependencies
- **`ODP-ORCH-REVIEW-HEAD-FREEZE-001`**: Upstream task delivering PR #505 control plane fix (exact review-head freeze, CI status check classification, and on-disk status sync).
- **PR #505 Source Hashes**: Merge commit `6af7b86ba4aa34d5bf26142f64f3cb96c429b557` providing immutable SHA256 hashes for `.orchestrator/supervisor.py` and `scripts/ai_status.py`.

### Downstream Dependencies
- **`ODP-ORCH-ACTOR-REF-LIVE-ROLLOUT-001`**: Depends on live Supervisor running the exact-head review freeze to prevent dispatch churn during actor reference rollout.
- **`ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001`**: Depends on review-head freeze control plane stability for deferred approval reconciliation.

---

## 4. Fail-Closed Acceptance Checklist Matrix

Parent task `ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001` must satisfy all fail-closed criteria below. Any violation requires an immediate fail-closed state.

| Criterion | Rule Description | Fail-Closed Trigger (Must Reject) | Verification & Audit Evidence |
| :--- | :--- | :--- | :--- |
| **Criterion A** | **Fleet Dispatch Preflight Rule** | Modifying live files before committing `docs/evidence/fleet_dispatch/ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001.md`. | Preflight dispatch doc commit receipt timestamp prior to live write. |
| **Criterion B** | **Source Blob Integrity Rule** | Source file SHA256 mismatch with PR #505 exact-head hashes (`3bb01341...` / `bc1ba0c2...`). | Direct `sha256sum` verification output on source and deployed target files. |
| **Criterion C** | **Preflight Backup & Atomic Write Rule** | Non-atomic file copying, missing byte backups, or overwriting live files without rollback script. | Preflight backup file existence and `os.replace` invocation log. |
| **Criterion D** | **Controlled Single Restart Rule** | Uncontrolled process crash, multiple restarts, or failure of live Supervisor to achieve running state. | Systemd journal timestamp, PID transition evidence, and single restart assertion. |
| **Criterion E** | **Isolated Probe Execution Rule** | Running live probes (B23, B24, N3) against the live status root instead of temporary `PANTHEON_STATUS_ROOT`. | Probe command logs proving `$PANTHEON_STATUS_ROOT` redirection to `/tmp/...`. |
| **Criterion F** | **Disabled Agents Safety Rule** | Accidentally enabling disabled agent lanes (`Claude`, `Claude2`, `Claude3`) in dispatcher config. | Config inspection receipt showing `ready_dispatcher.disabled_agents` intact. |
| **Criterion G** | **Independent Review & Non-Mutation Rule** | Modifying Package 10 UI, API logic, cloud resources, or completing closeout without independent `Antigravity5` review. | Independent review approval entry in activity log and clean diff outside control plane evidence. |

---

## 5. Verification Suite & Baseline Checks

### Required Verification Commands
1. **Orchestrator Unit Test Suite**:
   ```bash
   /home/lupin/oday-plus-supervisor-live/.venv/bin/pytest .orchestrator scripts -q -m "not requires_live_env"
   ```
2. **Code & Style Integrity**:
   ```bash
   ruff check .orchestrator scripts
   git diff --check
   ```
3. **Live Probe Verification (Isolated Environment)**:
   ```bash
   PANTHEON_STATUS_ROOT=/tmp/test-status-root AI_NAME=Antigravity5 python3 -m pytest .orchestrator/test_supervisor.py -k "B23 or B24 or N3"
   ```

---

## 6. Handoff & Sign-Off Instructions

- **Assigned Reviewer / Parent Owner**: `Antigravity2`
- **Sidecar Owner Verification**: `Antigravity5` (Sidecar support packet verified; git diff clean; ready for handoff)
- **Handoff Instructions**:
  1. Review this acceptance packet for alignment with parent task `ODP-ORCH-REVIEW-HEAD-FREEZE-LIVE-ROLLOUT-001`.
  2. Verify that live Supervisor rollout evidence satisfies all 7 criteria in Section 4.
  3. Keep this support packet linked as the authoritative acceptance reference for parent rollout closeout.
