# ODP-INTAKE-UX-DETAIL-001 Handoff & Evidence Summary

- Task ID: ODP-INTAKE-UX-DETAIL-001
- Title: Implement durable processing detail, timeline, evidence, errors, and receipts
- Owner: Antigravity3
- Reviewer: Antigravity5
- Phase: Assisted Listing Intake R7 UI Implementation
- Target Branch: task/ODP-INTAKE-UX-DETAIL-001

## Delivered Components & Surfaces

1. `apps/web/features/operator/network/intake/IntakeProcessingDetail.tsx`:
   - High-level container component rendering the durable processing detail view.
   - Header with Intake ID, stage/outcome/policy/SLA badges, deep link (`#intake/[id]`), refresh and action toolbar.
   - Sub-nav tabs for Timeline, Evidence, Receipts, and Error Recovery.
   - Purpose binding and data masking controls for sensitive fields.

2. `apps/web/features/operator/network/intake/IntakeStageTimeline.tsx`:
   - Stepper rendering exact intake stages without fabricated percentages.
   - Stage status tracking (done, current, upcoming, failed, retrying).
   - Detailed job execution & DLQ status panel with Replay DLQ action.
   - SLA and assignment state tracking.
   - History transition audit nodes log.

3. `apps/web/features/operator/network/intake/EvidencePanel.tsx`:
   - Original vs Canonical URL comparison with transclusion diff highlight.
   - Snapshot & Parser evidence (Snapshot ID, Parser Run ID, Correlation ID, ETag header).
   - Model match recommendation vs human operator decision comparison panel.
   - Parsed fields table with field confidence, classification badges, and field fix affordance.
   - Audit event references.

4. `apps/web/features/operator/network/intake/DurableReceiptPanel.tsx`:
   - Durable receipts for Submission, Assignment, Decision, SLA, and Field Corrections.
   - Cryptographic payload checksum (SHA-256) and verification status badge.
   - Copy JSON payload & Export JSON file download affordance.
   - Traceability links to canonical entities.

5. `apps/web/features/operator/network/intake/IntakeErrorRecovery.tsx`:
   - Displays exact declared error codes (e.g. `ERR_PARSE_MALFORMED_HTML`, `ERR_GEOCODE_FAILED`, `ERR_DEDUP_CONFLICT`, `ERR_HARD_RULE_VIOLATION`, `VALIDATION_FAILED`, etc.).
   - Correlation ID, timestamp, current version, retryability status badge.
   - Preserved input drawer showcasing failure point payload with purpose binding masking on secret credentials.
   - Recovery action toolbar (Retry, Replay DLQ, Correct Input, Risk-acknowledged Override & Proceed, Cancel).

6. `apps/web/features/operator/network/intake/__tests__/IntakeProcessingDetail.test.tsx`:
   - Comprehensive test suite validating timeline stepper, evidence comparison, durable receipts, error recovery, and data masking.
