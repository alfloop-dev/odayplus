# HeatZone human data gate

Task: `ODP-PLAN-HEATZONE-LABEL-BACKFILL-001`  
Validator: `Codex3`  
Required supplier and reviewer: `Human/Ops`

## Current decision

**Blocked, fail closed. HeatZone remains `GOVERNED_DISABLED`.**

No Human/Ops source-system readback, immutable label snapshot, dataset hash,
owner-authored receipt, freshness/cutoff result, or eligibility replay was
present at repository HEAD `eed83c0937f491211247ee3fdb0bdf8d932564fb` on
`2026-08-01T15:01:45Z`. The latest resolvable authoritative evidence still
reports `0 / 200` eligible mature real labels and `0 / 2442` stores with an
approved immutable `opened_on`.

[`VALIDATION_BLOCKER.json`](./VALIDATION_BLOCKER.json) is an AI-authored
validation result only. It deliberately does not claim a production count or
act as a source receipt. It pins the prior evidence hashes, reconciles every
task acceptance criterion, and declares the exact packet Human/Ops must
supply.

## Unblock rule

Human/Ops must provide the complete authoritative packet in one batch:

1. reviewer-accessible source-system snapshot and immutable locator;
2. exact canonical dataset SHA-256, serialization, and ordering;
3. named data owner and owner-authored attestation;
4. source observed time, event cutoff, freshness SLA, and result;
5. exact eligibility query/hash and reproducible eligible count of at least
   200;
6. zero or fully explained exclusion counts for synthetic/fixture,
   auto-seeded, duplicate, immature, ownerless, stale, and unresolvable rows;
7. row-level canonical identity, opening authority, historical H3/POS lineage,
   and 90-prior-day/28-forward-day maturity fields required by
   `heatzone-training-view-v2`.

After receipt, the validator and reviewer must re-run the entire inventory,
eligibility, deduplication, maturity, freshness, lineage, and hash/count
reconciliation. A partial correction does not permit review handoff or model
activation.
