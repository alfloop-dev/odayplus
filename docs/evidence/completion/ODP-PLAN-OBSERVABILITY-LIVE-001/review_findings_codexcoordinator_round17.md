# ODP-PLAN-OBSERVABILITY-LIVE-001 — Coordinator review findings, round 17

## Decision

Rejected at exact pushed owner head
`53042242eb63902478a9177daa34164f933625ed`.

The repository private key, unsigned attestation fallback, and endpoint
port/query gaps are removed. However, both production trust roots are now
public constructor parameters and remain caller-controlled.

## B1 — caller-supplied verifier keys authorize production delivery

`OnCallNotificationAdapter.__init__()` accepts `provider_public_key_pem` and
`platform_public_key_pem`. `send()` prefers those values over the pinned
provider and platform keys. A caller can therefore generate two private keys,
pass the corresponding public keys, sign its own deployment attestation and
provider response, and satisfy every production `DELIVERED` condition.

Testability must not widen production authority. Production construction must
obtain verifier keys from immutable deployment trust configuration that the
application caller cannot replace. If tests need injected keys, the injected
mode must be structurally `TEST_ONLY` and incapable of emitting `DELIVERED`, or
the verification component must be instantiated only by a protected
composition root with a non-forgeable production capability.

## Independent negative mutation

The mutation dynamically generated provider and platform Ed25519 keypairs,
passed both public keys through the public constructor, wrote an attestation
signed by the caller platform key, and signed the provider receipt with the
caller provider key. It used the canonical endpoint and unmodified default
transport path.

```text
[REAL ON-CALL DELIVERY RECEIPT] del-928ea67ebf7f
Endpoint: https://oncall-router.oday.plus/api/v1/alerts (HTTP 200 DELIVERED)

{"caller_platform_key": true, "caller_provider_key": true,
 "error": null, "ok": true, "status": "DELIVERED",
 "used_default_transport": true}
```

Add this exact two-key caller-injection mutation. Also prove that test keys,
test transports, arbitrary attestation paths, and any non-production
composition cannot emit `DELIVERED`. Release remains **NO-GO**.
