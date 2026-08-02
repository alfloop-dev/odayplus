# ODP-PLAN-OBSERVABILITY-LIVE-001 — Independent Review Round 13

- Reviewer: `CodexCoordinator`
- Reviewed exact pushed owner head: `8e3cf775b48f760d58ef680c47efedfed6d180c5`
- Result: `CHANGES_REQUESTED`
- Live/release decision: `NO-GO`

## B1 — Caller-controlled provenance labels and function attributes still reach `DELIVERED`

The adapter now checks ambient strings `ONCALL_SECRET_PROVENANCE` and
`DEPLOYMENT_RELEASE_PROVENANCE`, and treats an injected function as production
when the function has `is_production` or `is_production_transport` set.
All three values remain caller-controlled in the same process that supplies
`ONCALL_PROVIDER_SECRET`, release SHA values, and the injected transport.

Exact-head mutation:

1. set `ONCALL_PROVIDER_SECRET`, `RELEASE_SHA`, and
   `TRUSTED_DEPLOYED_RELEASE_SHA`;
2. set the two provenance environment values to accepted strings;
3. attach `is_production_transport=True` to a caller function;
4. return a hash computed from the caller-selected secret and release.

Result:

```text
{'ok': True, 'error': None, 'status': 'DELIVERED'}
```

This is the same authority problem as Round 12 with two additional
caller-selected labels. A Python function attribute is not a deployment trust
boundary.

Required correction:

- remove ambient provenance labels and transport attributes as authority;
- make the production adapter obtain secret provenance and deployed-release
  identity from a fixed deployment-controlled integration/readback that the
  invoking process cannot select;
- keep injected transports permanently test-only and unable to emit
  `DELIVERED`, regardless of function attributes or environment values;
- retain this exact mutation as a negative regression.

## B2 — Arbitrary hex signatures are explicitly accepted

`authenticate_provider_watch_signature()` computes candidate signatures, but
when the supplied signature does not match any candidate it only checks whether
the value looks like `sig-sha256-` plus 16 or 64 hexadecimal characters. A
well-formed non-matching value then falls through as valid. Readback identity is
also accepted when an arbitrary string merely ends with a release-SHA prefix.

Exact-head mutation:

```text
provider_signature = "sig-sha256-" + "e" * 16
provider_readback_identity = "anything-ending-" + release_sha[:8]
arbitrary_hex_signature_authenticated=True
```

The production path additionally falls back to the public constant
`monitoring-provider-trust-root` when no secret exists. Candidate signatures
that omit `proof_hash`, and release-SHA-shaped signature tokens, are accepted as
well. These alternatives do not authenticate an exact provider response.

Required correction:

- require constant-time equality against exactly one provider-authenticated
  signature scheme bound to provider receipt, project, full release SHA, exact
  interval, query, metric series/proof hash, and readback identity;
- reject every non-matching signature, including syntactically valid hex;
- remove public fallback secrets, truncated release tokens, suffix-only
  readbacks, and legacy weaker signature candidates from live classification;
- obtain the verification trust root from fixed deployment-controlled
  provenance or authenticated provider API readback;
- add arbitrary-valid-hex, suffix-only-readback, default-secret, omitted-proof,
  and caller-controlled-provenance negative mutations.

## Verification receipts

```text
python3 -m pytest -q tests/reliability/test_runtime_observability.py \
  -k 'round12_remediation'
. [100%]

caller-controlled provenance/transport mutation
{'ok': True, 'error': None, 'status': 'DELIVERED'}

direct non-matching valid-hex mutation
arbitrary_hex_signature_authenticated=True
```

Do not deploy or claim live provider delivery/watch-window evidence from this
rejected head. Release remains `NO-GO`.
