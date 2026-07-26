# Package 10 Canonical Runtime Fleet Dispatch

- Program ID: `ODP-P10-CANONICAL-R3`
- Recovery task: `ODP-P10-PROGRAM-RECOVERY-001`
- Status: `no_go_pending_CAN001_R3`
- Next owner: `ODP-P10-CAN-001-R3A`
- Worktree: `/home/lupin/oday-plus-package10-final`
- Branch: `fix/package10-final-20260725`
- Execution model: strict serial ownership transfer
- Prepared: `2026-07-26T12:01:23Z`

## Dispatch Authority

This file and its JSON peer are the Fleet dispatch source of truth. They do
not authorize parallel waves. Chat, `/tmp`, the dirty
`/home/lupin/oday-plus` worktree, an uncommitted worktree, or a route-only
deletion claim is not delivery evidence.

Every owner must begin from the named worktree and branch and must read the
committed recovery documents, this committed dispatch pair, and the
immediately preceding committed ACK. The first owner must additionally read
the committed program recovery ACK after the coordinator checkpoint.

## Mandatory Wave Protocol

Every wave, without exception, must:

1. Verify the worktree and branch, then read the committed program documents,
   committed dispatch documents, and immediately preceding committed ACK.
2. Inspect current assignments, ACKs, and repository evidence for other-LLM
   conflicts. Record each conflict and its resolution in the wave ACK.
3. Change only the wave's authorized paths and update only its own ACK.
4. Run the wave-specific gates and `git diff --check`.
5. Obtain and record coordinator review. A worker self-review is not a
   coordinator review.
6. Commit only authorized paths, push the exact commit to
   `origin/fix/package10-final-20260725`, and record the full commit SHA,
   pushed ref, gate results, and coordinator decision in the ACK.
7. Transfer ownership only after the coordinator confirms that the committed
   ACK and pushed SHA satisfy the checkpoint.

If any item is missing, the wave remains `no_go` and the next wave must not
start. Evidence supplied only in chat or `/tmp` is rejected.

## Strict Sequence and Ownership Transfer

| Order | Wave | Owner scope | Entry checkpoint | Exit checkpoint | Next |
|---:|---|---|---|---|---|
| 1 | `ODP-P10-CAN-001-R3A` | Retire old runtime, routes, feature roots, shells, navigation, loader, alternate legacy visuals, and the 18 legacy visual specs. Preserve canonical routes and reusable nonvisual/API/domain behavior. Do not implement the intake redesign and do not edit the 16 canonical E2E specs. | Committed recovery documents and coordinator-checkpointed `ODP-P10-PROGRAM-RECOVERY-001` ACK | Retirement gates pass; no old executable visual surface remains; own ACK is coordinator-reviewed, committed, pushed, and names the exact SHA | `ODP-P10-CAN-001-R3B` |
| 2 | `ODP-P10-CAN-001-R3B` | Integrate the canonical intake detail into the production `OperatorConsole -> NetworkFindAreasWorkspace -> AssistedIntakeSection` path and durable `/intake/[intakeId]` route. Produce one continuous full-page composition with source policy, parsed/normalized/corrected values, match confidence and signals, desktop comparison, 390px inline `DESKTOP_REQUIRED`, tablet functionality, assignment/SLA, WORM/receipts, promotion, human decisions, timeline, and durable return/deep link. Migrate reusable logic before deleting orphan alternatives. Do not edit canonical E2E specs. | Committed/pushed passing R3A ACK and coordinator ownership transfer | Product/unit/typecheck/build/accessibility/import/orphan/route gates pass; own ACK is coordinator-reviewed, committed, pushed, and names the exact SHA | `ODP-P10-CAN-002-R3` |
| 3 | `ODP-P10-CAN-002-R3` | Re-verify API/security contracts. Permissions, tenant boundaries, source policy, self-review denial, idempotency, version conflict, audit/WORM evidence, and promotion remain fail-closed. No web visual or E2E changes. | Committed/pushed passing R3B ACK and coordinator ownership transfer | API/security gates pass; own ACK is coordinator-reviewed, committed, pushed, and names the exact SHA | `ODP-P10-CAN-003-R3A` |
| 4 | `ODP-P10-CAN-003-R3A` | Align only the canonical E2E suite with the implemented Package 10 runtime and responsive contract. A missing required UI is a product no-go, never a stale assertion to weaken. | Committed/pushed passing CAN-002-R3 ACK and coordinator ownership transfer | Canonical assertions cover continuous detail, compare/signals/decisions, 390/1024/1440 behavior, durable route, accessibility, reads/writes, and fail-closed behavior; own ACK is coordinator-reviewed, committed, pushed, and names the exact SHA | `ODP-P10-CAN-003-R3B` |
| 5 | `ODP-P10-CAN-003-R3B` | Run the complete Chromium gate read-only, with the expected count declared from the committed canonical spec inventory read at pickup. Product, test, and config edits are forbidden. | Committed/pushed passing R3A ACK and coordinator ownership transfer | Every declared Chromium test passes unchanged; own ACK is coordinator-reviewed, committed, pushed, and names the exact SHA | `ODP-P10-CAN-004-R3` |
| 6 | `ODP-P10-CAN-004-R3` | Release/integration/deployment readiness: reconcile every ACK and SHA; rerun release gates; confirm old pages and alternate intake visuals cannot be served; require real deployment receipts before any deployment claim. | Committed/pushed passing R3B ACK and coordinator ownership transfer | Release evidence is reconciled, coordinator-approved, committed, and pushed; deployment is claimed only with a real receipt | Program closure |

## Committed Read Set

At pickup, each wave must resolve and record the committed versions of:

- `docs/evidence/fleet_dispatch/package10_20260726/PROGRAM_LEDGER_RECOVERY_TASK.md`
- `docs/evidence/fleet_dispatch/PACKAGE_10_CANONICAL_RUNTIME_FLEET_DISPATCH_2026-07-26.md`
- `docs/evidence/fleet_dispatch/PACKAGE_10_CANONICAL_RUNTIME_FLEET_DISPATCH_2026-07-26.json`
- `docs/design/PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.md`
- `docs/design/PACKAGE_10_CANONICAL_RUNTIME_EXECUTION_TASKS_2026-07-26.json`
- `docs/design/PACKAGE_10_INTAKE_DETAIL_CANONICALIZATION_EXECUTION_ADDENDUM_2026-07-26.md`
- `docs/design/PACKAGE_10_INTAKE_DETAIL_CANONICALIZATION_EXECUTION_ADDENDUM_2026-07-26.json`
- `docs/evidence/PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.md`
- `docs/evidence/PACKAGE_10_PAGE_BY_PAGE_RUNTIME_DIFF_2026-07-26.json`
- `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-CONFLICT-VISUAL-AUDIT.md`
- `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-CONFLICT-VISUAL-AUDIT.json`
- for R3A:
  `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-CAN-001-R3A-ORPHAN-SHELL-ADDENDUM.md`
  and its JSON peer
- the immediately preceding ACK under
  `docs/evidence/fleet_dispatch/package10_20260726/acks/`

An absent or uncommitted required document is a pickup blocker, not permission
to infer its contents from chat.

## Other-LLM Conflict Rules

- The smoke ACK proves Fleet health only. Its instruction to resume
  CAN-003 is stale and is superseded by this recovery order.
- Evidence from `/home/lupin/oday-plus` is wrong-worktree evidence.
- Route retirement alone does not prove feature, shell, internal intake
  visual, or legacy-spec retirement.
- A unit or E2E test that directly mounts an orphan component is not
  production-runtime evidence.
- Package 6/7 or OpsBoard visuals are not Package 10 visual authority.
- No owner may weaken or delete a canonical assertion to conceal a missing
  Package 10 product requirement.
- Any new conflicting assignment or output discovered at pickup must be
  stopped, named in the ACK, and resolved by the coordinator before work or
  ownership transfer continues.

## Current Checkpoint

Program recovery persistence is `coordinator_checkpoint_complete`: dispatch
and recovery ACK were pushed in `ff39d14f`, and the independently reviewed
audit/task documents were pushed in `2d45ced6`. The total status remains
`no_go_pending_CAN001_R3`; the next and only eligible owner is
`ODP-P10-CAN-001-R3A`.
