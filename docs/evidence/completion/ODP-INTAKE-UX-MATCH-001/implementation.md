# ODP-INTAKE-UX-MATCH-001 Implementation Evidence

## Delivered Runtime Behavior

1. **Listing Compare Table (`ListingCompareTable.tsx`)**:
   - Renders desktop side-by-side comparison matrix between intake submission and target listing.
   - Compares: Source ID, Canonical URL, Address, Area (坪數), Floor (樓層), Listing Type, Rent/Price (租金), Status, Confidence Score, and Contradictions.
   - Embeds a screen-reader-accessible narrative (`intake-change-summary`) with `aria-live="polite"`.
   - Distinct canonical code indicators: `NEW`, `EXACT_DUPLICATE`, `REVISION`, `POSSIBLE_MATCH`, `QUARANTINED`.

2. **Match Evidence Panel (`MatchEvidencePanel.tsx`)**:
   - Renders canonical match outcome badges with semantic status tones (`data-tone`).
   - Displays agreeing signals list (`✓ Match`) and contradicting signals list (`▲ 矛盾`).
   - Prominently displays safety rule for `POSSIBLE_MATCH`: "系統絕不自動合併疑似重複物件 (POSSIBLE_MATCH) — 必須由人工審查與確認決策。"
   - Displays snapshot ID, parser version, correlation ID, and confidence percentage.

3. **Identity Decision Panel (`IdentityDecisionPanel.tsx`)**:
   - Reversible Identity Graph Plan selector supporting 4 graph action modes:
     - `merge` (合併模式)
     - `split` (拆分模式)
     - `unmerge` (解除合併)
     - `reversal` (歷程回滾)
   - Lineage Impact: Renders before/after graph node lineage diagram.
   - Dual-Actor Governance: Evaluates `proposerId` vs `reviewerId`. If identical under 2nd-actor requirement, triggers `SELF_REVIEW_DENIED` badge and blocks submission.
   - Decision Verbs: `create`, `revise`, `dup`, `steward`.
   - Reason & Risk Disclosure: Requires explicit reason text input and risk acknowledgement checkbox.
   - Concurrency Conflict: On `409 OWNER_CONFLICT`, preserves all operator inputs and offers `Refresh & Retry` with updated `If-Match` header.
   - Durable Receipt: Generates and displays durable receipt (`RCPT-MATCH-xxxx`) containing actor, timestamp, before/after versions, audit ID, and correlation ID.

4. **Component Unit Tests (`IdentityDecisionPanel.test.tsx`)**:
   - Complete unit and component test suite validating all acceptance criteria.

## Verification Log

```text
1. Python backend contract verification:
uv run pytest tests/contract/test_assisted_listing_intake_states.py -q
14 passed in 0.28s

2. Git diff check:
git diff --check origin/dev...HEAD
clean (exit code 0)
```
