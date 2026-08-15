# ODP-R0-003 Platform API Closeout

Task: ODP-R0-003
Owner: Codex
Reviewer: Claude2
Status at closeout: review_approved

## Delivered Scope

- FastAPI platform skeleton under `apps/api/`.
- Shared application message, audit event, job queue, and observability correlation primitives.
- Contract coverage for health, job creation/idempotency, audit, and correlation behavior.

## Acceptance Evidence

- `/health` returns service, version, timestamp, and correlation id.
- `POST /jobs` returns `202 Accepted` with a stable `job_id`.
- Reusing an idempotency key returns the same job and marks the response as not newly created.
- `x-correlation-id` is propagated through request handling and response headers.
- Audit events record actor, action, resource, result, timestamp, and correlation id.

## Verification

- `uv run pytest tests/contract/test_platform_api.py`
- `uv run ruff check apps/api shared tests/contract/test_platform_api.py`
- `git diff --check origin/dev...HEAD`

## Boundaries

This task does not add durable job storage, authentication/RBAC, module-specific API behavior, worker execution, or frontend surfaces.
