---
task_id: ODP-INTAKE-FCL-INTEGRATION-001
artifact: canonical-browser-e2e-verification
status: passed
verified_at: 2026-07-23
suite: tests/e2e/operator-assisted-listing-intake-functional-closure.spec.ts
test_count: 23
---

# Assisted Listing Intake Browser E2E Verification

## Current Result

The canonical Playwright suite contains 23 tests and contains no `skip`,
`fixme`, or expected-failure declaration. The latest full run completed in one
uninterrupted serial invocation.

Exact result:

```text
23 passed
0 failed
0 did not run
0 declared skips
Duration: 6.3 minutes
```

Command:

```bash
python3 scripts/e2e/run_assisted_intake_functional_runtime.py
```

The run proves:

- canonical URL submission, queued receipt, detail polling, direct-open, and
  reload against `/api/v1/intakes`;
- all five source-policy outcomes, with no retrieval for non-approved states;
- terminal `CANCELLED`, exact duplicate navigation to the real Listing detail
  route, assisted-entry correction lineage, and distinct match outcomes;
- deterministic `POSSIBLE_MATCH`, durable proposal receipts, self-review
  denial, independent second-actor approval, Candidate creation, and SiteScore
  receipts;
- all six role modes, masking/purpose binding, desktop/tablet/mobile fallback,
  assignment claim/transfer, and SLA pause/resume/escalate/complete readback;
- authoritative FAILED retry/cancel/DLQ/replay history and two-actor quarantine
  release;
- complete 428, 409, 403, and 422 recovery envelopes with draft preservation;
- zero serious or critical Axe violations, keyboard focus containment/return,
  and reduced-motion behavior on the canonical durable route.

## Focused Gate

The keyboard/focus gate also passed alone before the complete run:

```text
1 passed
0 failed
0 skipped
Duration: 9.7 seconds
```

Command:

```bash
python3 scripts/e2e/run_assisted_intake_functional_runtime.py \
  --grep "canonical durable intake dialog is keyboard operable"
```

## Responsive Evidence

The responsive test passed in the latest full run and wrote:

| Viewport | File | Dimensions | SHA-256 |
|---|---|---:|---|
| Mobile | `screenshots/viewport-390.png` | 390 x 1012 | `0a095ad65af19a980e496a50bf73384a76aab9efe595b8fd4e80f78941168097` |
| Tablet | `screenshots/viewport-1024.png` | 1024 x 901 | `15ef71a8462e7507352bfc6853b489dc417abc05ab85711244236ffb8268947a` |
| Desktop | `screenshots/viewport-1440.png` | 1440 x 968 | `13b52050a742f58fdc332de20154ca643c185b30ac65e23da2bb9bf2800b88fb` |

## Evidence Boundary

Browser tests use canonical `/api/v1/intakes` contracts and durable routes.
They do not use legacy `/api/v1/operator/network-listings` intake,
`fixture_replay`, fabricated receipts, `test.fail`, or synchronous `READY`
assumptions.

Route-manipulated presentation checks do not claim persistence proof. Real HTTP
provider and backend lifecycle persistence remain covered by:

```text
tests/integration/test_assisted_listing_functional_runtime.py
tests/contract/test_assisted_listing_operations.py
tests/contract/test_api_error_envelope.py
```
