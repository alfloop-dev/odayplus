# R7 Network Review decision dialog acceptance

The implementation authority is Package 10 / R7 together with
`ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`.
The prototype is a visual reference; API, authorization and persistence
contracts remain authoritative for decisions.

## Restored UI contract

`Dialog Review Decision` retains the original 520 px width, 14 px radius and
20 px viewport margins. Its panel, rather than the background overlay, owns
the modal semantics and focus boundary. At a 390 px viewport the panel is
350 px wide. The panel scrolls within the available viewport height.

GO, WAIT, Return and Reject preserve their existing validation rules: reason
at least ten characters, WAIT conditions, Return required-data list, and risk
acknowledgement when overriding the system recommendation. Every form field
has an associated visible label. Errors are announced through an alert.

Focus starts in the reason. Tab and Shift+Tab remain within the panel, Escape
closes it and closing restores the invoking control. While submission is
pending, the reason retains focus and all textual inputs become read-only;
risk acknowledgement, close and cancel controls are disabled. Escape does
not hide a pending request. An unconfirmed result keeps the reason,
conditions, required data and acknowledgement for operator review.

## Verification scope

Supplemental browser checks exercise four action forms at 1440, 1024 and
390 px. They verify the review-tab cold-open URL, panel geometry, document
width, accessible labels, axe, Tab/Shift+Tab, Escape and focus return, without
posting a decision. They remain outside the exact 130-case business E2E
inventory.

The existing golden GO business flow holds the actual API response while
asserting pending controls and focus, then releases that same response and
checks the committed outcome. No synthetic success replaces the API write.
The other existing API/browser cases retain mapping, transaction failure,
replay and authorization checks.

```bash
npm run test --workspace=@oday-plus/web -- --maxWorkers=2
npm run typecheck --workspace=@oday-plus/web
npx playwright test --config tests/visual/operator-store-ops-parity.config.ts tests/visual/operator-network-review-parity.spec.ts
npx playwright test tests/e2e/operator-network-review.spec.ts --workers=1 --retries=0
```

These isolated fixture checks do not authorize real-account actions or grant
remote visual approval. They do not prove all canonical record scopes,
physical audit/outbox atomicity, first-party model/data admission, operational
release rehearsals or the nine release-authority approvals. Full-system
acceptance remains open until those requirements have their own evidence.
