from __future__ import annotations

from typing import Any

from modules.external_data.geo import GeoPipeline
from modules.listing import InMemoryListingRepository, ListingPipeline

try:
    from fastapi import APIRouter, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    router: Any = None
else:
    class ListingImportPayload(BaseModel):
        records: list[dict[str, Any]] = Field(default_factory=list)
        source_id: str | None = None


    router = APIRouter(prefix="/listings", tags=["listings"])


    @router.post("/import", status_code=status.HTTP_202_ACCEPTED)
    @router.post("/import-jobs", status_code=status.HTTP_202_ACCEPTED)
    def import_listings(body: ListingImportPayload, request: Request) -> dict[str, Any]:
        repository = _repository(request)
        result = ListingPipeline(repository=repository, geo_pipeline=_geo_pipeline(request)).import_records(
            body.records,
            source_id=body.source_id,
        )
        return result.to_dict()


    @router.get("/candidates")
    def list_candidate_sites(request: Request) -> dict[str, Any]:
        repository = _repository(request)
        return {"candidates": [candidate.to_card_dict() for candidate in repository.list_candidates()]}


    def _repository(request: Request) -> InMemoryListingRepository:
        repository = getattr(request.app.state, "listing_repository", None)
        if repository is None:
            repository = InMemoryListingRepository()
            request.app.state.listing_repository = repository
        return repository


    def _geo_pipeline(request: Request) -> GeoPipeline | None:
        return getattr(request.app.state, "listing_geo_pipeline", None)
