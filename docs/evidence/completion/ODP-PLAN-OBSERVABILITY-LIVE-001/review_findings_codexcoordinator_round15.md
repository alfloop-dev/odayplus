# ODP-PLAN-OBSERVABILITY-LIVE-001 — Coordinator review findings, round 15

## Decision

Rejected at exact pushed owner head
`d187ac2cac0c561f311ebbcd55d9c32779a1724c`.

The Round-14 loopback mutation is now rejected from `DELIVERED`, but the same
caller-authority flaw remains for an arbitrary HTTPS origin. This is not a
production delivery proof and must not be approved or deployed.

## B1 — endpoint authority is still caller-selected

`OnCallNotificationAdapter.send()` reads
`ONCALL_PRODUCTION_ENDPOINT_AUTHORITY` / `ONCALL_AUTHORITATIVE_ENDPOINT` from
the same process environment that supplies `endpoint_url`. A caller can set
both to `https://evil.example/attacker`; `allowed_host` then becomes
`evil.example`, and the endpoint is classified as production. The additional
`hostname.endswith(".oday.plus")` branch is also broader than an exact
provisioned origin and path.

The gate therefore proves only that the URL is HTTPS and not syntactic
loopback. It does not prove that deployment authority provisioned the endpoint.

## B2 — provider and deployment attestations remain self-issued

The response signature is a SHA-256 token derived from
`ONCALL_PROVIDER_SECRET`, which the same caller process selects and also uses
to verify the response. `provider_readback` is derived locally from
`request_hash`; it is not an independently authenticated provider readback.
`RELEASE_SHA` and `TRUSTED_DEPLOYED_RELEASE_SHA` are two ambient values from the
same caller authority, so equality does not attest the deployed revision.

Required remediation remains:

1. Bind the exact production origin/path to deployment-controlled authority
   that the application caller cannot redefine. Fail closed for arbitrary
   HTTPS origins, subdomains, userinfo, redirects, and authority drift.
2. Verify a provider-issued asymmetric signature against a pinned public key,
   or perform independently authenticated provider readback. Do not let the
   application mint and verify the same HMAC-style token.
3. Obtain deployed revision identity from authenticated platform metadata or
   a signed deployment attestation, not two caller-selected environment values.

## Independent negative mutation

The mutation used the unmodified `_default_http_transport` path and patched
only the lower HTTPS response boundary. The caller selected:

- endpoint and authority: `https://evil.example/attacker`
- `ONCALL_PROVIDER_SECRET`
- matching `RELEASE_SHA` and `TRUSTED_DEPLOYED_RELEASE_SHA`

The forged HTTPS response recomputed the implementation's exact signature and
readback formulas. Result:

```text
[REAL ON-CALL DELIVERY RECEIPT] del-48db6aa223d7
Endpoint: https://evil.example/attacker (HTTP 200 DELIVERED)

{"endpoint": "https://evil.example/attacker", "error": null,
 "ok": true, "status": "DELIVERED", "used_default_transport": true}
```

The next regression must include this arbitrary-HTTPS/default-transport
mutation in addition to loopback, injected-transport, non-HTTPS, and suffix
allowlist cases. Release remains **NO-GO**.
