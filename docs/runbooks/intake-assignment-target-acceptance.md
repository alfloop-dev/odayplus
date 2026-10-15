# Intake assignment target and conflict acceptance

This supplements the R7 Intake VDC-001 action split and VDC-004 navigation
acceptance. It does not close the full 40-screen, real-role, or release review.

## Required behavior

- The Transfer dialog obtains recipients from the assignment-scoped API. The
  production UI must not send design-prototype names or non-UUID actor aliases
  as account identities.
- `GET /assignments/{assignment_id}/transfer-targets` returns
  `assignment_id`, `assignment_version`, and `items` containing `subject_id`,
  `display_name`, and `owner_role`. The normal API prefix applies.
- The server authorizes the requesting actor against the bound assignment,
  Intake scope, SLA, and registry policy. Recipient eligibility requires a
  live active account in that tenant, the actual Intake scope, and a role
  mapped by the bound policy. The current owner is not a transfer recipient.
- The response is a read snapshot, not a transfer authorization receipt.
  The transfer command continues to check live identity, scope, policy,
  workflow state, resource version, and idempotency when it writes.
- Loading, empty, denied, unavailable, or mismatched-version target results
  do not permit submission. The user explicitly chooses a subject and role;
  multiple eligible roles for one subject remain distinct choices.
- Transfer retains target and handoff note. Pause SLA retains pause reason
  and expected resume time. Neither action should gain the other action's
  fields to compensate for an API mismatch.
- Display and `If-Match` use the assignment or SLA resource version, not the
  Intake version. A receipt belongs only to its matching resource ID. Within
  that resource, a newer server snapshot supersedes an older receipt, and a
  newer acknowledged receipt remains usable while a read catches up.
- Conflict refresh preserves entered fields, reports a failed refresh, and
  only clears the conflict after a successful read. A transport timeout does
  not prove that the server did not commit; retry keeps the operation's
  idempotency key according to the existing command flow.

## Verification scope

Use isolated synthetic accounts and registry records for automated tests.
Check an allowed recipient and reject a recipient from another tenant, an
inactive account, an account outside the Intake scope, and an account without
the bound role. Check requester authority separately from recipient eligibility.
Also test missing policy, empty results, multiple role choices, stale response
versions, and loss of recipient eligibility before command submission.

Mount the actual Intake container when testing successful command receipts,
conflict refresh, subsequent `If-Match`, and retained user input. A pure helper
test alone is insufficient to prove that the dialog sends the corrected version.
Test the typed client's operation path and response shape against generated
OpenAPI artifacts; do not bypass the shared transport with a raw UI fetch.

## PostgreSQL bootstrap prerequisite

The runtime SQL fingerprint prevents identical startup DDL from competing with
active commands. Schema validation remains a separate prerequisite. Both
`odp_runtime.durable_sequences` and `odp_runtime.notification_inapp_inbox` must
exist even when the exact runtime fingerprint is already recorded.

An isolated restore-drift test retains the fingerprint, drops one required
relation, and attempts startup. Startup must fail with the missing relation;
it must not silently repair the live schema or erase the migration record.
Production migration or restoration still follows the approved runbook.

## Remaining admission

Passing these tests does not establish deployed account/policy readiness,
real-role UAT, every Inbox filter required by UI handoff section 8.1, or release
authority. Preserve those open requirements in the full-system acceptance
report, with exact source and evidence scope.
