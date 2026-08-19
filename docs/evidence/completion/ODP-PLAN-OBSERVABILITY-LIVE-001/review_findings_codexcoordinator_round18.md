# ODP-PLAN-OBSERVABILITY-LIVE-001 — Coordinator review findings, round 18

## Decision

Rejected at exact pushed owner head
`8749bda5ebc6855481334d1935f7edd626c6cb60`.

Constructor-injected verifier keys now correctly force `TEST_ONLY`. The
production composition is still caller-mutable, however: both purported pinned
trust roots are ordinary writable module globals, and the purported default
transport is a writable class attribute. The owner positive test itself
replaces all three at runtime before asserting `DELIVERED`.

## B1 — mutable module/class composition still authorizes caller delivery

Moving public keys from constructor defaults to
`PINNED_ONCALL_PROVIDER_PUBLIC_KEY_PEM` and
`PINNED_PLATFORM_DEPLOYMENT_PUBLIC_KEY_PEM` does not make them an immutable
deployment trust root. A normal application caller can assign both module
globals and replace `OnCallNotificationAdapter._default_http_transport`. The
constructor then receives no injected keys or transport, so
`has_injected_keys` and `is_injected_transport` are both false.

The independent mutation generated provider and platform Ed25519 keypairs,
assigned their public keys to those module globals, replaced the class default
transport, supplied a caller-signed deployment attestation, and returned a
caller-signed provider response. It used the canonical endpoint and constructed
the adapter with no arguments.

```text
[REAL ON-CALL DELIVERY RECEIPT] del-4d7c68cc5f5b
Endpoint: https://oncall-router.oday.plus/api/v1/alerts (HTTP 200 DELIVERED)

{"constructor_platform_key": null,
 "constructor_provider_key": null,
 "error": null,
 "http_status": 200,
 "ok": true,
 "provider_receipt_id": "provider-real-looking-r18",
 "status": "DELIVERED",
 "transport_equals_runtime_default": true}
```

This is the same authority boundary used by the new positive test, not a
constructor-key variant of the Round 17 finding.

## Required remediation

Production `DELIVERED` authority must be obtained from a protected composition
boundary that ordinary application/test callers cannot replace by mutating
module or class state. Test-only transports, keys, attestation sources, or
composition overrides must be permanently incapable of producing
`DELIVERED`. Add the exact no-argument module/class mutation above and prove it
stays `TEST_ONLY` or fails closed.

Re-audit the complete acceptance packet after fixing the composition boundary;
do not hand off after only special-casing this mutation. A real provider
readback and real deployment-attestation evidence remain required for release.

## Independent checks

```text
PYTHONPATH=. .venv/bin/python /tmp/odp_observability_round18_mutation.py
status=DELIVERED (blocking negative mutation reproduced)

PYTHONPATH=. .venv/bin/pytest -q \
  tests/reliability/test_runtime_observability.py
exit 0

.venv/bin/python -m ruff check \
  modules/notifications/infrastructure/adapters.py \
  tests/reliability/test_runtime_observability.py
All checks passed

git diff --check
clean
```

Release remains **NO-GO**. No PR, merge, live route change, or deployment was
performed.
