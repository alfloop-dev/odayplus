# Growth outcome confirmation

Task trace: `ODP-SYS-ACCEPT-261011`.
Visual source: R7 / Package 10 `Dialog Growth Outcome`, with the binding
VDC-002 mobile and VDC-003 accessibility corrections in
`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`.

The workbench opens a real confirmation dialog before submitting an outcome.
It shows the current action's target/observed lift and causal evidence, preserves
the existing closeout gate and requires an operator-entered reason. Opening,
typing, cancelling or navigating the dialog does not submit a business write.
Ineffective and insufficient-evidence actions remain blocked from direct closeout
even after a reason is entered. The interface uses the current data/gate rather
than the prototype's freely selectable mock verdicts.

The dialog uses the existing focus/keyboard behavior: initial reason focus,
Tab confinement, Escape/close and focus return. An in-flight submission keeps
the dialog visible and the reason read-only. A successful API response must
identify the same action and outcome; the interface reports submission and
asks for refreshed state rather than inferring final closure from HTTP status.

The browser retains one idempotency key for retries of the same action, outcome,
evidence and trimmed reason. Editing that intent creates a new key. The outcome
API binds supplied keys to the verified tenant, authenticated subject and request
digest in the tenant aggregate's `outcomeIdempotencyResults`. A changed request
with that key is a conflict. Other actors do not inherit the prior receipt;
authorization is checked before replay. Legacy unbound receipts return
`UNBOUND_LEGACY_REPLAY` for governed reconciliation, rather than being assigned
an actor or reapplied. A blank/missing reason is rejected before business mutation.
Body actor/persona names do not set outcome attribution.

This register is an application replay record, not a new canonical audit schema
or approved retention policy. These changes do not certify complete Growth
first-party evidence authority, canonical audit/outbox atomicity, every scoped
business permission or all R7 screens. The release registers, independent
discipline reviews and authenticated remote visual acceptance remain required.

Repeatable isolated checks:

```bash
.venv/bin/python -m pytest tests/contract/test_operator_growth_api.py \
  tests/integration/test_operator_live_domain_modules.py -q
npm run test --workspace=@oday-plus/web
npx playwright test tests/e2e/operator-growth.spec.ts --workers=1
npx playwright test --config tests/visual/operator-store-ops-parity.config.ts
```

The supplemental visual configuration includes Store Ops and Growth outcome
geometry/axe checks outside the fixed business E2E receipt inventory. Its
fixtures are local test evidence, not production or remote approval.
