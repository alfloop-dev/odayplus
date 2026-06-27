from __future__ import annotations

from typing import Any

from shared.audit import AuditEvent, InMemoryAuditLog
from shared.jobs import InMemoryJobQueue, JobRequest
from shared.observability import CORRELATION_ID_HEADER, CorrelationContext


def health_payload() -> dict[str, str]:
    return {"status": "ok", "service": "oday-api"}


try:
    from fastapi import FastAPI, Header, HTTPException, Request, Response, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover - dependency added by backend task
    app: Any = None
else:
    class JobCreatePayload(BaseModel):
        job_type: str = Field(min_length=1)
        payload: dict[str, Any] = Field(default_factory=dict)
        idempotency_key: str | None = None


    def create_app(
        *,
        audit_log: InMemoryAuditLog | None = None,
        job_queue: InMemoryJobQueue | None = None,
    ) -> FastAPI:
        audit_log = audit_log or InMemoryAuditLog()
        job_queue = job_queue or InMemoryJobQueue()
        api = FastAPI(title="ODay Plus API", version="0.1.0")

        @api.middleware("http")
        async def attach_correlation_id(request: Request, call_next: Any) -> Response:
            context = CorrelationContext.from_header(request.headers.get(CORRELATION_ID_HEADER))
            request.state.correlation_id = context.correlation_id
            response = await call_next(request)
            response.headers[CORRELATION_ID_HEADER] = context.correlation_id
            return response

        @api.get("/healthz", tags=["system"])
        def healthz() -> dict[str, str]:
            return health_payload()

        @api.get("/platform/health", tags=["platform"])
        def platform_health(request: Request) -> dict[str, str]:
            return {
                **health_payload(),
                "correlation_id": request.state.correlation_id,
            }

        @api.post("/jobs", status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
        def enqueue_job(
            body: JobCreatePayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_idempotency_key = body.idempotency_key or idempotency_key
            job, created = job_queue.enqueue(
                JobRequest(
                    job_type=body.job_type,
                    payload=body.payload,
                    idempotency_key=effective_idempotency_key,
                ),
                correlation_id=request.state.correlation_id,
            )
            audit_event = audit_log.record(
                AuditEvent(
                    event_type="job.enqueue",
                    actor="system",
                    action="enqueue",
                    resource=f"job/{job.job_type}",
                    outcome="accepted" if created else "idempotent_replay",
                    correlation_id=request.state.correlation_id,
                    job_id=job.job_id,
                    metadata={"idempotency_key": effective_idempotency_key, "created": created},
                )
            )
            return {
                "job": job.to_dict(),
                "created": created,
                "audit_event_id": audit_event.event_id,
            }

        @api.get("/jobs/{job_id}", tags=["jobs"])
        def get_job(job_id: str) -> dict[str, Any]:
            job = job_queue.get(job_id)
            if job is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
            return job.to_dict()

        @api.get("/audit/events", tags=["audit"])
        def list_audit_events(correlation_id: str | None = None) -> dict[str, Any]:
            return {
                "events": [
                    event.to_dict()
                    for event in audit_log.list_events(correlation_id=correlation_id)
                ]
            }

        api.state.audit_log = audit_log
        api.state.job_queue = job_queue
        return api

    app = create_app()
