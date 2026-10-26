-- ODP-EVIDENCE-LEVEL-ALIGNMENT-001: align evidence_level with ADR-0004 D2
--
-- Three sets of evidence-level values existed with no conversion between any
-- pair. ADR-0004 settles on the ML-05 ladder (L0..L5), which is the only one
-- with a producer and a consumer -- modules/adlift/domain/incrementality.py
-- computes it and the AdLift API emits it. This column held a different
-- vocabulary entirely:
--
--     evidence_level VARCHAR(50) NOT NULL DEFAULT 'medium'
--                                -- low/medium/high/causal_candidate
--
-- Two problems, both addressed here.
--
-- `causal_candidate` is a dead value: it appears only in its own two type
-- declarations (this column's comment and packages/schemas/canonical), and no
-- code has ever produced or tested for it. The concept it names is carried in
-- practice by CAUSAL_MIN_EVIDENCE = L3 in the AdLift domain.
--
-- `DEFAULT 'medium'` is fail-open. An outcome with no assessed evidence was
-- stored as medium-strength evidence rather than as unrated -- the same shape
-- as the promotion-path defect fixed under ODP-LISTING-PROMOTION-FAILOPEN-001,
-- where a missing geocode confidence became 1.0. Nothing currently writes this
-- column, so the fault is latent rather than active; removing the default now
-- means it cannot activate the moment something starts writing.
--
-- What this migration does NOT do: it does not rewrite existing rows.
-- ADR-0004 Open Question 1 records that the row count and distribution have
-- not been read -- all three environments have zero workloads running, so no
-- live query was performed. Backfilling to NULL is the agreed direction, but
-- doing it blind inside a schema migration would be exactly the kind of
-- unverified data change this codebase is trying to stop making. The backfill
-- is left to a separate, evidenced step once the data has been inspected.
--
-- Rerunnable: every statement is conditional or idempotent.

ALTER TABLE operations.intervention_outcomes
    ALTER COLUMN evidence_level DROP DEFAULT;

ALTER TABLE operations.intervention_outcomes
    ALTER COLUMN evidence_level DROP NOT NULL;

COMMENT ON COLUMN operations.intervention_outcomes.evidence_level IS
    'ADR-0004: ML-05 ladder L0..L5. NULL means unrated -- there is no default. '
    'When evidence cannot be assessed at all, evidence_level stays NULL and the '
    'reason is carried by insufficiency_reason_code (ADR-0004 D3, landing '
    'separately); it is not a tier of this ladder.';

-- Rows written before ADR-0004 carry the removed vocabulary. They are left in
-- place deliberately: see the note above. Any row still holding a legacy value
-- is unrated in ADR-0004 terms, and the follow-up backfill will say so
-- explicitly rather than this migration assuming it.
