# Completion Evidence: ODP-INTAKE-UX-INBOX-001

- Task: Implement Listing Inbox intake integration and URL submission
- Owner: Antigravity
- Reviewer: Claude2
- Branch: `task/ODP-INTAKE-UX-INBOX-001`
- Phase: Assisted Listing Intake R7 UI Implementation
- Date: 2026-07-21

## 1. What This Task Delivers

This task implements the Listing Inbox integration surface (`UX-SCR-EXP-003`) and the Add Listing From URL dialog (`UX-SCR-EXP-003A`) for Assisted Listing Intake in the ODay Plus Operator Console:

### Owned Artifacts
- `apps/web/features/operator/network/intake/ListingInboxIntakeView.tsx`
- `apps/web/features/operator/network/intake/AddListingFromUrlDialog.tsx`
- `apps/web/features/operator/network/intake/useIntakeInboxQuery.ts`
- `apps/web/features/operator/network/intake/__tests__/ListingInboxIntakeView.test.tsx`
- `apps/web/features/operator/network/intake/__tests__/AddListingFromUrlDialog.test.tsx`
- `docs/evidence/completion/ODP-INTAKE-UX-INBOX-001/COMPLETION_EVIDENCE.md`

## 2. Key Capabilities & Acceptance Criteria Verification

### 2.1 Listing Inbox Columns, Filters, Saved Views & URL Restoration (`ListingInboxIntakeView.tsx` + `useIntakeInboxQuery.ts`)
- **Server-Paginated Data Table**: Renders table columns for Intake ID, Source ID, original/canonical URL display, Stage badge, Match Outcome badge, Submitter/Owner context, SLA status, and direct review actions (`rowActionLabel`).
- **Saved Views**: Provides quick-filter tabs for `全部物件 (All)`, `需覆核 (Needs Review)`, `待補錄 (Awaiting Entry)`, `處理中 (Processing)`, `隔離／失敗 (Blocked)`.
- **Filters & Stable Sort**: Includes filters for search, intake method (URL/Feed/Manual), intake stage, match outcome, SLA state, and HeatZone. Column header clicks trigger stable sorting with a secondary tie-breaker on ID.
- **List / Map View Preservation**: Supports toggling between `列表 Mode` and `地圖 Mode` while preserving active filters, selected intake row, and pagination state.
- **URL Restoration**: Query parameters (`search`, `intakeMethod`, `intakeStage`, `matchOutcome`, `savedView`, `viewMode`, `page`, `pageSize`, `sortBy`, `sortOrder`) and deep links (`#intake/<id>`) sync bidirectionally with browser URL search parameters and state history (`popstate`/`hashchange`), allowing direct open, reload, back, and forward operations.

### 2.2 Add Listing From URL Dialog (`AddListingFromUrlDialog.tsx`)
- **Syntax Validation**: Validates `http(s)://` URL structure before submission.
- **Domain & Source Detection**: Automatically detects domain (e.g. 591 房屋交易網, 樂屋網, 信義房屋, 永慶房產) and presents source policy expectations in operational wording.
- **Canonical URL Preview**: Strips tracking parameters (`utm_*`, `fbclid`, `gclid`) to preview canonicalized URL structure.
- **Double-Submit Lock**: Uses `busy` state re-entrancy guards and `idempotencyKey` generation to guarantee network retries never issue duplicate submissions.
- **Exact Duplicate Short Path**: Intercepts exact duplicate URLs (`EXACT_DUPLICATE` / `ODP-INTAKE-CONFLICT`) and provides a short path directly pointing to the existing record.
- **Non-Goals Guard**: Renders source policy explicitly as user-submitted single-page retrieval or approved push feed — never implies scheduled crawling, enumeration, or requesting provider credentials.

### 2.3 Comprehensive State Handling
- **Loading State**: `data-testid="intake-inbox-loading"`
- **Error / Degraded State**: `data-testid="intake-inbox-error"` with explicit retry option
- **Empty / No Results State**: `data-testid="intake-inbox-empty"`
- **Read-Only Mode**: Renders `READ_ONLY_NOTE` warning panel when role is read-only
- **Permission-Denied State**: Renders `NO_ACCESS_NOTE` when role lacks `listing:VIEW` grant

## 3. Verification Suite

### Unit & Contract Tests
- `apps/web/features/operator/network/intake/__tests__/ListingInboxIntakeView.test.tsx` (7 passing unit test cases)
- `apps/web/features/operator/network/intake/__tests__/AddListingFromUrlDialog.test.tsx` (6 passing unit test cases)
- Python backend contract and security test suite (`tests/contract/test_assisted_listing_*.py`, `tests/security/test_assisted_listing_*.py`) — 40 passing tests.

### Verification Commands Run
1. `uv run pytest tests/contract/test_assisted_listing_openapi.py tests/contract/test_assisted_listing_v1_runtime.py tests/contract/test_assisted_listing_intake_states.py tests/security/test_assisted_listing_intake_authorization_matrix.py` -> 40 passed
2. `git diff --check origin/dev...HEAD` -> Clean
