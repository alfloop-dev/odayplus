from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from io import StringIO
from typing import Any, Protocol

from modules.external_data.application.external_contracts import external_contract, validate_record
from modules.external_data.geo import GeoPipeline
from modules.integration.application.identity_resolution import InMemoryIdentityResolver
from modules.integration.application.mapping import SourceToCanonicalMapper
from modules.listing.domain.models import (
    CandidateSiteDraft,
    ListingDedupKey,
    ListingDuplicateGroup,
    ListingHardRulePolicy,
    ListingIssue,
    ListingPipelineStatus,
)
from modules.listing.infrastructure.repositories import InMemoryListingRepository
from shared.domain import AddressLocation, CandidateSite, Listing


class ListingRepository(Protocol):
    def has_duplicate(self, key: ListingDedupKey) -> bool:
        ...

    def save_listing(self, listing: Listing, address: AddressLocation, key: ListingDedupKey) -> None:
        ...

    def save_candidate(self, candidate: CandidateSiteDraft) -> None:
        ...

    def list_candidates(self) -> list[CandidateSiteDraft]:
        ...

    def list_listings(self) -> list[Listing]:
        ...

    def get_listing(self, listing_id: str) -> Listing | None:
        ...


@dataclass(frozen=True)
class ListingPipelineRecord:
    source_record: Mapping[str, Any]
    status: ListingPipelineStatus
    listing: Listing | None = None
    address: AddressLocation | None = None
    candidate_site: CandidateSiteDraft | None = None
    issues: tuple[ListingIssue, ...] = ()
    duplicate_group: ListingDuplicateGroup | None = None
    duplicate_key: str = ""
    field_lineage: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_record": dict(self.source_record),
            "status": self.status.value,
            "listing": asdict(self.listing) if self.listing else None,
            "address": asdict(self.address) if self.address else None,
            "candidate_site": self.candidate_site.to_card_dict() if self.candidate_site else None,
            "issues": [asdict(issue) for issue in self.issues],
            "duplicate_group": asdict(self.duplicate_group) if self.duplicate_group else None,
            "duplicate_key": self.duplicate_key,
            "field_lineage": list(self.field_lineage),
        }


@dataclass(frozen=True)
class ListingImportResult:
    records: tuple[ListingPipelineRecord, ...]
    imported_at: datetime
    source_system: str
    snapshot_id: str = ""

    @property
    def accepted_count(self) -> int:
        return sum(1 for record in self.records if record.status == ListingPipelineStatus.CANDIDATE)

    @property
    def duplicate_count(self) -> int:
        return sum(1 for record in self.records if record.status == ListingPipelineStatus.DUPLICATE)

    @property
    def rejected_count(self) -> int:
        return sum(
            1
            for record in self.records
            if record.status in {ListingPipelineStatus.FAILED_HARD_RULE, ListingPipelineStatus.RAW}
        )

    @property
    def candidates(self) -> tuple[CandidateSiteDraft, ...]:
        return tuple(record.candidate_site for record in self.records if record.candidate_site)

    @property
    def error_rows(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "row_index": index,
                "source_record": dict(record.source_record),
                "status": record.status.value,
                "issues": [asdict(issue) for issue in record.issues],
            }
            for index, record in enumerate(self.records, start=1)
            if record.issues and record.status != ListingPipelineStatus.DUPLICATE
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "imported_at": self.imported_at.isoformat(),
            "source_system": self.source_system,
            "snapshot_id": self.snapshot_id,
            "accepted_count": self.accepted_count,
            "duplicate_count": self.duplicate_count,
            "rejected_count": self.rejected_count,
            "records": [record.to_dict() for record in self.records],
            "candidates": [candidate.to_card_dict() for candidate in self.candidates],
            "error_rows": list(self.error_rows),
        }


class ListingPipeline:
    def __init__(
        self,
        *,
        repository: ListingRepository | None = None,
        geo_pipeline: GeoPipeline | None = None,
        policy: ListingHardRulePolicy | None = None,
        identity_resolver: InMemoryIdentityResolver | None = None,
        created_by: str = "listing-pipeline",
    ) -> None:
        self.contract = external_contract("listing_raw_snapshot")
        self.repository = repository or InMemoryListingRepository()
        self.geo_pipeline = geo_pipeline or GeoPipeline()
        self.policy = policy or ListingHardRulePolicy()
        self.mapper = SourceToCanonicalMapper(identity_resolver)
        self.created_by = created_by

    def import_records(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        source_id: str | None = None,
        imported_at: datetime | None = None,
    ) -> ListingImportResult:
        effective_source_id = source_id or self.contract.source_system
        now = imported_at or datetime.now(UTC)
        outputs: list[ListingPipelineRecord] = []
        seen_source_keys: set[str] = set()
        seen_property_keys: set[str] = set()
        snapshot_id = ""

        for record in records:
            if not snapshot_id and record.get("snapshot_id"):
                snapshot_id = str(record["snapshot_id"])
            output = self._process_record(
                record,
                source_id=effective_source_id,
                imported_at=now,
                seen_source_keys=seen_source_keys,
                seen_property_keys=seen_property_keys,
            )
            outputs.append(output)

        return ListingImportResult(
            records=tuple(outputs),
            imported_at=now,
            source_system=effective_source_id,
            snapshot_id=snapshot_id,
        )

    def import_csv(
        self,
        csv_text: str,
        *,
        source_id: str | None = None,
        imported_at: datetime | None = None,
    ) -> ListingImportResult:
        reader = csv.DictReader(StringIO(csv_text))
        return self.import_records(
            (_coerce_csv_record(row) for row in reader),
            source_id=source_id,
            imported_at=imported_at,
        )

    def _process_record(
        self,
        record: Mapping[str, Any],
        *,
        source_id: str,
        imported_at: datetime,
        seen_source_keys: set[str],
        seen_property_keys: set[str],
    ) -> ListingPipelineRecord:
        validation = validate_record(self.contract, dict(record))
        if not validation.ok:
            return ListingPipelineRecord(
                source_record=record,
                status=ListingPipelineStatus.RAW,
                issues=tuple(
                    ListingIssue(issue.code, issue.message, issue.field) for issue in validation.errors
                ),
            )

        mapping = self.mapper.map_record(
            "listing",
            _canonical_payload(record, source_id),
            source_id=source_id,
        )
        listing = mapping.canonical
        geocode = self.geo_pipeline.geocode_record(record, as_of=imported_at)
        address = geocode.address
        listing = _with_address_id(listing, address.address_id)
        key = ListingDedupKey(
            source_id=source_id,
            source_listing_id=listing.source_listing_id,
            normalized_address=address.normalized_address,
            rent_amount=listing.rent_amount,
            area_ping=listing.area_ping,
        )
        duplicate_group = _duplicate_group(
            key,
            seen_source_keys=seen_source_keys,
            seen_property_keys=seen_property_keys,
            repository=self.repository,
        )
        if duplicate_group:
            return ListingPipelineRecord(
                source_record=record,
                status=ListingPipelineStatus.DUPLICATE,
                listing=listing,
                address=address,
                issues=(ListingIssue("duplicate_listing", "listing matched an existing source or property key"),),
                duplicate_group=duplicate_group,
                duplicate_key=duplicate_group.duplicate_key,
                field_lineage=tuple(asdict(item) for item in mapping.field_lineage),
            )

        seen_source_keys.add(key.source_key)
        seen_property_keys.add(key.property_key)
        self.repository.save_listing(listing, address, key)
        failures = self.policy.evaluate(listing, address)
        if failures:
            return ListingPipelineRecord(
                source_record=record,
                status=ListingPipelineStatus.FAILED_HARD_RULE,
                listing=listing,
                address=address,
                issues=tuple(ListingIssue(code, f"hard rule failed: {code}") for code in failures),
                field_lineage=tuple(asdict(item) for item in mapping.field_lineage),
            )

        candidate_site = CandidateSite(
            listing_id=listing.listing_id,
            address_id=address.address_id,
            target_format_code=self.policy.target_format_code,
            created_by=self.created_by,
            created_at=imported_at,
        )
        candidate = CandidateSiteDraft(
            listing=listing,
            address=address,
            candidate_site=candidate_site,
            feasibility_flags=(),
            heat_zone_id=address.h3_res_9,
            listing_source=source_id,
        )
        self.repository.save_candidate(candidate)
        return ListingPipelineRecord(
            source_record=record,
            status=ListingPipelineStatus.CANDIDATE,
            listing=listing,
            address=address,
            candidate_site=candidate,
            field_lineage=tuple(asdict(item) for item in mapping.field_lineage),
        )


def run_listing_import(
    records: Iterable[Mapping[str, Any]],
    *,
    source_id: str | None = None,
    repository: ListingRepository | None = None,
    geo_pipeline: GeoPipeline | None = None,
    policy: ListingHardRulePolicy | None = None,
    imported_at: datetime | None = None,
) -> ListingImportResult:
    return ListingPipeline(
        repository=repository,
        geo_pipeline=geo_pipeline,
        policy=policy,
    ).import_records(records, source_id=source_id, imported_at=imported_at)


def run_listing_csv_import(
    csv_text: str,
    *,
    source_id: str | None = None,
    repository: ListingRepository | None = None,
    geo_pipeline: GeoPipeline | None = None,
    policy: ListingHardRulePolicy | None = None,
    imported_at: datetime | None = None,
) -> ListingImportResult:
    return ListingPipeline(
        repository=repository,
        geo_pipeline=geo_pipeline,
        policy=policy,
    ).import_csv(csv_text, source_id=source_id, imported_at=imported_at)


def _canonical_payload(record: Mapping[str, Any], source_id: str) -> dict[str, Any]:
    payload = dict(record)
    payload["source_id"] = source_id
    payload.setdefault("currency", "TWD")
    if "available_from" in payload and payload["available_from"] not in (None, ""):
        payload["available_from"] = date.fromisoformat(str(payload["available_from"]))
    for boolean_field in (
        "corner_flag",
        "parking_flag",
        "utility_electricity_flag",
        "utility_drainage_flag",
        "utility_gas_flag",
    ):
        if boolean_field in payload:
            payload[boolean_field] = _parse_bool(payload[boolean_field])
    return payload


def _coerce_csv_record(row: Mapping[str, Any]) -> dict[str, Any]:
    record = {key: value for key, value in row.items() if key and value not in (None, "")}
    for numeric_field in ("rent_amount", "area_ping", "frontage_m", "depth_m", "confidence"):
        if numeric_field in record:
            record[numeric_field] = float(record[numeric_field])
    return record


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "有"}
    return bool(value)


def _with_address_id(listing: Listing, address_id: str) -> Listing:
    values = asdict(listing)
    values["address_id"] = address_id
    return Listing(**values)


def _duplicate_group(
    key: ListingDedupKey,
    *,
    seen_source_keys: set[str],
    seen_property_keys: set[str],
    repository: ListingRepository,
) -> ListingDuplicateGroup | None:
    if key.source_key in seen_source_keys:
        return ListingDuplicateGroup(
            duplicate_group_id=f"dup:{key.source_key}",
            match_strategy="source_key",
            confidence=1.0,
            duplicate_key=key.source_key,
        )
    if key.property_key in seen_property_keys:
        return ListingDuplicateGroup(
            duplicate_group_id=f"dup:{key.property_key}",
            match_strategy="normalized_address_rent_area",
            confidence=0.88,
            duplicate_key=key.property_key,
        )
    if repository.has_duplicate(key):
        return ListingDuplicateGroup(
            duplicate_group_id=f"dup:{key.source_key}",
            match_strategy="repository_existing",
            confidence=0.95,
            duplicate_key=key.source_key,
        )
    return None
