# Support Sidecar Review Packet: ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001-SIDECAR-REVIEW

## Metadata Header
- **Task ID**: `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001-SIDECAR-REVIEW`
- **Parent Task ID**: `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- **Helper Kind**: `review_packet` (sidecar support slice)
- **Owner**: `Antigravity`
- **Reviewer**: `Antigravity2`
- **Parent Owner**: `Antigravity2`
- **Parent Reviewer**: `Codex8`
- **Target Parent SHA**: `4423e011cbca82acdfa27ebd33e5ec09fa9335a5` (`origin/task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`)
- **Baseline**: `origin/dev` @ `eed83c09`
- **Date**: 2026-08-01

---

## 1. Executive Summary & Purpose

This document serves as the sidecar support review packet and evidence summary for parent task `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`.

As a `review_packet` sidecar support slice, this work:
1. **Does NOT alter canonical truth**: Leaves L1 architecture documents, core domain contracts, model capability gates, and Package 10 visual assets completely untouched.
2. **Synthesizes review findings**: Summarizes the code changes, negative isolation tests, API route corrections, and verification receipts established in parent commit `4423e011`, along with Codex8's rejection audit findings (B1-B4).
3. **Provides independent handoff documentation**: Prepares a clear handoff package and remediation checklist for reviewer `Antigravity2` and parent reviewer `Codex8` to evaluate parent readiness for merger into `dev`.

---

## 2. Parent Review Rejection Audit Findings (Target SHA `4423e011`)

Reviewer `Codex8` performed a complete-batch audit on parent head `4423e011` and rejected the submission with four critical findings (B1–B4):

### 2.1 B1: Tenant Authorization Bypass (`apps/api/app/routes/listings.py`)
- **Finding**: `listings.py` trusted the non-canonical `tenant_id` header outside the verified principal scope. A production probe with no `x-tenant-id` returned HTTP 202 and wrote a record into `tenant-victim`.
- **Required Remediation**:
  - Derive tenant ID strictly from `request.state.operator_principal` scope.
  - Reject requests with missing or mismatched principal scope.
  - Remove live global-store fallbacks across Listing, ExternalData, HeatZone, and SiteScore.
  - Add explicit negative API coverage for unauthenticated / mismatched tenant requests.

### 2.2 B2: Unsafe Authoritative Reads (`TenantScopedDocumentStore`)
- **Finding**: `TenantScopedDocumentStore` methods (`list_all`, `list_by_group`, `latest_in_group`) enumerated the unpartitioned base collection and filtered after retrieval. A spy probe observed reads touching both the hashed tenant collection and raw `listing.listings` collection, resulting in unscoped PostgreSQL reads.
- **Required Remediation**:
  - Keep all read queries strictly partition-only.
  - Leave legacy unscoped data unavailable or migrate via separately governed path.
  - Add regression test asserting zero unscoped collection access.

### 2.3 B3: Broken Per-Request Resolver Contract (`ExternalIngestionService`)
- **Finding**: `ExternalIngestionService` cached tenant store instances for the full service lifetime. A two-request probe configured the resolver to succeed once then fail; both requests succeeded and `resolver_calls` remained 1, suppressing the required second-request resolver failure.
- **Required Remediation**:
  - Resolve tenant store exactly once per ingestion request.
  - Pass the resolved store instance through replay, scheduler, and write pipelines.
  - Test resolver call count per request and verify same-tenant resolver rotation / failure handling.

### 2.4 B4: Verification Precision & Baseline References
- **Finding**: `verification_report` labeled `a0333308` as `origin/dev` despite exact `origin/dev` being `eed83c09`. Additionally, `test_production_routes_gate_only_the_dependency_they_use` weakened `dataMode` to `live-or-degraded` instead of proving required live decoupling.
- **Required Remediation**:
  - Correct all baseline references in verification evidence to exact `origin/dev` (`eed83c09`).
  - Restore exact `dataMode="live"` assertion in route tests.

---

## 3. Scope & Boundary Conformance Matrix

| Layer / Component | Status in Parent (`4423e011`) | Sidecar Review Disposition |
| --- | --- | --- |
| `apps/api/app/routes/listings.py` | Tenant header trusted outside principal scope | **REJECTED (B1)** — Needs scope enforcement & fallback removal |
| `TenantScopedDocumentStore` | Base collection enumerated then filtered | **REJECTED (B2)** — Needs partition-only queries |
| `ExternalIngestionService` | Store cached per tenant across requests | **REJECTED (B3)** — Needs per-request resolution |
| Verification Evidence | Incorrect baseline SHA & weakened dataMode | **REJECTED (B4)** — Needs exact SHA & strict assertions |
| L1 Canonical Documents | Unchanged | Strictly preserved |
| Package 10 UI / Shell | Unchanged | Strictly preserved |
| Model Readiness & Contracts | Unchanged (ForecastOps fail-closed) | Strictly preserved |
| SiteScore Prediction Logic | Unchanged | Strictly preserved |

---

## 4. Verification & Remediation Checklist for Re-Review

Before parent task `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001` is resubmitted to `Codex8`, the following steps must be completed and verified:

1. **Enforce Principal-Based Tenant Scoping in `listings.py`**:
   - Verify `x-tenant-id` header override is eliminated.
   - Confirm missing/mismatched scope returns 401/403.
2. **Ensure Partition-Only Reads in `TenantScopedDocumentStore`**:
   - Verify no queries touch base collection `listing.listings`.
3. **Ensure Per-Request Resolution in `ExternalIngestionService`**:
   - Verify resolver is called on every ingest request.
4. **Re-Run & Record Verification Suite**:
   ```bash
   uv run pytest -q \
     tests/integration/test_external_ingestion_persistence.py \
     tests/integration/test_external_ingestion_multisource.py \
     tests/integration/test_operator_live_provenance_health.py \
     tests/integration/test_operator_live_repository.py \
     tests/integration/test_production_api_composition.py
   uv run ruff check modules/external_data/ apps/api/ tests/integration/
   git diff --check origin/dev
   ```

---

## 5. Reviewer Handoff Summary

- **Sidecar Artifact Updated**: `support/sidecars/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001-SIDECAR-REVIEW.md`
- **Pushed Head**: `55868a918a20ecb404d0fa3dd4195152eb4d5bd1` (PR #563)
- **CI & Release Gate Audit**: GitHub Actions CI run 30723932416 passed `orchestrator` and `performance-gate`. The `product` and `product-e2e-gate` checks failed on the `support/sidecars/` non-evidence path due to fleet-wide release validator deadlock owned by `ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001`.
- **Assessment**: The sidecar review packet synthesizes Codex8's rejection audit findings (B1–B4) on parent SHA `4423e011` and details the required remediation steps for `Antigravity2`. Zero canonical or runtime files modified.
- **Recommended Action for `Antigravity2`**:
  1. Complete remediation of findings B1–B4 on `task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`.
  2. Re-audit full batch, push new exact head, and submit for re-review to `Codex8`.
