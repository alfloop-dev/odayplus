# ODP-OC-R5-011 — Completion Evidence

- Task: Remediate and complete R5 assisted listing product slice
- Owner: Claude · Reviewer: Codex
- Branch: `task/ODP-OC-R5-011`
- Date: 2026-07-15
- Status at handoff: implementation complete, **not self-finalized** — awaiting
  independent review by Codex.

## 1. What this task delivers

The R5 assisted listing intake slice as a real, API-bound product surface: the
five Package 7 screens, a complete typed client contract, and a broad product
E2E suite driven through the real UI against the real backend.

### The five Package 7 screen labels

Each is a real Operator Console surface carrying its exact archived label
(`data-screen-label`), verified by E2E, not by inspection:

| Screen label | Component |
|---|---|
| `Network URL 收件佇列` | `network/intake/AssistedIntakeQueuePanel.tsx` |
| `Dialog 從網址新增物件` | `network/intake/AddListingFromUrlDialog.tsx` |
| `Dialog 收件處理詳情` | `network/intake/IntakeDetailDialog.tsx` |
| `Dialog 欄位修正` | `network/intake/IntakeFieldFixDialog.tsx` |
| `Dialog 收件決策確認` | `network/intake/IntakeDecisionDialog.tsx` |

Design source: `docs_archive/00_source_zips/operator_console/r5-20260715-package-7/extracted/Oday Plus Operator Console.dc.html`
(the interactive source was opened and read, not just the prose requirement).

## 2. Corrections to the inherited baseline

Three defects were found in what was handed over as "backend/type contracts are
anchored". Each was verified, not assumed:

### 2.1 HEAD `6bc0ccff` did not build (fixed in `e3e66490`)

`packages/openapi-client/src/index.ts:930` carried a stray unmatched `}`. The
class closes at :760, so the brace closed nothing and the package did not parse.

- Proof it was real: `npx tsc -p apps/web/tsconfig.json --listFiles` shows the
  web project compiles this file, and `npm run typecheck --workspace=@oday-plus/web`
  failed with `Expression expected` at :930 before the fix.

### 2.2 The typed client could never work in a browser (fixed)

`OdpApiClient` stored `globalThis.fetch` unbound and invoked it as
`this.fetchImpl(...)`. In a browser this throws `Illegal invocation` — the
request never leaves the page.

This was not merely latent: `OperatorConsole.tsx` already builds a client
client-side for `loadNetworkFindAreasBindings`, whose failure was swallowed by
`.catch(() => undefined)`, so the workspace silently fell back to fixtures
while appearing to be API-bound. Observed directly — a browser trace showed
**zero** outbound requests from the typed client. After binding `fetch`, the
same trace shows the real calls and real records.

### 2.3 Only `expansion-manager` can use this surface at all

`shared/auth/rbac.py` grants `listing` permissions to `EXPANSION_USER` only.
Of the console's roles, `operatorSecurityHeaders()` maps just
`expansion-manager` onto it; `operations_manager`, `regional_supervisor`,
`marketing_manager` and `auditor` hold **no** `listing` grant — not even VIEW.

Since `ops-lead` is the console's default role, an unguarded queue would have
403'd on its very first read. The queue therefore renders the
permission-limited state and issues no request for such roles. It deliberately
does not render an empty queue, which would imply "no submissions exist" when
the truth is "you may not see them".

## 3. Correctness and honesty properties

- **No fixture fallback on this surface.** Sibling network panels fall back to
  bundled fixtures when the API is down — right for read-only analytics, wrong
  here. An intake queue records real human submissions and governance
  decisions; synthetic rows would present fabricated evidence. An unreachable
  backend renders an explicit error state instead.
- **No direct fetch, no hardcoded auth.** The prior ad-hoc block in
  `ListingRadarPanel` (raw `fetch` + literal `X-Operator-Role`/`X-Subject-Id`/
  `X-Roles`/`X-Tenant-Id` + `actorRoleId: "expansionManager"`) is removed.
  Identity now derives from the console's active role.
- **Real stages, not a fabricated percentage.** The stepper renders the actual
  path taken; a record that was never retrieved shows no RETRIEVING/PARSING.
- **No auto-merge.** `POSSIBLE_MATCH` always requires a human decision.
- **No optimistic UI on decisions.** The dialog stays open and busy until the
  server answers; a failure keeps the typed reason. A decision that only looked
  applied would imply an audit record that does not exist.
- **Reason gates mirror the server**, so operators get guidance rather than a
  422: identity-field corrections and every decision require a reason.
- **Server copy is surfaced verbatim.** `OdpApiError` now parses the response
  body and exposes FastAPI's `detail` (`string | ValidationError[]`), so the
  backend's own zh-TW refusal text is shown rather than an invented message,
  alongside error code, correlation ID and occurred time.
- **Correlation IDs are sent on every write**, so the record's `correlationId`
  is populated as source evidence (previously null — caught by E2E).
- **No new retrieval authorization.** Source policy remains a server decision;
  the client does not pre-judge it. Fixture URLs are inputs to the real
  pipeline and are never presented as live provider evidence.

## 4. Verification (all commands run; output observed)

| Command | Result |
|---|---|
| `npm run typecheck --workspace=@oday-plus/web` | clean |
| `npm run build --workspace=@oday-plus/web` | ✓ Compiled successfully, 24/24 static pages |
| `uv run pytest tests/contract/test_operator_assisted_listing_api.py tests/integration/test_assisted_listing_intake_persistence.py` | **17 passed** |
| `uv run pytest tests/contract tests/integration -q` | all passed (no failures) |
| `npx playwright test tests/e2e/operator-network-assisted-intake.spec.ts` | **14 passed** |
| `git diff --check` | clean |

### E2E coverage (14 tests, real UI → real API)

Five screen labels present · empty state · clean URL → durable READY/NEW ·
exact duplicate caught before retrieval with no second record · possible match
requires human decision and refuses an empty reason · identity-field correction
demands a reason then records before/after · assisted-entry-only source never
fetches the page · unapproved source fails closed to quarantine · retryable
failure exposes code/correlation/next action · revision offers append-version ·
deep link (`#intake/<id>`) reopens after leaving the page · queue counts track
real server state · permission-limited role state · dialogs keyboard operable
with Escape · mobile routes side-by-side compare to a desktop-required state.

### Regression check

`e2e-operator-console.spec.ts` "six remaining tabs" and two
`e2e-network-find-areas-api-binding.spec.ts` tests failed on first run. Each was
checked against a clean `origin/dev` worktree rather than assumed:

- The two Find Areas failures **reproduce identically on `dev`** — pre-existing,
  not caused by this task.
- The "six remaining tabs" failure was **my own test pollution**: that test's
  `network-reviews/reset` is sent without auth headers and its rejection is
  swallowed, so it depends on fresh server state, and my reused server had
  already decided RV-702. On fresh ports it **passes on this branch**.

## 5. Known gaps / follow-ups for the reviewer

1. **Shared-backend E2E interference (pre-existing).** `fullyParallel` runs spec
   files concurrently against one FastAPI process, and several operator specs
   each POST `network-listings/reset`, wiping state under whichever file is
   running. This suite passes standalone (the task's verification command);
   running a broad local selection needs a dedicated API port. Not introduced
   here, but worth a harness fix.
2. **`submit_intake` / `retry_intake` have no service-side role allowlist**,
   unlike correct/decide/promote — they rely solely on the `listing:UPDATE`
   HTTP guard. Consistent today, but asymmetric and easy to regress.
3. **`site_reviewer` cannot decide despite being on the service allowlist**,
   because every intake write is a POST behind `listing:UPDATE` and
   `SITE_REVIEWER` lacks that grant. A cross-layer inconsistency worth a
   product decision; not changed here.
4. **`GET /intake` returns a bare array** and ignores `X-Correlation-Id`, unlike
   the enveloped snapshot. Left as-is to avoid an unrequested contract change.
5. **`promoteIntake` returns `ConvertListingResponse`, not the intake**, so a
   promote requires a refetch. The queue's promote path is not exercised by the
   UI yet — decisions currently route through `decide`.

## 6. Commits

| Commit | Scope |
|---|---|
| `e3e66490` | openapi-client parse fix (unblocks all typechecking) |
| _(anchor)_ | Package 7 intake UI + typed contracts + structured errors |
| _(final)_ | product E2E suite + this evidence |
