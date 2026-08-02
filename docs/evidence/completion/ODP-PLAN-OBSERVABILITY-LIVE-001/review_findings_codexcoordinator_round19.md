# ODP-PLAN-OBSERVABILITY-LIVE-001 — Coordinator review findings, round 19

## Decision

Rejected at exact pushed owner head
`3b88cfc3d846d44a5318b9c760275a65470c96cb`.

The Round 18 mutation now yields `TEST_ONLY`, but the remediation creates a
second set of mutable module globals named `_CANONICAL_*`. A leading underscore
is a naming convention, not a protected composition boundary. The production
decision still compares runtime-resolved caller-mutable values against other
runtime-resolved caller-mutable values in the same Python process.

## B1 — mutating both aliases restores caller-authorized `DELIVERED`

The independent mutation replaces both public key aliases, both
`_CANONICAL_*` key aliases, the `_CANONICAL_REAL_HTTP_TRANSPORT` global, and the
class default transport. It then constructs the adapter with no arguments and
reuses a caller-signed deployment attestation and provider response.

```text
[REAL ON-CALL DELIVERY RECEIPT] del-084073146444
Endpoint: https://oncall-router.oday.plus/api/v1/alerts (HTTP 200 DELIVERED)

ROUND19_RESULT={
  "canonical_transport_is_caller": true,
  "constructor_platform_key": null,
  "constructor_provider_key": null,
  "error": null,
  "ok": true,
  "status": "DELIVERED"
}
```

This proves that adding more equality checks inside the same mutable module
cannot close the authority boundary.

## Required architectural remediation

Do not add another in-process alias or comparison. The application adapter must
not promote a locally mutable/test composition to production `DELIVERED`.

- Unit, injected, monkeypatched, or caller-composed execution may emit only
  `TEST_ONLY` or `PENDING_VERIFICATION`.
- Production `DELIVERED` must be issued by an external protected verifier,
  authenticated provider readback, or deployment authority whose trust roots
  and transport are outside ordinary application/test caller control.
- A real provider/deployment receipt may be verified in process only when its
  authority is anchored outside the mutable Python module and the test suite
  cannot substitute that authority.
- Remove the monkeypatched in-process positive `DELIVERED` test. Replace it
  with a fail-closed contract test and reserve the positive delivery claim for
  real external integration evidence.
- Add the exact dual-alias mutation and re-audit the complete acceptance packet
  after changing the boundary.

If the external provider/deployment evidence is not available, report the
engineering contract as complete but leave the live-delivery gate pending; do
not fabricate `DELIVERED` locally.

## Independent check

```text
PYTHONPATH=.:/tmp .venv/bin/python \
  /tmp/odp_observability_round19_mutation.py
status=DELIVERED (blocking negative mutation reproduced)
```

Release remains **NO-GO**. No PR, merge, live route change, or deployment was
performed.
