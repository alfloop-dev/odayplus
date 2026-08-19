# ODP-PLAN-OBSERVABILITY-LIVE-001 — Round 10 independent review

- Reviewer: CodexCoordinator
- Owner handoff head: `7f405a3b56fb8079f299a4107c8ae924ac207027`
- Verdict: **CHANGES_REQUESTED / NO-GO**
- Scope: full B1–B4 re-audit after the round-9 rejection

## Exact-head result

The handoff does not close the authentic-provider, exact-release, or measured-lineage
requirements. The focused owner tests pass, but they encode caller-controlled strings
as authentic and therefore do not prove the acceptance contract.

## B1 — Caller-controlled alert response still becomes `DELIVERED`

`OnCallNotificationAdapter` accepts any string beginning with
`sig-authentic-`, `sig-sha256-verified`, `readback-`, or
`readback-verified`. Those values are returned by the same caller-controlled HTTP
response and are not verified with a configured provider public key, MAC trust root,
provider API readback, or immutable expected digest.

Independent mutation:

```text
provider_receipt_id=attacker-selected-receipt
provider_signature=sig-authentic-attacker-controlled
provider_readback=readback-attacker-controlled
result.ok=True
result.status=DELIVERED
```

The mutation used release SHA `1111111111111111111111111111111111111111` and a
locally injected transport. No provider secret or authentic provider response was
present.

The checked-in test `test_round8_oncall_adapter_authenticity_and_sha_enforced`
also injects `sig-sha256-verified-123456` and
`readback-verified-654321`, then expects `DELIVERED`. That assertion ratifies the
vulnerability instead of detecting it.

## B2 — Release syntax is not release authenticity

Rejecting blank and all-zero values is useful input validation, but any 40-character
hex string is still accepted as an authentic deployed release. The independent
mutation above proves an arbitrary repeated-`1` value reaches `DELIVERED`.

Bind delivery to an immutable expected deployed-release identity obtained from a
trusted deployment/control-plane source. Do not infer authenticity from length and
hex syntax.

## B3 — Watch evidence is locally fabricated and not bound to the handoff head

The committed receipt contains release SHA
`f8b3266fef4d5b1e782b8d07561bda05c3953527`, while the exact pushed handoff
head is `7f405a3b56fb8079f299a4107c8ae924ac207027`. Exact-head verification fails:

```text
ValueError: Release SHA mismatch in watch-window receipt:
expected '7f405a3b56fb8079f299a4107c8ae924ac207027',
got 'f8b3266fef4d5b1e782b8d07561bda05c3953527'.
```

More importantly, `record_deployment_watch_window_status` locally constructs:

- `provider_receipt_id` from `sha256(release_sha + project)`;
- `provider_signature` from the locally computed proof hash;
- `provider_readback_identity` from project and release substrings.

These are deterministic repository-generated labels, not provider-issued proof. The
verifier only checks that they are non-empty strings and never authenticates them.
Changing only the committed receipt SHA cannot close this finding.

## B4 — HeatZone lineage is still not an implemented measured source

`get_measured_topk_adoption_rate` reads ad-hoc attributes
`adopted_topk_count` and `total_topk_count` from the latest scoring result. No
authoritative survey/outcome repository, source identity, observation timestamp,
eligible population, or immutable lineage receipt is wired. Repository-wide search
shows no producer of those attributes. The inventory nevertheless labels the call
site as “measured survey adoption lineage.”

Either wire the authoritative measured source and prove its lineage, or explicitly
mark this KPI unavailable/NO-GO without claiming production coverage.

## Required next batch

Close B1–B4 together on one exact pushed head:

1. Verify alert-provider proof against a non-caller-controlled trust root and perform
   provider API readback (or retain `TEST_ONLY`/NO-GO).
2. Bind alert delivery and watch evidence to a trusted deployed-release identity.
3. Store provider-issued query/receipt identity and authenticate it; locally generated
   hashes may be integrity aids but cannot establish provider authenticity.
4. Wire measured HeatZone adoption lineage or explicitly report it unavailable.
5. Add the mutations above and exact-head receipt verification to the permanent test
   suite, then rerun the complete acceptance packet.

No PR, deployment, or release claim is authorized by this review.
