# Completion Evidence: ODP-INTAKE-UX-INBOX-001

- Task: Implement Listing Inbox intake integration and URL submission
- Owner: Codex2
- Reviewer: Antigravity4
- Branch: `task/ODP-INTAKE-UX-INBOX-001`
- Phase: Assisted Listing Intake R7 UI Implementation
- Date: 2026-07-21

## 1. What This Task Delivers

This task implements the Listing Inbox integration surface (`UX-SCR-EXP-003`) and the Add Listing From URL dialog (`UX-SCR-EXP-003A`) for Assisted Listing Intake in the ODay Plus Operator Console:

### Owned Artifacts
- `apps/web/features/operator/network/intake/ListingInboxIntakeView.tsx`
- `apps/web/features/operator/network/intake/AddListingFromUrlDialog.tsx`
- `apps/web/features/operator/network/intake/AssistedIntakeSection.tsx`
- `apps/web/features/operator/network/intake/useIntakeInboxQuery.ts`
- `apps/web/features/operator/network/intake/__tests__/ListingInboxIntakeView.test.tsx`
- `apps/web/features/operator/network/intake/__tests__/AddListingFromUrlDialog.test.tsx`
- `docs/evidence/completion/ODP-INTAKE-UX-INBOX-001/COMPLETION_EVIDENCE.md`
- `apps/web/package.json`, `apps/web/vitest.config.ts`, and root `package-lock.json` (focused test runner)

## 2. Key Capabilities & Acceptance Criteria Verification

### 2.1 Listing Inbox Columns, Filters, Saved Views & URL Restoration (`ListingInboxIntakeView.tsx` + `useIntakeInboxQuery.ts`)
- **Runtime Integration**: `AssistedIntakeSection` now renders this inbox while retaining the existing generated-client reads/writes, durable idempotency-key reuse, detail receipt, and refresh behavior.
- **Server-paginated Data Table**: The typed client sends page/pageSize, filters, saved view, and sort to the operator endpoint. The server applies authorization and masking first, then filters, stable sorts with ID tie-breaking, and returns only the requested page with total and queue counts. The browser no longer filters, sorts, or slices a fully fetched array.
- **Canonical query values**: New URL submissions persist `intakeMethod=URL`; filters use the canonical `URL` / `MANUAL` / `CSV` / `APPROVED_FEED` values. Processing counts and saved views use `SUBMITTED`, `CHECKING_IDENTITY`, `CHECKING_SOURCE_POLICY`, `RETRIEVING`, `PARSING`, and `MATCHING` rather than UI-only stage aliases.
- **Saved Views**: Provides quick-filter tabs for `全部物件 (All)`, `需覆核 (Needs Review)`, `待補錄 (Awaiting Entry)`, `處理中 (Processing)`, `隔離／失敗 (Blocked)`.
- **Filters & Stable Sort**: Includes visible filters for search, intake method, intake stage, and match outcome; saved state also supports HeatZone. Column header clicks trigger stable sorting with a secondary tie-breaker on ID.
- **List / Map View Preservation**: The modes are mutually exclusive. Map mode renders the current server page as HeatZone markers (including explicit `待定位`) and does not leave the table visible; filters and pagination remain preserved.
- **Operational dimensions and actions**: Rows show source, submitter, assignment/owner, SLA availability, observed/updated time, evidence/masking readiness, quarantine/retryability, and contextual claim/review/retry/request-correction wording. Retryable failures invoke a direct retry mutation; review and correction flow into the governed detail dialogs.
- **URL Restoration**: Query parameters (`search`, `intakeMethod`, `intakeStage`, `matchOutcome`, `savedView`, `viewMode`, `page`, `pageSize`, `sortBy`, `sortOrder`) sync with browser history. `popstate` restores defaults plus the target query, and the existing section router retains `#intake/<id>` direct-open/reload handling.

### 2.2 Add Listing From URL Dialog (`AddListingFromUrlDialog.tsx`)
- **Syntax Validation**: Validates `http(s)://` URL structure before submission.
- **Server-owned source policy**: The browser validates URL syntax but does not maintain a provider allowlist or claim approval/retrieval permission. It explicitly says the server will decide policy after submission.
- **Canonical URL Preview**: Strips tracking parameters (`utm_*`, `fbclid`, `gclid`) to preview canonicalized URL structure.
- **Double-Submit Lock**: Uses a synchronous dialog-local lock before awaiting the parent plus the section's stable server idempotency key. A failed/lost response keeps that key for replay; success clears it.
- **Exact Duplicate Short Path**: Intercepts `ODP-INTAKE-CONFLICT`, extracts an existing intake ID when returned in the error summary, and provides an explicit button to open that record.
- **Durable Receipt**: Successful submission is merged from the authoritative response, opens the persisted detail route, renders the receipt/toast, and then refreshes the inbox.
- **Non-Goals Guard**: Renders source policy explicitly as user-submitted single-page retrieval or approved push feed — never implies scheduled crawling, enumeration, or requesting provider credentials.

### 2.3 Comprehensive State Handling
- **Loading State**: `data-testid="intake-inbox-loading"`
- **Error / Degraded State**: transport error uses `intake-inbox-error`; page metadata separately renders `intake-evidence-partial` for missing snapshots and `intake-evidence-degraded` for failed processing, without fixture fallback
- **Empty / No Results State**: `data-testid="intake-inbox-empty"` (covers both empty source and filtered no-results)
- **Read-Only Mode**: Supported by the permission helper if a future role receives view without writes; current role mapping has no such role
- **Permission-Denied State**: Renders `NO_ACCESS_NOTE` when role lacks `listing:VIEW` grant

## 3. Verification Suite

### Unit & Contract Tests
- `apps/web/features/operator/network/intake/__tests__/ListingInboxIntakeView.test.tsx` (9 passing cases, including history restoration and degraded direct retry)
- `apps/web/features/operator/network/intake/__tests__/AddListingFromUrlDialog.test.tsx` (7 passing cases, including rapid double-submit locking and duplicate short path)

### Verification Commands Run
1. `npm run typecheck --workspace=@oday-plus/web` -> passed
2. `npm test --workspace=@oday-plus/web -- ListingInboxIntakeView AddListingFromUrlDialog` -> 16 passed
3. `uv run pytest tests/contract/test_operator_assisted_listing_api.py -q` -> 22 passed
4. `uv run pytest tests/security/test_assisted_listing_intake_authorization_matrix.py -q` -> 11 passed
5. `git diff --check` -> clean

## 4. Finalization

- Review disposition: approved by Antigravity4 on 2026-07-22.
- Approved scope: server-owned filtering, pagination, stable sorting and counts; mutually exclusive list/map modes; operational actions and route transitions; server-owned source-policy authority.
- Closeout verification at `0ad3ef605d4d7d23a3586ecb9785b0efc2347423`:
  - `npm test --workspace=@oday-plus/web -- ListingInboxIntakeView AddListingFromUrlDialog` -> 16 passed.
  - `git diff --check origin/dev...HEAD` and `git diff --check` -> clean.
  - `npm run typecheck --workspace=@oday-plus/web` -> blocked by pre-existing sister-lane errors in `IdentityDecisionPanel.test.tsx` (stale `MatchSignalDto`, `IntakeFieldCell`, `IntakeAuditEvent`, and `MatchResultDto` fixtures plus missing Jest globals); no errors point to this task's owned files.
