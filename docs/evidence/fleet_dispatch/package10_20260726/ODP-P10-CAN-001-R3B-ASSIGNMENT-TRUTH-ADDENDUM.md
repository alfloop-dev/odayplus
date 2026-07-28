# ODP-P10-CAN-001-R3B Assignment and Missing-Data Truth Addendum

- Addendum ID: `ODP-P10-CAN-001-R3B-ADD-004`
- Parent task: `ODP-P10-CAN-001-R3B`
- Status: `ready_for_pickup`
- Prepared: `2026-07-26`
- Trigger: coordinator fourth-remediation code review

## Rejection

R3B remains `NO-GO`. Fleet verification passed, but coordinator review found
that the implementation and ACK still overstate the assignment and missing-data
contract:

1. `handleClaim()` still calls `assignIntake()` when no authoritative
   assignment exists. That branch fabricates `owner_subject_id` from a role ID,
   hard-codes `owner_role` as `reviewer`, and invents a due date five days in
   the future.
2. The existing-assignment `claimAssignment()` call omits the required
   `If-Match` version even though the API requires it. The visible claim action
   can therefore fail with `428`.
3. Claim is exposed from owner absence alone. Pause is exposed when
   `slaInstanceId` exists even if the authoritative SLA state is absent. These
   controls do not fail closed on the API state needed by the action.
4. The continuous audit section does not visibly render `UNAVAILABLE` when
   transition history or hydrated job data is absent, despite ADD-003 making
   that a binding requirement.
5. The submission summary omits submitted time entirely when the legacy read
   model does not provide it. It must show a truthful `Submitted At:
   UNAVAILABLE`, not silently substitute `capturedAt` or omit the field.
6. Legacy audit rows show actor name but omit the authoritative
   `actorRoleId`. They must show actor and role while keeping unsupported event
   version and other absent evidence `UNAVAILABLE`.
7. The ACK claims that no owner, role, due date, or deadline is synthesized,
   which is contradicted by the current product code.

The coordinator compared the retained Package 10 screenshots after review.
The source cards and Listing Radar below the canonical detail are part of the
archived Package 10 page. They are not a rejection finding. Do not remove or
redesign them in this remediation.

## Required Remediation

### Assignment and SLA

- Remove the `assignIntake()` fallback from the claim path. Claim may operate
  only on an authoritative `assignmentId`.
- Pass `If-Match: W/"{record.version}"` to `claimAssignment()`.
- Show claim only when role and session subject checks pass and authoritative
  assignment ID/status make the existing assignment claimable. Do not create
  an owner, owner role, assignee, due date, SLA deadline, or replacement
  receipt in the browser.
- Show pause only when `slaInstanceId` and an explicit authoritative active SLA
  state are present. Show resume only for authoritative `PAUSED`.
- Missing or unknown assignment/SLA data must keep the relevant action absent
  or disabled with a stable `UNAVAILABLE` explanation.

### Submission, History, Job, and Audit Truth

- Add a visible `Submitted At` row. The current legacy `AssistedIntake` model
  has no submitted timestamp, so render `UNAVAILABLE` unless an authoritative
  field is actually present.
- Keep `Captured At` and `Observed At` separate from `Submitted At`.
- In audit mode, explicitly show unavailable states for missing transition
  history, job data, and SLA receipt/data. Do not leave a heading that implies
  evidence exists.
- Legacy audit rows must include `actorRoleId` when supplied. Missing role,
  time, ID, correlation, event version, before/after, or metadata remains
  visibly `UNAVAILABLE`; do not append the current intake version.
- Keep the already hydrated authoritative promotion receipt and score job
  wiring. Do not manufacture history or job receipts to fill an empty state.

## Scope

No additional product or test paths are authorized. Use only the parent R3B
write set and the previously authorized ACK path. The ADD-003 authorization for
`playwright.config.ts` and the single canonical a11y selector remains narrow;
this remediation does not authorize further changes to either file.

Forbidden:

- `apps/api/**`, auth, middleware, source policy, permission tables, archives,
  task ledgers, package manager files, or any other E2E spec;
- removing Package 10 source cards or the Listing Radar composition;
- commit, push, rebase, merge, deployment, or spawning another agent.

## Required Tests

- Production-container unit coverage proving no `assignIntake()` fallback,
  no synthetic owner/role/due date, and claim includes authoritative
  assignment ID plus `If-Match`.
- Unit coverage proving missing/unknown assignment and SLA data hides or
  disables claim/pause/resume.
- Unit coverage for visible submitted/history/job/SLA/audit-role
  `UNAVAILABLE` states.
- Focused changed-graph units, full web unit suite, web/root typecheck, root
  build, and the complete six-test canonical accessibility spec.
- Static scans for the removed synthetic assignment literals and date
  generation, exact scope, E2E scope, and `git diff --check`.
- Confirm no Fleet-owned process remains before returning.

Update only the R3B ACK. Record this rejection, pickup SHA, exact commands and
counts, and the corrected result. Keep
`program_status: no_go_pending_coordinator_visual_review` and do not claim
coordinator approval.

## Other-LLM Conflict Check

At dispatch preparation:

- `origin/dev` remains six commits ahead of the Package 10 branch, while the
  Package 10 branch remains twenty-four commits ahead.
- Active live-runtime, AVM-outcomes, and model-ready-geo worktrees have no
  dirty-path overlap with the R3B intake write set.
- Those external worktrees overlap each other in model release/contracts and
  integration-test paths. The live-runtime branch also contains a committed
  auth change. These are final integration risks, not permission to edit their
  worktrees during R3B.
