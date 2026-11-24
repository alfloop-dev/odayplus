from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from functools import cache
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
NATIVE_GEO_COMPLETED_ACTION = "intake.geocode_completed.v1"
NATIVE_GEO_SETTLED_ACTION = "intake.geocode_delivery_settled.v1"
NATIVE_GEO_CHECKPOINT = "GEOCODING"
NATIVE_GEO_DONE_CHECKPOINT = "GEOCODED"
NATIVE_GEO_MEDIA_TYPE = "application/json"
NATIVE_GEO_ACTOR_ROLE = "SVC_INTAKE"
NATIVE_GEO_ORIGIN_KINDS = frozenset({"IDENTITY_DECISION", "NATIVE_EXACT_URL"})


@dataclass(frozen=True)
class NativeIntakeGeoProvider:
    """The existing LIVE geocode adapter, injected by the persistence factory.

    Listing never imports the frozen provider packages itself: the factory
    hands over the original mode resolver, endpoint variable, client,
    provider, response and error types unchanged. Without this injection the
    owner is unadmitted, so no configuration can forge production geodata.
    """

    provider_mode: Any
    live_mode: Any
    endpoint_env_var: str
    client_type: Any
    provider_type: Any
    response_type: Any
    auth_error: type[BaseException]
    provider_error: type[BaseException]
    rate_limit_error: type[BaseException]
    timeout_error: type[BaseException]


class NativeIntakeGeoOwner:
    """The single native Intake Geo evidence owner in the original Listing pipeline.

    Reservation runs only inside the existing identity execution unit that
    moves a native Intake to READY (fresh CREATE/REVISE and exact-existing
    DUPLICATE). Execution later reuses ``GeoPipeline`` with one LIVE lookup.
    """

    def __init__(self, graph: Any, *, geocode_runner: Any = None, snapshot_transport_factory: Any = None,
                 env: Mapping[str, str] | None = None) -> None:
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        # Only the factory-composed identity graph carries the LIVE adapter.
        self.geo_provider: NativeIntakeGeoProvider | None = getattr(graph, "geo_provider", None)
        # Component tests may pass an owned synthetic child runner/storage. The
        # runtime never selects a child, address or transport by configuration.
        self.geocode_runner = geocode_runner
        self.snapshot_transport_factory = snapshot_transport_factory
        self.http_transport = None
        self.env = env

    @property
    def auth(self) -> Any:
        from modules.listing.application.native_intake_retrieval_policy import (
            NativeIntakeGeoAuthenticator,
        )

        return NativeIntakeGeoAuthenticator()

    @property
    def quota(self) -> Any:
        from modules.listing.application.native_intake_retrieval_quota import (
            NativeIntakeRetrievalQuota,
        )

        return NativeIntakeRetrievalQuota(self.engine)

    def _clock(self) -> datetime:
        value = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
        value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if value.utcoffset() is None:
            raise ValueError("NATIVE_GEO_TIME_UNAVAILABLE")
        return value

    def live_endpoint(self) -> str:
        """Only an explicit LIVE provider with an HTTPS endpoint may produce geodata.

        Fixture, disabled and unknown modes are never production-geodata ready.
        """
        import os
        from urllib.parse import urlsplit

        from modules.listing.application.native_intake_retrieval_policy import (
            NativeIntakeGeoUnadmitted,
        )

        env = self.env if self.env is not None else os.environ
        provider = self.geo_provider
        if provider is None or provider.provider_mode(env) is not provider.live_mode:
            raise NativeIntakeGeoUnadmitted("NATIVE_GEO_PROVIDER_NOT_LIVE")
        endpoint = str(env.get(provider.endpoint_env_var, "")).strip()
        try:
            parts = urlsplit(endpoint)
            valid = (parts.scheme == "https" and bool(parts.hostname) and parts.port in (None, 443)
                     and not parts.username and not parts.password and not parts.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise NativeIntakeGeoUnadmitted("NATIVE_GEO_PROVIDER_ENDPOINT_UNAVAILABLE")
        return endpoint

    def native_intake(self, tenant: str, intake: Mapping[str, Any]) -> bool:
        """True only for an Intake the native start producer committed."""
        return self.engine.query_one(
            "SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_type='native-intake-start' "
            "AND aggregate_type='intake' AND aggregate_id=? AND idempotency_key=? AND status='SUCCEEDED'",
            (tenant, intake["intake_id"], intake["intake_id"]),
        ) is not None

    @staticmethod
    def job_id(origin_id: str) -> str:
        from uuid import UUID, uuid5

        from modules.listing.application.native_intake_retrieval_policy import GEO_JOB_TYPE

        return str(uuid5(UUID(origin_id), GEO_JOB_TYPE))

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
        self.live_endpoint()
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

    def reserve_in_identity_unit(self, tenant: str, intake: Mapping[str, Any], *, origin_kind: str,
                                 origin_id: str, ready_transition_id: str,
                                 identity_job_id: str, listing_id: str, listing_version: int,
                                 listing_revision_id: str | None, property_id: str, property_version: int,
                                 intake_version: int, source_snapshot_id: str | None,
                                 parser_run_id: str | None) -> dict | None:
        """Reserve one purpose-bound Geo job in the caller's open identity unit.

        Returns ``None`` without any Geo write for a non-native Intake or a
        typed pre-reservation ``NativeIntakeGeoUnadmitted`` refusal. Every
        later SQL/queue/audit failure propagates and rolls back the caller.

        Two READY producers call this: the identity decision execution unit
        (``IDENTITY_DECISION``: fresh CREATE/REVISE and exact-existing
        DUPLICATE) and the native identity phase's exact canonical-URL match
        (``NATIVE_EXACT_URL``). ``origin_id`` is that unit's idempotency key
        and ``ready_transition_id`` its committed READY transition.
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

        if origin_kind not in NATIVE_GEO_ORIGIN_KINDS:
            raise ValueError("NATIVE_GEO_ORIGIN_KIND_REJECTED")
        if not self.native_intake(tenant, intake):
            return None
        job_id = self.job_id(origin_id)
        try:
            policy, token, source, address_sha256 = self._admission(
                tenant, intake, listing_id, listing_revision_id, job_id)
        except NativeIntakeGeoUnadmitted:
            return None
        at = self._clock()
        audit_id = str(uuid4())
        occurrence = intake["intake_id"] + ":" + origin_id
        receipt = {"job_id": job_id, "intake_id": intake["intake_id"], "admitted_intake_version": intake_version,
                   "job_status_at_admission": "QUEUED",
                   "timeout_at": (at + timedelta(seconds=policy.deadline_seconds)).isoformat(),
                   "identity_origin_id": origin_id, "audit_event_id": audit_id}
        payload = {"origin_kind": origin_kind, "origin_id": origin_id, "ready_transition_id": ready_transition_id,
                   "identity_job_id": identity_job_id,
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
            "actor_subject_id": None, "service_principal": GEO_JOB_TYPE, "actor_role": NATIVE_GEO_ACTOR_ROLE,
            "correlation_id": intake["correlation_id"], "causation_id": origin_id,
            "occurred_at": at.isoformat(), "before_value": None,
            "after_value": {**receipt, "native_geo_provenance": self._provenance(payload)},
            "reason": "Reserve native Geo evidence for the committed identity result.",
        }, {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
        return receipt

    # ------------------------------------------------------------------
    # Execution: one purpose-bound Geo job handled by the normal ODayWorker.
    # Every SQL root revalidates the committed parent/source/version binding;
    # IO (one LIVE provider exchange plus the GEOCODE JSON object) never runs
    # inside an SQL root and is journaled by the shared native IO intents.
    # ------------------------------------------------------------------

    def _admission_budget(self, bound: datetime) -> None:
        import math

        milliseconds = math.floor((bound - self._clock()).total_seconds() * 1000) - 1
        if milliseconds < 1:
            from shared.jobs.queue import JobFenceRejectedError

            raise JobFenceRejectedError("NATIVE_GEO_ROOT_BUDGET_EXPIRED")
        value = str(milliseconds) + "ms"
        self.engine.query_one("SELECT set_config('lock_timeout',?,true), "
                              "set_config('statement_timeout',?,true)", (value, value))

    def _audit(self, intake: Mapping[str, Any], action: str, before: Any, after: Any, at: datetime, *,
               audit_id: str, causation: str) -> None:
        from modules.listing.application.native_intake_retrieval_policy import GEO_JOB_TYPE

        self.graph.audit_sink.persist({
            "audit_event_id": audit_id, "tenant_id": intake["tenant_id"], "resource_type": "intake",
            "resource_id": intake["intake_id"], "action": action, "result": "SUCCEEDED",
            "actor_subject_id": None, "service_principal": GEO_JOB_TYPE, "actor_role": NATIVE_GEO_ACTOR_ROLE,
            "correlation_id": intake["correlation_id"], "causation_id": causation,
            "occurred_at": at.isoformat(), "before_value": before, "after_value": after,
            "reason": "Record native Geo evidence for the committed identity result.",
        }, {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})

    @staticmethod
    def _provenance(payload: Mapping[str, Any]) -> dict:
        return {key: payload[key] for key in ("origin_kind", "ready_transition_id", "listing_id", "listing_version",
                                              "listing_revision_id", "property_id", "property_version",
                                              "address_sha256", "geo_source_version", "policy_sha256")}

    @staticmethod
    def _object(value: Any) -> dict:
        import json

        from shared.jobs.queue import JobFenceRejectedError

        result = json.loads(value) if isinstance(value, str) else value
        if not isinstance(result, dict):
            raise JobFenceRejectedError("NATIVE_GEO_OBJECT_REJECTED")
        return result

    @staticmethod
    def _time(value: Any) -> datetime:
        from shared.jobs.queue import JobFenceRejectedError

        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise JobFenceRejectedError("NATIVE_GEO_TIME_UNAVAILABLE")
        return result

    def _current_policy_sha256(self, tenant: str) -> str | None:
        from modules.listing.application.native_intake_retrieval_policy import (
            NativeIntakeGeoUnadmitted,
            native_intake_geo_policy,
        )

        try:
            return native_intake_geo_policy(tenant).sha256
        except NativeIntakeGeoUnadmitted:
            return None

    def _revision_address(self, tenant: str, listing_id: str, revision_id: str) -> str:
        import json

        from modules.external_data.application.assisted_intake import normalize_address

        revision = self.engine.query_one(
            "SELECT effective_values FROM expansion.listing_revisions WHERE tenant_id=? AND listing_id=? "
            "AND listing_revision_id=? FOR SHARE", (tenant, listing_id, revision_id))
        values = None if revision is None else revision["effective_values"]
        values = json.loads(values) if isinstance(values, str) else values
        return normalize_address(values.get("address") if isinstance(values, dict) else None) or ""

    def _receipt(self, job: Mapping[str, Any], payload: Mapping[str, Any], before: Any) -> dict:
        from modules.listing.application.native_intake_retrieval import audited_value_matches
        from modules.listing.application.native_intake_retrieval_policy import GEO_JOB_TYPE
        from shared.jobs.queue import JobFenceRejectedError

        receipt = self._object(payload["admission_receipt"])
        if (set(receipt) != {"job_id", "intake_id", "admitted_intake_version", "job_status_at_admission",
                             "timeout_at", "identity_origin_id", "audit_event_id"}
                or receipt["job_id"] != job["job_id"] or receipt["intake_id"] != job["aggregate_id"]
                or receipt["admitted_intake_version"] != payload["intake_version"]
                or receipt["job_status_at_admission"] != "QUEUED"
                or receipt["identity_origin_id"] != payload["origin_id"]
                or self._time(receipt["timeout_at"]) != self._time(job["timeout_at"])):
            raise JobFenceRejectedError("NATIVE_GEO_ADMISSION_RECEIPT_REJECTED")
        before()
        audit = self.engine.query_one(
            "SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role=? AND action=? AND result='SUCCEEDED' AND causation_id=? AND correlation_id=?",
            (job["tenant_id"], receipt["audit_event_id"], job["aggregate_id"], GEO_JOB_TYPE, NATIVE_GEO_ACTOR_ROLE,
             NATIVE_GEO_REQUESTED_ACTION, payload["origin_id"], job["correlation_id"]))
        if audit is None or not audited_value_matches(
                self._object(audit["after_value"]), {**receipt, "native_geo_provenance": self._provenance(payload)}):
            raise JobFenceRejectedError("NATIVE_GEO_ADMISSION_AUDIT_REJECTED")
        before()
        return receipt

    def _locked(self, delivery: Any, tenant: str, generation: int, *, settlement: bool = False,
                business_fence: int | None = None) -> tuple:
        import hashlib
        from datetime import timedelta

        from modules.listing.application.native_intake_job_runtime import (
            set_native_intake_sql_budget,
        )
        from modules.listing.application.native_intake_retrieval_policy import (
            GEO_JOB_TYPE,
            canonical_sha256,
        )
        from shared.jobs.queue import JobFenceRejectedError, JobStatus

        if self.auth.decode(delivery) != (tenant, generation):
            raise JobFenceRejectedError("NATIVE_GEO_CAPABILITY_BINDING_REJECTED")
        self.engine.query_one("SELECT set_config('statement_timeout',?,true)", ("750ms",))
        self.engine.query_one("SELECT set_config('lock_timeout',?,true)", ("750ms",))
        entry = self._clock()
        preliminary = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                      (tenant, delivery.job_id))
        peek = self.queue.get(delivery.job_id)
        if peek is None or peek.lease_expires_at is None:
            raise JobFenceRejectedError("NATIVE_GEO_PHYSICAL_LEASE_MISSING")
        # Fixed root budget: protective settlement may outlive the total work
        # timeout, never either actual lease. Positive roots keep the timeout.
        bound = min(entry + timedelta(milliseconds=750), peek.lease_expires_at)
        if preliminary["status"] == "RUNNING":
            if preliminary["lease_expires_at"] is None:
                raise JobFenceRejectedError("NATIVE_GEO_BUSINESS_LEASE_MISSING")
            bound = min(bound, self._time(preliminary["lease_expires_at"]))
        if not settlement:
            bound = min(bound, self._time(preliminary["timeout_at"]))

        def before() -> None:
            set_native_intake_sql_budget(self.engine, bound, bound, self._clock(),
                                         error_code="NATIVE_GEO_ROOT_BUDGET_EXPIRED")

        before()
        self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (delivery.job_id,))
        current = self.queue.get(delivery.job_id)
        before()
        job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
                              (tenant, delivery.job_id))
        payload = self._object(job["payload"])
        now = self._clock()
        if (current is None or current.status != JobStatus.RUNNING or current.job_type != GEO_JOB_TYPE
                or not current.locked_by or current.locked_by != delivery.locked_by
                or current.fence_token != delivery.fence_token or current.payload != delivery.payload
                or current.correlation_id != delivery.correlation_id
                or current.lease_expires_at is None or current.lease_expires_at <= now
                or job["job_type"] != GEO_JOB_TYPE or job["aggregate_type"] != "intake"
                or job["idempotency_key"] != job["aggregate_id"] + ":" + payload.get("origin_id", "")
                or payload.get("origin_kind") not in NATIVE_GEO_ORIGIN_KINDS
                or job["job_id"] != self.job_id(payload["origin_id"])
                or job["correlation_id"] != current.correlation_id
                or type(payload.get("delivery_generation")) is not int or payload["delivery_generation"] != generation
                or payload.get("attempt_token_sha256")
                != hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest()
                or payload.get("policy_sha256") != canonical_sha256(payload["policy"])
                or payload["policy_sha256"] != self._current_policy_sha256(tenant)):
            raise JobFenceRejectedError("NATIVE_GEO_COMMAND_NOT_OWNED")
        if job["status"] == "RUNNING" and (
                job["lease_expires_at"] is None or self._time(job["lease_expires_at"]) <= now
                or job["fence_token"] != payload.get("accepted_business_fence")
                or job["attempt"] != payload.get("accepted_attempt")
                or payload.get("accepted_delivery_fence") != current.fence_token
                or payload.get("accepted_worker") != current.locked_by
                or (business_fence is not None and job["fence_token"] != business_fence)):
            raise JobFenceRejectedError("NATIVE_GEO_ACCEPTED_ATTEMPT_NOT_OWNED")
        self._receipt(job, payload, before)
        before()
        return current, job, payload, before

    def _parent(self, tenant: str, intake: Mapping[str, Any], payload: Mapping[str, Any], before: Any) -> None:
        """The committed native identity READY unit is the only Geo parent."""
        from shared.jobs.queue import JobFenceRejectedError

        if (intake["processing_state"] != "READY" or intake["version"] != payload["intake_version"]
                or intake["deleted_at"] is not None or intake["resolved_listing_id"] != payload["listing_id"]):
            raise JobFenceRejectedError("NATIVE_GEO_CURRENT_INTAKE_CHANGED")
        before()
        transition = self.engine.query_one(
            "SELECT transition_id FROM intake.intake_stage_transitions WHERE tenant_id=? AND transition_id=? "
            "AND intake_id=? AND to_state='READY' AND idempotency_key=? AND job_id=? AND resulting_version=? "
            "AND source_snapshot_id IS NOT DISTINCT FROM ?::uuid",
            (tenant, payload["ready_transition_id"], intake["intake_id"],
             payload["origin_id"], payload["identity_job_id"], payload["intake_version"],
             payload["source_snapshot_id"]))
        if transition is None or not self.native_intake(tenant, intake):
            raise JobFenceRejectedError("NATIVE_GEO_PARENT_PROOF_MISSING")
        before()
        listing = self.engine.query_one("SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=? FOR SHARE",
                                        (tenant, payload["listing_id"]))
        if (listing is None or listing["version"] != payload["listing_version"]
                or str(listing["current_revision_id"]) != payload["listing_revision_id"]
                or str(listing["property_id"]) != payload["property_id"] or listing["lifecycle_state"] != "ACTIVE"):
            raise JobFenceRejectedError("NATIVE_GEO_CURRENT_LISTING_CHANGED")
        before()
        prop = self.engine.query_one("SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=? FOR SHARE",
                                     (tenant, payload["property_id"]))
        if (prop is None or prop["version"] != payload["property_version"] or prop["status"] != "ACTIVE"
                or prop["current_redirect_property_id"] is not None):
            raise JobFenceRejectedError("NATIVE_GEO_CURRENT_PROPERTY_CHANGED")
        before()
        # Revisions are immutable; the listing version and current revision
        # above pin it. An exact-existing READY keeps the original revision, so
        # this Intake's own snapshot/parser binding is the READY transition's.
        import hashlib

        address = self._revision_address(tenant, payload["listing_id"], payload["listing_revision_id"])
        if not address or hashlib.sha256(address.encode()).hexdigest() != payload["address_sha256"]:
            raise JobFenceRejectedError("NATIVE_GEO_CURRENT_ADDRESS_CHANGED")
        before()

    def _current_source(self, payload: Mapping[str, Any], before: Any) -> tuple:
        """Current Geo source authority; a historical admission grants no IO."""
        import json
        from types import SimpleNamespace

        from modules.listing.application.native_intake_identity import NativeIntakeIdentityOwner
        from modules.listing.application.native_intake_retrieval_policy import canonical_sha256
        from modules.listing.application.native_intake_source_policy_rules import (
            source_policy_snapshot_json,
        )

        binding = payload["policy"]["source_binding"]
        before()
        source = self.graph._one("SELECT * FROM intake.source_registry WHERE source_id=? FOR SHARE",
                                 (binding["source_id"],))
        before()
        now = self._clock()
        allowed = (source["version"] == payload["geo_source_version"] == binding["source_policy_version"]
                   and NativeIntakeIdentityOwner._source_reusable(source, now))
        valid_until = self._time(source["review_expires_at"]) if allowed else now
        return source, SimpleNamespace(
            target_state=NATIVE_GEO_CHECKPOINT if allowed else "DENIED",
            effective_policy="APPROVED_RETRIEVAL" if allowed else "DENIED",
            source_snapshot_sha256=canonical_sha256(json.loads(source_policy_snapshot_json(source))),
            valid_until=valid_until)

    @staticmethod
    def _positive_source(payload: Mapping[str, Any], classified: Any) -> None:
        from modules.listing.application.native_intake_retrieval import (
            NativeRetrievalQuarantineRequired,
            NativeRetrievalSourceChanged,
        )

        if classified.target_state != NATIVE_GEO_CHECKPOINT:
            raise NativeRetrievalQuarantineRequired("NATIVE_GEO_CURRENT_SOURCE_DENIED")
        if payload["accepted_source_sha256"] != classified.source_snapshot_sha256:
            raise NativeRetrievalSourceChanged("NATIVE_GEO_SOURCE_CHANGED_DURING_ATTEMPT")

    def _fresh(self, delivery: Any, tenant: str, generation: int, *, url: str | None = None,
               settlement: bool = False, business_fence: int | None = None) -> tuple:
        from shared.jobs.queue import JobFenceRejectedError

        physical, job, payload, before = self._locked(delivery, tenant, generation, settlement=settlement,
                                                      business_fence=business_fence)
        if job["status"] != "RUNNING" or job["result"] is not None or job["cancellation_requested_at"] is not None:
            raise JobFenceRejectedError("NATIVE_GEO_RUNNING_CHECKPOINT_REQUIRED")
        before()
        intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR SHARE",
                                 (tenant, job["aggregate_id"]))
        self._parent(tenant, intake, payload, before)
        source, classified = self._current_source(payload, before)
        return physical, job, payload, before, intake, source, classified

    def _accept(self, delivery: Any, tenant: str, generation: int) -> tuple:
        import json

        from modules.listing.application.native_intake_retrieval_policy import (
            canonical_json,
            native_intake_geo_policy,
        )
        from modules.listing.application.native_intake_source_policy_rules import (
            source_policy_snapshot_json,
        )
        from shared.jobs.queue import JobFenceRejectedError

        with self.graph.transaction(tenant):
            physical, job, payload, before = self._locked(delivery, tenant, generation)
            policy = native_intake_geo_policy(tenant)
            now = self._clock()
            if (job["status"] not in {"QUEUED", "RETRYING"} or job["result"] is not None
                    or job["attempt"] >= job["max_attempts"] or job["max_attempts"] != policy.max_attempts
                    or job["cancellation_requested_at"] is not None or self._time(job["timeout_at"]) <= now
                    or (job["next_attempt_at"] is not None and self._time(job["next_attempt_at"]) > now)):
                raise JobFenceRejectedError("NATIVE_GEO_ATTEMPT_NOT_ADMITTED")
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR SHARE",
                                     (tenant, job["aggregate_id"]))
            self._parent(tenant, intake, payload, before)
            source, classified = self._current_source(payload, before)
            if classified.target_state != NATIVE_GEO_CHECKPOINT:
                raise JobFenceRejectedError("NATIVE_GEO_CURRENT_SOURCE_DENIED")
            accepted = {**payload, "accepted_worker": physical.locked_by,
                        "accepted_delivery_fence": physical.fence_token,
                        "accepted_business_fence": job["fence_token"] + 1, "accepted_attempt": job["attempt"] + 1,
                        "accepted_source_sha256": classified.source_snapshot_sha256,
                        "accepted_source_snapshot": json.loads(source_policy_snapshot_json(source))}
            before()
            if self.engine.execute(
                    "UPDATE workflow.jobs SET status='RUNNING',attempt=attempt+1,fence_token=fence_token+1,"
                    "version=version+1,heartbeat_at=clock_timestamp(),lease_expires_at=?,payload=?::jsonb,"
                    "updated_at=clock_timestamp() WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? "
                    "AND status IN ('QUEUED','RETRYING') AND result IS NULL AND cancellation_requested_at IS NULL "
                    "AND timeout_at>clock_timestamp() AND attempt<max_attempts",
                    (physical.lease_expires_at.isoformat(), canonical_json(accepted), tenant, job["job_id"],
                     job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_GEO_ACCEPTANCE_CAS_REJECTED")
            before()
            return accepted["accepted_business_fence"], policy

    def _snapshot(self, intake: Mapping[str, Any], job: Mapping[str, Any], payload: Mapping[str, Any],
                  evidence: Mapping[str, Any], before: Any) -> tuple:
        """Persist the raw provider JSON as an immutable snapshot of the Geo source."""
        from uuid import uuid4

        from modules.listing.application.native_intake_retrieval_policy import GEO_JOB_TYPE
        from modules.listing.application.native_intake_retrieval_results import (
            _SNAPSHOT_FIELDS,
            _snapshot_payload,
        )
        from shared.domain.events import DomainEvent
        from shared.infrastructure.persistence.assisted_listing_intake import (
            NativeIntakeSnapshotMetadataRepository,
        )
        from shared.jobs.queue import JobFenceRejectedError

        verified, upload, bucket, exchange = (evidence["verified"], evidence["upload"], evidence["bucket"],
                                              evidence["exchange_intent"])
        intents = {i["intent_id"]: i for i in payload["attempt_intents"]}
        intent, lookup = intents.get(upload["intent_id"]), intents.get(exchange["intent_id"])
        binding = payload["policy"]["storage_binding"]
        metadata = verified.metadata
        arguments = None if intent is None else intent["arguments"]
        if (intent is None or lookup is None or intent["operation"] != "gcs_geocode_upload"
                or lookup["operation"] != "geocode_post" or lookup["state"] != "FINISHED"
                or intent["state"] not in {"FINISHED", "ERROR"}
                or any(entry["delivery_generation"] != payload["delivery_generation"]
                       or entry["business_fence"] != job["fence_token"] or entry["attempt"] != job["attempt"]
                       or entry["physical_fence"] != payload["accepted_delivery_fence"]
                       or entry["worker"] != payload["accepted_worker"]
                       or entry["input_sha256"] != payload["address_sha256"]
                       or entry["source_snapshot_sha256"] != payload["accepted_source_sha256"]
                       for entry in (intent, lookup))
                or verified.content_sha256 != arguments["content_sha256"]
                or metadata.byte_length != arguments["byte_length"] or metadata.media_type != NATIVE_GEO_MEDIA_TYPE
                or arguments["media_type"] != NATIVE_GEO_MEDIA_TYPE
                or metadata.uri != "gs://" + binding["bucket"] + "/" + arguments["key"]
                or bucket.bucket_name != binding["bucket"] or bucket.location != "ASIA-EAST1"
                or bucket.location_type != "region"):
            raise JobFenceRejectedError("NATIVE_GEO_UPLOAD_JOURNAL_REJECTED")
        source_id = payload["policy"]["source_binding"]["source_id"]
        # Truthful snapshot: the Geo source owns it; capture time is the DB time
        # the provider exchange was admitted, never a provider-claimed time.
        snapshot = {"source_snapshot_id": str(uuid4()), "tenant_id": intake["tenant_id"],
                    "intake_id": intake["intake_id"], "source_id": source_id, "original_url": None,
                    "canonical_url": None, "raw_object_uri": metadata.uri, "content_sha256": verified.content_sha256,
                    "media_type": metadata.media_type, "byte_length": metadata.byte_length,
                    "captured_at": lookup["admitted_at"], "observed_at": lookup["admitted_at"],
                    "capture_method": "SERVER_RETRIEVAL", "retention_class": binding["retention_class"],
                    "encryption_key_ref": metadata.kms_key_version_name, "object_generation": metadata.generation,
                    "residency_mode": binding["residency_mode"], "version": 1}
        before()
        snapshots = NativeIntakeSnapshotMetadataRepository(self.graph)
        if snapshots.find_content(intake["tenant_id"], intake["intake_id"], source_id, verified.content_sha256):
            # Equal provider bytes for this Intake were already committed by
            # another attempt; never adopt that object or its event here.
            raise JobFenceRejectedError("NATIVE_GEO_SNAPSHOT_COLLISION_UNPROVEN")
        before()
        snapshots.insert(tuple(snapshot[key] for key in _SNAPSHOT_FIELDS))
        event = DomainEvent(event_type="snapshot.created", producer="retrieval_service", tenant_id=intake["tenant_id"],
            aggregate_type="source_snapshot", aggregate_id=snapshot["source_snapshot_id"],
            aggregate_version=snapshot["version"], partition_key=intake["tenant_id"] + ":" + intake["intake_id"],
            correlation_id=job["correlation_id"], causation_id=payload["origin_id"], actor_ref=GEO_JOB_TYPE,
            schema_ref="#/payloads/SnapshotCreatedV1", sensitive_fields=["payload.object_uri"],
            payload=_snapshot_payload(snapshot))
        before()
        self.graph._outbox(event)
        return snapshot, event.event_id

    @staticmethod
    def _geocode_result(result: Any) -> dict:
        address = result.address
        observed = result.provider_observed_at
        return {"provider": result.geocode_provider, "provider_request_id": result.provider_request_id,
                "provider_observed_at": None if observed is None else observed.isoformat(),
                "precision": address.geocode_precision, "confidence": address.geocode_confidence,
                "latitude": address.latitude, "longitude": address.longitude,
                "h3_res_8": address.h3_res_8, "h3_res_9": address.h3_res_9, "h3_res_10": address.h3_res_10,
                "admin_match_flag": result.admin_match_flag, "quality_flags": list(result.quality_flags)}

    def _commit(self, delivery: Any, leases: Any, evidence: Mapping[str, Any]) -> dict:
        """Commit snapshot, Address, audit, outbox and both job rows atomically."""
        from dataclasses import replace
        from uuid import uuid4

        from modules.listing.application.native_intake_retrieval_policy import canonical_json
        from shared.infrastructure.persistence.repositories import DurableAddressLocationRepository
        from shared.jobs.queue import JobFenceRejectedError, JobStatus

        tenant, generation = self.auth.decode(delivery)
        with leases.root():
            physical, job, payload, before, intake, _, classified = self._fresh(
                delivery, tenant, generation, business_fence=leases.business_fence)
            self._positive_source(payload, classified)
            snapshot, snapshot_event_id = self._snapshot(intake, job, payload, evidence, before)
            geo = evidence["geo"]
            address = replace(geo.address, address_id=str(uuid4()), tenant_id=tenant, revision=1)
            before()
            DurableAddressLocationRepository(self.engine).save_address(address)
            at, audit_id = self._clock(), str(uuid4())
            result = {"job_id": job["job_id"], "intake_id": intake["intake_id"], "status": "SUCCEEDED",
                      "code": "NATIVE_GEO_EVIDENCE_COMMITTED", "origin_kind": payload["origin_kind"],
                      "origin_id": payload["origin_id"],
                      "listing_id": payload["listing_id"], "listing_revision_id": payload["listing_revision_id"],
                      "property_id": payload["property_id"], "address_id": address.address_id,
                      "address_revision": address.revision, "geocode": self._geocode_result(geo),
                      "source_snapshot_id": snapshot["source_snapshot_id"],
                      "snapshot_created_event_id": snapshot_event_id, "audit_event_id": audit_id,
                      "correlation_id": job["correlation_id"],
                      "native_snapshot_proof": {"snapshot_fields": snapshot,
                                                "bucket_metadata": {"bucket_name": evidence["bucket"].bucket_name,
                                                                    "location": evidence["bucket"].location,
                                                                    "location_type": evidence["bucket"].location_type},
                                                "upload_intent_id": evidence["upload"]["intent_id"],
                                                "exchange_intent_id": evidence["exchange_intent"]["intent_id"]},
                      "accepted_delivery_fence": physical.fence_token, "accepted_worker": physical.locked_by,
                      "accepted_business_fence": job["fence_token"], "accepted_attempt": job["attempt"]}
            before()
            self._audit(intake, NATIVE_GEO_COMPLETED_ACTION, {"job_id": job["job_id"], "status": "RUNNING"},
                        {**result, "native_geo_provenance": self._provenance(payload)}, at,
                        audit_id=audit_id, causation=payload["origin_id"])
            before()
            if (self._current_policy_sha256(tenant) != payload["policy_sha256"]
                    or not self.engine.query_one(
                        "SELECT job_id FROM durable_jobs WHERE job_id=? AND status=? AND fence_token=? "
                        "AND locked_by=? AND lease_expires_at>clock_timestamp()",
                        (job["job_id"], JobStatus.RUNNING.value, physical.fence_token, physical.locked_by))):
                raise JobFenceRejectedError("NATIVE_GEO_FINAL_PHYSICAL_FENCE_REJECTED")
            before()
            if self.engine.execute(
                    "UPDATE workflow.jobs SET status='SUCCEEDED',checkpoint=?,result=?::jsonb,version=version+1,"
                    "heartbeat_at=clock_timestamp(),lease_expires_at=NULL,updated_at=clock_timestamp() "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                    "AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp() "
                    "AND timeout_at>clock_timestamp() AND ?::timestamptz>clock_timestamp()",
                    (NATIVE_GEO_DONE_CHECKPOINT, canonical_json(result), tenant, job["job_id"], job["version"],
                     job["fence_token"], classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_GEO_BUSINESS_TERMINAL_CAS_REJECTED")
            before()
            self.queue.update_status(job["job_id"], JobStatus.SUCCEEDED, expected_version=physical.version,
                                     fence_token=physical.fence_token)
            before()
            return result

    def committed_result(self, delivery: Any, tenant: str, generation: int) -> dict | None:
        """Historical result proof only; never an external-IO capability."""
        import hashlib
        from datetime import timedelta

        from modules.listing.application.native_intake_retrieval import audited_value_matches
        from modules.listing.application.native_intake_retrieval_policy import GEO_JOB_TYPE
        from modules.listing.application.native_intake_retrieval_results import (
            _SNAPSHOT_FIELDS,
            _read_event,
            _snapshot_payload,
        )
        from shared.infrastructure.persistence.assisted_listing_intake import (
            NativeIntakeSnapshotMetadataRepository,
        )
        from shared.jobs.queue import JobFenceRejectedError, JobStatus

        self.engine.query_one("SELECT set_config('statement_timeout',?,true)", ("750ms",))
        bound = self._clock() + timedelta(milliseconds=750)

        def before() -> None:
            self._admission_budget(bound)

        before()
        job = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                    (tenant, delivery.job_id))
        if job is None or job["result"] is None:
            return None
        payload, result = self._object(job["payload"]), self._object(job["result"])
        before()
        physical = self.queue.get(delivery.job_id)
        fields = {"job_id", "intake_id", "status", "code", "origin_kind", "origin_id", "listing_id",
                  "listing_revision_id",
                  "property_id", "address_id", "address_revision", "geocode", "source_snapshot_id",
                  "snapshot_created_event_id", "audit_event_id", "correlation_id", "native_snapshot_proof",
                  "accepted_delivery_fence", "accepted_worker", "accepted_business_fence", "accepted_attempt"}
        if (set(result) != fields or physical is None or physical.job_type != GEO_JOB_TYPE
                or physical.status != JobStatus.SUCCEEDED or physical.payload != delivery.payload
                or job["status"] != "SUCCEEDED" or job["checkpoint"] != NATIVE_GEO_DONE_CHECKPOINT
                or job["job_type"] != GEO_JOB_TYPE or job["aggregate_type"] != "intake"
                or job["idempotency_key"] != job["aggregate_id"] + ":" + payload["origin_id"]
                or payload.get("delivery_generation") != generation
                or payload.get("attempt_token_sha256")
                != hashlib.sha256(delivery.payload["attempt_token"].encode()).hexdigest()
                or result["job_id"] != delivery.job_id or result["intake_id"] != job["aggregate_id"]
                or result["origin_id"] != payload["origin_id"] or result["origin_kind"] != payload["origin_kind"]
                or result["status"] != "SUCCEEDED"
                or result["correlation_id"] != job["correlation_id"]
                or physical.correlation_id != job["correlation_id"]
                or any(result[key] != payload[key] for key in ("listing_id", "listing_revision_id", "property_id"))
                or result["accepted_delivery_fence"] != physical.fence_token
                or result["accepted_delivery_fence"] != payload.get("accepted_delivery_fence")
                or result["accepted_worker"] != payload.get("accepted_worker")
                or result["accepted_business_fence"] != job["fence_token"]
                or result["accepted_business_fence"] != payload.get("accepted_business_fence")
                or result["accepted_attempt"] != job["attempt"]
                or result["accepted_attempt"] != payload.get("accepted_attempt")):
            raise JobFenceRejectedError("NATIVE_GEO_TERMINAL_RECEIPT_REJECTED")
        self._receipt(job, payload, before)
        before()
        audit = self.engine.query_one(
            "SELECT after_value FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=? "
            "AND resource_type='intake' AND resource_id=? AND actor_subject_id IS NULL AND service_principal=? "
            "AND actor_role=? AND action=? AND result='SUCCEEDED' AND correlation_id=? AND causation_id=?",
            (tenant, result["audit_event_id"], job["aggregate_id"], GEO_JOB_TYPE, NATIVE_GEO_ACTOR_ROLE,
             NATIVE_GEO_COMPLETED_ACTION, job["correlation_id"], payload["origin_id"]))
        if audit is None or not audited_value_matches(
                self._object(audit["after_value"]), {**result, "native_geo_provenance": self._provenance(payload)}):
            raise JobFenceRejectedError("NATIVE_GEO_TERMINAL_AUDIT_REJECTED")
        proof = self._object(result["native_snapshot_proof"])
        snapshot = self._object(proof.get("snapshot_fields"))
        binding = payload["policy"]["storage_binding"]
        if (set(proof) != {"snapshot_fields", "bucket_metadata", "upload_intent_id", "exchange_intent_id"}
                or set(snapshot) != set(_SNAPSHOT_FIELDS) or snapshot["tenant_id"] != tenant
                or snapshot["intake_id"] != job["aggregate_id"]
                or snapshot["source_snapshot_id"] != result["source_snapshot_id"]
                or snapshot["source_id"] != payload["policy"]["source_binding"]["source_id"]
                or snapshot["media_type"] != NATIVE_GEO_MEDIA_TYPE or snapshot["residency_mode"] != "TW_ONLY"
                or proof["bucket_metadata"] != {"bucket_name": binding["bucket"], "location": "ASIA-EAST1",
                                                "location_type": "region"}):
            raise JobFenceRejectedError("NATIVE_GEO_TERMINAL_SNAPSHOT_BINDING_REJECTED")
        before()
        actual = NativeIntakeSnapshotMetadataRepository(self.graph).required_for_intake(
            tenant, result["source_snapshot_id"], job["aggregate_id"])
        if any(actual[key] != value for key, value in snapshot.items()):
            raise JobFenceRejectedError("NATIVE_GEO_TERMINAL_SNAPSHOT_CHANGED")
        event = _read_event(self, tenant, result["snapshot_created_event_id"], event_type="snapshot.created",
            aggregate_type="source_snapshot", aggregate_id=result["source_snapshot_id"],
            aggregate_version=snapshot["version"], partition=tenant + ":" + job["aggregate_id"],
            correlation=job["correlation_id"], causation=payload["origin_id"], actor=GEO_JOB_TYPE, before=before)
        if (event["producer"] != "retrieval_service" or event["schema_ref"] != "#/payloads/SnapshotCreatedV1"
                or event["payload"] != _snapshot_payload(snapshot)):
            raise JobFenceRejectedError("NATIVE_GEO_TERMINAL_SNAPSHOT_EVENT_REJECTED")
        intents = {i["intent_id"]: i for i in payload["attempt_intents"]}
        upload, lookup = intents.get(proof["upload_intent_id"]), intents.get(proof["exchange_intent_id"])
        if (upload is None or lookup is None or upload["operation"] != "gcs_geocode_upload"
                or lookup["operation"] != "geocode_post" or lookup["state"] != "FINISHED"
                or any(entry["delivery_generation"] != generation or entry["business_fence"] != job["fence_token"]
                       for entry in (upload, lookup))
                or upload["arguments"]["content_sha256"] != snapshot["content_sha256"]
                or upload["arguments"]["byte_length"] != snapshot["byte_length"]
                or snapshot["captured_at"] != lookup["admitted_at"]
                or snapshot["raw_object_uri"] != "gs://" + binding["bucket"] + "/" + upload["arguments"]["key"]):
            raise JobFenceRejectedError("NATIVE_GEO_TERMINAL_UPLOAD_JOURNAL_REJECTED")
        before()
        address = self.engine.query_one(
            "SELECT address_id,tenant_id,revision,latitude,longitude,geocode_precision,geocode_confidence,"
            "h3_res_8,h3_res_9,h3_res_10 FROM address_locations WHERE address_id=?", (result["address_id"],))
        geocode = result["geocode"]
        if (address is None or str(address["tenant_id"]) != tenant or address["revision"] != result["address_revision"]
                or address["geocode_precision"] != geocode["precision"]
                or any((address[key] or "") != (geocode[key] or "") for key in ("h3_res_8", "h3_res_9", "h3_res_10"))
                or any(address[key] is None or abs(float(address[key]) - float(geocode[name])) > 1e-6
                       for key, name in (("latitude", "latitude"), ("longitude", "longitude")))):
            raise JobFenceRejectedError("NATIVE_GEO_TERMINAL_ADDRESS_CHANGED")
        before()
        return result

    def _replay(self, delivery: Any, tenant: str, generation: int, leases: Any = None) -> dict | None:
        if leases is not None:
            leases.pause()
        with self.graph.transaction(tenant):
            return self.committed_result(delivery, tenant, generation)

    def execute(self, delivery: Any) -> dict | None:
        from modules.external_data.application.native_intake_process import NativeIOUnreaped
        from shared.jobs.queue import JobFenceRejectedError

        tenant, generation = self.auth.decode(delivery)
        if result := self._replay(delivery, tenant, generation):
            return result
        try:
            fence, policy = self._accept(delivery, tenant, generation)
        except JobFenceRejectedError as error:
            return self._unaccepted(delivery, tenant, generation, error)
        except Exception as error:
            # An unknown acceptance commit is only adopted when the fresh
            # owned RUNNING row proves it; otherwise it is unaccepted.
            try:
                from modules.listing.application.native_intake_retrieval_policy import (
                    native_intake_geo_policy,
                )

                with self.graph.transaction(tenant):
                    _, job, _, _, _, _, _ = self._fresh(delivery, tenant, generation)
                    fence, policy = job["fence_token"], native_intake_geo_policy(tenant)
            except Exception:
                return self._unaccepted(delivery, tenant, generation, error)
        leases = native_geo_leases(self.graph, delivery, business_fence=fence, policy_sha256=policy.sha256)
        io, terminal_attempted = None, False
        try:
            io = native_geo_io(self, delivery, leases, policy)
            evidence = io.capture()
            terminal_attempted = True
            return self._commit(delivery, leases, evidence)
        except NativeIOUnreaped:
            # Unresolved execution keeps its slot and recovery stays visible.
            raise
        except Exception as error:
            return self._settle(delivery, tenant, generation, leases, error, io=io,
                                terminal_attempted=terminal_attempted)
        finally:
            leases.pause()

    def _job_only(self, delivery: Any, tenant: str, generation: int, *, cancelled: bool, code: str,
                  accepted: bool, cause: BaseException | None = None) -> None:
        """Close the Geo job without any Address/snapshot/result."""
        from uuid import uuid4

        from modules.listing.application.native_intake_retrieval_policy import (
            GEO_JOB_TYPE,
            canonical_json,
        )
        from modules.listing.application.native_intake_retrieval_results import dead_letter
        from modules.listing.application.native_intake_retrieval_runtime import _safe_failure
        from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus

        # Typed cause codes only; never driver, provider or address text.
        evidence = {} if cause is None else _safe_failure(cause)
        with self.graph.transaction(tenant):
            physical, job, payload, before = self._locked(delivery, tenant, generation, settlement=True)
            statuses = {"RUNNING"} if accepted else {"QUEUED", "RETRYING"}
            if job["status"] not in statuses or job["result"] is not None:
                raise JobFenceRejectedError("NATIVE_GEO_JOB_ONLY_SETTLEMENT_NOT_OWNED")
            if cancelled != (job["cancellation_requested_at"] is not None):
                raise JobFenceRejectedError("NATIVE_GEO_CANCELLATION_CHANGED")
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?",
                                     (tenant, job["aggregate_id"]))
            status, at = "CANCELLED" if cancelled else "FAILED", self._clock()
            before()
            if self.engine.execute(
                    "UPDATE workflow.jobs SET status=?,last_error=?::jsonb,lease_expires_at=NULL,"
                    "heartbeat_at=clock_timestamp(),version=version+1,updated_at=clock_timestamp() "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND result IS NULL "
                    "AND (?::boolean OR cancellation_requested_at IS NULL)",
                    (status, canonical_json({**self._object(job.get("last_error") or {}), **evidence, "code": code}),
                     tenant,
                     job["job_id"], job["version"], job["fence_token"], cancelled)).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_GEO_JOB_ONLY_CAS_REJECTED")
            before()
            self._audit(intake, NATIVE_GEO_SETTLED_ACTION, {"job_id": job["job_id"], "status": job["status"]},
                        {"job_id": job["job_id"], "status": status, "code": code, "intake_mutated": False,
                         "address_created": False}, at, audit_id=str(uuid4()), causation=payload["origin_id"])
            before()
            self.queue.update_status(job["job_id"], JobStatus.CANCELLED if cancelled else JobStatus.FAILED,
                delivery_state=None if cancelled else JobDeliveryState.DEAD_LETTER,
                expected_version=physical.version, fence_token=physical.fence_token, error_message=code)
            if not cancelled:
                before()
                dead_letter(self, job, payload, code, at, checkpoint=job["checkpoint"], version=job["version"] + 1,
                            attempt=job["attempt"] if accepted else physical.attempts,
                            job_type=GEO_JOB_TYPE, causation=payload["origin_id"])
            before()
        return None

    def _unaccepted(self, delivery: Any, tenant: str, generation: int, error: Exception) -> dict | None:
        if result := self._replay(delivery, tenant, generation):
            return result
        with self.graph.transaction(tenant):
            _, job, _, _ = self._locked(delivery, tenant, generation, settlement=True)
            if job["status"] not in {"QUEUED", "RETRYING"}:
                raise error
            cancelled = job["cancellation_requested_at"] is not None
        return self._job_only(delivery, tenant, generation, cancelled=cancelled,
                              code="NATIVE_GEO_CANCELLED" if cancelled else "NATIVE_GEO_ACCEPTANCE_REJECTED",
                              accepted=False, cause=error)

    @staticmethod
    def _due(job: Mapping[str, Any], payload: Mapping[str, Any], now: datetime) -> datetime | None:
        from datetime import timedelta

        timeout = NativeIntakeGeoOwner._time(job["timeout_at"])
        if job["attempt"] >= job["max_attempts"] or timeout <= now:
            return None
        due = now + timedelta(seconds=payload["policy"]["backoff_seconds"][job["attempt"] - 1])
        return due if due < timeout else None

    def _retryable(self, error: BaseException) -> bool:
        from modules.listing.application.native_intake_retrieval_runtime import _retryable
        from shared.jobs.queue import JobFenceRejectedError

        if (provider := self.geo_provider) is not None:
            # The original provider error taxonomy decides; only 5xx retries.
            if isinstance(error, provider.auth_error):
                return False
            if isinstance(error, (provider.rate_limit_error, provider.timeout_error)):
                return True
            if isinstance(error, provider.provider_error):
                return (error.code == "http_error" and error.status_code is not None
                        and 500 <= error.status_code < 600)
        return (_retryable(error) or (isinstance(error, JobFenceRejectedError)
                                      and str(error) == "NATIVE_GEO_ROOT_BUDGET_EXPIRED"))

    def _retry(self, delivery: Any, tenant: str, generation: int, leases: Any, due: datetime) -> None:
        import hashlib
        from uuid import uuid4

        from modules.listing.application.native_intake_retrieval_policy import canonical_json
        from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus

        with leases.root():
            physical, job, payload, before, intake, _, classified = self._fresh(
                delivery, tenant, generation, business_fence=leases.business_fence)
            if classified.target_state != NATIVE_GEO_CHECKPOINT:
                raise JobFenceRejectedError("NATIVE_GEO_CURRENT_SOURCE_DENIED")
            if not self._due(job, payload, self._clock()) or self._time(job["timeout_at"]) <= due:
                raise JobFenceRejectedError("NATIVE_GEO_RETRY_BUDGET_CHANGED")
            token = self.auth.sign(job["job_id"], tenant, generation + 1)
            updated = {**payload, "delivery_generation": generation + 1,
                       "attempt_token_sha256": hashlib.sha256(token.encode()).hexdigest()}
            at, code = self._clock(), "NATIVE_GEO_RETRY_SCHEDULED"
            before()
            if self.engine.execute(
                    "UPDATE workflow.jobs SET status='RETRYING',next_attempt_at=?,payload=?::jsonb,"
                    "lease_expires_at=NULL,last_error=?::jsonb,version=version+1,updated_at=clock_timestamp() "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                    "AND cancellation_requested_at IS NULL AND lease_expires_at>clock_timestamp() "
                    "AND timeout_at>?::timestamptz",
                    (due.isoformat(), canonical_json(updated),
                     canonical_json({**self._object(job.get("last_error") or {}), "code": code}), tenant,
                     job["job_id"], job["version"], job["fence_token"], due.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_GEO_RETRY_CAS_REJECTED")
            before()
            self._audit(intake, NATIVE_GEO_SETTLED_ACTION, {"job_id": job["job_id"], "status": "RUNNING"},
                        {"job_id": job["job_id"], "status": "RETRYING", "next_attempt_at": due.isoformat(),
                         "code": code}, at, audit_id=str(uuid4()), causation=payload["origin_id"])
            before()
            self.queue.update_status(job["job_id"], JobStatus.QUEUED,
                payload={"job_id": job["job_id"], "tenant_id": tenant, "attempt_token": token},
                delivery_state=JobDeliveryState.RETRYING, expected_version=physical.version,
                fence_token=physical.fence_token, error_message=code)
            before()
            self.engine.execute("UPDATE durable_jobs SET available_at=? WHERE job_id=?",
                                (due.isoformat(), job["job_id"]))
            before()
        return None

    def _settle(self, delivery: Any, tenant: str, generation: int, leases: Any, error: Exception, *,
                io: Any = None, terminal_attempted: bool = False) -> dict | None:
        from fastapi import HTTPException

        from modules.external_data.application.native_intake_process import (
            NativeIORejected,
            NativeIOUnreaped,
        )
        from modules.listing.application.native_intake_retrieval import (
            NativeRetrievalQuarantineRequired,
            NativeRetrievalSourceChanged,
        )
        from modules.listing.application.native_intake_retrieval_policy import canonical_json
        from modules.listing.application.native_intake_retrieval_runtime import _safe_failure
        from shared.jobs.queue import JobFenceRejectedError

        if result := self._replay(delivery, tenant, generation, leases):
            return result
        with leases.root():
            _, job, payload, before = self._locked(delivery, tenant, generation, settlement=True,
                                                   business_fence=leases.business_fence)
            if job["status"] != "RUNNING":
                raise JobFenceRejectedError("NATIVE_GEO_ACCEPTED_SETTLEMENT_REQUIRED")
            cancelled = job["cancellation_requested_at"] is not None
            failure = {**_safe_failure(error), "code": "NATIVE_GEO_EXECUTION_ERROR"}
            before()
            if self.engine.execute(
                    "UPDATE workflow.jobs SET last_error=?::jsonb,version=version+1,updated_at=clock_timestamp() "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                    "AND result IS NULL AND lease_expires_at>clock_timestamp()",
                    (canonical_json(failure), tenant, job["job_id"], job["version"], job["fence_token"])).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_GEO_ERROR_EVIDENCE_FENCE_REJECTED")
            before()
        if cancelled:
            return self._job_only(delivery, tenant, generation, cancelled=True, code="NATIVE_GEO_CANCELLED",
                                  accepted=True)
        try:
            with leases.root():
                _, job, payload, _, _, _, classified = self._fresh(
                    delivery, tenant, generation, settlement=True, business_fence=leases.business_fence)
                now = self._clock()
        except (PermissionError, JobFenceRejectedError, HTTPException, LookupError):
            # Still-owned settlement closes the job; it never writes evidence
            # under a changed parent/revision/property/address/policy.
            return self._job_only(delivery, tenant, generation, cancelled=False,
                                  code="NATIVE_GEO_CURRENT_AUTHORITY_LOST", accepted=True)
        if classified.target_state != NATIVE_GEO_CHECKPOINT or isinstance(error, NativeRetrievalQuarantineRequired):
            return self._job_only(delivery, tenant, generation, cancelled=False,
                                  code="NATIVE_GEO_CURRENT_SOURCE_DENIED", accepted=True)
        if io is not None and not terminal_attempted:
            try:
                io.cleanup_known_unreferenced()
            except NativeIOUnreaped:
                raise
            except (NativeIORejected, JobFenceRejectedError, NativeRetrievalSourceChanged):
                # An unresolved cleanup stays in its durable intent.
                pass
        if self._retryable(error) and (due := self._due(job, payload, now)):
            return self._retry(delivery, tenant, generation, leases, due)
        code = (str(error) if isinstance(error, NativeGeoResultRejected) else "NATIVE_GEO_FAILED")
        return self._job_only(delivery, tenant, generation, cancelled=False, code=code, accepted=True)


class NativeGeoResultRejected(ValueError):
    """A missing/invalid provider result; never becomes Address or retries."""


@cache
def _native_geo_leases_class() -> type:
    """Bind the one shared dual-lease implementation to the Geo purpose.

    Resolved lazily: this legacy Listing module is imported widely and must not
    load the native runtime stack at import time.
    """
    from modules.listing.application.native_intake_retrieval_leases import (
        NativeRetrievalCompositeLeases,
    )
    from modules.listing.application.native_intake_retrieval_policy import (
        GEO_JOB_TYPE,
        NativeIntakeGeoAuthenticator,
    )

    class _NativeGeoCompositeLeases(NativeRetrievalCompositeLeases):
        job_type = GEO_JOB_TYPE
        thread_name = "native-geo-dual-leases"

        @staticmethod
        def _authenticator() -> Any:
            return NativeIntakeGeoAuthenticator()

        def _current_policy_sha256(self) -> str | None:
            return NativeIntakeGeoOwner(self.graph)._current_policy_sha256(self.tenant)

    return _NativeGeoCompositeLeases


def native_geo_leases(graph: Any, delivery: Any, *, business_fence: int, policy_sha256: str) -> Any:
    """The shared dual-lease renewal bound to the Geo job purpose."""
    return _native_geo_leases_class()(graph, delivery, business_fence=business_fence, policy_sha256=policy_sha256)


@cache
def _native_geo_io_class() -> type:
    """Bind the one shared fenced IO journal to the closed Geo operation family."""
    from modules.listing.application.native_intake_retrieval_io import NativeIntakeRetrievalIO

    class _NativeIntakeGeoIO(NativeIntakeRetrievalIO):
        operations = frozenset({"geocode_post", "gcs_geocode_bucket", "gcs_geocode_upload", "gcs_geocode_head",
                                "gcs_geocode_download", "gcs_geocode_delete"})
        delete_operation = "gcs_geocode_delete"
        rate_operation = "geocode_post"
        upload_operation = "gcs_geocode_upload"
        input_key = "address_sha256"

        def _transports(self, owner: Any, leases: Any) -> None:
            from modules.external_data.application.native_assisted_snapshot_process_transport import (
                NativeAssistedSnapshotProcessTransport,
            )
            from modules.external_data.application.native_assisted_snapshot_transport import (
                GEOCODE_JSON_PURPOSE,
            )

            self.http = None
            if owner.snapshot_transport_factory is not None:
                self.storage = owner.snapshot_transport_factory(guard=leases.guard)
            else:
                self.storage = NativeAssistedSnapshotProcessTransport(guard=leases.guard,
                                                                      purpose=GEOCODE_JSON_PURPOSE)

        def _current_policy(self) -> Any:
            from modules.listing.application.native_intake_retrieval_policy import (
                native_intake_geo_policy,
            )

            return native_intake_geo_policy(self.tenant)

        def capture(self) -> dict:
            return _native_geo_capture(self)

    return _NativeIntakeGeoIO


def native_geo_io(owner: Any, delivery: Any, leases: Any, policy: Any) -> Any:
    """The shared fenced IO journal bound to the Geo operation family."""
    return _native_geo_io_class()(owner, delivery, leases, policy)


def _native_geo_capture(io: Any) -> dict:
    """One LIVE provider exchange, then the GEOCODE JSON object round trip."""
    import hashlib
    import json
    import time
    from uuid import uuid4

    from modules.external_data.application.native_assisted_snapshot_transport import (
        GEOCODE_JSON_PURPOSE,
        NativeAssistedSnapshotProofRejected,
        NativeVerifiedAssistedSnapshot,
        native_json_snapshot_object_key,
    )
    from modules.external_data.application.native_intake_process import (
        NativeGeocodeProcessTransport,
        NativeIORejected,
        NativeIOUnreaped,
    )
    from modules.external_data.geo.pipeline import normalize_address as geo_normalize_address
    from shared.jobs.queue import JobFenceRejectedError

    owner, leases, policy = io.owner, io.leases, io.policy
    with leases.root():
        _, job, payload, _, _, _, _ = io._fresh(None)
        raw_address = owner._revision_address(io.tenant, payload["listing_id"], payload["listing_revision_id"])
        correlation_id = job["correlation_id"]
    endpoint = owner.live_endpoint()
    geo_provider = owner.geo_provider  # live_endpoint() proved the injected LIVE adapter
    normalized = geo_normalize_address(raw_address)
    bucket, _ = io.call("gcs_geocode_bucket", endpoint, {},
                        lambda deadline: io.storage.bucket_info(policy.storage, deadline=deadline))

    def lookup(deadline: float) -> tuple:
        transport = NativeGeocodeProcessTransport(deadline=deadline, guard=leases.guard, runner=owner.geocode_runner,
                                                  response_type=geo_provider.response_type)
        client = geo_provider.client_type(endpoint, timeout_seconds=max(0.001, deadline - time.monotonic()),
                                          transport=transport)
        provider = geo_provider.provider_type(client=client, env=owner.env, mode=geo_provider.live_mode,
                                              retry_budget=0, correlation_id=correlation_id)
        candidate, response = provider.lookup_with_payload(normalized)
        return candidate, response, transport.exchange

    (candidate, response, exchange), exchange_intent = io.call(
        "geocode_post", endpoint, {"address_sha256": payload["address_sha256"], "correlation_id": correlation_id},
        lookup)
    raw = None if exchange is None else exchange.response_body
    if (exchange is None or exchange.status != 200 or not isinstance(raw, bytes) or not raw
            or exchange.media_type != NATIVE_GEO_MEDIA_TYPE or not isinstance(response, dict)
            or json.loads(raw.decode("utf-8")) != response):
        raise NativeGeoResultRejected("NATIVE_GEO_RESULT_INVALID")
    if candidate is None or "malformed_provider_response" in candidate.quality_flags:
        raise NativeGeoResultRejected("NATIVE_GEO_RESULT_MISSING")
    geo = GeoPipeline().result_from_candidate(normalized, candidate)
    digest = hashlib.sha256(raw).hexdigest()
    capture_id = str(uuid4())
    key = native_json_snapshot_object_key(GEOCODE_JSON_PURPOSE, io.tenant, io.delivery.job_id, io.generation,
                                          leases.business_fence, capture_id)
    arguments = {"key": key, "content_sha256": digest, "byte_length": len(raw), "media_type": NATIVE_GEO_MEDIA_TYPE,
                 "capture_id": capture_id}
    try:
        metadata, upload = io.call("gcs_geocode_upload", endpoint, arguments,
            lambda deadline: io.storage.upload_with_kms_if_absent(policy.storage, key, raw, NATIVE_GEO_MEDIA_TYPE,
                                                                  deadline=deadline))
        generation = metadata.generation
        io.object_metadata, io.upload_intent = metadata, upload
    except NativeIOUnreaped:
        raise
    except (NativeIORejected, NativeAssistedSnapshotProofRejected):
        # Reconcile only this exact committed upload intent/key.
        with leases.root():
            _, _, current, _, _, _, _ = io._fresh(endpoint)
            matches = [i for i in current["attempt_intents"] if i["operation"] == "gcs_geocode_upload"
                       and i["arguments"] == arguments and i["delivery_generation"] == io.generation
                       and i["business_fence"] == leases.business_fence]
            if len(matches) != 1:
                raise JobFenceRejectedError("NATIVE_GEO_UNKNOWN_UPLOAD_INTENT_MISSING") from None
            upload = matches[0]
        generation = None
    metadata, _ = io.call("gcs_geocode_head", endpoint, {"key": key, "generation": generation},
        lambda deadline: io.storage.head_version(policy.storage, key, generation, deadline=deadline))
    io.object_metadata, io.upload_intent = metadata, upload
    downloaded, _ = io.call("gcs_geocode_download", endpoint, {"key": key, "generation": metadata.generation},
        lambda deadline: io.storage.download_version_bounded(policy.storage, key, metadata.generation,
                                                             deadline=deadline))
    verified = NativeVerifiedAssistedSnapshot.from_download(downloaded, metadata, expected_sha256=digest,
                                                            purpose=GEOCODE_JSON_PURPOSE)
    if metadata.media_type != NATIVE_GEO_MEDIA_TYPE or metadata.byte_length != len(raw):
        raise NativeAssistedSnapshotProofRejected("NATIVE_GEOCODE_SNAPSHOT_PROOF_REJECTED")
    return {"verified": verified, "upload": upload, "bucket": bucket, "exchange_intent": exchange_intent,
            "geo": geo}


def native_intake_geo_owner(persistence: Any) -> NativeIntakeGeoOwner:
    """The default worker's Geo owner over the canonical identity graph."""
    return NativeIntakeGeoOwner(persistence.canonical_identity_graph())


def run_native_intake_geocode_delivery(delivery: Any, owner: NativeIntakeGeoOwner) -> dict | None:
    return owner.execute(delivery)
