# ODP-P10-CAN-001-R3B Direct Route, API, and A11y Addendum

- Addendum ID: `ODP-P10-CAN-001-R3B-ADD-003`
- Parent task: `ODP-P10-CAN-001-R3B`
- Status: `ready_for_pickup`
- Prepared: `2026-07-26`
- Trigger: independent Fleet review plus coordinator Playwright execution

## Rejection

R3B remains `NO-GO`. The following findings are binding:

1. `/intake/{id}` is still blocked by the global Operator shell API gate, so
   shell loading/error can prevent the intake API and detail page from mounting.
2. A missing authoritative target listing does not fail closed for outcomes
   that require comparison.
3. Existing authoritative promotion/job/read-model data is not consistently
   passed into the continuous receipt and timeline sections.
4. The deep link omits inbox context and opening detail uses history replace,
   so browser back/forward is not proven.
5. Correction, assignment/SLA, and decision controls are not all gated by the
   same fail-closed role/subject checks. The UI must not invent owner, role, or
   due-date assignment values.
6. The fixed `64px` detail offset can be shorter than a wrapped mobile/tablet
   Top Navigation, allowing the detail layer to cover navigation.
7. Captured time is labeled as submission time, audit rows append the current
   record version instead of event-specific evidence, and provider identity is
   conflated with provider listing ID.
8. Unit tests do not exercise shell API failure, a real direct pathname,
   push-versus-replace history, missing target fail-closed behavior, or
   read-only controls.
9. The unchanged a11y spec reaches the product only when the Playwright web
   child uses the repository's isolated-E2E runtime markers. Without them,
   fail-closed auth redirects to `/login` and readiness ends at
   `WEB_AUTH_NOT_CONFIGURED` 503.
10. The a11y spec still expects the retired
    `data-screen-label="Dialog 收件處理詳情"` instead of the canonical
    `data-screen-label="Intake 收件處理詳情頁"`.
11. Coordinator axe execution found serious violations in the detail:
    invalid ARIA table row/child relationships and insufficient contrast in
    assignment/SLA and completed stage text.

## Additional Narrow Write Authorization

In addition to the existing R3B path contract, R3B may edit only:

```text
playwright.config.ts
tests/e2e/operator-assisted-listing-intake-a11y.spec.ts
```

The a11y spec edit is limited to replacing the retired detail screen-label
selector with the canonical Package 10 full-page selector. No test may be
removed, skipped, softened, or made conditional.

The Playwright config edit is limited to:

1. starting the API through the committed `uv` environment so declared Python
   dependencies such as `mlflow` are available; and
2. setting the complete existing isolated-E2E runtime markers only on the
   Playwright webServer child, together with its API base URL.

Production auth, middleware, runtime policy, retries, reporters, projects,
workers, timeouts, and assertions must not be weakened.

## Required Product Remediation

### Durable Direct Route

- A durable intake detail context must mount the Network/Intake runtime even
  while the unrelated Operator shell bootstrap is loading, empty, or failed.
- All non-intake workspaces remain behind the existing shell API fail-closed
  gate.
- Opening detail from the inbox creates a browser history entry.
- Direct and visible deep links retain filters, sort, view, selected intake,
  and other safe inbox context.
- Closing detail returns to the same inbox context. Browser back/forward must
  be covered with distinct push and replace behavior.

### Authoritative Data and Decisions

- Use the authoritative legacy Operator intake read model already returned by
  the API for parsed fields and audit events.
- Pass an authoritative hydrated promotion receipt and score job into the
  receipt/timeline composition when present.
- Missing receipts, target listing values, history, or job data stay visibly
  `UNAVAILABLE`; never synthesize them.
- `POSSIBLE_MATCH`, `REVISION`, and duplicate decisions that require a target
  are disabled when the exact target listing is unavailable. Show the stable
  reason and refresh/return action.
- Keep provider/source identity distinct from provider listing ID. Use
  `sourceListingId` when that exact API value is displayed or compared.
- Label captured/observed/submitted times according to their actual field.
  Never attach the current record version to an audit event unless the API
  supplied that event version.

### Permissions and Assignment

- `canCorrect` gates every field-fix trigger.
- `canDecide` gates every identity decision.
- Assignment claim/transfer/pause/resume controls are absent or disabled for
  read-only roles and when required authoritative assignment, SLA, or session
  subject data is unavailable.
- Do not fabricate an owner subject, owner role, due date, receipt, actor, or
  correlation value to make a control appear functional.

### Responsive and Accessibility

- Detail placement must derive from the actual Top Navigation height or normal
  document flow; a fixed guessed `64px` offset is not acceptance evidence.
- At 390px, 1024px, and 1440px the navigation remains visible and operable,
  the detail is not covered, and there is no page-level horizontal overflow.
- Replace invalid ARIA grid/table relationships with valid native table
  semantics or another valid accessible structure.
- Fix all serious/critical axe contrast findings without hiding content.
- Keep the screen-reader change summary and keyboard-operable controls.

## Required Tests

- Unit tests must cover shell loading/error with direct intake detail,
  `/intake/{id}` pathname behavior, history push versus replace, preserved
  query context, missing target decision lockout, read-only correction and
  assignment controls, promotion/job receipt wiring, truthful labels, and
  valid semantic structure.
- The complete web unit suite, web/root typecheck, and root build must pass.
- The complete six-test a11y spec must run unchanged except for the one
  authorized canonical screen-label selector and pass `6/6`.
- Axe must report zero serious or critical violations for both inbox and
  detail.
- `tests/e2e/**` must otherwise be unchanged.
- API, auth, middleware, source policy, permission rules, and archives remain
  unchanged.

The R3B ACK must record this rejection, cite the committed addendum SHA, list
the exact two newly authorized test/config diffs, and remain no-go until
coordinator visual review at 390px, 1024px, and 1440px passes.
