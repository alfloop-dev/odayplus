-- ODay Plus Assisted Listing Intake promotion state consistency patch
-- Contract patch: 0003 / effective design version 0.2.1
-- Apply after schema baseline and 0002 consistency patch.

BEGIN;

ALTER TABLE expansion.promotion_decisions
  DROP CONSTRAINT IF EXISTS promotion_decisions_status_check;

ALTER TABLE expansion.promotion_decisions
  ADD CONSTRAINT promotion_decisions_status_check
  CHECK (
    status IN (
      'REQUESTED',
      'VALIDATING',
      'PENDING_REVIEW',
      'REJECTED',
      'APPROVED',
      'CANDIDATE_CREATING',
      'CANDIDATE_CREATED',
      'SCORE_QUEUED',
      'COMPLETED',
      'FAILED',
      'SCORE_FAILED'
    )
  ) NOT VALID;

-- Validate only after current rows have been reconciled. A validation failure is
-- a BLOCKING STATE_MAPPING_CONFLICT finding; it must not be silently rewritten.

COMMIT;
