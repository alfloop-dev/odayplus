---
doc_id: ODP-SD-INTAKE-AUTH-001
title: ODay Plus Assisted Listing Intake Authorization and Segregation Matrix
version: 1.0.0
status: proposed
owner: Security Architecture / System Design
reviewers: Product / Security / Privacy / Expansion Engineering / QA
updated_at: 2026-07-17
---

# ODay Plus Assisted Listing Intake Authorization and Segregation Matrix

Normative for `SDI-004`, `SDI-006`, `SDI-016`, `SDI-017`, and `SDI-018`.
Authorization is deny-by-default and evaluated by the backend in this order:

```text
authenticated
AND role/service grant
AND tenant scope
AND brand/region/assigned-area/HeatZone scope
AND ownership relation
AND workflow-state rule
AND decision-risk/segregation rule
AND field-classification clearance
AND purpose/source-policy/legal-hold obligations
```

A frontend-hidden action is not authorization. Every denial or masking decision is audited with the reason code below.

## 1. Principals

| Principal | Type | Normal scope |
|---|---|---|
| `EXPANSION_STAFF` | Human | Tenant + assigned brands/regions/areas/HeatZones; own submissions and assigned queue |
| `EXPANSION_MANAGER` | Human | Tenant + managed brands/regions/areas; review and promotion authority |
| `DATA_STEWARD` | Human | Tenant + governed sources/data domains; parser, correction, identity, quarantine |
| `GOVERNANCE_REVIEWER` | Human | Tenant-wide read-only audit/evidence, legal-hold review, export approval |
| `PRIVACY_OFFICER` | Human | Tenant-wide restricted-field/privacy operations; legal hold, purge, export approval |
| `PLATFORM_ADMIN` | Human | Platform configuration only; no implicit business-data access or business approval |
| `EMERGENCY_ADMIN` | Human break-glass | Time-limited incident scope; availability restoration only; no business outcome approval |
| `SVC_INTAKE` | Service | Create/read intake and transitions for a supplied tenant context |
| `SVC_RETRIEVAL` | Service | Read approved source policy; write snapshots and retrieval job state |
| `SVC_PARSER` | Service | Read immutable snapshots; write parser runs and proposed parsed/normalized fields |
| `SVC_MATCHER` | Service | Read matching inputs; write match cases/candidates only, never human decisions |
| `SVC_PROMOTION` | Service | Execute already-approved promotion decisions and create candidate/score job |
| `SVC_SLA` | Service | Read assignments/policies; write SLA derived states/escalations |
| `SVC_OUTBOX` | Service | Read unpublished outbox rows; publish and mark delivery status |
| `SVC_RECONCILER` | Service | Read SQL/GCS/job/audit metadata; write reconciliation findings, not business decisions |

## 2. Field classifications and masking

| Class | Examples | Default visibility | Export rule |
|---|---|---|---|
| `PUBLIC` | provider name, public listing URL, coarse district | All scoped internal roles | Allowed in ordinary scoped export |
| `INTERNAL` | normalized address, rent, area, floor, parsing quality | Expansion staff/manager, steward, governance reviewer | Purpose + manifest; watermark for tenant export |
| `CONFIDENTIAL` | exact coordinates, commercial notes, broker company contact, source snapshot content | Manager/steward/governance; staff only when assigned and purpose-bound | Independent approval + manifest + expiry |
| `RESTRICTED` | broker/owner personal contact, private notes, unredacted evidence, legal-hold material | Privacy officer and specifically authorized governance reviewer; services only for defined processing | Two-person approval, watermark, WORM receipt; no bulk UI export |
| `CREDENTIAL` | cookies, bearer tokens, passwords, private API credentials | Never collected or returned by product APIs/UI | Never exportable; references only in Secret Manager metadata |

Masked responses retain the field name and return `masked=true`, no value, and `mask_reason_code=FIELD_MASKED`. A required field that is fully masked makes the operation unavailable rather than encouraging client inference.

## 3. Scope and ownership rules

| Rule | Binding behavior | Denial code |
|---|---|---|
| Tenant isolation | `principal.tenant_id == resource.tenant_id`; service tokens carry exactly one tenant for business calls | `TENANT_SCOPE_DENIED` |
| Brand/region/area/HeatZone | Resource scope must be contained in all non-empty principal scope dimensions | `SCOPE_DENIED` |
| Own submission | Staff may update a submission only while owner/submitter or explicitly assigned | `OWNERSHIP_REQUIRED` |
| Queue assignment | Only manager/router may assign across users; assignee may claim/complete own assignment | `ASSIGNMENT_SCOPE_DENIED` |
| Source governance | Steward permissions are limited to registered source IDs in the steward grant | `SOURCE_SCOPE_DENIED` |
| Legal hold | Purge/archive/evidence deletion denied while active hold exists | `LEGAL_HOLD_CONFLICT` |
| Residency | Export/snapshot destination must be allowed by tenant residency policy | `RESIDENCY_DENIED` |

## 4. Resource/action matrix

Legend: `A` allowed within scope; `O` only own/assigned resource; `R` read-only; `2P` requires independent second actor; `—` denied.

| Resource / action | Staff | Manager | Steward | Governance | Privacy | Platform admin | Service identity |
|---|---:|---:|---:|---:|---:|---:|---:|
| Intake view | O | A | A | R | R | — | SVC_INTAKE/relevant worker |
| Intake submit URL/manual | A | A | A | — | — | — | SVC_INTAKE |
| Intake submit CSV/feed | A, max assigned scope | A | A | — | — | — | SVC_INTAKE |
| Intake cancel | O before decision execution | A before execution | A for governance defect | — | — | — | SVC_INTAKE executes approved command |
| Intake reopen from failed | O if retryable | A | A | R | — | — | authorized worker/replay service |
| Intake reopen quarantine | — | `2P` | `2P` | R | `2P` when privacy cause | — | SVC_INTAKE executes approved decision |
| Parsed/normalized field view | O, masked by class | A | A | R | R | — | parser/matcher minimum fields |
| Ordinary field correction | O | A | A | R | R | — | no autonomous human correction |
| Identity/address/rent/area correction | propose only | review/approve | propose/review; cannot self-review | R | review if restricted | — | apply approved correction |
| Snapshot metadata view | O | A | A | R | R | — | relevant worker |
| Snapshot content view | purpose-bound, assigned, redacted | purpose-bound | purpose-bound | purpose-bound R | A restricted | — | retrieval/parser only |
| Match case view | O | A | A | R | R | — | SVC_MATCHER |
| Decide NEW/REVISION/DUPLICATE | propose | A | A if data-quality case | R | — | — | matcher cannot decide human cases |
| Quarantine/reject | propose | A | A | R | A if privacy | — | execution service only |
| Merge | — | `2P` | `2P` | R | review if restricted | — | SVC identity executor after approval |
| Split | — | `2P` | `2P` | R | review if restricted | — | SVC identity executor after approval |
| Unmerge/reversal | — | `2P` | `2P` | R | review if restricted | — | SVC identity executor after approval |
| Assignment assign/transfer | own handoff request | A | A for steward queue | R | — | — | SVC_SLA/router according to policy |
| Assignment claim/complete | O | A | O/A steward queue | R | — | — | no human completion by service |
| Candidate promotion request | A | A | propose if correction resolved | R | — | — | — |
| Candidate promotion approve | — | `2P` when manager proposed; otherwise A | — | R | — | — | SVC_PROMOTION executes only |
| SiteScore job enqueue | after approved promotion | A | — | R | — | — | SVC_PROMOTION/SiteScore |
| Audit view | own-resource subset | managed scope | source/data scope | A | A privacy scope | configuration logs only | append/search service as assigned |
| Evidence export request | O limited | A scoped | A source-scoped | A | A | — | exporter after approval |
| Evidence export approve | — | not own request, internal only | not own request, source scope | `2P` | `2P` restricted | — | exporter executes |
| Place legal hold | — | request | request | approve | approve | — | hold service executes |
| Release legal hold | — | — | — | `2P` | `2P` | — | hold service executes |
| Purge expired record | — | request | request | approve non-restricted | `2P` | — | purge service executes |
| Provider kill switch | — | request | A source scope | R | — | infrastructure config only with steward approval | policy service enforces |
| Parser release/canary/rollback | — | — | `2P` | R | — | deploy config only | parser release service executes |
| Job replay | own retryable intake | A | A | R | — | break-glass availability only | worker executes authorized replay |

## 5. Workflow and risk restrictions

| Operation | Allowed workflow states | Risk | Required actors | Additional conditions | Denial code |
|---|---|---|---|---|---|
| Ordinary correction | `AWAITING_ASSISTED_ENTRY`, `NEEDS_REVIEW`, `READY` before promotion | medium | proposer; reviewer if material field | `If-Match`, reason for address/rent/area/identity | `WORKFLOW_STATE_DENIED` |
| Identity-affecting correction | `NEEDS_REVIEW` | high | proposer + independent manager/steward | before/after, snapshot/parser lineage, risk acknowledgement | `SECOND_ACTOR_REQUIRED` |
| Merge/split/unmerge | Match decision `PENDING_REVIEW` or executed decision reversal | critical | proposer + independent reviewer | reason >=20 chars, graph plan, dependency/cycle check | `SECOND_ACTOR_REQUIRED` |
| Quarantine release | Intake/listing `QUARANTINED` | high | steward/manager + independent reviewer | root cause resolved, source policy current | `QUARANTINE_RELEASE_DENIED` |
| Candidate promotion | Intake `READY`, listing `ACTIVE`, no active candidate duplicate | high | staff proposer + manager approver; manager proposer needs another manager | gate snapshot hash, listing/property versions | `PROMOTION_APPROVAL_REQUIRED` |
| Export confidential | any readable state | high | requester + independent governance approver | purpose, scope, field mask, expiry, watermark | `EXPORT_APPROVAL_REQUIRED` |
| Export restricted | any readable state | critical | requester + privacy/governance second actor | no bulk contact export; explicit subject scope | `RESTRICTED_EXPORT_DENIED` |
| Purge | retention reached and not under hold | critical | requester + privacy/governance approver | signed manifest, dry-run count, WORM audit | `PURGE_APPROVAL_REQUIRED` |
| Emergency admin | active incident only | critical | incident commander + second authorized admin | incident ID, 24h expiry, no business decision | `BREAK_GLASS_DENIED` |

## 6. Segregation pairs

| First actor action | Prohibited second actor | Valid second actor |
|---|---|---|
| Staff submits intake | Same staff may correct ordinary fields but may not approve promotion | Expansion manager in scope |
| Manager proposes promotion | Same manager | Another expansion manager or designated executive reviewer |
| Steward proposes identity merge/split/unmerge | Same steward | Expansion manager or different data steward with explicit reviewer grant |
| Governance reviewer requests export | Same reviewer | Privacy officer or different governance reviewer per data class |
| Privacy officer places legal hold | Same officer releases it | Different privacy officer or governance authority |
| Parser release author validates canary | Same author promotes production | Different data steward/release owner |

## 7. Service identity limitations

- Services may act only with an explicit tenant context propagated from a user request or scheduled tenant partition.
- `SVC_MATCHER` may propose match cases but cannot create human decisions or effective identity edges.
- `SVC_PROMOTION` may execute an approved decision but cannot approve one.
- `SVC_RETRIEVAL` receives a policy decision ID, not legal-policy discretion.
- `SVC_OUTBOX` can publish and mark outbox rows but cannot mutate business aggregates.
- `SVC_RECONCILER` can quarantine operational discrepancies and create findings; it cannot silently repair identity or promotion state.

## 8. Backend denial and masking codes

| Code | Meaning | HTTP |
|---|---|---:|
| `AUTHENTICATION_REQUIRED` | No valid principal | 401 |
| `ROLE_DENIED` | Role/service identity lacks resource/action grant | 403 |
| `TENANT_SCOPE_DENIED` | Cross-tenant access | 403 |
| `SCOPE_DENIED` | Brand/region/area/HeatZone outside scope | 403 |
| `OWNERSHIP_REQUIRED` | Resource not owned/assigned where O-rule applies | 403 |
| `ASSIGNMENT_SCOPE_DENIED` | Assignment target or queue unavailable | 403 |
| `SOURCE_SCOPE_DENIED` | Source not in steward/service grant | 403 |
| `FIELD_MASKED` | Field returned masked due classification | 200 with mask metadata |
| `DATA_CLASSIFICATION_DENIED` | Action requires unmasked field but clearance absent | 403 |
| `PURPOSE_REQUIRED` | Sensitive evidence access lacks purpose | 428 |
| `WORKFLOW_STATE_DENIED` | Action illegal in current state | 409 |
| `SECOND_ACTOR_REQUIRED` | Segregation/independent review absent | 409 |
| `SELF_REVIEW_DENIED` | Proposer attempted own approval | 403 |
| `RISK_ACKNOWLEDGEMENT_REQUIRED` | High-risk confirmation absent | 422 |
| `SOURCE_POLICY_DENIED` | Source policy forbids retrieval/processing | 403 |
| `LEGAL_HOLD_CONFLICT` | Delete/purge/archive conflicts with hold | 409 |
| `RESIDENCY_DENIED` | Destination violates residency policy | 403 |
| `EXPORT_APPROVAL_REQUIRED` | Export lacks approved decision | 409 |
| `BREAK_GLASS_DENIED` | Emergency grant missing/expired/out of incident scope | 403 |

## 9. Audit obligations

Every allow, deny, mask, break-glass grant, sensitive-view, export, purge, hold, correction, decision, assignment, replay, and promotion produces an audit event. Denied/masked events contain no sensitive value, only field path/classification, policy ID/version, principal, resource, reason code, correlation ID, and timestamp.
