"""Data Platform Facade Adapter for Field Survey Evidence.

Contract: `odayplus.market-data-facade.v2` (requires), `emgi.field-survey.v1` (requires).
Task ID: `ODP-SURVEY-001`.

Connects the data platform read facade and generated product contract models to the
market survey application service, maintaining the boundary that platform survey observations
are ingested strictly as evidence requiring odayplus review and promotion.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from modules.external_data.application.market_data_facade import MarketDataFacade
from modules.market_survey.application.survey_service import MarketSurveyService
from modules.market_survey.domain.models import FieldSurveyEvidence
from packages.oday_data_product_contracts_client.models.field_survey import (
    FieldSurveyDocument,
    FieldSurveyObservation,
)
from shared.auth import Principal


class PlatformSurveyFacadeAdapter:
    """Adapter bridging MarketDataFacade / DataPlatformClient to MarketSurveyService."""

    def __init__(
        self,
        service: MarketSurveyService,
        facade: MarketDataFacade | None = None,
    ) -> None:
        self.service = service
        self.facade = facade

    def ingest_observation_payload(
        self,
        payload: Mapping[str, Any],
        *,
        tenant_id: str,
        correlation_id: str | None = None,
    ) -> FieldSurveyEvidence:
        """Parse wire payload via generated contract model and ingest as evidence."""
        observation_model = FieldSurveyObservation.from_dict(payload)
        return self.service.ingest_platform_observation(
            observation_model,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )

    def ingest_document_payload(
        self,
        payload: Mapping[str, Any],
        *,
        tenant_id: str,
        correlation_id: str | None = None,
    ) -> list[FieldSurveyEvidence]:
        """Parse document payload via generated contract model and ingest all evidence observations."""
        doc_model = FieldSurveyDocument.from_dict(payload)
        return self.service.ingest_platform_document(
            doc_model,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )

    def fetch_and_ingest_document(
        self,
        document_id: str | None = None,
        *,
        tenant_id: str,
        principal: Principal | None = None,
        correlation_id: str | None = None,
    ) -> list[FieldSurveyEvidence]:
        """Fetch FieldSurveyDocument from data platform client and ingest observations into odayplus."""
        if self.facade is None:
            raise ValueError("MarketDataFacade is required to fetch documents from data platform")

        raw = self.facade.client.transport.fetch_document(
            "emgi.field-survey.v1",
            document_id=document_id,
            params={"tenant_id": tenant_id},
        )
        if raw is None:
            return []

        doc_model = FieldSurveyDocument.from_dict(raw)
        return self.service.ingest_platform_document(
            doc_model,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )


__all__ = [
    "PlatformSurveyFacadeAdapter",
]
