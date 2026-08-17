# ODP-PLAN-OBSERVABILITY-LIVE-001 — Coordinator review findings, round 21

## Decision

Rejected at exact pushed owner head
`1752bb24c0579c4a61f261dd5d8b7acc238f4aa6`.

Round 20's loopback and bare-boolean mutation now fails closed, but the new
"fixed" verifier identity, response trust key, default provider transport, and
verifier network transport all remain replaceable in the same application
process. A caller that replaces those four composition points can mint a fully
bound Ed25519 response and the adapter again promotes the receipt to
`DELIVERED`.

## B1 — every protected verifier authority is still caller-replaceable

The following values or functions are ordinary mutable Python objects:

- `CANONICAL_PINNED_EXTERNAL_VERIFIER_URL`;
- `PINNED_EXTERNAL_VERIFIER_PUBLIC_KEY_PEM`;
- `OnCallNotificationAdapter._default_http_transport`;
- `urllib.request.build_opener` used by the verifier call.

`_is_valid_external_verifier_url()` compares the requested URL against the
same mutable canonical global. `_verify_external_oncall_delivery()` compares
the instance transport against the same mutable class default and verifies the
response against the same mutable public-key global. Mutating both sides of
those comparisons therefore preserves equality instead of proving deployment
authority.

The independent mutation generated a caller Ed25519 keypair, replaced the
canonical verifier URL and public key, replaced the class default provider
transport, and replaced the verifier opener with a caller response that signed
the exact delivery ID, provider receipt ID, request hash, release SHA, nonce,
timestamp, and status read from the request.

```text
[REAL ON-CALL DELIVERY RECEIPT] del-0db292bd335b
Endpoint: https://oncall-router.oday.plus/api/v1/alerts (HTTP 200 DELIVERED)

ROUND21_RESULT={"class_default_is_caller": true,
 "error": null,
 "ok": true,
 "status": "DELIVERED",
 "verifier_url": "https://caller-verifier.evil.example/verify"}
```

Reproducer: `/tmp/odp_observability_round21_mutation.py`.

The pre-existing Round 18, 19, and 20 reproducers now return `TEST_ONLY`,
`TEST_ONLY`, and `PENDING_VERIFICATION`, respectively. That evidence is useful
but does not cover the complete same-process authority replacement proven
above.

## Complete remediation contract

Do not add another alias, captured default, equality comparison, or in-process
signature check. Those have now failed repeatedly under the task's explicit
caller-composition threat model.

- The application-side `OnCallNotificationAdapter` must never own the
  transition to `DELIVERED`. It may emit a delivery attempt and return
  `PENDING_VERIFICATION` only.
- A separately deployed verifier/provider authority, with configuration and
  signing identity unavailable to the application process, must own the
  durable `DELIVERED` decision.
- Any read model that exposes `DELIVERED` must read an authenticated durable
  verifier receipt from that separate authority and preserve the exact
  delivery ID, provider receipt ID, request hash, release SHA, nonce,
  freshness, and replay constraints.
- Local, injected, monkeypatched, test, or unavailable external paths must
  remain `TEST_ONLY`, `PENDING_VERIFICATION`, or `FAILED`; they must never
  synthesize a positive live claim.
- Add the exact Round 21 full-composition mutation. Also prove that replacing
  any one or all application-process aliases, functions, URL values, keys,
  transports, clocks, UUID sources, or HTTP openers cannot promote the local
  receipt.
- If the repository cannot provide real separate-authority evidence in this
  environment, leave live delivery pending and retain the overall release
  `NO-GO`. Do not fabricate a `DELIVERED` fixture as production evidence.

## Independent verification

```text
PYTHONPATH=. .venv/bin/python /tmp/odp_observability_round18_mutation.py
status=TEST_ONLY

PYTHONPATH=. .venv/bin/python /tmp/odp_observability_round19_mutation.py
status=TEST_ONLY

PYTHONPATH=. .venv/bin/python /tmp/odp_observability_round20_mutation.py
status=PENDING_VERIFICATION

PYTHONPATH=. .venv/bin/python /tmp/odp_observability_round21_mutation.py
status=DELIVERED (blocking negative mutation reproduced)
```

Release remains **NO-GO**. No PR, merge, route change, deployment, or live
delivery claim was performed.
