# ODP-PLAN-OBSERVABILITY-LIVE-001 — Independent Review Round 14

- Reviewer: `CodexCoordinator`
- Reviewed exact pushed owner head: `6e0123f5afb79f14afa50015c26d934b4c1d5e3a`
- Result: `CHANGES_REQUESTED`
- Release decision: `NO-GO`

## Confirmed corrections retained

The three platform routes are now mounted through the shared API-v1 mechanism,
and the checked-in OpenAPI schema/client are regenerated. Independent exact-head
contract tests pass:

```text
test_every_product_operation_is_served_under_api_v1              PASS
test_artifact_is_checked_in_and_matches_the_live_app              PASS
```

The watch-window verifier also removed valid-hex and suffix-only signature
fallbacks and now compares one exact signature/readback scheme.

## B1 — Caller-owned loopback provider still produces `DELIVERED`

`OnCallNotificationAdapter` treats every call through `_default_http_transport`
as production-capable, regardless of endpoint provenance. The endpoint is a
caller-supplied constructor argument. The provider HMAC secret, release SHA, and
"trusted" release SHA are all caller-set process environment values. A caller
can therefore start its own HTTP server, select all authority inputs, calculate
the expected response tokens, and receive a production `DELIVERED` receipt.

Exact-head mutation receipt:

```json
{
  "caller_selected_endpoint": "http://127.0.0.1:<ephemeral>/attacker",
  "caller_selected_provider_secret": true,
  "caller_selected_release_and_trusted_sha": true,
  "error": null,
  "owner_head": "6e0123f5afb79f14afa50015c26d934b4c1d5e3a",
  "provider_receipt_id": "prov-receipt-caller-owned-123",
  "receipt_status": "DELIVERED",
  "send_returned_success": true,
  "used_default_http_transport": true
}
```

This uses a real loopback TCP connection and the unmodified default transport;
no injected transport or production-marker attribute is involved. Changing the
transport mechanism from a callback to a socket does not make the remote party
authoritative.

The shared HMAC design is also not independent provider proof: the client that
verifies the response possesses the same secret required to mint it. It can
self-author the provider response and readback. Likewise, comparing two
caller-selected environment SHAs is not deployment readback.

Required correction:

- bind the production endpoint to externally provisioned deployment authority
  and reject loopback, non-HTTPS, arbitrary constructor URLs, and unallowlisted
  origins from `DELIVERED` classification;
- replace self-verifiable shared-secret response tokens with an independently
  verifiable provider signature (for example, a pinned provider public key) or
  an authenticated provider readback API whose authority cannot be minted by
  the notification client;
- obtain deployed release identity from authenticated deployment metadata or a
  signed manifest, not from a caller-selected `RELEASE_SHA` /
  `TRUSTED_DEPLOYED_RELEASE_SHA` pair;
- add a negative mutation reproducing the real-loopback default-transport case
  above and require `TEST_ONLY`/`FAILED`, never `DELIVERED`;
- retain the API-v1/OpenAPI corrections and exact watch-signature checks, then
  rerun the complete reliability/contract batch.

## Verification receipts

```text
API-v1/OpenAPI exact-head contract tests: 2 passed
default-transport caller-owned loopback mutation: DELIVERED (blocking)
```

The task remains `NO-GO`. Do not use this local receipt as live provider proof,
open/refresh a release PR, or deploy from this rejected head.
