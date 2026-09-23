# Gate 1 (Contract Gate) — receipt for candidate 9694320fc8a9922cbfed9b63e647508db32ef26c

- **Task**: `ODP-DEV-RELEASE-GATE-RECONCILIATION-005`
- **Recorded by**: Claude (owner of this reconciliation task)
- **Gate owner of record**: Claude / reviewer Codex2
- **Result**: pass
- **Candidate**: `9694320fc8a9922cbfed9b63e647508db32ef26c`
- **CI run**: [35886760039](https://github.com/alfloop-dev/odayplus/actions/runs/35886760039) on that exact head

## Why this gate was blocked, and what changed

The static contract evidence already passed on the previous candidate; the gate was held
open by one unmet item:

> Event schema policy/implementation discrepancy.
> `docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml:8` compatibility_policy
> requires 'consumers must ignore unknown optional fields', but all 16 payload schemas in
> `ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENT_PAYLOAD_SCHEMAS_V1.yaml` set
> `additionalProperties: false`, and `shared/domain/events.py:174`
> `EventContractValidator.validate_schema()` rejects additional properties.

The consequence was concrete: once a producer added an optional field in a minor version,
`AssistedListingIntakeConsumer.consume()` would fail validation on every event and route
it to the DLQ. Forward compatibility did not hold.

PR #1359 separated the two validation stances instead of loosening the schemas. Producers
keep the closed schema, so a mistyped field name is still caught at publish time;
consumers read with `mode=CONSUMER_MODE`, which ignores unknown payload fields and nothing
else. Required fields, types, enums, formats and `sensitive_fields` are still enforced in
both modes.

The repair carries its own A/B evidence: with the two behavioural lines reverted and only
the `CONSUMER_MODE` constant kept, the two new guards fail with
`Routing event ... to DLQ. Reason: Validation failed: ['Payload: Additional property not
allowed: claimed_via']` (2 failed, 12 passed); with the repair in place, 14 passed.
`test_producer_mode_rejects_unknown_payload_field` passes in both, which is what shows the
producer side was not weakened.

## Measurement on this candidate

`product-api-contract` job [107268834937](https://github.com/alfloop-dev/odayplus/actions/runs/35886760039/job/107268834937):

```
uv run python delivery_toolchain/openapi/check_drift.py --base-ref origin/dev
      OK: 0 additive, 0 approved breaking, 0 unapproved breaking.
API contract gate: PASS
```

`tests/contract` runs inside `product-lint-unit` (success on this candidate). The
event-contract suite was measured directly on the repair at 869 passed across
`tests/contract` plus the two assisted-listing integration modules.

## Required checks

| Criterion | Evidence on this candidate |
|---|---|
| OpenAPI diff reviewed for breaking changes | `check_drift.py`: 0 unapproved breaking |
| event schema compatibility checked | consumer forward compatibility restored by PR #1359; guards in `tests/contract/test_assisted_listing_intake_events.py` |
| data contract compatibility checked | `data_contract_digest` `sha256:05e2cb05…` recorded in the release manifest for this candidate |
| model input/output compatibility checked | no model interface change between `39ae43f6` and this candidate |
| breaking changes carry migration/compatibility window/rollback | no breaking change in this candidate |

## What this receipt does not claim

Live runtime event replay against a deployed environment is not covered here; it remains
with `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` and is a post-deployment measurement.
