from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any
from uuid import uuid4

from modules.external_data.geo import GeoPipeline
from modules.listing import InMemoryListingRepository, ListingPipeline
from shared.audit import InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:
    class ListingImportPayload(BaseModel):
        records: list[dict[str, Any]] = Field(default_factory=list)
        source_id: str | None = None


    def create_listings_router(
        *,
        audit_log: InMemoryAuditLog | None = None,
        repository: Any = None,
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        # ODP-FLOW-002: an injected repository (durable in E2E mode) keeps listing
        # dedup keys and converted candidate sites across a process restart.
        bound_repository = repository

        router = APIRouter(prefix="/listings", tags=["listings"])

        @router.post(
            "/import",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[Depends(require_permission("listing", Action.CREATE, engine=authz_engine))],
        )
        @router.post(
            "/import-jobs",
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[Depends(require_permission("listing", Action.CREATE, engine=authz_engine))],
        )
        def import_listings(body: ListingImportPayload, request: Request) -> dict[str, Any]:
            repository = _repository(request, bound_repository)
            result = ListingPipeline(
                repository=repository, geo_pipeline=_geo_pipeline(request)
            ).import_records(
                body.records,
                source_id=body.source_id,
            )
            return result.to_dict()

        @router.get(
            "/candidates",
            dependencies=[Depends(require_permission("listing", Action.VIEW, engine=authz_engine))],
        )
        def list_candidate_sites(request: Request) -> dict[str, Any]:
            repository = _repository(request, bound_repository)
            return {
                "candidates": [
                    candidate.to_card_dict() for candidate in repository.list_candidates()
                ]
            }

        return router


    class AssistedIntakeStore:
        """Small process-local contract store; durable adapters can replace it later."""

        def __init__(self) -> None:
            self.intakes: dict[str, dict[str, Any]] = {}
            self.assignments: dict[str, dict[str, Any]] = {}
            self.jobs: dict[str, dict[str, Any]] = {}
            self.decisions: dict[str, dict[str, Any]] = {}
            self.promotions: dict[str, dict[str, Any]] = {}
            self.slas: dict[str, dict[str, Any]] = {}
            self.saved_views: list[dict[str, Any]] = []
            self.replays: dict[str, tuple[str, dict[str, Any], int]] = {}


    def create_assisted_intake_router(store: AssistedIntakeStore | None = None) -> APIRouter:
        """Implement the approved ODP assisted-intake `/api/v1` contract."""

        active = store or AssistedIntakeStore()
        router = APIRouter(prefix="/api/v1", tags=["assisted-listing-intake"])

        def now() -> str:
            return datetime.now(UTC).isoformat().replace("+00:00", "Z")

        def require_actor(
            x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
            authorization: str | None = Header(default=None, alias="Authorization"),
        ) -> str:
            if not x_tenant_id:
                raise HTTPException(403, "tenant scope is required")
            if authorization is not None and not authorization.startswith("Bearer "):
                raise HTTPException(403, "invalid bearer authorization")
            return x_tenant_id

        def fingerprint(body: Any) -> str:
            return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

        def replay(key: str | None, body: Any, make: Any) -> tuple[dict[str, Any], int, bool]:
            if not key:
                raise HTTPException(422, "Idempotency-Key is required")
            digest = fingerprint(body)
            prior = active.replays.get(key)
            if prior:
                if prior[0] != digest:
                    raise HTTPException(409, "idempotency key was used with another payload")
                return prior[1], 200, True
            result, code = make()
            active.replays[key] = (digest, result, code)
            return result, code, False

        def require_version(if_match: str | None, current: int = 1) -> None:
            if if_match is None:
                raise HTTPException(428, "If-Match is required")
            supplied = if_match.strip('W/"')
            if supplied not in {str(current), f"v{current}"}:
                raise HTTPException(409, f"version conflict; current version is {current}")

        def receipt(resource: str, state: str, version: int = 1) -> dict[str, Any]:
            return {
                "transition_id": str(uuid4()), "from_state": resource, "to_state": state,
                "occurred_at": now(), "actor": "api", "version_after": version,
            }

        @router.get("/intakes", dependencies=[Depends(require_actor)])
        def list_intakes(cursor: str | None = None, page_size: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
            if cursor and cursor != "end":
                raise HTTPException(400, "invalid or expired cursor")
            items = list(active.intakes.values())[:page_size]
            return {"items": items, "next_cursor": None, "page_size": page_size,
                    "total_count": len(active.intakes), "total_count_accuracy": "exact",
                    "snapshot_time": now(), "query_fingerprint": fingerprint({"page_size": page_size})}

        @router.post("/intakes/url", status_code=202, dependencies=[Depends(require_actor)])
        def submit_url(body: dict[str, Any], response: Response,
                       key: str | None = Header(None, alias="Idempotency-Key")) -> dict[str, Any]:
            if not body.get("original_url") or not isinstance(body.get("scope"), dict) or not body["scope"].get("tenant_id"):
                raise HTTPException(422, "original_url and scope.tenant_id are required")
            def make() -> tuple[dict[str, Any], int]:
                intake_id, job_id = str(uuid4()), str(uuid4())
                value = {"intake_id": intake_id, "state": "SUBMITTED", "intake_method": "URL",
                         "scope": body["scope"], "submitted_at": now(), "updated_at": now(), "version": 1,
                         "job_id": job_id, "correlation_id": str(uuid4())}
                active.intakes[intake_id] = value
                active.jobs[job_id] = {"job_id": job_id, "status": "QUEUED", "checkpoint": "RETRIEVE", "attempt": 0, "version": 1, "correlation_id": value["correlation_id"]}
                return {k: value[k] for k in ("intake_id", "state", "version", "job_id", "correlation_id", "submitted_at")}, 202
            value, code, was_replayed = replay(key, body, make)
            response.status_code = code
            response.headers["Idempotency-Replayed"] = str(was_replayed).lower()
            response.headers["ETag"] = '"1"'
            return value

        @router.post("/intake-batches", status_code=207, dependencies=[Depends(require_actor)])
        def submit_batch(body: dict[str, Any], response: Response,
                         key: str | None = Header(None, alias="Idempotency-Key")) -> dict[str, Any]:
            rows = body.get("rows")
            if not isinstance(rows, list) or not body.get("batch_id") or not body.get("scope"):
                raise HTTPException(422, "batch_id, scope, and rows are required")
            def make() -> tuple[dict[str, Any], int]:
                receipts, accepted = [], 0
                for index, row in enumerate(rows):
                    if not isinstance(row, dict) or not row.get("address_raw"):
                        receipts.append({"row_index": index, "status": "REJECTED", "error": {"code": "VALIDATION_ERROR"}})
                    else:
                        accepted += 1
                        intake_id = str(uuid4())
                        active.intakes[intake_id] = {"intake_id": intake_id, "state": "SUBMITTED", "intake_method": "BATCH", "scope": body["scope"], "submitted_at": now(), "updated_at": now(), "version": 1}
                        receipts.append({"row_index": index, "status": "ACCEPTED", "intake_id": intake_id})
                result = {"batch_id": body["batch_id"], "submitted_at": now(), "accepted_count": accepted,
                          "rejected_count": len(rows)-accepted, "rows": receipts, "correlation_id": str(uuid4())}
                return result, 207 if accepted != len(rows) else 202
            value, code, _ = replay(key, body, make)
            response.status_code = code
            return value

        @router.get("/intakes/{intake_id}", dependencies=[Depends(require_actor)])
        def get_intake(intake_id: str, response: Response) -> dict[str, Any]:
            value = active.intakes.get(intake_id)
            if value is None: raise HTTPException(404, "intake not found")
            response.headers["ETag"] = f'"{value["version"]}"'
            return value

        def mutate(collection: dict[str, dict[str, Any]], resource_id: str, action: str,
                   body: dict[str, Any], key: str | None, if_match: str | None) -> dict[str, Any]:
            current = collection.get(resource_id)
            if current is None: raise HTTPException(404, "resource not found")
            require_version(if_match, int(current.get("version", 1)))
            def make() -> tuple[dict[str, Any], int]:
                current["version"] = int(current.get("version", 1)) + 1
                current["status"] = action.upper()
                current["updated_at"] = now()
                return receipt(action, current["status"], current["version"]), 200
            return replay(key, body, make)[0]

        @router.post("/intakes/{intake_id}/corrections", status_code=201, dependencies=[Depends(require_actor)])
        def correct(intake_id: str, body: dict[str, Any], key: str | None = Header(None, alias="Idempotency-Key"), if_match: str | None = Header(None, alias="If-Match")) -> dict[str, Any]:
            current = active.intakes.get(intake_id)
            if current is None: raise HTTPException(404, "intake not found")
            require_version(if_match, current["version"])
            if not all(k in body for k in ("field_path", "corrected_value", "reason")): raise HTTPException(422, "invalid correction")
            def make() -> tuple[dict[str, Any], int]:
                current["version"] += 1
                return {"correction_id": str(uuid4()), "status": "ACCEPTED", "intake_id": intake_id,
                        "version": current["version"], "audit_event_id": str(uuid4()), "correlation_id": str(uuid4())}, 201
            return replay(key, body, make)[0]

        @router.put("/intakes/{intake_id}/assignment", dependencies=[Depends(require_actor)])
        def assign(intake_id: str, body: dict[str, Any], key: str | None = Header(None, alias="Idempotency-Key"), if_match: str | None = Header(None, alias="If-Match")) -> dict[str, Any]:
            current = active.intakes.get(intake_id)
            if current is None: raise HTTPException(404, "intake not found")
            require_version(if_match, current["version"])
            def make() -> tuple[dict[str, Any], int]:
                aid = str(uuid4()); current["version"] += 1
                value = {"assignment_id": aid, "status": "ASSIGNED", "owner_subject_id": body.get("owner_subject_id"), "due_at": body.get("due_at"), "version": 1, "audit_event_id": str(uuid4())}
                active.assignments[aid] = value; return value, 200
            return replay(key, body, make)[0]

        @router.post("/jobs/{job_id}/retry", status_code=202, dependencies=[Depends(require_actor)])
        def retry_job(job_id: str, body: dict[str, Any], key: str | None = Header(None, alias="Idempotency-Key"), if_match: str | None = Header(None, alias="If-Match")) -> dict[str, Any]:
            job = active.jobs.get(job_id)
            if job is None: raise HTTPException(404, "job not found")
            require_version(if_match, job["version"])
            def make() -> tuple[dict[str, Any], int]:
                job.update(status="QUEUED", checkpoint=body.get("checkpoint"), attempt=job["attempt"]+1, version=job["version"]+1); return job, 202
            return replay(key, body, make)[0]

        @router.get("/saved-views", dependencies=[Depends(require_actor)])
        def saved_views() -> list[dict[str, Any]]: return active.saved_views

        @router.post("/saved-views", status_code=201, dependencies=[Depends(require_actor)])
        def create_saved_view(body: dict[str, Any], key: str | None = Header(None, alias="Idempotency-Key")) -> dict[str, Any]:
            def make() -> tuple[dict[str, Any], int]:
                value = {"saved_view_id": str(uuid4()), **body}; active.saved_views.append(value); return value, 201
            return replay(key, body, make)[0]

        @router.post("/intakes/{intake_id}/promotion-requests", status_code=202, dependencies=[Depends(require_actor)])
        def promote(intake_id: str, body: dict[str, Any], key: str | None = Header(None, alias="Idempotency-Key"), if_match: str | None = Header(None, alias="If-Match")) -> dict[str, Any]:
            current = active.intakes.get(intake_id)
            if current is None: raise HTTPException(404, "intake not found")
            require_version(if_match, current["version"])
            def make() -> tuple[dict[str, Any], int]:
                did = str(uuid4()); value = {"promotion_decision_id": did, "intake_id": intake_id, "listing_id": str(uuid4()), "status": "PENDING_REVIEW", "decision_type": "PROMOTION", "version": 1, "audit_event_id": str(uuid4()), "correlation_id": str(uuid4())}; active.promotions[did] = value; return value, 202
            return replay(key, body, make)[0]

        @router.get("/promotion-decisions/{resource_id}", dependencies=[Depends(require_actor)])
        def get_promotion(resource_id: str) -> dict[str, Any]:
            if resource_id not in active.promotions: raise HTTPException(404, "promotion decision not found")
            return active.promotions[resource_id]

        @router.get("/identity-decisions/{resource_id}", dependencies=[Depends(require_actor)])
        def get_identity(resource_id: str) -> dict[str, Any]:
            if resource_id not in active.decisions: raise HTTPException(404, "identity decision not found")
            return active.decisions[resource_id]

        # Uniform state-command endpoints. Their payloads remain unmodified and are
        # covered by the effective OpenAPI; this handler only owns concurrency/replay.
        for path, name, collection, action in [
            ("/intakes/{resource_id}/actions/cancel", "cancelIntake", active.intakes, "cancelled"),
            ("/intakes/{resource_id}/actions/quarantine", "quarantineIntake", active.intakes, "quarantined"),
            ("/intakes/{resource_id}/actions/reopen", "reopenIntake", active.intakes, "reopened"),
            ("/assignments/{resource_id}/actions/claim", "claimAssignment", active.assignments, "claimed"),
            ("/assignments/{resource_id}/actions/transfer", "transferAssignment", active.assignments, "transferred"),
            ("/assignments/{resource_id}/actions/complete", "completeAssignment", active.assignments, "completed"),
            ("/sla-instances/{resource_id}/actions/pause", "pauseSla", active.slas, "paused"),
            ("/sla-instances/{resource_id}/actions/resume", "resumeSla", active.slas, "resumed"),
            ("/promotion-decisions/{resource_id}/actions/review", "reviewPromotionDecision", active.promotions, "reviewed"),
            ("/identity-decisions/{resource_id}/actions/review", "reviewIdentityDecision", active.decisions, "reviewed"),
            ("/identity-decisions/{resource_id}/actions/reverse", "requestIdentityDecisionReversal", active.decisions, "reversal_requested"),
        ]:
            def command(resource_id: str, body: dict[str, Any] | None = None,
                        key: str | None = Header(None, alias="Idempotency-Key"),
                        if_match: str | None = Header(None, alias="If-Match"),
                        _collection: Any = collection, _action: str = action) -> dict[str, Any]:
                return mutate(_collection, resource_id, _action, body or {}, key, if_match)
            router.add_api_route(path, command, methods=["POST"], operation_id=name)

        for path, name, action in [
            ("/match-cases/{resource_id}/decisions", "decideMatchCase", "match_decision"),
            ("/identity/merge", "mergeProperties", "merge"),
            ("/identity/split", "splitProperty", "split"),
            ("/identity/unmerge", "unmergeProperty", "unmerge"),
        ]:
            def identity_command(resource_id: str = "identity", body: dict[str, Any] | None = None,
                                 key: str | None = Header(None, alias="Idempotency-Key"),
                                 if_match: str | None = Header(None, alias="If-Match"),
                                 _action: str = action) -> dict[str, Any]:
                require_version(if_match)
                def make() -> tuple[dict[str, Any], int]:
                    did = str(uuid4()); value = {"decision_id": did, "status": "ACCEPTED", "resource_versions": {}, "job_id": str(uuid4()), "audit_event_id": str(uuid4()), "correlation_id": str(uuid4())}; active.decisions[did] = {**value, "version": 1, "action": _action}; return value, 202
                return replay(key, body or {}, make)[0]
            router.add_api_route(path, identity_command, methods=["POST"], operation_id=name, status_code=202)

        return router


    def _repository(request: Request, bound_repository: Any = None):
        if bound_repository is not None:
            return bound_repository
        repository = getattr(request.app.state, "listing_repository", None)
        if repository is None:
            repository = InMemoryListingRepository()
            request.app.state.listing_repository = repository
        return repository


    def _geo_pipeline(request: Request) -> GeoPipeline | None:
        return getattr(request.app.state, "listing_geo_pipeline", None)


    __all__ = ["AssistedIntakeStore", "ListingImportPayload", "create_assisted_intake_router", "create_listings_router"]
