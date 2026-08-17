# ODP-P10-CAN-001-R3B Promotion Test Addendum

- Addendum ID: `ODP-P10-CAN-001-R3B-ADD-002`
- Parent task: `ODP-P10-CAN-001-R3B`
- Status: `ready_for_pickup`
- Prepared: `2026-07-26`

## Reason

The canonical Package 10 detail renders promotion as a continuous section, not
a tab. The unchanged unit integration test still clicks
`data-testid="tab-promotion"`, which forced a false production wrapper to remain
after the visible tab control was retired.

## Narrow Write Authorization

R3B may additionally edit:

```text
apps/web/features/operator/network/intake/__tests__/PromotionSagaIntegration.test.tsx
```

The edit must only:

1. remove interactions or assertions that require `tab-promotion`;
2. assert the already-visible continuous `intake-promotion-section` and
   `promotion-review-panel`; and
3. preserve every promotion API, permission, second-actor, idempotency,
   conflict, receipt, and failure assertion.

No product workaround, hidden compatibility tab, test skip, assertion
weakening, E2E change, API change, or auth change is authorized.

## Exit Gate

- Production contains no `tab-promotion` marker or control.
- The complete web unit suite passes.
- The ACK cites this committed addendum and names the exact test diff.
