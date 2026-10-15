# Governance URL state and record ownership

R7 acceptance requires route cold-open, reload, back/forward and shareable
selection state (Operator Console R7 uplift task acceptance gate 3). A URL is
a navigation request, never approval authority.

## URL contract

The existing shell keys `ws=govern`, `tab` and `entity` now reach the Governance
workspace. The eight tab IDs are `approvals`, `decisions`, `audit`,
`userManagement`, `evidencePackage`, `statusBoard`, `featureFlags` and
`learningHub`. A missing or legacy `overview` tab selects approvals.
`evidence` selects evidence within the requested approval. `auditCategory`
restores the audit filter; missing means all. Existing unrelated query keys
are retained. Reasons, tokens and business-write payloads are never serialized.

User navigation creates history entries. Popstate restores the allowed
workspace, tab, approval, evidence and audit category using the current
persona's workspace ceiling; the API still verifies the principal and grants.
Role/reset navigation replaces the entry and clears entity/evidence/filter
selection rather than keeping a previous persona's target in the URL.

An explicit approval ID absent from the authorized snapshot remains
unavailable: it never falls back to the first row and exposes no decision
controls. An explicit unavailable evidence ID likewise never displays another
item's evidence. Missing owner, evidence readiness and completed-decision
reason are displayed as unavailable, rather than invented from the persona or
a generic policy sentence.

## Decision state

The in-memory reason is associated with its approval ID. Evidence or tab
changes on the same record preserve it; another record never inherits it.
While a decision is pending, queue/tab/evidence controls are disabled and the
reason is read-only. An external history change waits until the pending
request finishes before applying its new target. The request retains the
approval and reason captured when submitted; API authorization is unchanged.

## Checks and boundary

Focused component cases cover asynchronous snapshot selection, missing
approval/evidence, reason ownership and navigation during an unconfirmed
write. Supplemental browser cases at 1440/1024/390 cover all eight tabs,
reload, back/forward, audit filters and unavailable targets with zero decision
or export writes. Existing business E2E cases retain actual API-backed writes.
These supplemental cases remain outside the exact 130 business-case inventory.

```bash
npm run test --workspace=@oday-plus/web -- --maxWorkers=2
npm run typecheck --workspace=@oday-plus/web
npx playwright test --config tests/visual/operator-store-ops-parity.config.ts tests/visual/operator-governance-navigation.spec.ts
npx playwright test tests/e2e/operator-governance.spec.ts --workers=1 --retries=0
```

This implements navigation and record ownership. It does not close all R7
visual states, real-account admission, first-party/model bindings, canonical
physical audit/outbox atomicity, release rehearsals or the nine human release
approvals. Authenticated remote visual review and independent exact-source
review remain required.
