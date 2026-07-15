# ODP-OC-R5-011 — Completion Evidence

- Task: Remediate and complete R5 assisted listing product slice
- Owner: Claude · Reviewer: Codex
- Branch: `task/ODP-OC-R5-011`
- Date: 2026-07-15
- Status at handoff: review findings remediated, **not self-finalized** —
  awaiting independent re-review by Codex.
- Review round 2 (2026-07-15): all four findings from Codex's rejection are
  addressed in §7, with the exact commit and command for each.

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

Re-run after the round-2 remediation, so these are current — not round-1 numbers.
The E2E runs used fresh servers on unused ports with reuse disabled; see §7.6 for
why that matters.

| Command | Result |
|---|---|
| `npm run typecheck --workspace=@oday-plus/web` | clean |
| `npm run build --workspace=@oday-plus/web` | ✓ Compiled successfully, 24/24 static pages |
| `uv run pytest tests/contract/test_operator_assisted_listing_api.py tests/integration/test_assisted_listing_intake_persistence.py` | **29 passed** (was 17 in round 1; +12 risk-disclosure and durable-contract tests) |
| `uv run pytest tests/contract tests/integration -q` | **483 passed** |
| `npx playwright test tests/e2e/operator-network-assisted-intake.spec.ts` | **14 passed** |
| `git diff --check origin/dev..HEAD` | clean (see §7.3 — the earlier bare `git diff --check` claim was wrong) |

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

1. ~~**Shared-backend E2E interference.**~~ **FIXED in `e80fc3c9`** — see §7.4.
   It is no longer a documented workaround: the suite is deterministic as
   configured, verified by re-running the exact selection that used to fail.
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

Round 1 (merged into `dev` as PR #297, merge commit `222c9954`):

| Commit | Scope |
|---|---|
| `6bc0ccff` | backend implementation |
| `e3e66490` | openapi-client parse fix (unblocks all typechecking) |
| `0867edea` | Package 7 intake UI + typed contracts |
| `393238bb` | E2E expansion preserved before worker timeout |
| `faa3102c` | product E2E suite + first evidence |

Round 2 — review remediation (this branch, on top of `dev`):

| Commit | Scope |
|---|---|
| `41d4c7ad` | typed durable intake repository contract (P0-1) |
| `ebbaed6b` | caller-provided risk disclosure contract (P0-2) |
| `5407c5b3` | trailing-whitespace fix on branch-added lines (P1-1) |
| `e80fc3c9` | deterministic product E2E in the configured suite (P1-2) |

Note: PR #297 was merged into `dev` while the task was in review, so round 1 is
already on `dev` and this branch carries only the remediation. A new PR is
needed; #297 cannot be reused.

## 7. Review round 2 — how each finding was addressed

### 7.1 P0 — public durable repository contract (`41d4c7ad`)

**Finding:** `NetworkListingService` still received the generic `document_store`
and used it directly for intake, idempotency, and metadata collections.

**Fix:** a typed public contract, `AssistedIntakeRepository` (Protocol) plus
`IntakeIdempotencyRecord`, in the opsboard application layer, with an in-memory
default and `DurableAssistedIntakeRepository` in
`shared/infrastructure/persistence/operator_network_listings.py` (mirroring the
existing `DurableIngestionRunStore` pattern). The service holds no
`document_store` reference at all:

```
$ grep -c document_store modules/opsboard/application/network_listings.py
0
```

`reset()` also used to delete `listing.*` collections behind the listing
repository's back; `DurableListingRepository` / `InMemoryListingRepository` each
gained `clear()` so the owning repository does it.

**Restart-safety proven through the contract**, not around it —
`test_durable_intake_repository_round_trips_through_public_contract` writes via
the contract, closes the SQLite engine, reopens it, and reads back intakes,
idempotency records, and metadata; `clear()` is asserted too.
`test_service_replays_idempotent_write_through_repository_after_restart` proves
an idempotent replay after restart returns the cached response and creates no
second intake.

```
$ uv run pytest tests/integration/test_assisted_listing_intake_persistence.py -q
9 passed
```

### 7.2 P0 — caller-provided risk summary + acknowledgement (`ebbaed6b`)

**Finding:** DTOs/client/dialog sent only `reason`; the server invented
`riskSummary`.

That audit field was evidence of nothing: it recorded consent to text the
operator never saw. The disclosure is now caller-owned end to end.

- **Service:** `_require_acknowledged_risk` gates `correct` / `decide` /
  `promote` / `merge` — the acceptance list is "correction merge split decision
  and promotion". Missing summary or `riskAcknowledged=false` → 422.
- **Audit:** `metadata.riskSummary` is the caller's verbatim text, plus
  `riskAcknowledged`. The server-derived text moved to a separate
  `effectSummary` key so it can never be mistaken for acknowledged text.
  `archive` is not gated (not in the acceptance list) but its invented summary
  was likewise renamed to `effectSummary`, so `riskSummary` means exactly one
  thing everywhere.
- **Client:** `RiskDisclosure` is **required**, not optional, on the typed
  payloads — an omitted disclosure is a compile error, not a runtime 422. This
  is what located all four call sites.
- **UI:** the decision, field-fix, and assisted-entry surfaces each render a
  `風險摘要 RISK SUMMARY` box and an acknowledgement checkbox, and send **the
  same string they rendered** rather than re-deriving it at submit time, so the
  audit stores what was actually read. Confirm is blocked until it is ticked.

Tests (negative and positive, per surface):

```
$ uv run pytest tests/contract/test_operator_assisted_listing_api.py -q
20 passed
```

covering, for each of correct/decide/promote: 422 on missing summary, 422 on
summary-supplied-but-unacknowledged, merge 422 without disclosure, and positive
tests asserting the caller's exact text lands in the audit record with
`riskAcknowledged: true` (and, for decide, that `effectSummary` is kept
separately).

Full backend suite:

```
$ uv run pytest tests/contract tests/integration -q
483 passed
```

### 7.3 P1 — branch-range whitespace check (`5407c5b3`)

**Finding:** `git diff --check origin/dev..HEAD` failed on 12 trailing-whitespace
lines although the evidence claimed clean.

The claim was wrong, and the reason matters: the bare `git diff --check` that was
run only compares the working tree to the index, which is trivially clean on a
committed branch. It never checked the branch. All 12 were blank lines this
branch added; no line inherited from `dev` was touched.

```
$ git diff --check origin/dev..HEAD
$ echo $?
0
```

### 7.4 P1 — deterministic product E2E in the configured suite (`e80fc3c9`)

**Finding:** broad related E2E exposed shared reset interference; make it
deterministic in the configured suite, not only standalone.

**Root cause:** `playwright.config.ts` sets `fullyParallel`, so spec *files* run
concurrently against one FastAPI process, and three files
(`operator-network-assisted-intake`, `operator-network-listings`,
`e2e-operator-console`) each POST `.../network-listings/reset`, wiping the
singleton mid-test. Playwright's `serial` mode only orders tests *within* a file,
and the operator service is pinned to one tenant
(`dependencies.OPERATOR_TENANT_ID`), so it cannot be isolated per file by tenant
or session.

**Fix:** `tests/e2e/_operatorBackendLock.ts` — an atomic `mkdir`-based
cross-process mutex over the shared backend. The two listing spec files hold it
for their whole run; `e2e-operator-console` takes it only for the one test that
resets listings. Stale locks from a killed worker are reclaimed, so a crash
cannot wedge the suite; everything else still runs in parallel. Acquiring also
raises the calling hook's timeout, since the default 30s `beforeAll` budget is
shorter than a legitimate wait and would defeat the lock.

Exact command and result (fresh servers, unused ports, reuse disabled):

```
$ ODP_API_PORT=8131 OPSBOARD_PORT=3131   ODP_API_BASE_URL=http://127.0.0.1:8131 ODP_PLAYWRIGHT_REUSE_EXISTING=0   npx playwright test tests/e2e/operator-network-assisted-intake.spec.ts     tests/e2e/operator-network-listings.spec.ts
17 passed (2.7m)
```

The same two-file selection failed **2/17** before this change (this task's
"empty state … durable READY / NEW" wiped mid-test, plus the R4 map test).
All three reset-owning files together:

```
$ ODP_API_PORT=8132 OPSBOARD_PORT=3132   ODP_API_BASE_URL=http://127.0.0.1:8132 ODP_PLAYWRIGHT_REUSE_EXISTING=0   npx playwright test tests/e2e/operator-network-assisted-intake.spec.ts     tests/e2e/operator-network-listings.spec.ts tests/e2e/e2e-operator-console.spec.ts
21 passed (3.0m)
```

### 7.5 A defect this work found: the UI merge path

The typed client's required `RiskDisclosure` did **not** catch
`NetworkFindAreasWorkspace`'s merge, because it posts through an untyped raw
`fetch`. It would have 422'd against the new merge gate. It now discloses the
risk and merges only on acknowledgement.

This is a crude `window.confirm` on an R4 surface this task does not own. The
alternative was hardcoding `riskAcknowledged: true` to keep the test green,
which would record consent nobody gave — precisely the defect being remediated
here. A real, minimal disclosure was preferred over a polished fake one, and it
is flagged for the reviewer as a deliberate call.

### 7.6 Honesty note: an earlier local E2E run was invalid

`playwright.config.ts` reuses an existing server when not in CI. A `uvicorn`
from an earlier session (2h32m old, started **before** these changes) was still
listening on `:8099`, so an initial local run executed against **stale backend
code** — the merge test passed for the wrong reason, which is what exposed the
gap in §7.5. That process was killed and every result quoted above is from fresh
servers on unused ports with `ODP_PLAYWRIGHT_REUSE_EXISTING=0`. Any earlier
local pass on this branch should be treated as unverified.

## 8. Full-suite E2E status (measured against a clean `dev` baseline)

The full suite is **not** green on this branch — and it is not green on `dev`
either. Both were run under identical conditions (fresh servers, unused ports,
reuse disabled), and `dev` here means `origin/dev` @ `222c9954`, which already
contains round 1:

| Run | Result |
|---|---|
| this branch, `npx playwright test` | 11 failed, 97 passed |
| `origin/dev` @ `222c9954`, same command | 10 failed, 94 passed |

Nine failures are identical on both. The differences:

- **`dev` fails `operator-network-assisted-intake` "empty state … durable
  READY / NEW"** — the interference bug of §7.4. **This branch does not.**
- **This branch shows `e2e-map` and `e2e-map-tooltip-evidence`** in the failing
  set. Checked rather than assumed: `e2e-map.spec.ts:7` "renders nonblank
  MapLibre canvas" **fails identically on the clean `dev` baseline** when run in
  the same isolated selection (1 failed / 2 passed on both), so it is
  pre-existing. `e2e-map-tooltip-evidence` **passes in isolation on both** and
  only fails under full-suite load — load-sensitive canvas flake present on both.

No failure in the suite is introduced by this task, and one real failure is
removed. The remaining pre-existing failures (`e2e-exp`, `e2e-learning-audit`,
`e2e-intervention-price-ad`, `e2e-map-a11y`, `e2e-network-find-areas-api-binding`
×2, `operator-shell-today`, `opsboard-shell`, `product-e2e-env`) are outside this
task's scope and are reported here rather than left implied.
