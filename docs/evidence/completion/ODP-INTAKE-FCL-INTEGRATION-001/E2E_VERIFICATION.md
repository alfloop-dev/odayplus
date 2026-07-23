---
task_id: ODP-INTAKE-FCL-INTEGRATION-001
artifact: canonical-browser-e2e-verification
status: passed
verified_at: 2026-07-23T21:49:32Z
worktree_head: 47daa5918400e9823a3a2dd3bf758c46a13cab42
suite: tests/e2e/operator-assisted-listing-intake-functional-closure.spec.ts
test_count: 23
---

# Assisted Listing Intake Browser E2E Verification

## Current Result

The canonical Playwright suite contains 23 tests and contains no `skip`,
`fixme`, or expected-failure declaration. The latest run used the current
shared integration worktree and completed in one uninterrupted serial
invocation.

Exact result:

```text
23 passed
0 failed
0 did not run
0 declared skips
Duration: 6.9 minutes
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
- authoritative `RUN`, FAILED retry/cancel/DLQ/replay history and two-actor
  quarantine release;
- complete 428, 409, 403, and 422 recovery envelopes with draft preservation;
- zero serious or critical Axe violations, keyboard focus containment/return,
  and reduced-motion behavior on the canonical durable route.

## Focused Recheck

The FAILED retry/cancel/DLQ/replay test passed alone before the complete run:

```text
1 passed
0 failed
0 skipped
Duration: 55.9 seconds
```

Command:

```bash
python3 scripts/e2e/run_assisted_intake_functional_runtime.py \
  --grep "FAILED intake exposes retry/cancel/DLQ/replay controls"
```

The subsequent complete serial run also passed the same lifecycle assertions.

## Responsive Evidence

The responsive test passed in the complete run and wrote:

| Viewport | File | Dimensions | SHA-256 |
|---|---|---:|---|
| Mobile | `screenshots/viewport-390.png` | 390 x 1012 | `23755c5da0d1b513ccc6ea9fabfcc011c18c652c465285c9b0a857ae3debe48b` |
| Tablet | `screenshots/viewport-1024.png` | 1024 x 901 | `b7a17b1a14e9ad054de92c0e8a5168b7f60ed785f1c9cdb39212471141d74f49` |
| Desktop | `screenshots/viewport-1440.png` | 1440 x 968 | `802277f3fa617d8554c2216a90dfef677413885d5f3eb8ead8dd1c8f33544c68` |

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
