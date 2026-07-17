---
doc_id: ODP-SD-INTAKE-REVIEW-003
title: ODay Plus Assisted Listing Intake System Design Response Review
review_version: 0.3.0
response_version: 0.2.1
status: under-independent-review
decision: PENDING
owner: Independent Architecture Review
reviewers: Product / Security-Privacy / Data / Platform-SRE / Expansion Engineering / QA
reviews: ODP-SD-INTAKE-001
responds_to: ODP-SD-INTAKE-ALIGN-001
reviewed_commit: d75fe8ab13d69f039c2cabe237d2401face8418b
review_branch: review/assisted-listing-intake-v021-d75fe8ab
review_base_branch: agent/assisted-listing-intake-system-design
base_branch: dev
base_commit: e2ef2156375c733747d968346fd85ca54cc751c1
supersedes_review: ODP-SD-INTAKE-REVIEW-002
updated_at: 2026-07-17
---

# ODay Plus Assisted Listing Intake System Design Response Review

## 1. Exact target and merge safety

This review is limited to response commit:

```text
d75fe8ab13d69f039c2cabe237d2401face8418b
```

The review branch was created directly from that commit. The review commit must
remain a descendant of `reviewed_commit`. The review pull request targets the
response branch, not `dev`; therefore the review artifact cannot be merged into
`dev` without the artifacts it reviews.

The review must stop with `STALE_REVIEW_TARGET` if the response head changes,
if ancestry is broken, or if the review PR is retargeted directly to `dev`.

Previous review status:

- `ODP-SD-INTAKE-REVIEW-001`: historical only; reviewed `ffe14c77...`.
- `ODP-SD-INTAKE-REVIEW-002`: invalid lineage; PR #320 closed without merge.
- Review PR #321: stale after the final version-label correction; closed without merge.

## 2. Exact-head validation evidence

| Gate | Result | Run / artifact |
|---|---|---|
| Assisted Listing Intake Design Contract Gate | PASS | GitHub Actions run `29585217973` |
| Repository CI | PASS | GitHub Actions run `29585217903` |
| Product E2E release gate | PASS | repository CI |
| Effective OpenAPI overlay application | PASS | committed overlay builder |
| OpenAPI 3.1 structural validation | PASS | `openapi-spec-validator` |
| Redocly effective-bundle lint | PASS, zero errors and zero warnings | uploaded validation artifact |
| PostgreSQL 16 schema stack application | PASS | baseline plus patches `0002`–`0004` |
| FORCE RLS / policy / tenant-lineage catalog validation | PASS | `validate_assisted_listing_intake_schema.sql` |

Automated validation is necessary pre-review evidence; it is not an author-issued
approval.

## 3. P0 finding disposition for independent verification

### SDR-003 — Persistence, tenant isolation, and lineage

**Pre-review disposition: corrected.**

Binding artifacts:

- `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql`
- `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0002_CONSISTENCY_PATCH.sql`
- `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0003_PROMOTION_STATE_PATCH.sql`
- `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0004_TENANT_RLS_LINEAGE_PATCH.sql`
- `scripts/validate_assisted_listing_intake_schema.sql`

Implemented corrections:

1. Every tenant-bearing contract table uses `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`.
2. Every tenant-bearing table has a fail-closed `tenant_isolation` policy using request-scoped `app.tenant_id` in both `USING` and `WITH CHECK`.
3. Tenant-qualified composite foreign keys cover current pointers, state-transition evidence, source snapshots, parser runs, identity edges, redirects, decision/correction lineage, promotion, candidate and audit evidence.
4. PostgreSQL catalog validation fails on missing FORCE RLS, missing policy, unqualified tenant relationships, or missing lineage constraints.
5. All schema artifacts are executed against PostgreSQL 16 before catalog checks.
6. Patch `0004` is explicitly aligned to response version `0.2.1`.

Independent reviewer must confirm the table inventory, intentional polymorphic
references, service-role bypass controls, and the cutover requirement to validate
all `NOT VALID` constraints.

### SDR-004 — Effective OpenAPI contract

**Pre-review disposition: corrected.**

Normative bundle order:

1. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml`
2. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_0_1_PRELUDE_OVERLAY.yaml`
3. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_OVERLAY.yaml`
4. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_1_CONSISTENCY_OVERLAY.yaml`
5. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_2_LINT_OVERLAY.yaml`
6. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_3_REDOCLY_OVERLAY.yaml`

Implemented corrections:

1. CI builds the effective document by applying every overlay in order.
2. The effective document passes `openapi-spec-validator`.
3. Redocly runs on the effective document and treats warnings as blocking; the exact target has zero errors and zero warnings.
4. Every effective Response Object has `description`.
5. `ApiError` requires and defines `occurred_at` and `next_action`.
6. Local references, operation IDs, component responses and error shape receive structural validation.
7. The effective bundle and diagnostic files are retained as workflow artifacts.
8. Superseded pre-review promotion response contracts are removed from the effective bundle.

Independent reviewer must inspect the effective artifact rather than linting only
the base source fragment, and must confirm client generation and contract tests
consume the effective bundle.

### Validation strength

**Pre-review disposition: corrected.**

The Design Contract Gate now performs executable, structural checks rather than
string-presence evidence:

- composes and validates the effective OpenAPI;
- enforces zero-warning Redocly lint;
- executes the full schema on PostgreSQL 16;
- validates RLS policies, FORCE RLS and tenant-qualified lineage from PostgreSQL catalogs;
- uploads all outputs even when a structural step fails;
- fails closed unless all structural gates pass.

## 4. Independent decision rules

The independent reviewer must replace `decision: PENDING` with exactly one of:

- `APPROVED`
- `APPROVED_WITH_CONDITIONS`
- `CHANGES_REQUESTED`

The final decision must record reviewer identity, review timestamp, exact
`reviewed_commit`, ancestry confirmation, SDR-003 disposition, SDR-004
disposition, validation evidence, and remaining fail-closed release conditions.

## 5. Current disposition

```text
PENDING_INDEPENDENT_REVIEW
```

The P0 corrections and exact-head structural gates are complete. No approval is
asserted by the System Design author.
