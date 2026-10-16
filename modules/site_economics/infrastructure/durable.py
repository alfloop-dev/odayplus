"""Immutable economics documents in the existing PostgreSQL tenant partition.

Persistence is not financial admission. The producer and request owner must
separately establish current authorization and the trusted source/version chain
before these documents can enter SiteScore or NetPlan.
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import datetime
from functools import cache
from math import isfinite
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints
from uuid import UUID

from modules.site_economics.domain.contracts import (
    CONTRACT_VERSION,
    SiteEconomicsDocument,
    validate_site_economics_document,
)
from modules.site_economics.infrastructure.repositories import SiteEconomicsRepository
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore


class SiteEconomicsPersistenceError(RuntimeError):
    """An immutable identity, tenant owner or persisted document is invalid."""


@cache
def _contract_hints(model: type) -> dict[str, Any]:
    return get_type_hints(model)


def _validate_json(value: Any) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and isfinite(value):
        return
    if type(value) in (list, tuple):
        for item in value:
            _validate_json(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _validate_json(item)
        return
    raise ValueError("Metadata must contain finite JSON values and string keys")


def _validate_typed(value: Any, expected: Any) -> None:
    """Check the original contract objects without casting or repairing them."""
    if expected is Any:
        _validate_json(value)
        return
    origin, arguments = get_origin(expected), get_args(expected)
    if origin in (Union, UnionType):
        for branch in arguments:
            try:
                _validate_typed(value, branch)
                return
            except ValueError:
                continue
        raise ValueError("Value does not match any declared contract type")
    if origin is tuple and type(value) is tuple:
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            for item in value:
                _validate_typed(item, arguments[0])
            return
        if len(value) == len(arguments):
            for item, item_type in zip(value, arguments, strict=True):
                _validate_typed(item, item_type)
            return
    if origin is dict and type(value) is dict:
        for key, item in value.items():
            _validate_typed(key, arguments[0])
            _validate_typed(item, arguments[1])
        return
    if isinstance(expected, type) and is_dataclass(expected) and type(value) is expected:
        hints = _contract_hints(expected)
        for field in fields(expected):
            _validate_typed(getattr(value, field.name), hints[field.name])
        return
    if expected is float and type(value) in (int, float) and isfinite(value):
        return
    # Exact primitive and enum types exclude bool-as-int and string-as-enum.
    if expected in (str, bool, int, type(None)) and type(value) is expected:
        return
    if isinstance(expected, type) and type(value) is expected and not is_dataclass(expected):
        if expected not in (float, tuple, dict, list):
            return
    raise ValueError("Value does not match its declared contract type")


class DurableSiteEconomicsRepository(SiteEconomicsRepository):
    """Save once by document ID; order a site's versions by evaluation instant.

    Repeating identical content is idempotent. A second payload under the same
    ID is refused atomically rather than replacing the original financial facts.
    """

    COLLECTION = "site_economics.documents"

    def __init__(self, documents: TenantScopedDocumentStore) -> None:
        if (not isinstance(documents, TenantScopedDocumentStore)
                or getattr(documents.engine, "dialect", None) != "postgresql"):
            raise SiteEconomicsPersistenceError("SITE_ECONOMICS_DURABLE_OWNER_REQUIRED")
        try:
            UUID(documents.tenant_id)
        except (ValueError, TypeError, AttributeError):
            raise SiteEconomicsPersistenceError("SITE_ECONOMICS_TENANT_REQUIRED") from None
        self.documents = documents
        self.tenant_id = documents.tenant_id

    @staticmethod
    def _identity(value: Any) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise SiteEconomicsPersistenceError("SITE_ECONOMICS_IDENTITY_INVALID")
        return value

    @staticmethod
    def _payload(document: SiteEconomicsDocument) -> str:
        return json.dumps(
            document.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False,
        )

    @staticmethod
    def _instant(document: SiteEconomicsDocument) -> datetime:
        instant = datetime.fromisoformat(document.evaluated_at)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Evaluation time must include its offset")
        return instant

    def _validated(self, value: Any) -> SiteEconomicsDocument:
        try:
            _validate_typed(value, SiteEconomicsDocument)
            if (not isinstance(value, SiteEconomicsDocument)
                    or value.tenant_id != self.tenant_id
                    or value.contract_version != CONTRACT_VERSION):
                raise ValueError("Document owner or contract does not match")
            self._identity(value.document_id)
            self._identity(value.site_id)
            self._instant(value)
            validate_site_economics_document(value)
            # Pickle loading does not execute dataclass validation. Check the
            # original field types above, then the complete JSON representation.
            self._payload(value)
            return value
        except (AttributeError, TypeError, ValueError, OverflowError, RecursionError):
            raise SiteEconomicsPersistenceError("SITE_ECONOMICS_DOCUMENT_INVALID") from None

    def save(self, document: SiteEconomicsDocument) -> None:
        snapshot = self._validated(deepcopy(document))
        inserted = self.documents.put_if_absent(
            self.COLLECTION, snapshot.document_id, snapshot, group_key=snapshot.site_id,
        )
        if not inserted:
            existing = self.get_by_document_id(snapshot.document_id)
            if existing is None or self._payload(existing) != self._payload(snapshot):
                raise SiteEconomicsPersistenceError("SITE_ECONOMICS_IMMUTABLE_ID_CONFLICT")

    def get_by_document_id(self, document_id: str) -> SiteEconomicsDocument | None:
        key = self._identity(document_id)
        raw = self.documents.get(self.COLLECTION, key)
        if raw is None:
            return None
        document = self._validated(raw)
        if document.document_id != key:
            raise SiteEconomicsPersistenceError("SITE_ECONOMICS_STORED_IDENTITY_INVALID")
        return document

    def list_by_site_id(self, site_id: str) -> list[SiteEconomicsDocument]:
        key = self._identity(site_id)
        result = []
        for raw in self.documents.list_by_group(self.COLLECTION, key):
            document = self._validated(raw)
            current = self.get_by_document_id(document.document_id)
            if (document.site_id != key or current is None
                    or self._payload(current) != self._payload(document)):
                raise SiteEconomicsPersistenceError("SITE_ECONOMICS_STORED_IDENTITY_INVALID")
            result.append(current)
        if len({row.document_id for row in result}) != len(result):
            raise SiteEconomicsPersistenceError("SITE_ECONOMICS_STORED_IDENTITY_INVALID")
        return sorted(result, key=lambda row: (self._instant(row), row.document_id))

    def get_latest_by_site_id(self, site_id: str) -> SiteEconomicsDocument | None:
        documents = self.list_by_site_id(site_id)
        return documents[-1] if documents else None
