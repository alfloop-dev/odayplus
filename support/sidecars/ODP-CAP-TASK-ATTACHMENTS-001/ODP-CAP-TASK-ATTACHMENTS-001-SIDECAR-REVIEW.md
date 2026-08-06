# Review Packet: ODP-CAP-TASK-ATTACHMENTS-001

- Sidecar task: `ODP-CAP-TASK-ATTACHMENTS-001-SIDECAR-REVIEW`
- Parent task: `ODP-CAP-TASK-ATTACHMENTS-001`
- Sidecar owner: `Antigravity4`
- Assigned sidecar reviewer / parent owner: `Antigravity`
- Parent reviewer: `Antigravity2`
- Evidence captured: `2026-08-05` UTC
- Parent branch: `task/ODP-CAP-TASK-ATTACHMENTS-001`
- Exact reviewed parent HEAD: `d2af7238d964dabcd73e3a4ebace1226712f003c`
- Scope: review and evidence only; no parent implementation or canonical truth changed

## Executive disposition

The Task Attachments with Scoped Access and Sensitivity Masking (`FR-OPS-002` and `FR-SHARED-007`) implementation at parent HEAD `d2af7238` is **fully verified and recommended for approval**.

Independent verification confirms that all contract and security test cases pass cleanly with zero errors or warnings, and all linting/formatting checks (`ruff check`, `git diff --check`) pass completely. The change fulfills the explicit requirements of `FR-OPS-002` (enumerating tasks, assignments, comments, attachments, approvals, escalations, and notifications for StoreOps issues) and `FR-SHARED-007` (sensitivity masking for controlled site photos and lease scans). High-risk controlled attachments are automatically masked based on role permissions and requested masking profiles (`X-Masking-Profile`).

## Reviewed change surface

Compared with `origin/dev` tip (`6eb68ace`), parent commit `d2af7238` (comprising `4498055c` and `d2af7238`) modifies 5 files with 647 insertions and 2 deletions:

| File | Contract role | Review observation |
| --- | --- | --- |
| `apps/api/app/routes/operator_modules/store_ops.py` | Admin REST API endpoints | Implements `/issues/{issue_id}/attachments` endpoints for listing, fetching, uploading, downloading, and deleting task attachments with request headers (`X-Masking-Profile`, `X-Roles`, `X-Tenant-Id`). |
| `apps/web/features/operator/storeOpsWorkflowTypes.ts` | Frontend Type System | Defines `AttachmentUploadPayload`, `TaskAttachment`, `AttachmentMaskingProfile`, and issue detail attachment fields for frontend integration. |
| `modules/opsboard/application/store_ops.py` | StoreOps Application Service | Implements attachment storage methods (`list_attachments`, `get_attachment`, `upload_attachment`, `download_attachment`, `delete_attachment`), sensitivity masking rules under `FR-SHARED-007`, tenant scoping, and audit logging. |
| `modules/opsboard/domain/r4_dtos.py` | Domain DTOs | Defines `AttachmentUploadRequest` and `AttachmentResponse` DTO classes with sensitivity classifications (`public`, `internal`, `controlled_lease_scan`, `controlled_site_photo`). |
| `tests/contract/test_operator_attachments.py` | Contract & Security Test Suite | Implements full 7-scenario contract test suite verifying upload, listing, downloading, role-based sensitivity masking, tenant isolation, and deletion audit trail. |

No L1 canonical document or core schema definitions were modified.

## Contract & Enforcement Matrix

| Capability / Endpoint | Unmasked / Privileged Role Behavior | Masked / Standard Role Behavior | Evidence / Test Case |
| --- | --- | --- | --- |
| **Attachment Upload** (`POST /attachments`) | Accepts file payload, records sensitivity class (`controlled_lease_scan` / `controlled_site_photo`), logs upload audit event | Validates payload parameters, requires valid actor role | `test_upload_task_attachment` |
| **Attachment List** (`GET /attachments`) | Returns full list of attachments for target issue | Returns attachments with masked metadata for controlled files if caller lacks privileged role | `test_list_task_attachments` |
| **Sensitivity Masking** (`FR-SHARED-007`) | Privileged roles (`expansionManager`, `facilitiesLead`, `auditPm`) or `X-Masking-Profile: unmasked` receive unmasked content & metadata | Standard roles with `X-Masking-Profile: masked` receive redacted file content (`[REDACTED SENSITIVE LEASE DATA]`) & masked preview URLs | `test_sensitivity_masking_controlled_lease_scan` |
| **Tenant Isolation** | Returns target attachment matching `X-Tenant-Id` | Attempts to access attachment across tenant boundary fail closed with `StoreOpsNotFound` | `test_tenant_isolation_on_attachments` |
| **Attachment Deletion** (`DELETE /attachments/{id}`) | Deletes attachment entry, records audit trail event with `actor_role_id` and timestamp | Deletion forbidden for unauthorized roles | `test_delete_task_attachment_audit_log` |

## Independent verification at exact parent HEAD

The following commands were executed in a temporary detached worktree at parent commit `d2af7238d964dabcd73e3a4ebace1226712f003c`:

```bash
# 1. Run full task attachments pytest suite
/home/lupin/oday-plus/.venv/bin/pytest -q tests/contract/test_operator_attachments.py
# Output: 7 passed in 0.85s

# 2. Run Ruff linter on modified Python sources
/home/lupin/oday-plus/.venv/bin/ruff check \
  apps/api/app/routes/operator_modules/store_ops.py \
  modules/opsboard/application/store_ops.py \
  modules/opsboard/domain/r4_dtos.py \
  tests/contract/test_operator_attachments.py
# Output: All checks passed!

# 3. Check git diff formatting
git diff --check origin/dev..task/ODP-CAP-TASK-ATTACHMENTS-001
# Output: clean
```

## Reviewer Attention Points

1. **Sensitivity Masking Compliance (`FR-SHARED-007`)**: Lease scans (`controlled_lease_scan`) and site photos (`controlled_site_photo`) contain controlled financial and spatial data. The implementation in `StoreOpsService._apply_sensitivity_masking` correctly masks binary content and preview URLs unless the caller presents `X-Masking-Profile: unmasked` AND holds an authorized role (`expansionManager`, `facilitiesLead`, or `auditPm`).
2. **Audit Trail Provenance**: Every attachment upload and deletion produces a structured audit log entry in `StoreOpsIssue.events` with event types `ATTACHMENT_UPLOADED` and `ATTACHMENT_DELETED`.
3. **Tenant & Scoped Access**: Attachment access validates `tenant_id` against the issue's tenant scope, preventing cross-tenant data leakage.

## Recommended reviewer disposition

- **APPROVE** the implementation of `ODP-CAP-TASK-ATTACHMENTS-001` at HEAD `d2af7238d964dabcd73e3a4ebace1226712f003c`.
- The parent owner (`Antigravity`) may proceed with finalizing the task branch PR and merging into `dev`.

## Sidecar boundary and handoff

This artifact is the sole repository deliverable of `ODP-CAP-TASK-ATTACHMENTS-001-SIDECAR-REVIEW`. It does not mutate canonical documents, core schemas, or parent runtime implementations.

Handoff target: `Antigravity` (Parent Owner / Sidecar Reviewer).
