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
reviewed_commit: 0a56922d68860b56a0554fa4e1dac9875409da41
review_branch: review/assisted-listing-intake-v021-0a56922d
review_base_branch: agent/assisted-listing-intake-system-design
base_branch: dev
base_commit: e2ef2156375c733747d968346fd85ca54cc751c1
supersedes_review: ODP-SD-INTAKE-REVIEW-002
updated_at: 2026-07-17
---

# ODay Plus Assisted Listing Intake System Design Response Review

## 1. Review target and lineage gate

This review is limited to response commit:

```text
0a56922d68860b56a0554fa4e1dac9875409da41
```

The review branch was created directly from that commit. The review commit must
remain a descendant of `reviewed_commit`. This review PR targets the response
branch, not `dev`, so the review artifact cannot merge independently of the
artifacts it reviews.

The review must stop with `STALE_REVIEW_TARGET` if any of the following is true:

- the response branch head changes after review begins;
- `reviewed_commit` is not an ancestor of the review commit;
- the review PR is retargeted directly to `dev` before the response artifacts are integrated;
- the committed validation artifacts do not correspond to `reviewed_commit`.

`ODP-SD-INTAKE-REVIEW-001` is historical evidence for `ffe14c77...` only.
`ODP-SD-INTAKE-REVIEW-002` is invalid because its commit was not a descendant of
its claimed review target and its PR could merge independently of the reviewed
artifacts.

## 2. Automated pre-review evidence

The following checks completed successfully on the exact reviewed commit:

| Gate | Result | Evidence |
|---|---|---|
| Assisted Listing Intake Design Contract Gate | PASS | GitHub Actions run `29584609361` |
| Repository CI | PASS | GitHub Actions run `29584609398` |
| Commit-bound cross-contract validator | PASS | `scripts/validate_assisted_listing_intake_design.py` |
| Effective OpenAPI overlay build | PASS | `scripts/build_validate_assisted_listing_intake_openapi.py` |
| OpenAPI 3.1 structural validation | PASS | `openapi-spec-validator` on the effective bundle |
| Redocly lint | PASS, zero errors and zero warnings | effective OpenAPI artifact |
| PostgreSQL schema application | PASS | PostgreSQL 16, schema baseline plus patches `0002`-`0004` |
| Tenant RLS and lineage catalog validation | PASS | `scripts/validate_assisted_listing_intake_schema.sql` |
| Product tests, API drift, security, Node checks | PASS | repository CI |
| Product E2E release gate | PASS | repository CI |

Automated PASS is necessary but does not constitute the independent human review
decision.

## 3. Findings requiring re-review

### SDR-003 — Persistence, tenant isolation, and lineage

**Pre-review status: corrected; independent verification required.**

Binding artifacts:

- `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql`
- `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0002_CONSISTENCY_PATCH.sql`
- `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0003_PROMOTION_STATE_PATCH.sql`
- `docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA_0004_TENANT_RLS_LINEAGE_PATCH.sql`
- `scripts/validate_assisted_listing_intake_schema.sql`

Corrections now implemented:

1. Every tenant-bearing contract table is configured with both `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`.
2. Every tenant-bearing table receives a fail-closed `tenant_isolation` policy using request-scoped `app.tenant_id` in both `USING` and `WITH CHECK`.
3. Tenant-qualified composite foreign keys cover business ownership and lineage references, including current revision/observation pointers, source identity supersession, property redirects, decision lineage, correction lineage, candidate promotion, transition evidence, jobs, snapshots, and parser runs.
4. PostgreSQL catalog validation fails if a tenant table lacks FORCE RLS/policy, if a tenant-scoped foreign-key relationship lacks a tenant-qualified counterpart, or if a required lineage constraint is absent.
5. The complete schema stack is applied to PostgreSQL 16 in CI before catalog validation.

Independent reviewer checks:

- Confirm all business tables carrying `tenant_id` are present in the enforced RLS table list.
- Confirm application/service-role bypass requirements are explicitly controlled outside ordinary product connections.
- Confirm every intentional polymorphic reference is documented and covered by a separate integrity verifier.
- Confirm `NOT VALID` constraints remain a rollout mechanism only and are required to be validated before authoritative cutover.

### SDR-004 — Versioned OpenAPI contract

**Pre-review status: corrected; independent verification required.**

Binding effective bundle order:

1. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml`
2. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_0_1_PRELUDE_OVERLAY.yaml`
3. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_OVERLAY.yaml`
4. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_1_CONSISTENCY_OVERLAY.yaml`
5. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_2_LINT_OVERLAY.yaml`
6. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1_1_3_REDOCLY_OVERLAY.yaml`

Corrections now implemented:

1. CI applies every overlay in the declared order and writes the effective OpenAPI document.
2. The effective document is validated by `openapi-spec-validator`.
3. A structural validator resolves every local reference, enforces unique/non-empty `operationId`, requires every inline or referenced Response Object to have `description`, and validates the required `ApiError` shape.
4. Redocly lint runs on the effective bundle and treats both errors and warnings as blocking.
5. Missing response descriptions were added.
6. `ApiError` now requires and defines `occurred_at` and `next_action`.
7. Operation summaries, tag descriptions, license metadata, examples, a missing 4xx response, and the superseded unused `PromotionReceipt` were corrected.
8. CI uploads the effective bundle and lint diagnostics as review evidence.

Independent reviewer checks:

- Inspect the uploaded effective OpenAPI artifact, not the uncomposed base fragment.
- Confirm overlay application semantics match OpenAPI Overlay 1.0 expectations used by the project.
- Confirm generated clients and contract tests will consume the effective bundle only.
- Confirm the error schema is compatible with existing platform error envelopes or has an explicit migration boundary.

### SDR-005 / SDR-006 — Authorization and event cross-contract checks

**Pre-review status: no new blocker detected by the strengthened gate; independent verification required.**

The committed cross-contract validator verifies canonical authorization reason
codes, required command paths, event catalog coverage, and payload-schema
resolution. The reviewer must still verify segregation-of-duties semantics and
sensitive-field classification against the Product and Security/Privacy policy.

### Review evidence strength

**Pre-review status: corrected.**

The Design Contract Gate no longer relies solely on string presence. It now:

- builds and structurally validates the effective OpenAPI bundle;
- runs Redocly with zero-warning enforcement;
- executes the complete schema stack on PostgreSQL 16;
- inspects PostgreSQL catalogs for RLS policy/FORCE RLS and tenant-qualified lineage;
- preserves all validation outputs as a workflow artifact;
- fails closed unless every structural gate succeeds.

## 4. Decision rules

An independent reviewer must replace `decision: PENDING` with exactly one of:

- `APPROVED`
- `APPROVED_WITH_CONDITIONS`
- `CHANGES_REQUESTED`

A final decision must include:

1. the exact `reviewed_commit`;
2. confirmation that the review commit is a descendant of the reviewed commit;
3. explicit disposition for SDR-003 and SDR-004;
4. links or identifiers for the successful validation runs and effective artifacts;
5. any remaining fail-closed release conditions;
6. reviewer identity and review timestamp.

The System Design response remains `proposed` until this independent review is
completed. Production readiness is not implied by design approval.

## 5. Current review disposition

```text
PENDING_INDEPENDENT_REVIEW
```

The prior P0 findings have been corrected and the exact-target structural gates
pass. No approval is asserted by the System Design author.
