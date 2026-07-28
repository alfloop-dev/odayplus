---
task_id: ODP-P10-CAN-003-R3A
title: Retire legacy Package 6 Governance E2E assertions
status: ready-for-fleet
owner: Claude
reviewer: Antigravity4
updated_at: 2026-07-26
---

# ODP-P10-CAN-003-R3A Governance Addendum

## Conflict Finding

The isolated R3C Governance run at product commit `c743d34` produced
`1 passed / 3 failed`. The failures are legacy E2E assertions, not permission
to restore a retired visual:

- `tests/e2e/operator-governance.spec.ts` last changed at
  `19c7ab8e516ea6ee6ee62075f1e02686d23428e1` on 2026-07-14 and explicitly
  names the archived Package 6 design.
- `GovernanceWorkspace.tsx` was redesigned for Package 10 at
  `919f3676766fcc24c3467ed1781b4ee6559010c4` on 2026-07-25.
- The current canonical runtime renders Decision Log and Audit Trail as
  semantic article feeds and labels the destructive action `駁回`.
- The legacy spec still searches for a `<table>`, an English `Reject` button,
  and the old validation copy.

The Playwright error snapshots prove that Store Ops, Growth, Network,
Decision Log, approval evidence, actor, model, dataset snapshot, and the
reason form are present in the current runtime. The stale locators fail after
the required content is already rendered.

## Fleet Assignment

Within the existing R3A ownership of `tests/e2e/operator-governance.spec.ts`:

1. Remove archived Package 6 baseline wording and identify Package 10 as the
   canonical runtime contract.
2. Exercise `駁回` and assert the current reason-gate copy without changing
   the product back to an English compatibility label.
3. Assert Store Ops and Growth decisions within the visible Decision Log
   region/article feed, not a retired table.
4. Assert the exported evidence audit event within the visible Audit Trail
   feed, including its persisted identifier or correlation evidence where
   exposed.
5. Preserve all behavioral requirements: insufficient reason is blocked,
   sufficient reason writes a decision, Network approval remains reachable,
   evidence export produces a record, and the audit event survives the
   workspace transition.
6. Run the corrected spec against the accepted R3C product SHA on isolated
   ports before the 16-spec R3B read-only gate.

## Forbidden Compatibility Work

- Do not add a hidden or duplicate table to satisfy the old locator.
- Do not add a duplicate English `Reject` control.
- Do not restore Package 6 visual markup, fixture-only paths, or archived copy.
- Do not remove decision, audit, reason, persistence, actor, evidence, or
  correlation assertions.
- Do not edit `apps/web/**` or `apps/api/**` from R3A.

Any required content genuinely absent from the canonical runtime is a product
NO-GO and must be returned to the owning product task before an assertion is
changed.
