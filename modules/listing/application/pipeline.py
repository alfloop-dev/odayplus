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

    def get_address(self, address_id: str) -> AddressLocation | None:
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
            dataset_snapshot_id=listing.snapshot_id or None,
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


NATIVE_GEO_REQUESTED_ACTION = "intake.geocode_requested.v1"
NATIVE_GEO_CHECKPOINT = "GEOCODING"


class NativeIntakeGeoOwner:
    """The single native Intake Geo evidence owner in the original Listing pipeline.

    Reservation runs only inside the existing identity execution unit that
    moves a native Intake to READY (fresh CREATE/REVISE and exact-existing
    DUPLICATE). Execution later reuses ``GeoPipeline`` with one LIVE lookup.
    """

    def __init__(self, graph: Any) -> None:
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue

    def _clock(self) -> datetime:
        value = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
        value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if value.utcoffset() is None:
            raise ValueError("NATIVE_GEO_TIME_UNAVAILABLE")
        return value

    def native_intake(self, tenant: str, intake: Mapping[str, Any]) -> bool:
        """True only for an Intake the native start producer committed."""
        return self.engine.query_one(
            "SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_type='native-intake-start' "
            "AND aggregate_type='intake' AND aggregate_id=? AND idempotency_key=? AND status='SUCCEEDED'",
            (tenant, intake["intake_id"], intake["intake_id"]),
        ) is not None

    @staticmethod
    def job_id(decision_id: str) -> str:
        from uuid import UUID, uuid5

        from modules.listing.application.native_intake_retrieval_policy import GEO_JOB_TYPE

        return str(uuid5(UUID(decision_id), GEO_JOB_TYPE))

    def _admission(self, tenant: str, intake: Mapping[str, Any], listing_id: str,
                   listing_revision_id: str | None, job_id: str) -> tuple[Any, str, dict, str]:
        """Prove every pre-reservation input; refusals are NO_JOB_UNADMITTED."""
        import hashlib
        import json

        from modules.external_data.application.assisted_intake import normalize_address
        from modules.listing.application.native_intake_identity import NativeIntakeIdentityOwner
        from modules.listing.application.native_intake_retrieval_policy import (
            NativeIntakeGeoAuthenticator,
            NativeIntakeGeoUnadmitted,
            native_intake_geo_policy,
        )

        policy = native_intake_geo_policy(tenant)
        token = NativeIntakeGeoAuthenticator.sign(job_id, tenant, 1)
        if listing_revision_id is None:
            raise NativeIntakeGeoUnadmitted("NATIVE_GEO_LISTING_REVISION_UNAVAILABLE")
        source = self.engine.query_one("SELECT * FROM intake.source_registry WHERE source_id=? FOR SHARE",
                                       (policy.source.source_id,))
        # The Geo provider grant is its own registered source; the listing
        # source's retrieval approval is never reused for it.
        if (source is None or source["version"] != policy.source.source_policy_version
                or not NativeIntakeIdentityOwner._source_reusable(source, self._clock())):
            raise NativeIntakeGeoUnadmitted("NATIVE_GEO_SOURCE_NOT_ADMITTED")
        revision = self.engine.query_one(
            "SELECT effective_values FROM expansion.listing_revisions WHERE tenant_id=? AND listing_id=? "
            "AND listing_revision_id=?", (tenant, listing_id, listing_revision_id))
        if revision is None:
            raise LookupError("NATIVE_GEO_LISTING_REVISION_MISSING")
        values = revision["effective_values"]
        values = json.loads(values) if isinstance(values, str) else values
        address = normalize_address(values.get("address") if isinstance(values, dict) else None)
        if not address:
            raise NativeIntakeGeoUnadmitted("NATIVE_GEO_ADDRESS_UNAVAILABLE")
        return policy, token, source, hashlib.sha256(address.encode()).hexdigest()

    def reserve_in_identity_unit(self, tenant: str, intake: Mapping[str, Any], *, decision_id: str,
                                 identity_job_id: str, listing_id: str, listing_version: int,
                                 listing_revision_id: str | None, property_id: str, property_version: int,
                                 intake_version: int, source_snapshot_id: str | None,
                                 parser_run_id: str | None) -> dict | None:
        """Reserve one purpose-bound Geo job in the caller's open identity unit.

        Returns ``None`` without any Geo write for a non-native Intake or a
        typed pre-reservation ``NativeIntakeGeoUnadmitted`` refusal. Every
        later SQL/queue/audit failure propagates and rolls back the caller.
        """
        import hashlib
        import json
        from datetime import timedelta
        from uuid import uuid4

        from modules.listing.application.native_intake_retrieval_policy import (
            GEO_JOB_TYPE,
            NativeIntakeGeoUnadmitted,
        )
        from shared.jobs.queue import JobRequest

        if not self.native_intake(tenant, intake):
            return None
        job_id = self.job_id(decision_id)
        try:
            policy, token, source, address_sha256 = self._admission(
                tenant, intake, listing_id, listing_revision_id, job_id)
        except NativeIntakeGeoUnadmitted:
            return None
        at = self._clock()
        audit_id = str(uuid4())
        occurrence = intake["intake_id"] + ":" + decision_id
        receipt = {"job_id": job_id, "intake_id": intake["intake_id"], "admitted_intake_version": intake_version,
                   "job_status_at_admission": "QUEUED",
                   "timeout_at": (at + timedelta(seconds=policy.deadline_seconds)).isoformat(),
                   "identity_decision_id": decision_id, "audit_event_id": audit_id}
        payload = {"decision_id": decision_id, "identity_job_id": identity_job_id,
                   "intake_version": intake_version, "listing_id": listing_id, "listing_version": listing_version,
                   "listing_revision_id": listing_revision_id, "property_id": property_id,
                   "property_version": property_version, "source_snapshot_id": source_snapshot_id,
                   "parser_run_id": parser_run_id, "address_sha256": address_sha256,
                   "geo_source_version": source["version"], "policy": policy.snapshot(),
                   "policy_sha256": policy.sha256, "delivery_generation": 1,
                   "attempt_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                   "admission_receipt": receipt, "attempt_intents": []}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.engine.execute("INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
            "status,checkpoint,attempt,max_attempts,fence_token,timeout_at,payload,correlation_id,idempotency_key) "
            "VALUES (?,?,?,'intake',?,'QUEUED',?,0,?,1,?,?::jsonb,?,?)",
            (job_id, tenant, GEO_JOB_TYPE, intake["intake_id"], NATIVE_GEO_CHECKPOINT, policy.max_attempts,
             receipt["timeout_at"], encoded, intake["correlation_id"], occurrence))
        physical, created = self.queue.enqueue(JobRequest(GEO_JOB_TYPE,
            {"job_id": job_id, "tenant_id": tenant, "attempt_token": token},
            idempotency_key=GEO_JOB_TYPE + ":" + tenant + ":" + occurrence, job_id=job_id),
            correlation_id=intake["correlation_id"])
        if not created or physical.job_id != job_id:
            raise ValueError("NATIVE_GEO_DELIVERY_RESERVATION_CONFLICT")
        self.engine.execute("UPDATE durable_jobs SET max_retries=? WHERE job_id=?", (policy.max_attempts, job_id))
        self.graph.audit_sink.persist({
            "audit_event_id": audit_id, "tenant_id": tenant, "resource_type": "intake",
            "resource_id": intake["intake_id"], "action": NATIVE_GEO_REQUESTED_ACTION, "result": "SUCCEEDED",
            "actor_subject_id": None, "service_principal": GEO_JOB_TYPE, "actor_role": "SVC_INTAKE",
            "correlation_id": intake["correlation_id"], "causation_id": decision_id,
            "occurred_at": at.isoformat(), "before_value": None,
            "after_value": {**receipt, "native_geo_provenance": {
                key: payload[key] for key in ("listing_id", "listing_version", "listing_revision_id",
                    "property_id", "property_version", "address_sha256", "geo_source_version", "policy_sha256")}},
            "reason": "Reserve native Geo evidence for the committed identity result.",
        }, {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
        return receipt
