# ODP-PLAN-OBSERVABILITY-LIVE-001 — CodexCoordinator review round 9

- Verdict: `CHANGES_REQUESTED`
- Reviewed implementation head:
  `9cb6129fb602f83d92eb5335d6a2e9314c7d7cdb`
- Reviewer: `CodexCoordinator`
- Release state: `NO-GO`

## Improvements confirmed

1. API request/error/latency metrics now have concrete middleware call sites.
2. Worker and scheduler loops invoke their exporter paths and the previous
   nonexistent logger method is removed.
3. Metric definitions now carry more explicit unit/range constraints.
4. The notification response is redacted before persistence.
5. Ruff and `git diff --check` pass on the changed paths.

## Blocking findings

### B1 — Caller-controlled response fields still manufacture “authentic” on-call delivery

`OnCallNotificationAdapter` treats any truthy `provider_signature`,
`provider_readback_verified` or `authentic_provider_token` returned by the
injected transport as authentic. It never verifies a signature, authenticates
the issuer, performs independent provider readback or binds the response to the
request/release.

The new positive test explicitly returns caller-created values
`provider_signature="sig-sha256-verified"` and
`provider_readback_verified=True`, then expects `DELIVERED`.

An independent mutation returned arbitrary values and was accepted:

```text
blank_release=0000000000000000000000000000000000000000
caller_response_status=DELIVERED
caller_response_ok=True
```

Required:

1. Verify a provider-issued signature/credential against a configured trust
   root or perform authenticated independent readback.
2. Bind provider receipt, request hash, release SHA, route and response.
3. Treat injected/mock transports as `TEST_ONLY` unless the verifier itself is
   external and authenticated.

### B2 — Missing release identity is converted into a valid-looking all-zero SHA

When no release environment value exists, the adapter substitutes forty
zeroes. That value passes the 40-hex format check and can be marked
`DELIVERED`. Missing identity must fail closed; a syntactically shaped sentinel
must never be treated as a release.

Worker and scheduler exporters also silently return `None` when release identity
is absent instead of emitting an explicit governed-disabled/NO-GO result.

### B3 — The committed watch receipt is not bound to the handed-off head

The owner claims the watch receipt was updated to exact implementation head
`9cb6129f...`, but the committed receipt contains:

```text
release_sha=d11da9642018804ac33b3aee1feef2cffbdcfac7
```

Exact-head verification fails:

```text
Release SHA mismatch: expected 9cb6129f..., got d11da964...
```

The stored `provider_query_response` is repository JSON with no provider query
receipt/id, authenticated caller identity, response signature or independent
retrieval reference. Its canonical hash proves only self-consistency.

Required:

1. Keep the receipt `NO-GO` unless an authenticated provider query/readback
   exists.
2. Bind the final deployed release non-circularly and verify it from a clean
   checkout.
3. Do not claim exact-head or live proof from a prior/local synthetic response.

### B4 — Business signals remain fabricated or speculative

The HeatZone route writes
`heatzone_topk_adoption_rate = 1.0 if created else 0.5`. Creation is not user
adoption and `0.5` is a fabricated fallback value. It also records request
feature count as prediction count without binding to a verified scored output.

The inventory still names generic “evaluation call sites” for many data/model/
business metrics rather than concrete production writers. A no-op
`TelemetryMiddleware: pass` class was added even though the real middleware is
a separate function, which makes the inventory name look implemented without
providing behavior.

Required:

1. Emit business KPIs only from actual measured outcomes with source/run
   lineage; mark unavailable otherwise.
2. Replace every generic inventory row with a concrete writer or explicit
   `NOT_IMPLEMENTED/NO-GO`.
3. Remove decorative/no-op implementation markers.

## Re-handoff rule

Close B1-B4 together. Do not patch only the receipt SHA. A new handoff must
prove caller-created signatures/readback booleans fail, blank/all-zero release
fails, exact deployed-release provider proof verifies non-circularly, and no
fabricated business KPI is emitted. Keep release `NO-GO` without authentic live
resources.
