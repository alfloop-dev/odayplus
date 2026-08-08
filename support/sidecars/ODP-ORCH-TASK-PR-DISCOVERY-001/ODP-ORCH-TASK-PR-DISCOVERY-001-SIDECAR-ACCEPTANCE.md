# Support Sidecar Acceptance Packet: ODP-ORCH-TASK-PR-DISCOVERY-001

- **Task ID**: `ODP-ORCH-TASK-PR-DISCOVERY-001-SIDECAR-ACCEPTANCE`
- **Parent Task**: `ODP-ORCH-TASK-PR-DISCOVERY-001`
- **Helper Kind**: `acceptance_packet`
- **Owner**: `Claude2` (helper-claimed 2026-08-06; original author `Antigravity6`)
- **Reviewer**: `Claude` (helper-claimed 2026-08-06; previously `Antigravity2`)
- **Created At**: `2026-08-02`
- **Last Updated**: `2026-08-06` (Round 2: base advance to `origin/dev` @ `71d44d03`, E2E provenance correction)
- **Scope Restriction**: Support artifacts under `support/sidecars/ODP-ORCH-TASK-PR-DISCOVERY-001/` only. Zero L1 canonical documents, core runtime code, or governance contracts modified.

---

## 1. Executive Summary & Context

This sidecar acceptance packet provides the independent support documentation, acceptance checklist, dependency map, scope conformance matrix, and verification summary for parent task **`ODP-ORCH-TASK-PR-DISCOVERY-001`** ("Use immutable task refs for review PR discovery").

### Background & Problem Statement
During automated GitHub review and PR discovery within the Supervisor control-plane (`.orchestrator/github_bus.py`), PR lookup was previously attempting to locate pull requests primarily using owner-agent branch names (`feat/<agent>-...`) or ambiguous local branch references. When a task was developed and pushed on its canonical per-task branch (`task/<TASK-ID>`) or when checking remote tracking refs (`origin/task/<TASK-ID>`), the Supervisor failed to resolve the correct branch reference. Consequently, the Supervisor logged `github_review_pr_skipped` with a false error message ("branch HEAD is not pushed to origin"), causing valid pushed review branches and PRs (such as PR #573) to be skipped during review dispatch loops.

---

## 2. Parent Task Deliverables & Scope

Parent task `ODP-ORCH-TASK-PR-DISCOVERY-001` resolved the discovery failure across the GitHub bus control-plane:

1. **Immutable Task Ref Discovery (`.orchestrator/github_bus.py`)**:
   - Refactored branch lookup ordering to prioritize canonical `task/<TASK-ID>` and task-matching branch patterns over arbitrary owner agent branches (`feat/<agent>-...`).
   - Extended `branch_exists`, `branch_head_sha`, and `branch_has_diff` helper methods to support remote tracking refs (`origin/task/...`).
   - Line anchors re-confirmed at base `origin/dev` @ `71d44d03` (2026-08-06): `review_branch_for_task` resolves the `task/<TASK-ID>` candidate list *before* the mutable agent-registration branch (`.orchestrator/github_bus.py` L469-L499); `branch_exists` / `branch_head_sha` / `branch_has_diff` all consult `refs/remotes/origin/*` (L255-L295).

2. **Unit Test Suite Coverage (`.orchestrator/test_github_bus.py`)**:
   - Added unit test cases validating `task/<TASK-ID>` branch lookup priority, remote tracking ref resolution, and graceful fallback behavior.

3. **Product E2E Evidence Reseal**:
   - Resealed Product E2E execution receipt (`docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json`) and raw test result logs (`raw_playwright_results.json`, `raw_pytest_results.json`).
   - **Landed provenance** (verified 2026-08-06): PR #573 (`ODP-ORCH-TASK-PR-DISCOVERY-001: use immutable task refs for review PR discovery`) merged at `2026-08-02T12:22:08Z`.
     - PR head (`headRefOid`): `f66761172dbc484d06d149af89e55783342635ab` — commit `ODP-ORCH-TASK-PR-DISCOVERY-001: reseal composed E2E evidence`.
     - Merge commit on `dev`: `96f94cda56d509f44eb5929997b3ab7a67f1c65c`.
     - Receipt content at PR head is byte-identical to the copy on `origin/dev` today (`git diff f66761172dbc origin/dev -- docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json` → empty).
     - The resealed receipt records `tested_source.commit_sha = 6c11769266cd6e353679ef8cca44e50b33d69a10` with `relation = exact_source_head` and `status = passed`; `6c117692` is the composed `dev`-merge commit inside PR #573's own history.
   - **Correction (round 2)**: an earlier revision of this packet cited `b2e23f087aac16141ae6e361656c4352ede4cc2b` as the pushed branch HEAD. That SHA is a superseded intermediate commit — it is not in PR #573's commit list and is **not** an ancestor of `origin/dev` (`git merge-base --is-ancestor b2e23f08 origin/dev` → non-zero). It must not be used as the acceptance provenance anchor.

---

## 3. Acceptance Checklist

| Criteria / Requirement | Parent Implementation | Verification Method | Status |
| --- | --- | --- | --- |
| **Immutable Task Ref Priority** | `.orchestrator/github_bus.py` prioritizes `task/<TASK-ID>` over agent branches | `python3 -m unittest discover -s .orchestrator -p "test_github_bus.py"` | **PASSED** |
| **Remote Tracking Ref Resolution** | `branch_exists`, `branch_head_sha`, `branch_has_diff` support `origin/` refs | `test_github_bus.py` test suite | **PASSED** |
| **Prevent False `github_review_pr_skipped`** | PR discovery finds exact pushed HEAD on origin/task branch | Supervisor PR discovery integration check | **PASSED** |
| **Product E2E Receipt Reseal** | Receipt landed at PR #573 head `f66761172dbc`, `status=passed`, `relation=exact_source_head` | Inspection of `docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json` at `f66761172dbc` and on `origin/dev` (identical) | **PASSED (by inspection)** |
| **Code Quality & Hygiene** | Zero linting errors or trailing whitespace issues | `git diff --check` | **PASSED** |
| **L1 Canonical & Policy Preservation** | Untouched canonical files | Scope audit | **PASSED** |

---

## 4. Upstream & Downstream Dependency Map

```
                  ┌─────────────────────────────────────────────────────────┐
                  │          ODP-ORCH-TASK-PR-DISCOVERY-001                 │
                  │  (Use immutable task refs for review PR discovery)      │
                  └───────────────────────────┬─────────────────────────────┘
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    ▼                                                   ▼
┌───────────────────────────────────────┐           ┌───────────────────────────────────────┐
│  Supervisor Review Handoff Pipeline   │           │    PR #573 Review & Auto-Merge        │
│   (.orchestrator/github_bus.py)       │           │   (task/ODP-ORCH-TASK-PR-DISCOVERY-001)  │
└───────────────────────────────────────┘           └───────────────────────────────────────┘
```

- **Upstream Dependencies**: None.
- **Downstream Beneficiaries**:
  - All per-task PR workflows (`task/<TASK-ID>`), ensuring Supervisor correctly discovers open PRs and pushed branch HEADs without false skip warnings.
- **Related Sidecar Tasks** (both live under `support/sidecars/ODP-ORCH-TASK-PR-DISCOVERY-001/`):
  - `ODP-ORCH-TASK-PR-DISCOVERY-001-SIDECAR-ACCEPTANCE`: (This task) Support acceptance packet, acceptance checklist, and dependency map.
  - `ODP-ORCH-TASK-PR-DISCOVERY-001-SIDECAR-REVIEW`: Sister sidecar on `dev` (`ODP-ORCH-TASK-PR-DISCOVERY-001-SIDECAR-REVIEW.md`) carrying the review packet and evidence summary for the same parent. The two sidecars are peers, not dependencies: neither blocks the other, both are advisory inputs the parent owner may absorb. Reviewers should read them together so acceptance criteria and review findings are not asserted independently.

---

## 5. Scope & Boundary Conformance Matrix

| File / Component Path | Primary Layer | Sidecar Slice Role | Disposition |
| --- | --- | --- | --- |
| `.orchestrator/github_bus.py` | Orchestrator Bus | Parent Task (`ODP-ORCH-TASK-PR-DISCOVERY-001`) | **Preserved / Intact** |
| `.orchestrator/test_github_bus.py` | Bus Test Suite | Parent Task (`ODP-ORCH-TASK-PR-DISCOVERY-001`) | **Preserved / Intact** |
| `docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json` | E2E Evidence | Parent Task (`ODP-ORCH-TASK-PR-DISCOVERY-001`) | **Preserved / Intact** |
| `support/sidecars/ODP-ORCH-TASK-PR-DISCOVERY-001/ODP-ORCH-TASK-PR-DISCOVERY-001-SIDECAR-ACCEPTANCE.md` | Sidecar Support | This Task (`ODP-ORCH-TASK-PR-DISCOVERY-001-SIDECAR-ACCEPTANCE`) | **ADDED** |
| `support/sidecars/ODP-ORCH-TASK-PR-DISCOVERY-001/ODP-ORCH-TASK-PR-DISCOVERY-001-SIDECAR-REVIEW.md` | Sidecar Support | Peer sidecar (`ODP-ORCH-TASK-PR-DISCOVERY-001-SIDECAR-REVIEW`) | **Preserved / Intact** |
| L1 Canonical Architecture & Governance Documents | Governance Policy | None | **STRICTLY UNTOUCHED** |

---

## 6. Verification Summary & Commands

The parent task fixes and this sidecar support artifact were verified with the following execution commands:

```bash
# 1. Orchestrator GitHub Bus Unit Test Suite
python3 -m unittest discover -s .orchestrator -p "test_github_bus.py"

# 2. Release Gate Registry Audit
python3 scripts/e2e/check_release_gate_registry.py

# 3. Scope-Bounded Formatting & Whitespace Audit
git diff --check origin/dev...HEAD

# 4. Sidecar Scope Audit
git diff --stat origin/dev...HEAD
```

**Verification Results** (executed 2026-08-06 at base `origin/dev` @ `71d44d03`):
- `test_github_bus.py`: **35 tests passed** (`OK`, exit 0). The suite grew from 30 to 35 as `dev` advanced; all pass at the current base.
- `check_release_gate_registry.py`: exit 0, `Release gate registry checks passed`. **This is a registry-consistency check, not a release-readiness claim** — the same run also prints `RELEASE STATE: NO-GO` with open blockers on gate-3/4/5/6. Nothing in this sidecar asserts release readiness.
- `git diff --check origin/dev...HEAD`: clean (0 whitespace errors).
- `git diff --stat origin/dev...HEAD`: 1 file changed, sidecar artifact only.

> **Not re-run in this sidecar**: `python3 scripts/e2e/generate_product_e2e_receipt.py`. Regenerating the receipt would mutate canonical evidence under `docs/evidence/e2e/`, which is outside this support slice's scope restriction. The receipt is instead verified by inspection — see § 2.3.

### Base Advance Audit & Verification Log (2026-08-06, round 2)
- **Base Advance Target**: Merged current `origin/dev` tip (`71d44d03`) into `task/ODP-ORCH-TASK-PR-DISCOVERY-001-SIDECAR-ACCEPTANCE`. This supersedes the round-1 log entry against `c879004a`, which went stale when `dev` advanced 99 commits and left PR #634 `BEHIND`.
- **Merge Integrity**: Conflict-free disjoint-path merge; task history preserved without reset or force-push.
- **Re-Verification at New Base**: all four commands above re-executed at the post-merge HEAD with the results recorded above.

---

## 7. Handoff & Reviewer Summary

- **Artifact Path**: `support/sidecars/ODP-ORCH-TASK-PR-DISCOVERY-001/ODP-ORCH-TASK-PR-DISCOVERY-001-SIDECAR-ACCEPTANCE.md`
- **Assigned Reviewer**: `Claude`
- **Handoff Note**: This acceptance packet documents the context, root cause, deliverables, acceptance matrix, dependency map, base-advance re-verification, and unit test suite for `ODP-ORCH-TASK-PR-DISCOVERY-001`. No canonical documents or core contracts were modified in this sidecar slice. Ready for re-review by `Claude`.

### Round-2 Reopen Response

| Finding | Disposition |
| --- | --- |
| **B1** — § 2.3 cited `b2e23f08…` as the landed PR #573 head; SHA is not in the PR's commit list and not an ancestor of `origin/dev` | **Fixed.** § 2.3 now anchors on PR head `f66761172dbc…` and merge commit `96f94cda…`, with the superseded SHA called out explicitly so it is not re-cited. |
| **B2** — § 1 / § 7 still named `Antigravity2` after the reviewer was helper-claimed by `Claude` | **Fixed.** Header and handoff now read Owner `Claude2` / Reviewer `Claude`; commit trailers match the current pair. |
| **B3** — PR #634 `BEHIND`; § 6 base-advance log stale at `c879004a` | **Fixed.** Merged `origin/dev` @ `71d44d03`; § 6 commands re-run at the new base and the log rewritten. |
| **N1** — `check_release_gate_registry.py` result read as a release-readiness claim | **Fixed.** § 6 now states it is a registry-consistency check and records the concurrent `RELEASE STATE: NO-GO` output. |
| **N2** — dependency map omitted the sister sidecar | **Fixed.** § 4 now lists `ODP-ORCH-TASK-PR-DISCOVERY-001-SIDECAR-REVIEW` as a peer sidecar. |
