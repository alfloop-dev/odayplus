# ODP-PLAN-OBSERVABILITY-LIVE-001 — Independent Review Round 12

- Reviewer: CodexCoordinator
- Reviewed exact pushed head: `456995680b6073598a888eb7fedaa153dc6d625c`
- Result: `CHANGES_REQUESTED`
- Live/release decision: `NO-GO`

## B1 — Caller-controlled environment is still treated as provider authority

`OnCallNotificationAdapter` reads all three trust inputs from the worker
environment:

- `ONCALL_PROVIDER_SECRET`
- `RELEASE_SHA`
- `TRUSTED_DEPLOYED_RELEASE_SHA` / `EXPECTED_RELEASE_SHA`

It then verifies the response with a SHA-256 token computed from that same
caller-selected secret. Requiring a non-empty value does not make the value
non-caller-controlled.

The exact reviewed head was reproduced with an injected transport that selected
all three environment values and returned a signature computed from them:

```text
{'ok': True, 'error': None, 'status': 'DELIVERED',
 'receipt_id': 'prov-caller-forged-123'}
```

Required correction:

- bind the production adapter to deployment-controlled secret provenance and an
  immutable deployed-release readback that the invoking process cannot select;
- do not accept constructor or ambient environment values as the authority root
  for a `DELIVERED` classification;
- keep injected transports and caller secrets explicitly test-only, with a
  production path that cannot classify their output as authentic;
- add this exact caller-computes-signature mutation and require it to fail closed.

## B2 — Watch-window provider signature is present but not authenticated

`record_deployment_watch_window_status()` now requires non-empty
`provider_receipt_id`, `provider_signature`, and
`provider_readback_identity`. `verify_watch_window_receipt()` only compares those
fields against the duplicated values stored inside
`provider_query_response`. It never verifies the signature with a fixed provider
public key, external verification endpoint, or immutable provider readback.

Consequently an injected query response can author the metrics, release SHA,
receipt ID, signature, and readback identity together. The task test named
`valid_watch_transport` demonstrates the weakness: it uses arbitrary local
strings such as `sig-sha256-aaaaaaaaaaaaaaaa` and the verifier returns
`WATCH_PASSED`. Tampering only one duplicated copy is rejected, but consistently
forging both copies remains accepted.

Required correction:

- verify the provider signature independently using a fixed provider trust root,
  or perform authenticated provider API readback bound to the exact query,
  project, release, interval, metric series, and receipt ID;
- do not treat field presence or equality between two caller-authored copies as
  provider authentication;
- add a mutation where an injected transport supplies internally consistent fake
  proof fields and require both recording and verification to fail closed.

## B3 — Retained improvements

Missing provider-proof fields now fail closed, locally generated fallback fields
were removed, and unmeasured HeatZone adoption remains `None` / NO-GO. These
improvements remain required, but B1/B2 prevent live evidence or release
approval.

Re-audit the complete observability acceptance set on one new exact pushed head.
Do not deploy or claim live provider delivery/watch-window proof from this
rejected head.
