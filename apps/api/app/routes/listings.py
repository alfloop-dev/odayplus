from __future__ import annotations

from typing import Any

from modules.external_data.geo import GeoPipeline
from modules.listing import InMemoryListingRepository, ListingPipeline
from shared.audit import InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, Request, status
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
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)

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
            repository = _repository(request)
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
            repository = _repository(request)
            return {
                "candidates": [
                    candidate.to_card_dict() for candidate in repository.list_candidates()
                ]
            }

        return router


    def _repository(request: Request) -> InMemoryListingRepository:
        repository = getattr(request.app.state, "listing_repository", None)
        if repository is None:
            repository = InMemoryListingRepository()
            request.app.state.listing_repository = repository
        return repository


    def _geo_pipeline(request: Request) -> GeoPipeline | None:
        return getattr(request.app.state, "listing_geo_pipeline", None)


    __all__ = ["ListingImportPayload", "create_listings_router"]
