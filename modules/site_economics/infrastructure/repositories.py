"""Infrastructure persistence repositories for SiteEconomics documents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from modules.site_economics.domain.contracts import SiteEconomicsDocument


class SiteEconomicsRepository(ABC):
    """Abstract repository for saving and querying SiteEconomics documents."""

    @abstractmethod
    def save(self, document: SiteEconomicsDocument) -> None:
        """Save a site economics document."""

    @abstractmethod
    def get_by_document_id(self, document_id: str) -> SiteEconomicsDocument | None:
        """Retrieve by document ID."""

    @abstractmethod
    def get_latest_by_site_id(self, site_id: str) -> SiteEconomicsDocument | None:
        """Retrieve latest document for a site ID."""

    @abstractmethod
    def list_by_site_id(self, site_id: str) -> Sequence[SiteEconomicsDocument]:
        """List all document versions for a site ID."""


class InMemorySiteEconomicsRepository(SiteEconomicsRepository):
    """In-memory store for test and fast evaluation workflows."""

    def __init__(self) -> None:
        self._docs_by_id: dict[str, SiteEconomicsDocument] = {}
        self._docs_by_site: dict[str, list[SiteEconomicsDocument]] = {}

    def save(self, document: SiteEconomicsDocument) -> None:
        self._docs_by_id[document.document_id] = document
        if document.site_id not in self._docs_by_site:
            self._docs_by_site[document.site_id] = []
        self._docs_by_site[document.site_id].append(document)

    def get_by_document_id(self, document_id: str) -> SiteEconomicsDocument | None:
        return self._docs_by_id.get(document_id)

    def get_latest_by_site_id(self, site_id: str) -> SiteEconomicsDocument | None:
        docs = self._docs_by_site.get(site_id, [])
        if not docs:
            return None
        return docs[-1]

    def list_by_site_id(self, site_id: str) -> Sequence[SiteEconomicsDocument]:
        return list(self._docs_by_site.get(site_id, []))
