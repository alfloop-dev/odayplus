"""Immutable receipt-version matches for private listing search conditions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from modules.listing.application.saved_search import SavedListingCriteria, receipt_matching_facts
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore


class ListingSearchMatchConflict(RuntimeError):
    """One receipt/search version was reused with different matching facts."""


def evidence_digest(value: Any) -> str:
    """Canonical typed digest: bool/int/float and missing/null stay distinct.

    Non-finite numeric observations remain unknown to the matcher but still
    have a stable identity, so an unchanged outcome cannot hide fact changes.
    """

    def typed(item):
        if item is None:
            return ["null"]
        if type(item) is bool:
            return ["bool", item]
        if type(item) is int:
            return ["int", str(item)]
        if type(item) is float:
            return ["float", item.hex()]
        if isinstance(item, str):
            return ["str", item]
        if isinstance(item, list):
            return ["list", [typed(v) for v in item]]
        if isinstance(item, dict) and all(isinstance(k, str) for k in item):
            return ["dict", [[k, typed(item[k])] for k in sorted(item)]]
        from datetime import date
        from decimal import Decimal
        from uuid import UUID

        if isinstance(item, (Decimal, date, UUID)):
            return [type(item).__qualname__, str(item)]
        raise ValueError("LISTING_SEARCH_UNSUPPORTED_EVIDENCE_TYPE")

    encoded = json.dumps(typed(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


class ListingSearchMatchStore:
    COLLECTION = "listing.saved_search_receipt_matches"

    def __init__(self, documents: TenantScopedDocumentStore):
        if not isinstance(documents, TenantScopedDocumentStore):
            raise ValueError("LISTING_SEARCH_TENANT_STORE_REQUIRED")
        self.documents = documents
        self.tenant_id = documents.tenant_id

    @staticmethod
    def _group(search_id: str, version: int) -> str:
        return f"{search_id}:{version}"

    def reconcile(
        self,
        *,
        searches: list[dict[str, Any]],
        receipts: list[dict[str, Any]],
        changed_search_ids: set[str],
        changed_receipt_ids: set[str],
    ) -> None:
        # Only the post-commit worker holds this ledger's tenant fence. Intake
        # producers never take it and the worker never locks intake parents.
        for search in searches:
            criteria = SavedListingCriteria.from_query(search.get("query"))
            if criteria is None:
                continue
            search_id, version = search.get("saved_view_id"), search.get("version")
            if (
                search.get("tenant_id") != self.tenant_id
                or not isinstance(search_id, str)
                or not search_id
                or type(version) is not int
                or version < 1
                or not search.get("owner_subject_id")
                or search.get("visibility") != "PRIVATE"
            ):
                raise ValueError("LISTING_SEARCH_PRIVATE_OWNER_REQUIRED")
            criteria_hash = hashlib.sha256(
                json.dumps(
                    criteria.to_query(), sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            ).hexdigest()
            for receipt in receipts:
                identity, revision = receipt.get("intake_id"), receipt.get("version")
                if search_id not in changed_search_ids and identity not in changed_receipt_ids:
                    continue
                if (
                    not isinstance(identity, str)
                    or not identity
                    or type(revision) is not int
                    or revision < 1
                ):
                    raise ValueError("LISTING_SEARCH_RECEIPT_REVISION_REQUIRED")
                if (receipt.get("scope") or {}).get("tenant_id") != self.tenant_id:
                    raise ValueError("LISTING_SEARCH_RECEIPT_TENANT_REQUIRED")
                identity_key = json.dumps(
                    [search_id, version, identity, revision], separators=(",", ":")
                )
                match_id = hashlib.sha256(identity_key.encode()).hexdigest()
                value = {
                    "id": match_id,
                    "tenantId": self.tenant_id,
                    "searchId": search_id,
                    "searchVersion": version,
                    "ownerSubjectId": search["owner_subject_id"],
                    "criteriaSha256": criteria_hash,
                    "intakeId": identity,
                    "intakeVersion": revision,
                    "receiptFactsSha256": evidence_digest(receipt_matching_facts(receipt)),
                }
                # Identity and complete evidence are immutable. A newer evaluator
                # may reject an old MATCH (e.g. unknown rent currency); that does
                # not make an unchanged pair a reused version. Preserve its first
                # disposition; current readers still recheck facts and permissions.
                existing = self.documents.get(self.COLLECTION, match_id)
                if existing is not None:
                    if not isinstance(existing, dict) or any(
                        existing.get(k) != v for k, v in value.items()
                    ):
                        raise ListingSearchMatchConflict("LISTING_SEARCH_MATCH_VERSION_REUSED")
                    continue
                result = criteria.evaluate(receipt, tenant_id=self.tenant_id)
                recorded = {
                    **value,
                    "outcome": result.outcome,
                    "reasonCodes": list(result.reasons),
                    "recordedAt": datetime.now(UTC).isoformat(),
                }
                created = self.documents.put_if_absent(
                    self.COLLECTION, match_id, recorded, group_key=self._group(search_id, version)
                )
                if not created:
                    existing = self.documents.get(self.COLLECTION, match_id)
                    if not isinstance(existing, dict) or any(
                        existing.get(k) != v for k, v in value.items()
                    ):
                        raise ListingSearchMatchConflict("LISTING_SEARCH_MATCH_VERSION_REUSED")

    def history(self, *, search_id: str, search_version: int) -> list[dict[str, Any]]:
        # This is an internal storage read. HTTP consumers must first verify
        # the current saved-view owner and each linked receipt's current scope,
        # fields and version. Historical hits do not retain revoked access.
        return self.documents.list_by_group(self.COLLECTION, self._group(search_id, search_version))
