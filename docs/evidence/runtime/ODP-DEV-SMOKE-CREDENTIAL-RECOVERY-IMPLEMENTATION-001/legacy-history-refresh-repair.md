# Historical namespace / refresh availability repair (auxiliary, unapproved)

Independent static review of9fa161e42753a8d6ea4dd0fc12f66f47f0d927a0 returned
CHANGES_REQUIRED after44 complete source bodies/new follow-up hunks. It confirmed
the preceding request-namespace and committed-WORM fixes but identified two
additional current-composition defects. None of this changes the original task
owner/reviewer/six reopens, rejected PR1459, Human/Ops or operational/live/F11 holds.

## Reproductions / repairs

Six actual-PG cases first created valid recovery ACK history, then restored an
integrity-valid historical security.authentication row under either older fixed
provisioning or binding correlation below the new append barrier. Missing, other
and matching tenant metadata all produced erroneous HTTP200 partial audit on the
unchanged9fa production source. No future caller/audit writer is permitted to
append these rows. Projection now reads/validates all FIVE fixed correlations
before tenant/event-type/subject selection, includes old exact key sets and refuses
incompatible history without deleting/re-signing/filtering it. Other-tenant reads
still cannot perform global fixed-history queries; spoofed old ledger metadata
also statically refuses rather than exposing it cross-tenant.

Web tests exercise actual cookie seal/read and actual logout/session handlers with
an explicit memory store and mocked mint/update availability, NOT PG proof. Initial
launcher/import and NextRequest/Happy DOM cookie setup failures are retained; their
200 responses did NOT prove the refresh finding. After cookie/runtime/decryption
controls were added and maintained post-constructor headers/cookies installed, a
separate unchanged9fa production checkout reproduced TWO intended503 assertions:
refresh mint or bearer-update failure had returned logout200 without revocation.
The separate checkout's initial dependency-resolution failures remain as well.

readWebSession now throws a STATIC availability error when refresh mint/update
fails. Logout's existing catch returns503/no cookie clearance/no success, so a
transient401 at the session endpoint is not falsely coupled with successful
cleanup. Tests restore refresh availability and show the original session remains
usable after503 (truthful UNKNOWN), then healthy logout200 irrevocably revokes it;
afterward restoring refresh cannot resurrect that cookie. Underlying private
exception strings are not returned. Once-only foreground cleanup intent and
UNKNOWN remain unchanged; this does not authorize automatic cleanup retry.

Fresh exact-head measurements/independent review remain required. No source or
local test receipt constitutes canonical continuation, hosted CI, native custody,
writer exclusion, production reserves/admission or reviewed execution authority.

First immutable b720a1caf measurements passed11 selections but comprehensive
serial regression timed out at the unchanged1200s cap, with failure markers
already present. It is NOT a passing receipt; the full diagnostic remains.
Focused localization found legacy lifecycle fixtures intentionally bind the old
creation contract to a disposable tenant while recovery remains separately pinned.
The projection had incorrectly used the recovery tenant for BOTH namespaces.
Follow-up selects each namespace's pinned tenant from its OWN owning contract:
no caller configuration or relaxed check. Current production pins coincide; the
original lifecycle and isolated legacy-poison controls both pass. The follow-up
requires new exact-head measurements; no source/test budget or assertion waiver.
