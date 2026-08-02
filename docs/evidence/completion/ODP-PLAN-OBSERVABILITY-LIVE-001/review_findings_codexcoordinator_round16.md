# ODP-PLAN-OBSERVABILITY-LIVE-001 — Coordinator review findings, round 16

## Decision

Rejected at exact pushed owner head
`ba194e12e955fa773ea5d3e5e969f7894939d069`.

The arbitrary-host mutation is closed, but the new asymmetric and deployment
claims are not independent trust anchors. A public-path mutation still emits a
formal `DELIVERED` receipt without a deployment attestation.

## B1 — the provider signing private key is committed in the repository

Production code pins public key
`MCowBQYDK2VwAyEA6ZqyVQ53UCAtdWC17njGX5O7c1p2H5IwaiRISSgAX8M=`, while
`test_round15_remediation_findings_b1_b2_arbitrary_https_and_asymmetric_sig_mutation_verified`
commits its corresponding Ed25519 private key. Any caller that can read the
repository can therefore mint a signature accepted as the provider. This
cannot serve as an external provider authority.

Production provider private material must never be present in source, tests,
fixtures, images, or application runtime. Tests need a separately injected
test-only verifier that is structurally incapable of producing production
`DELIVERED`, or public verification vectors that do not expose signing
authority.

## B2 — deployed revision attestation remains optional and caller-controlled

`has_authentic_deployed_metadata` falls back to
`self.trusted_release_sha == release_sha`; both values originate from caller
environment variables. No attestation file is required. When a file is used,
its path is also caller-selected and its JSON content is accepted without a
signature or authenticated platform readback.

Require one authenticated platform metadata/readback or a signed deployment
attestation verified under a deployment key that the application cannot mint.
Remove the paired-environment fallback from production `DELIVERED` authority.

## B3 — canonical endpoint comparison omits authority components and redirects

The comparison checks scheme, hostname, and path, but not the effective port,
query, fragment, or final response URL. Python's default opener follows
redirects. Consequently
`https://oncall-router.oday.plus:444/api/v1/alerts?redirect=evil.example`
passes the allowlist.

Bind the exact normalized origin and path (including port 443), reject query,
fragment, and userinfo, and either disable redirects or revalidate every hop
and the final peer under the same authority.

## Independent negative mutation

The mutation used:

- the unmodified default HTTP transport;
- the private key committed by the Round-15 test;
- no deployment attestation path or manifest;
- caller-matched release SHA environment values;
- canonical hostname with unauthorized port and query.

Result:

```text
[REAL ON-CALL DELIVERY RECEIPT] del-e3ea64cda0a0
Endpoint: https://oncall-router.oday.plus:444/api/v1/alerts?redirect=evil.example
          (HTTP 200 DELIVERED)

{"attestation_path": null,
 "endpoint": "https://oncall-router.oday.plus:444/api/v1/alerts?redirect=evil.example",
 "error": null, "ok": true, "status": "DELIVERED",
 "used_default_transport": true}
```

Add this exact leaked-key/no-attestation/port-query mutation, plus redirect and
unsigned-attestation mutations. Release remains **NO-GO**.
