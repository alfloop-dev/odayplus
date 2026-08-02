# ODP-PLAN-OBSERVABILITY-LIVE-001 — Round 11 review

- Owner head: `fc2d4c3d935bfaa4241f9851872548833f1a9eed`
- Verdict: **CHANGES_REQUESTED / NO-GO**

The round-10 remediation remains caller-self-authenticated.

## Reproducible alert forgery

With `ONCALL_PROVIDER_SECRET`, `TRUSTED_DEPLOYED_RELEASE_SHA`, and
`EXPECTED_RELEASE_SHA` all absent, an injected transport selected release
`2222222222222222222222222222222222222222`, receipt id
`evil-provider-receipt`, recomputed the public request hash and the code's
empty-secret SHA-256 token, and returned that value.

Result:

```text
ok=True
status=DELIVERED
release=2222222222222222222222222222222222222222
```

The secret and trusted release are optional, and the test itself calculates the
same response token. This is not provider authentication or trusted deployment
binding.

## Watch receipt remains local and off-head

The committed receipt is bound to `6f1db1dd...`, not the handoff head
`fc2d4c3d...`; exact verification rejects it. The verifier also retains local
fallback derivations for receipt id, signature, and readback identity when the
stored response lacks provider-issued values. Recomputing locally stored,
caller-controlled data cannot prove provider issuance.

## Required complete batch

1. Fail closed when a non-caller-controlled provider trust root and trusted
   deployed-release binding are absent.
2. Verify an actual provider MAC/signature or authenticated provider API
   readback; tests must not self-establish the trust root or expected response.
3. Remove all locally generated provider-proof fallbacks.
4. Bind evidence to the exact pushed handoff head using a non-circular
   source/evidence protocol.
5. Keep HeatZone adoption explicitly NO-GO until authoritative lineage exists.

No PR, deployment, or release claim is authorized.
