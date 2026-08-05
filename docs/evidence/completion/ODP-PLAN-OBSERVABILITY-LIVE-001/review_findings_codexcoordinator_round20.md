# ODP-PLAN-OBSERVABILITY-LIVE-001 — Coordinator review findings, round 20

## Decision

Rejected at exact pushed owner head
`d1d06f4b07b5cafa15d162bba66c61c766ff82ff`.

The adapter no longer promotes the previous in-process key mutations, but the
new verifier is not a protected external authority. Its URL is selected by
caller-controlled environment variables and its unauthenticated boolean
response is accepted as production delivery proof.

## B1 — caller-owned verifier and injected provider transport emit `DELIVERED`

`EXTERNAL_ONCALL_VERIFIER_URL` / `ONCALL_EXTERNAL_VERIFIER_URL` accepts any URL.
`_verify_external_oncall_delivery()` uses the default redirect-following client,
does not require HTTPS or an exact origin/path/port, does not authenticate the
verifier, and accepts either `verified` or `delivered` truthiness without
checking an echoed binding, signature, nonce, or freshness. The delivery path
also no longer prevents an injected notification transport from being promoted
when that callback returns true.

The independent mutation ran a caller-owned loopback HTTP server that returned
only `{"verified": true}`, selected it via environment variable, and supplied
an injected provider transport returning a caller receipt ID.

```text
[REAL ON-CALL DELIVERY RECEIPT] del-6cdf49ec0c57
Endpoint: https://oncall-router.oday.plus/api/v1/alerts (HTTP 200 DELIVERED)

{"error": null,
 "http_status": 200,
 "injected_provider_transport": true,
 "ok": true,
 "provider_receipt_id": "caller-provider-receipt-r20",
 "status": "DELIVERED",
 "verifier_url": "http://127.0.0.1:37495/caller-verifier"}
```

## Complete remediation contract

Do not special-case only loopback. The protected verifier protocol must prove
all of the following together:

- verifier identity comes from immutable deployment composition, not caller
  environment or constructor input;
- exact HTTPS scheme, host, port, and path are enforced, with userinfo, query,
  fragment, loopback/private authority, and every redirect rejected;
- the request is authenticated to the verifier and the response is
  independently authenticated (for example mTLS/workload identity plus signed
  receipt), not a bare boolean;
- the authenticated response binds the exact `delivery_id`,
  `provider_receipt_id`, `request_hash`, `release_sha`, nonce, and freshness
  window, and rejects missing, duplicate, stale, replayed, or mismatched fields;
- injected/test provider transports, keys, verifier endpoints, verifier
  transports, and monkeypatched paths are structurally incapable of producing
  `DELIVERED`;
- `ONCALL_PROVIDER_SECRET` and release/deployment identity must either have a
  real authenticated role or be removed; caller-selected unused strings cannot
  contribute to a production claim;
- without real external provider and deployment evidence, engineering may
  return `PENDING_VERIFICATION`, but must not synthesize a positive live claim.

Add the exact caller-owned verifier mutation plus redirect, arbitrary HTTPS,
unsigned boolean, field mismatch, replay, stale, and injected-transport
mutations. Re-audit the entire acceptance packet after the protocol boundary is
implemented.

## Independent check

```text
PYTHONPATH=. .venv/bin/python /tmp/odp_observability_round20_mutation.py
status=DELIVERED (blocking negative mutation reproduced)
```

Release remains **NO-GO**. No PR, merge, route change, or deployment was
performed.
