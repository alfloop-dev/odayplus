# ODP-P10-CAN-001-R3B Resource Version and Audit Safety Addendum

- Addendum ID: `ODP-P10-CAN-001-R3B-ADD-005`
- Parent task: `ODP-P10-CAN-001-R3B`
- Status: `ready_for_pickup`
- Prepared: `2026-07-26`
- Trigger: coordinator ADD-004 code and backend-contract review

## Rejection

R3B remains `NO-GO`. ADD-004 removed synthetic assignment values, but the
visible controls are still not bound to the concurrency tokens required by the
real APIs:

1. Claim uses `selected.version`, the intake version, as `If-Match`.
   `claimAssignment` compares the header with the assignment resource version.
2. Transfer also uses `selected.version` for an assignment write. Pause and
   resume use it for SLA writes. Assignment, SLA, and intake versions can
   diverge after the first mutation.
3. The legacy `AssistedIntake` contract exposes `assignmentId`,
   `assignmentStatus`, `slaInstanceId`, and `slaState`, but does not currently
   expose authoritative assignment or SLA resource versions. A control cannot
   become operable by relabeling the intake version.
4. The new audit UI renders `event.metadata` and `beforeAfter` through raw
   `JSON.stringify`. The current backend intake masking path masks classified
   parsed fields and top-level evidence but does not mask arbitrary audit event
   metadata. Raw rendering can disclose values that the field UI masks.
5. Audit mode says `SLA DATA / RECEIPT: UNAVAILABLE` even when the authoritative
   legacy record contains SLA ID/state/due data. Read-model data and a durable
   SLA receipt must be distinguished.
6. The non-DLQ job status badge still uses white text on `#cbd5e1`, an
   insufficient-contrast combination not exercised by the current clean-state
   axe fixture.

## Binding Backend Facts

- `POST /api/v1/assignments/{assignment_id}/actions/claim` calls
  `require_version(if_match, current["version"])` on the assignment.
- Assignment transfer uses the same assignment resource version.
- SLA pause/resume use the SLA resource version.
- Intake version is valid only for intake-resource writes. It is not an
  assignment or SLA concurrency token.
- The current legacy Operator intake read model has no guaranteed
  `assignmentVersion` or `slaVersion`.

## Required Remediation

### Resource-Specific Concurrency

- Never use `record.version` or another intake version for assignment or SLA
  `If-Match`.
- Derive an assignment concurrency token only from an authoritative assignment
  receipt or an explicitly supplied assignment resource version.
- Derive an SLA concurrency token only from an authoritative SLA receipt or an
  explicitly supplied SLA resource version.
- Claim, transfer, pause, and resume must be absent or disabled when their
  resource-specific version is unavailable. Show a stable
  `RESOURCE_VERSION_UNAVAILABLE` explanation.
- Every handler repeats the same guard. URL manipulation or stale component
  state must not bypass it.
- A future read-model extension may be read conservatively, but do not invent
  version fields or change the OpenAPI package/API in this wave.

### Audit and SLA Truth

- Do not raw-render or export arbitrary `auditEvent.metadata`,
  `beforeAfter`, or unclassified nested values.
- Render the authoritative safe audit envelope only: event ID, time, action,
  actor name, actor role, target ID, correlation ID, and message.
- For before/after and metadata, render `UNAVAILABLE` until the API supplies a
  classified/masked audit-detail contract. Do not claim the data is absent;
  label the safe display limitation clearly.
- In audit mode, show authoritative legacy SLA read-model ID/state/due data
  separately from durable SLA receipt status. If the receipt is absent, say
  `SLA RECEIPT: UNAVAILABLE` without saying the read-model state is unavailable.
- Missing transition history and job data remain visibly `UNAVAILABLE`.
- Fix the job-status badge contrast for all non-DLQ and DLQ states. Add a
  production-component axe or deterministic contrast assertion that covers a
  hydrated job state.

## Required Tests

- Set intake version, assignment version, and SLA version to different values.
  Prove claim/transfer use only the assignment version and pause/resume use only
  the SLA version.
- Prove all four controls and handlers fail closed when the corresponding
  resource version is absent.
- Prove no assignment/SLA handler constructs `If-Match` from
  `selected.version` or `record.version`.
- Prove arbitrary nested audit metadata and before/after values are not
  rendered while the safe event envelope and `UNAVAILABLE` limitation remain.
- Prove SLA read-model state remains visible while the durable receipt is
  separately `UNAVAILABLE`.
- Cover the hydrated job badge with a serious/critical axe scan or a
  deterministic WCAG contrast test.
- Re-run focused units, full web units, web/root typecheck, root build, the
  complete six-test canonical a11y spec, exact scope/E2E scope, static scans,
  and `git diff --check`.

## Scope

No additional paths are authorized. Use the parent R3B write set and current
ACK only. Do not further edit `playwright.config.ts` or any E2E spec. Do not
edit the API, OpenAPI package, auth/middleware, permission/source-policy rules,
archives, task ledgers, package manager files, or external worktrees.

Do not commit, push, rebase, merge, deploy, or spawn another agent. Update only
the R3B ACK, cite pickup SHA, keep
`program_status: no_go_pending_coordinator_visual_review`, and return all
changes uncommitted for coordinator review.
