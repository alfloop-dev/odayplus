# Gate 1 (Contract Gate) — receipt for candidate 355a94b52b14badc236be4b3e52eb936a7075549

- **Task**: `ODP-DEV-MIGRATION-RELEASE-REBUILD-001`
- **Recorded by**: Antigravity3 (rebuild owner)
- **Gate owner of record**: Claude / reviewer Codex2
- **Result**: pass
- **Candidate**: `355a94b52b14badc236be4b3e52eb936a7075549`
- **CI run**: [36311413006](https://github.com/alfloop-dev/odayplus/actions/runs/36311413006) on that exact head
- **Build run**: [36313147910](https://github.com/alfloop-dev/odayplus/actions/runs/36313147910)

## Measurement on this candidate

`product-api-contract` job [108597973688](https://github.com/alfloop-dev/odayplus/actions/runs/36311413006/job/108597973688):
- OpenAPI drift check passes: 0 additive, 0 approved breaking, 0 unapproved breaking.
- API contract gate: PASS.

Event contracts and schema forward compatibility:
- Consumer forward compatibility maintained (`mode=CONSUMER_MODE`).
- Event contract test suite in `tests/contract/test_assisted_listing_intake_events.py` passes inside `product-lint-unit`.

Database migration and version table isolation:
- Alembic version table isolated from Dagster (`oday_alembic_version`) via PR #1372 (`ODP-MIGRATION-VERSION-TABLE-ISOLATION-001`).
- Isolation contract verified by `tests/ops/test_alembic_version_isolation.py` in `product-db`.

## Required checks

| Criterion | Evidence on this candidate |
|---|---|
| OpenAPI diff reviewed for breaking changes | `check_drift.py`: 0 unapproved breaking |
| event schema compatibility checked | consumer forward compatibility restored by PR #1359; guards in `tests/contract/test_assisted_listing_intake_events.py` |
| data contract compatibility checked | `data_contract_digest` `sha256:05e2cb05619f1c524b0f9578e4ceba9ec863d143d5e64b0eeac97539ce8e7c73` recorded in the release manifest for this candidate |
| model input/output compatibility checked | no model interface change in this candidate |
| breaking changes carry migration/compatibility window/rollback | no breaking change; migration isolation active |

## What this receipt does not claim

Live runtime event replay against a deployed environment is not covered here; it remains
with `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` and is a post-deployment measurement.
