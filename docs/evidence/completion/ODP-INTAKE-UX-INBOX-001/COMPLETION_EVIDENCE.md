# Completion Evidence: ODP-INTAKE-UX-INBOX-001

- Task: Implement Listing Inbox intake integration and URL submission
- Owner: Codex2
- Reviewer: Codex
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
- **Paginated Data Table**: Renders the authoritative server response in pages with Intake ID, Source ID, canonical URL, Stage, Match Outcome, Submitter/Owner, and direct review actions. Sorting uses persisted audit/capture time and ID as a deterministic tie-breaker. The current operator API client returns an array, so pagination is performed over that authoritative response; this evidence does not claim a cursor API that the runtime client does not expose.
- **Saved Views**: Provides quick-filter tabs for `全部物件 (All)`, `需覆核 (Needs Review)`, `待補錄 (Awaiting Entry)`, `處理中 (Processing)`, `隔離／失敗 (Blocked)`.
- **Filters & Stable Sort**: Includes visible filters for search, intake method, intake stage, and match outcome; saved state also supports HeatZone. Column header clicks trigger stable sorting with a secondary tie-breaker on ID.
- **List / Map View Preservation**: Supports toggling between `列表 Mode` and `地圖 Mode` while preserving active filters, selected intake row, and pagination state.
- **URL Restoration**: Query parameters (`search`, `intakeMethod`, `intakeStage`, `matchOutcome`, `savedView`, `viewMode`, `page`, `pageSize`, `sortBy`, `sortOrder`) sync with browser history. `popstate` restores defaults plus the target query, and the existing section router retains `#intake/<id>` direct-open/reload handling.

### 2.2 Add Listing From URL Dialog (`AddListingFromUrlDialog.tsx`)
- **Syntax Validation**: Validates `http(s)://` URL structure before submission.
- **Domain & Source Detection**: Automatically detects domain (e.g. 591 房屋交易網, 樂屋網, 信義房屋, 永慶房產) and presents source policy expectations in operational wording.
- **Canonical URL Preview**: Strips tracking parameters (`utm_*`, `fbclid`, `gclid`) to preview canonicalized URL structure.
- **Double-Submit Lock**: Uses a synchronous dialog-local lock before awaiting the parent plus the section's stable server idempotency key. A failed/lost response keeps that key for replay; success clears it.
- **Exact Duplicate Short Path**: Intercepts `ODP-INTAKE-CONFLICT`, extracts an existing intake ID when returned in the error summary, and provides an explicit button to open that record.
- **Durable Receipt**: Successful submission is merged from the authoritative response, opens the persisted detail route, renders the receipt/toast, and then refreshes the inbox.
- **Non-Goals Guard**: Renders source policy explicitly as user-submitted single-page retrieval or approved push feed — never implies scheduled crawling, enumeration, or requesting provider credentials.

### 2.3 Comprehensive State Handling
- **Loading State**: `data-testid="intake-inbox-loading"`
- **Error / Degraded State**: `data-testid="intake-inbox-error"` with explicit retry option and no fixture fallback
- **Empty / No Results State**: `data-testid="intake-inbox-empty"` (covers both empty source and filtered no-results)
- **Read-Only Mode**: Supported by the permission helper if a future role receives view without writes; current role mapping has no such role
- **Permission-Denied State**: Renders `NO_ACCESS_NOTE` when role lacks `listing:VIEW` grant

## 3. Verification Suite

### Unit & Contract Tests
- `apps/web/features/operator/network/intake/__tests__/ListingInboxIntakeView.test.tsx` (8 passing cases, including history restoration)
- `apps/web/features/operator/network/intake/__tests__/AddListingFromUrlDialog.test.tsx` (7 passing cases, including rapid double-submit locking and duplicate short path)

### Verification Commands Run
1. `npm run typecheck --workspace=@oday-plus/web` -> passed
2. `npm test --workspace=@oday-plus/web -- ListingInboxIntakeView AddListingFromUrlDialog` -> 15 passed
3. `git diff --check origin/dev...HEAD` -> clean
