---
adr_id: ADR-0003
title: "User & Role Management Self-Service Store and ODP_AUTH_PRINCIPAL_MAP Linkage Architecture"
version: 1.0.0
status: accepted
document_class: architecture-decision-record
project: ODay Plus
language: zh-TW
decision_date: 2026-08-10
updated_at: 2026-08-10
owner: "Architecture Owner"
approvers: "Technology Lead / Security Lead"
owners:
  - Architecture Owner
  - Security Lead
content_format: markdown
source_documents:
  - ODP-00-04_DOCUMENT_VERSION_AND_ADR_GOVERNANCE.md
  - ODP-SD-09_SECURITY_AND_AUTHORIZATION_DESIGN.md
  - .orchestrator/task-briefs/odp_cap_user_role_ui_001.md
related_requirements:
  - ODP-FR-OPS-003
  - ODP-UX-SCR-ADMIN-001
review_trigger: "Review when dynamic live authorization persistence or external IdP role sync is deployed."
---

# ADR-0003: User & Role Management Self-Service Store and ODP_AUTH_PRINCIPAL_MAP Linkage Architecture

## Context
In ODay Plus, user identity authentication and initial principal claim resolution rely on the Secret Manager secret `ODP_AUTH_PRINCIPAL_MAP` (configured via `ODP_AUTH_PRINCIPAL_MAP_SECRET` in runtime environments).
The User & Role Management capability (`ODP-CAP-USER-ROLE-UI-001`, `UX-SCR-ADMIN-001`, `FR-OPS-003`) introduces dynamic self-service user role and scope assignment.

## Decision
1. **Tenant-Partitioned & Durable Domain Persistence**: User role and scope assignments performed via `/operator/users` are persisted using `DurableOperatorDomainStateRepository` ("users-roles") and resolved per-tenant via `DurableTenantServiceResolver`.
2. **Authentication Boundary Linkage**:
   - `ODP_AUTH_PRINCIPAL_MAP` in Secret Manager serves as the authoritative static credential-to-principal mapping for initial authentication bootstrapping.
   - Dynamic user role changes saved through the Operator Console interface are stored in tenant-partitioned domain persistence.
   - Direct automated sync back into GCP Secret Manager `ODP_AUTH_PRINCIPAL_MAP` or external Enterprise IdP (SAML/OIDC SCIM) is explicitly scoped out of the Phase 1 UI release and governed by follow-up IdP integration.
   - All user role changes write an immutable audit trail (`user_role_management.*`) with server-derived actor subject identity (`request.state.operator_subject_id`).

## Consequences
- Dynamic user role management operates durably across process restarts and Cloud Run instances without in-memory state loss.
- Static secret authentication fallback remains secure and fail-closed.
