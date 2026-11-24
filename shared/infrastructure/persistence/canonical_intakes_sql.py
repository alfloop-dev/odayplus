"""Create real native Intake anchors without inventing processed evidence."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from modules.listing.application.intake_authorization import authorize_intake_action
from modules.listing.application.intake_workflow_bootstrap import (
    IntakeWorkflowUnavailable,
    initialize_intake_workflow,
    schedule_from_record,
)
from modules.listing.domain.identity_graph import IdentityConflictError, IdentityNotFoundError
from modules.listing.domain.intake_states import (
    Actor,
    DenialCode,
    DomainValidationError,
    IntakeAggregate,
    IntakeStage,
    IntakeStateMachine,
    PrincipalRole,
    TransitionContext,
)
from modules.listing.domain.models import CandidateSiteDraft
from shared.audit.backlog import CanonicalAuditBacklog
from shared.auth import Principal, Role
from shared.domain.events import DomainEvent
from shared.domain.models import AddressLocation, CandidateSite, Listing
from shared.jobs.queue import JobStatus

# Catalogued payload contracts for the events the shared workflow commands emit.
_WORKFLOW_EVENT_SCHEMAS = {
    "assignment.assigned": "#/payloads/AssignmentChangedV1",
    "assignment.transferred": "#/payloads/AssignmentChangedV1",
    "assignment.claimed": "#/payloads/AssignmentStateChangedV1",
    "assignment.completed": "#/payloads/AssignmentStateChangedV1",
    "sla.state_changed": "#/payloads/SlaStateChangedV1",
}

_SQL_TO_DOMAIN_CANDIDATE_STATUS = {
    "NEW": "new",
    "SCREENED": "screened",
    "SCORING": "scoring",
    "SCORED": "scored",
    "VISITED": "visited",
    "REJECTED": "rejected",
    "APPROVED": "approved",
    "OPENED": "opened",
    "SCORING_FAILED": "SCORING_FAILED",
}
_DOMAIN_TO_SQL_CANDIDATE_STATUS = {
    **{value: key for key, value in _SQL_TO_DOMAIN_CANDIDATE_STATUS.items()},
    "scoring_failed": "SCORING_FAILED",
    **{key: key for key in _SQL_TO_DOMAIN_CANDIDATE_STATUS},
}
_LOCK_SUFFIX = ("", " FOR SHARE", " FOR UPDATE")


def sql_candidate_status(status: str) -> str:
    mapped = _DOMAIN_TO_SQL_CANDIDATE_STATUS.get(str(status or "").strip())
    if mapped is None:
        raise ValueError(f"Unsupported candidate status: {status!r}")
    return mapped


def domain_candidate_status(status: str) -> str:
    mapped = _SQL_TO_DOMAIN_CANDIDATE_STATUS.get(str(status or "").strip().upper())
    if mapped is None:
        raise ValueError(f"Unsupported SQL candidate status: {status!r}")
    return mapped


def _valid_uuid(value: Any) -> str | None:
    if not isinstance(value, (str, UUID)):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = str(UUID(text))
    except (ValueError, TypeError, AttributeError):
        return None
    return parsed if parsed == text.lower() else None


def _ensure_tenant_context(engine: Any, tenant: str) -> str:
    clean = _valid_uuid(tenant)
    if clean is None:
        raise PermissionError("TENANT_SCOPE_DENIED")
    current = engine.query_one("SELECT current_setting('app.tenant_id', true) AS tenant")["tenant"]
    if current and current != clean:
        raise PermissionError("TENANT_SCOPE_DENIED")
    engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (clean,))
    return clean


def native_intake_tables_available(engine: Any) -> bool:
    if getattr(engine, "dialect", None) != "postgresql":
        return False
    row = engine.query_one("SELECT to_regclass('expansion.candidate_sites') AS rel")
    return bool(row and row.get("rel") is not None)


def is_native_sql_owned(
    engine: Any,
    tenant: str,
    *,
    intake_id: str | None = None,
    promotion_decision_id: str | None = None,
    candidate_site_id: str | None = None,
    listing_id: str | None = None,
) -> bool:
    """Return True only for actual tenant-qualified normalized SQL ownership."""
    if not native_intake_tables_available(engine):
        return False
    clean_tenant = _valid_uuid(tenant)
    if clean_tenant is None:
        return False
    _ensure_tenant_context(engine, clean_tenant)
    if promotion_decision_id is not None:
        pid = _valid_uuid(promotion_decision_id)
        if pid is not None and engine.query_one(
            "SELECT 1 FROM expansion.promotion_decisions WHERE tenant_id=? AND promotion_decision_id=? LIMIT 1",
            (clean_tenant, pid),
        ) is not None:
            return True
    if candidate_site_id is not None:
        cid = _valid_uuid(candidate_site_id)
        if cid is not None and engine.query_one(
            "SELECT 1 FROM expansion.candidate_sites WHERE tenant_id=? AND candidate_site_id=? LIMIT 1",
            (clean_tenant, cid),
        ) is not None:
            return True
    if listing_id is not None:
        lid = _valid_uuid(listing_id)
        if lid is not None and engine.query_one(
            "SELECT 1 FROM expansion.listings WHERE tenant_id=? AND listing_id=? LIMIT 1",
            (clean_tenant, lid),
        ) is not None:
            return True
    if intake_id is not None:
        iid = _valid_uuid(intake_id)
        if iid is not None:
            row = engine.query_one(
                "SELECT intake_id,resolved_listing_id FROM intake.intakes "
                "WHERE tenant_id=? AND intake_id=? AND deleted_at IS NULL",
                (clean_tenant, iid),
            )
            if row is not None and (
                row.get("resolved_listing_id") is not None
                or engine.query_one(
                    "SELECT 1 FROM expansion.promotion_decisions WHERE tenant_id=? AND intake_id=? LIMIT 1",
                    (clean_tenant, iid),
                ) is not None
                or engine.query_one(
                    "SELECT 1 FROM workflow.assignments WHERE tenant_id=? AND intake_id=? LIMIT 1",
                    (clean_tenant, iid),
                ) is not None
                or engine.query_one(
                    "SELECT 1 FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=? LIMIT 1",
                    (clean_tenant, iid),
                ) is not None
            ):
                return True
    return False


def _geo_cell_version(geo_row: dict[str, Any]) -> str:
    payload = {
        "geo_cell_id": str(geo_row["geo_cell_id"]),
        "h3_index": str(geo_row["h3_index"]),
        "h3_resolution": int(geo_row["h3_resolution"]),
        "parent_h3_index": geo_row.get("parent_h3_index"),
        "centroid_latitude": str(geo_row["centroid_latitude"]) if geo_row.get("centroid_latitude") is not None else None,
        "centroid_longitude": str(geo_row["centroid_longitude"]) if geo_row.get("centroid_longitude") is not None else None,
        "admin_city": geo_row.get("admin_city"),
        "admin_district": geo_row.get("admin_district"),
        "service_area_id": geo_row.get("service_area_id"),
    }
    return "geo-cell-v1:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timezone-aware datetime is required")
    return parsed.astimezone(UTC)


def _parse_optional_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _row_to_domain_address(row: dict[str, Any]) -> AddressLocation:
    return AddressLocation(
        address_id=str(row["address_id"]),
        raw_address=str(row.get("raw_address") or ""),
        normalized_address=str(row.get("normalized_address") or ""),
        city=str(row.get("city") or ""),
        district=str(row.get("district") or ""),
        village=str(row.get("village") or ""),
        road=str(row.get("road") or ""),
        latitude=float(row["latitude"]) if row.get("latitude") is not None else None,
        longitude=float(row["longitude"]) if row.get("longitude") is not None else None,
        geocode_precision=str(row.get("geocode_precision") or "manual"),
        geocode_confidence=float(row["geocode_confidence"]) if row.get("geocode_confidence") is not None else None,
        h3_res_8=str(row.get("h3_res_8") or ""),
        h3_res_9=str(row.get("h3_res_9") or ""),
        h3_res_10=str(row.get("h3_res_10") or ""),
        manual_override_flag=bool(row.get("manual_override_flag")),
        tenant_id=str(row.get("tenant_id") or ""),
        revision=int(row.get("revision") or 1),
    )


def load_native_address(
    engine: Any,
    tenant: str,
    *,
    address_id: str | None = None,
    normalized_address: str | None = None,
    lock_mode: int = 0,
) -> tuple[dict[str, Any], AddressLocation]:
    """Load an actual tenant-qualified core.address_locations row; never manufacture one."""
    clean_tenant = _ensure_tenant_context(engine, tenant)
    suffix = _LOCK_SUFFIX[lock_mode]
    if address_id is not None:
        clean_addr_id = _valid_uuid(address_id)
        if clean_addr_id is None:
            raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_ADDRESS_UNAVAILABLE")
        row = engine.query_one(
            "SELECT address_id,raw_address,normalized_address,city,district,village,road,"  # nosec B608 -- fixed literal lock suffix; values bound as ?.
            "latitude,longitude,geocode_precision,geocode_confidence,h3_res_8,h3_res_9,h3_res_10,"
            "manual_override_flag,tenant_id,revision FROM core.address_locations "
            "WHERE address_id=? AND tenant_id=?" + suffix,
            (clean_addr_id, clean_tenant),
        )
        if row is None:
            raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_ADDRESS_UNAVAILABLE")
        if normalized_address and row.get("normalized_address") and row["normalized_address"] != normalized_address:
            raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_ADDRESS_MISMATCH")
        return row, _row_to_domain_address(row)
    if not normalized_address or not normalized_address.strip():
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_ADDRESS_UNAVAILABLE")
    rows = engine.query(
        "SELECT address_id,raw_address,normalized_address,city,district,village,road,"  # nosec B608 -- fixed literal lock suffix; values bound as ?.
        "latitude,longitude,geocode_precision,geocode_confidence,h3_res_8,h3_res_9,h3_res_10,"
        "manual_override_flag,tenant_id,revision FROM core.address_locations "
        "WHERE tenant_id=? AND normalized_address=? ORDER BY address_id LIMIT 2" + suffix,
        (clean_tenant, normalized_address),
    )
    if len(rows) != 1:
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_ADDRESS_UNAVAILABLE")
    return rows[0], _row_to_domain_address(rows[0])


def load_native_geo_cell(engine: Any, h3_index: str, *, lock_mode: int = 0) -> tuple[dict[str, Any], str]:
    """Load an actual geo.h3_cells record; unknown Geo fails closed."""
    if not isinstance(h3_index, str) or not h3_index.strip():
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_GEO_CELL_UNAVAILABLE")
    suffix = _LOCK_SUFFIX[lock_mode]
    row = engine.query_one(
        "SELECT geo_cell_id,h3_index,h3_resolution,parent_h3_index,centroid_latitude,"  # nosec B608 -- fixed literal lock suffix; values bound as ?.
        "centroid_longitude,admin_city,admin_district,service_area_id FROM geo.h3_cells "
        "WHERE h3_index=?" + suffix,
        (h3_index.strip(),),
    )
    if row is None:
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_GEO_CELL_UNAVAILABLE")
    return row, _geo_cell_version(row)


def _domain_listing_from_native_rows(
    tenant: str,
    listing_row: dict[str, Any],
    revision_row: dict[str, Any],
    address_id: str,
    *,
    domain_status: str,
) -> Listing:
    eff = _json_value(revision_row.get("effective_values"))
    if not isinstance(eff, dict):
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_LISTING_REVISION_CORRUPT")
    rent_raw = eff.get("rent") if eff.get("rent") is not None else eff.get("rent_amount", eff.get("rentPerMonth"))
    area_raw = eff.get("areaPing") if eff.get("areaPing") is not None else eff.get("area_ping", eff.get("area"))
    frontage_raw = eff.get("frontage_m") if eff.get("frontage_m") is not None else eff.get("frontageMeters", eff.get("frontage"))
    confidence_raw = eff.get("confidence")
    confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) and not isinstance(confidence_raw, bool) else None
    listing = Listing(
        listing_id=str(listing_row["listing_id"]),
        source_listing_id=str(listing_row.get("source_listing_id") or ""),
        source_id=str(listing_row["source_id"]),
        listing_status=domain_status,
        address_id=address_id,
        rent_amount=float(rent_raw) if isinstance(rent_raw, (int, float)) and not isinstance(rent_raw, bool) else 0.0,
        currency=str(eff.get("currency") or "TWD"),
        area_ping=float(area_raw) if isinstance(area_raw, (int, float)) and not isinstance(area_raw, bool) else 0.0,
        floor=str(eff.get("floor") or ""),
        frontage_m=float(frontage_raw) if isinstance(frontage_raw, (int, float)) and not isinstance(frontage_raw, bool) else 0.0,
        depth_m=float(eff["depth_m"]) if isinstance(eff.get("depth_m"), (int, float)) and not isinstance(eff.get("depth_m"), bool) else 0.0,
        corner_flag=bool(eff.get("corner_flag", False)),
        parking_flag=bool(eff.get("parking_flag", eff.get("parkingOrTemporaryStop", False))),
        utility_electricity_flag=bool(eff.get("utility_electricity_flag", False)),
        utility_drainage_flag=bool(eff.get("utility_drainage_flag", False)),
        utility_gas_flag=bool(eff.get("utility_gas_flag", False)),
        available_from=_parse_optional_date(eff.get("available_from")),
        snapshot_id=str(revision_row["source_snapshot_id"]) if revision_row.get("source_snapshot_id") else "",
        confidence=confidence,
        tenant_id=tenant,
    )
    object.__setattr__(listing, "property_id", str(listing_row["property_id"]))
    if listing_row.get("current_observation_id") is not None:
        object.__setattr__(listing, "listing_obs_id", str(listing_row["current_observation_id"]))
    for attr, keys in (
        ("prior_90d_cell_net_revenue", ("prior_90d_cell_net_revenue", "prior90dCellNetRevenue")),
        ("prior_90d_cell_transaction_count", ("prior_90d_cell_transaction_count", "prior90dCellTransactionCount")),
        ("prior_90d_cell_store_count", ("prior_90d_cell_store_count", "prior90dCellStoreCount")),
        ("feature_snapshot_time", ("feature_snapshot_time", "featureSnapshotTime")),
        ("rent_benchmark_median", ("rent_benchmark_median", "rentBenchmarkMedian")),
        ("rent_benchmark_p25", ("rent_benchmark_p25", "rentBenchmarkP25")),
        ("rent_benchmark_p75", ("rent_benchmark_p75", "rentBenchmarkP75")),
        ("rent_benchmark_sample_count", ("rent_benchmark_sample_count", "rentBenchmarkSampleCount")),
        ("rent_benchmark_id", ("rent_benchmark_id", "rentBenchmarkId")),
    ):
        for key in keys:
            if key in eff and eff[key] is not None:
                val = eff[key]
                if attr == "feature_snapshot_time":
                    val = _parse_optional_datetime(val)
                elif attr in {"prior_90d_cell_transaction_count", "prior_90d_cell_store_count", "rent_benchmark_sample_count"}:
                    val = int(val)
                elif attr in {"prior_90d_cell_net_revenue", "rent_benchmark_median", "rent_benchmark_p25", "rent_benchmark_p75"}:
                    val = float(val)
                object.__setattr__(listing, attr, val)
                break
    return listing


def prove_native_listing_candidate_relation(
    engine: Any,
    tenant: str,
    *,
    listing_id: str,
    candidate_site_id: str | None = None,
    promotion_decision_id: str | None = None,
    expected_listing_version: int | None = None,
    expected_revision_id: str | None = None,
) -> dict[str, Any]:
    """Prove the verified tenant-qualified candidate/promotion/sourceListing relation.

    SQL expansion.listings remains ACTIVE; this check proves candidate
    membership in the same SQL unit without writing illegal CANDIDATE or shadow state.
    """
    clean_tenant = _ensure_tenant_context(engine, tenant)
    lid = _valid_uuid(listing_id)
    if lid is None:
        raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "NATIVE_LISTING_CANDIDATE_RELATION_UNPROVEN")
    listing = engine.query_one(
        "SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=? FOR SHARE",
        (clean_tenant, lid),
    )
    if (
        listing is None
        or listing["lifecycle_state"] != "ACTIVE"
        or listing["legal_hold"]
        or listing["archived_at"] is not None
        or listing["property_id"] is None
        or listing["current_revision_id"] is None
        or (expected_listing_version is not None and listing["version"] != expected_listing_version)
        or (expected_revision_id is not None and str(listing["current_revision_id"]) != expected_revision_id)
    ):
        raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "NATIVE_LISTING_CANDIDATE_RELATION_UNPROVEN")
    prop = engine.query_one(
        "SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=? FOR SHARE",
        (clean_tenant, listing["property_id"]),
    )
    if prop is None or prop["status"] != "ACTIVE" or prop["current_redirect_property_id"] is not None:
        raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "NATIVE_LISTING_CANDIDATE_RELATION_UNPROVEN")
    if candidate_site_id is not None:
        cid = _valid_uuid(candidate_site_id)
        if cid is None:
            raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "NATIVE_LISTING_CANDIDATE_RELATION_UNPROVEN")
        candidate = engine.query_one(
            "SELECT * FROM expansion.candidate_sites WHERE tenant_id=? AND candidate_site_id=? FOR SHARE",
            (clean_tenant, cid),
        )
    else:
        rows = engine.query(
            "SELECT * FROM expansion.candidate_sites WHERE tenant_id=? AND source_listing_id=? "
            "AND status NOT IN ('REJECTED','OPENED') ORDER BY created_at DESC,candidate_site_id DESC LIMIT 2 FOR SHARE",
            (clean_tenant, lid),
        )
        candidate = rows[0] if len(rows) == 1 else None
    if (
        candidate is None
        or str(candidate["source_listing_id"]) != lid
        or str(candidate["property_id"]) != str(listing["property_id"])
    ):
        raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "NATIVE_LISTING_CANDIDATE_RELATION_UNPROVEN")
    pid = _valid_uuid(promotion_decision_id) if promotion_decision_id is not None else str(candidate["promotion_decision_id"])
    if pid is None or str(candidate["promotion_decision_id"]) != pid:
        raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "NATIVE_LISTING_CANDIDATE_RELATION_UNPROVEN")
    promo = engine.query_one(
        "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? AND promotion_decision_id=? FOR SHARE",
        (clean_tenant, pid),
    )
    if (
        promo is None
        or str(promo["listing_id"]) != lid
        or str(promo["property_id"]) != str(listing["property_id"])
        or promo["target_format_code"] != candidate["target_format_code"]
        or promo["status"] not in {"CANDIDATE_CREATING", "CANDIDATE_CREATED", "SCORE_QUEUED", "COMPLETED", "SCORE_FAILED"}
        or (promo["candidate_site_id"] is not None and str(promo["candidate_site_id"]) != str(candidate["candidate_site_id"]))
    ):
        raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "NATIVE_LISTING_CANDIDATE_RELATION_UNPROVEN")
    return {"listing": listing, "property": prop, "candidate": candidate, "promotion": promo}


def load_native_listing_bundle(
    engine: Any,
    tenant: str,
    listing_id: str,
    *,
    expected_address_id: str | None = None,
    lock_mode: int = 0,
    domain_status: str | None = None,
) -> dict[str, Any]:
    """Load complete normalized SQL Listing, Revision, Property, Address and Geo."""
    clean_tenant = _ensure_tenant_context(engine, tenant)
    lid = _valid_uuid(listing_id)
    if lid is None:
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_LISTING_UNAVAILABLE")
    suffix = _LOCK_SUFFIX[lock_mode]
    listing_row = engine.query_one(
        "SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=?" + suffix,  # nosec B608 -- fixed literal lock suffix; values bound as ?.
        (clean_tenant, lid),
    )
    if listing_row is None:
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_LISTING_UNAVAILABLE")
    if (
        listing_row["property_id"] is None
        or listing_row["current_revision_id"] is None
        or listing_row["archived_at"] is not None
    ):
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_LISTING_LINEAGE_INCOMPLETE")
    revision_row = engine.query_one(
        "SELECT * FROM expansion.listing_revisions WHERE tenant_id=? AND listing_id=? AND listing_revision_id=?" + suffix,  # nosec B608
        (clean_tenant, lid, listing_row["current_revision_id"]),
    )
    if revision_row is None:
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_LISTING_REVISION_UNAVAILABLE")
    prop_row = engine.query_one(
        "SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=?" + suffix,  # nosec B608
        (clean_tenant, listing_row["property_id"]),
    )
    if prop_row is None or prop_row["status"] != "ACTIVE" or prop_row["current_redirect_property_id"] is not None:
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_PROPERTY_UNAVAILABLE")
    eff = _json_value(revision_row.get("effective_values"))
    if not isinstance(eff, dict):
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_LISTING_REVISION_CORRUPT")
    explicit_addr_id = expected_address_id or eff.get("address_id") or eff.get("addressId")
    address_row, domain_address = load_native_address(
        engine,
        clean_tenant,
        address_id=str(explicit_addr_id) if explicit_addr_id is not None else None,
        normalized_address=prop_row.get("normalized_address"),
        lock_mode=min(lock_mode, 1),
    )
    geo_row, geo_version = load_native_geo_cell(engine, domain_address.h3_res_9, lock_mode=min(lock_mode, 1))
    effective_status = domain_status if domain_status is not None else str(listing_row["lifecycle_state"]).lower()
    domain_listing = _domain_listing_from_native_rows(
        clean_tenant,
        listing_row,
        revision_row,
        domain_address.address_id,
        domain_status=effective_status,
    )
    return {
        "listing_row": listing_row,
        "revision_row": revision_row,
        "property_row": prop_row,
        "address_row": address_row,
        "geo_row": geo_row,
        "geo_version": geo_version,
        "domain_listing": domain_listing,
        "domain_address": domain_address,
    }


def _recorded_source_policy_version(engine: Any, tenant: str, intake_id: str) -> int | None:
    row = engine.query_one(
        "SELECT source_policy_version FROM intake.intake_stage_transitions "
        "WHERE tenant_id=? AND intake_id=? AND source_policy_version IS NOT NULL "
        "ORDER BY sequence_no DESC LIMIT 1",
        (tenant, intake_id),
    )
    if row is None or row.get("source_policy_version") is None:
        return None
    return int(row["source_policy_version"])


def load_native_intake_projection(
    engine: Any,
    tenant: str,
    intake_id: str,
    *,
    gate_snapshot: dict[str, Any] | None = None,
    revision_row: dict[str, Any] | None = None,
    lock_mode: int = 0,
) -> dict[str, Any]:
    """Project one native SQL Intake + pinned source/snapshot/parser facts."""
    clean_tenant = _ensure_tenant_context(engine, tenant)
    iid = _valid_uuid(intake_id)
    if iid is None:
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_INTAKE_UNAVAILABLE")
    suffix = _LOCK_SUFFIX[lock_mode]
    intake_row = engine.query_one(
        "SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? AND deleted_at IS NULL" + suffix,  # nosec B608
        (clean_tenant, iid),
    )
    if intake_row is None:
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_INTAKE_UNAVAILABLE")
    recorded_policy_version = _recorded_source_policy_version(engine, clean_tenant, iid)
    if gate_snapshot is not None:
        policy_version = gate_snapshot.get("source_policy_version")
        if recorded_policy_version is not None and policy_version != recorded_policy_version:
            raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_SOURCE_POLICY_VERSION_MISMATCH")
    else:
        policy_version = recorded_policy_version

    snapshot_id = (
        str(revision_row["source_snapshot_id"])
        if revision_row is not None and revision_row.get("source_snapshot_id") is not None
        else (gate_snapshot.get("source_snapshot_id") if isinstance(gate_snapshot, dict) else None)
    )
    parser_run_id = (
        str(revision_row["parser_run_id"])
        if revision_row is not None and revision_row.get("parser_run_id") is not None
        else (gate_snapshot.get("parser_run_id") if isinstance(gate_snapshot, dict) else None)
    )
    parser_release_id = None
    parser_release_version = None
    parser_input_schema = None
    parser_output_schema = None
    parser_run_version = None
    snapshot_version = None
    if snapshot_id is not None:
        snap = engine.query_one(
            "SELECT source_snapshot_id,capture_method,version FROM intake.source_snapshots "
            "WHERE tenant_id=? AND source_snapshot_id=? FOR SHARE",
            (clean_tenant, snapshot_id),
        )
        if snap is not None:
            snapshot_version = int(snap["version"])
            if snap["capture_method"] != "MANUAL_ENTRY":
                if parser_run_id is not None:
                    run = engine.query_one(
                        "SELECT parser_run_id,parser_release_id,version FROM intake.parser_runs "
                        "WHERE tenant_id=? AND parser_run_id=? FOR SHARE",
                        (clean_tenant, parser_run_id),
                    )
                    if run is not None:
                        parser_run_version = int(run["version"])
                        parser_release_id = str(run["parser_release_id"])
                        if isinstance(gate_snapshot, dict) and gate_snapshot.get("parser_release_version") is not None:
                            parser_release_version = gate_snapshot.get("parser_release_version")
                            parser_input_schema = gate_snapshot.get("parser_input_schema_version")
                            parser_output_schema = gate_snapshot.get("parser_output_schema_version")
                        else:
                            release = engine.query_one(
                                "SELECT version,input_schema_version,output_schema_version FROM intake.parser_releases "
                                "WHERE parser_release_id=? FOR SHARE",
                                (parser_release_id,),
                            )
                            if release is not None:
                                parser_release_version = int(release["version"])
                                parser_input_schema = release["input_schema_version"]
                                parser_output_schema = release["output_schema_version"]
    scope = {
        "tenant_id": clean_tenant,
        **{
            key: str(intake_row[key])
            for key in ("brand_id", "region_id", "assigned_area_id", "heat_zone_id")
            if intake_row.get(key) is not None
        },
    }
    return {
        "parent_owner": "native_sql",
        "intake_id": str(intake_row["intake_id"]),
        "id": str(intake_row["intake_id"]),
        "tenantId": clean_tenant,
        "scope": scope,
        "state": intake_row["processing_state"],
        "stage": intake_row["processing_state"],
        "processing_state": intake_row["processing_state"],
        "version": int(intake_row["version"]),
        "intake_method": intake_row["intake_method"],
        "source_id": intake_row["source_id"],
        "policy_state": intake_row["source_policy_state"],
        "source_policy_version": policy_version,
        "source_snapshot_id": snapshot_id,
        "source_snapshot_version": snapshot_version,
        "parser_run_id": parser_run_id,
        "parser_run_version": parser_run_version,
        "parser_release_id": parser_release_id,
        "parser_release_version": parser_release_version,
        "parser_input_schema_version": parser_input_schema,
        "parser_output_schema_version": parser_output_schema,
        "resolved_listing_id": str(intake_row["resolved_listing_id"]) if intake_row.get("resolved_listing_id") else None,
        "correlation_id": str(intake_row["correlation_id"]),
        "submitted_by": str(intake_row["submitter_subject_id"]),
        "legal_hold": bool(intake_row.get("legal_hold")),
    }


def _row_to_promotion_dict(row: dict[str, Any], *, audit_event_id: str | None = None) -> dict[str, Any]:
    gate_snapshot = _json_value(row.get("gate_snapshot"))
    if not isinstance(gate_snapshot, dict):
        gate_snapshot = {}
    caller_sha = gate_snapshot.get("caller_gate_snapshot_sha256") or gate_snapshot.get("server_gate_snapshot_sha256") or ""
    result = {
        "parent_owner": "native_sql",
        "promotion_decision_id": str(row["promotion_decision_id"]),
        "tenant_id": str(row["tenant_id"]),
        "intake_id": str(row["intake_id"]),
        "listing_id": str(row["listing_id"]),
        "property_id": str(row["property_id"]),
        "target_format_code": row["target_format_code"],
        "status": row["status"],
        "decision_type": row.get("decision_type") or "STANDARD",
        "version": int(row["version"]),
        "proposer": str(row["proposer_subject_id"]),
        "proposer_subject_id": str(row["proposer_subject_id"]),
        "reviewer": str(row["reviewer_subject_id"]) if row.get("reviewer_subject_id") else None,
        "reviewer_subject_id": str(row["reviewer_subject_id"]) if row.get("reviewer_subject_id") else None,
        "reason": row["reason"],
        "risk_acknowledged": bool(row.get("risk_acknowledged")),
        "gate_snapshot": gate_snapshot,
        "gate_snapshot_sha256": caller_sha,
        "candidate_site_id": str(row["candidate_site_id"]) if row.get("candidate_site_id") else None,
        "site_score_job_id": str(row["site_score_job_id"]) if row.get("site_score_job_id") else None,
        "created_at": _instant(row.get("created_at")),
        "reviewed_at": _instant(row.get("reviewed_at")),
        "executed_at": _instant(row.get("executed_at")),
    }
    if audit_event_id is not None:
        result["audit_event_id"] = audit_event_id
    return result


def reconstruct_native_candidate_draft(
    engine: Any,
    tenant: str,
    candidate_site_id: str,
    *,
    document_store: Any = None,
    lock_mode: int = 0,
) -> CandidateSiteDraft | None:
    """Reconstruct native CandidateSiteDraft from immutable SQL references plus live fences."""
    clean_tenant = _ensure_tenant_context(engine, tenant)
    cid = _valid_uuid(candidate_site_id)
    if cid is None:
        return None
    suffix = _LOCK_SUFFIX[lock_mode]
    cand_row = engine.query_one(
        "SELECT * FROM expansion.candidate_sites WHERE tenant_id=? AND candidate_site_id=?" + suffix,  # nosec B608
        (clean_tenant, cid),
    )
    if cand_row is None:
        return None
    promo_row = engine.query_one(
        "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? AND promotion_decision_id=?" + suffix,  # nosec B608
        (clean_tenant, cand_row["promotion_decision_id"]),
    )
    if (
        promo_row is None
        or str(promo_row["listing_id"]) != str(cand_row["source_listing_id"])
        or str(promo_row["property_id"]) != str(cand_row["property_id"])
        or promo_row["target_format_code"] != cand_row["target_format_code"]
        or (promo_row["candidate_site_id"] is not None and str(promo_row["candidate_site_id"]) != cid)
    ):
        raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "NATIVE_CANDIDATE_PROMOTION_BINDING_DENIED")
    gate_snapshot = _json_value(promo_row.get("gate_snapshot"))
    if not isinstance(gate_snapshot, dict) or not gate_snapshot.get("address_id"):
        raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_CANDIDATE_GATE_SNAPSHOT_INVALID")
    bundle = load_native_listing_bundle(
        engine,
        clean_tenant,
        str(cand_row["source_listing_id"]),
        expected_address_id=str(gate_snapshot["address_id"]),
        lock_mode=min(lock_mode, 1),
        domain_status="candidate" if True else "active",
    )
    if bundle["listing_row"]["lifecycle_state"] != "ACTIVE":
        raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "NATIVE_LISTING_NOT_ACTIVE")
    domain_listing = bundle["domain_listing"]
    domain_address = bundle["domain_address"]
    hr_failures = gate_snapshot.get("hard_rule_failures")
    if isinstance(hr_failures, list) and all(isinstance(item, str) for item in hr_failures):
        object.__setattr__(domain_listing, "hard_rule_failures", list(hr_failures))

    c_site = CandidateSite(
        candidate_site_id=cid,
        listing_id=str(cand_row["source_listing_id"]),
        address_id=domain_address.address_id,
        target_format_code=cand_row["target_format_code"],
        site_status=domain_candidate_status(cand_row["status"]),
        created_by=str(promo_row.get("reviewer_subject_id") or promo_row["proposer_subject_id"]),
        created_at=_parse_optional_datetime(cand_row.get("created_at")) or datetime.now(UTC),
    )
    object.__setattr__(c_site, "tenant_id", clean_tenant)
    object.__setattr__(c_site, "promotion_decision_id", str(cand_row["promotion_decision_id"]))
    object.__setattr__(c_site, "version", int(cand_row["version"]))

    recommendation = None
    model_version = None
    review_id = None
    job_row = engine.query_one(
        "SELECT status,result FROM workflow.jobs WHERE tenant_id=? AND job_type='intake-sitescore' "
        "AND aggregate_type='candidate_site' AND aggregate_id=? ORDER BY created_at DESC,job_id DESC LIMIT 1",
        (clean_tenant, cid),
    )
    if job_row is not None and job_row["status"] == "SUCCEEDED":
        result = _json_value(job_row.get("result"))
        if isinstance(result, dict):
            report_id = result.get("report_id")
            if document_store is not None and isinstance(report_id, str) and report_id:
                report = document_store.get("sitescore.reports", report_id)
                if report is not None and report.candidate_site_id == cid:
                    recommendation = getattr(report.recommendation, "value", report.recommendation)
                    model_version = report.model_version
            if model_version is None and isinstance(result.get("model_version"), str):
                model_version = result["model_version"]
    if document_store is not None:
        for decision in document_store.list_all("sitescore.decisions"):
            if getattr(decision, "candidate_site_id", None) == cid:
                review_id = getattr(decision, "decision_id", None)
                break

    draft = CandidateSiteDraft(
        listing=domain_listing,
        address=domain_address,
        candidate_site=c_site,
        feasibility_flags=tuple(hr_failures) if isinstance(hr_failures, list) else (),
        heat_zone_id=domain_address.h3_res_9,
        listing_source=domain_listing.source_id,
        status="CANDIDATE",
        score=None,
        recommendation=recommendation,
        model_version=model_version,
        dataset_snapshot_id=domain_listing.snapshot_id or None,
        review_id=review_id,
        prior_90d_cell_net_revenue=getattr(domain_listing, "prior_90d_cell_net_revenue", None),
        prior_90d_cell_transaction_count=getattr(domain_listing, "prior_90d_cell_transaction_count", None),
        prior_90d_cell_store_count=getattr(domain_listing, "prior_90d_cell_store_count", None),
        feature_snapshot_time=getattr(domain_listing, "feature_snapshot_time", None),
        property_entity_id=str(cand_row["property_id"]),
        listing_observation_id=getattr(domain_listing, "listing_obs_id", None),
        rent_benchmark_median=getattr(domain_listing, "rent_benchmark_median", None),
        rent_benchmark_p25=getattr(domain_listing, "rent_benchmark_p25", None),
        rent_benchmark_p75=getattr(domain_listing, "rent_benchmark_p75", None),
        rent_benchmark_sample_count=getattr(domain_listing, "rent_benchmark_sample_count", None),
        rent_benchmark_id=getattr(domain_listing, "rent_benchmark_id", None),
    )
    object.__setattr__(draft, "parent_owner", "native_sql")
    return draft


def list_native_candidate_drafts(engine: Any, tenant: str, *, document_store: Any = None) -> list[CandidateSiteDraft]:
    clean_tenant = _ensure_tenant_context(engine, tenant)
    rows = engine.query(
        "SELECT candidate_site_id FROM expansion.candidate_sites WHERE tenant_id=? ORDER BY created_at,candidate_site_id",
        (clean_tenant,),
    )
    drafts: list[CandidateSiteDraft] = []
    for row in rows:
        draft = reconstruct_native_candidate_draft(
            engine, clean_tenant, str(row["candidate_site_id"]), document_store=document_store
        )
        if draft is not None:
            drafts.append(draft)
    return drafts


def save_native_candidate_cas(
    engine: Any,
    tenant: str,
    candidate: CandidateSiteDraft,
    *,
    expected_state: str | None = None,
) -> dict[str, Any]:
    """Insert or CAS-update a native expansion.candidate_sites row without shadow docs."""
    clean_tenant = _ensure_tenant_context(engine, tenant)
    site = candidate.candidate_site
    cid = _valid_uuid(site.candidate_site_id)
    if cid is None:
        raise ValueError("Native candidate requires a UUID candidate_site_id")
    existing = engine.query_one(
        "SELECT * FROM expansion.candidate_sites WHERE tenant_id=? AND candidate_site_id=? FOR UPDATE",
        (clean_tenant, cid),
    )
    target_sql = sql_candidate_status(site.site_status)
    if existing is None:
        promo_id = _valid_uuid(getattr(site, "promotion_decision_id", None))
        lid = _valid_uuid(site.listing_id)
        prop_id = _valid_uuid(candidate.property_entity_id or getattr(candidate.listing, "property_id", None))
        if promo_id is None or lid is None or prop_id is None:
            raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "NATIVE_CANDIDATE_RELATION_REQUIRED")
        promo = engine.query_one(
            "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? AND promotion_decision_id=? FOR UPDATE",
            (clean_tenant, promo_id),
        )
        if (
            promo is None
            or str(promo["listing_id"]) != lid
            or str(promo["property_id"]) != prop_id
            or promo["target_format_code"] != site.target_format_code
            or promo["status"] != "CANDIDATE_CREATING"
        ):
            raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "NATIVE_CANDIDATE_PROMOTION_CONFLICT")
        try:
            row = engine.query_one(
                "INSERT INTO expansion.candidate_sites (candidate_site_id,tenant_id,property_id,"
                "source_listing_id,promotion_decision_id,target_format_code,status,version) "
                "VALUES (?,?,?,?,?,?,?,1) RETURNING *",
                (cid, clean_tenant, prop_id, lid, promo_id, site.target_format_code, target_sql),
            )
        except Exception as exc:
            if (
                getattr(exc, "sqlstate", None) == "23505"
                and getattr(getattr(exc, "diag", None), "constraint_name", None) == "ux_active_candidate_property_format"
            ):
                raise DomainValidationError(DenialCode.DEPENDENCY_CONFLICT, "DUPLICATE_CANDIDATE") from exc
            raise
        object.__setattr__(site, "version", int(row["version"]))
        return row

    if expected_state is not None and existing["status"] != sql_candidate_status(expected_state):
        raise ValueError("CANDIDATE_REVIEW_PRECONDITION_CONFLICT")
    expected_version = getattr(site, "version", None)
    if expected_version is not None and int(existing["version"]) != int(expected_version):
        raise IdentityConflictError("NATIVE_CANDIDATE_VERSION_CONFLICT")
    if existing["status"] in {"APPROVED", "OPENED"} and expected_state is None:
        # Retries and score callbacks never overwrite approved/opened decisions.
        object.__setattr__(site, "version", int(existing["version"]))
        return existing
    if target_sql == existing["status"]:
        object.__setattr__(site, "version", int(existing["version"]))
        return existing
    updated = engine.query_one(
        "UPDATE expansion.candidate_sites SET status=?,version=version+1,updated_at=clock_timestamp() "
        "WHERE tenant_id=? AND candidate_site_id=? AND version=? RETURNING *",
        (target_sql, clean_tenant, cid, int(existing["version"])),
    )
    if updated is None:
        raise IdentityConflictError("NATIVE_CANDIDATE_VERSION_CONFLICT")
    object.__setattr__(site, "version", int(updated["version"]))
    return updated


def read_native_current_candidate_inputs(
    engine: Any,
    tenant: str,
    candidate: Any,
) -> tuple[Listing, AddressLocation, dict[str, Any]]:
    """Independently verify native SQL parent/candidate/listing/address/geo/hard-rule proofs."""
    from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied
    from modules.listing.application.promotion import evaluate_native_hard_rules

    clean_tenant = _ensure_tenant_context(engine, tenant)
    cid = _valid_uuid(getattr(candidate.candidate_site, "candidate_site_id", None))
    lid = _valid_uuid(getattr(candidate.candidate_site, "listing_id", None))
    if cid is None or lid is None:
        raise ScoreDeliveryDenied("SITESCORE_CURRENT_INPUT_UNAVAILABLE")
    try:
        relation = prove_native_listing_candidate_relation(
            engine,
            clean_tenant,
            listing_id=lid,
            candidate_site_id=cid,
        )
    except (DomainValidationError, ValueError) as exc:
        raise ScoreDeliveryDenied("SITESCORE_CURRENT_CANDIDATE_GATE_DENIED") from exc
    promo = relation["promotion"]
    listing_row = relation["listing"]
    prop_row = relation["property"]
    gate_snapshot = _json_value(promo.get("gate_snapshot"))
    if not isinstance(gate_snapshot, dict):
        raise ScoreDeliveryDenied("SITESCORE_CURRENT_INPUT_UNAVAILABLE")
    intake_row = engine.query_one(
        "SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? AND deleted_at IS NULL FOR SHARE",
        (clean_tenant, promo["intake_id"]),
    )
    if (
        intake_row is None
        or intake_row["processing_state"] != "READY"
        or intake_row["legal_hold"]
        or str(intake_row.get("resolved_listing_id") or "") != lid
    ):
        raise ScoreDeliveryDenied("SITESCORE_CURRENT_INPUT_UNAVAILABLE")
    if (
        gate_snapshot.get("listing_version") != int(listing_row["version"])
        or gate_snapshot.get("listing_revision_id") != str(listing_row["current_revision_id"])
        or gate_snapshot.get("property_version") != int(prop_row["version"])
        or gate_snapshot.get("property_id") != str(prop_row["property_id"])
    ):
        raise ScoreDeliveryDenied("SITESCORE_CANDIDATE_INPUT_STALE")
    try:
        bundle = load_native_listing_bundle(
            engine,
            clean_tenant,
            lid,
            expected_address_id=str(gate_snapshot.get("address_id") or ""),
            lock_mode=1,
            domain_status="active",
        )
    except (DomainValidationError, ValueError) as exc:
        raise ScoreDeliveryDenied("SITESCORE_CURRENT_INPUT_UNAVAILABLE") from exc
    address_row = bundle["address_row"]
    geo_row = bundle["geo_row"]
    if (
        str(address_row["address_id"]) != str(candidate.candidate_site.address_id)
        or int(address_row["revision"]) != gate_snapshot.get("address_revision")
        or str(geo_row["geo_cell_id"]) != gate_snapshot.get("geo_cell_id")
        or bundle["geo_version"] != gate_snapshot.get("geo_version")
    ):
        raise ScoreDeliveryDenied("SITESCORE_CANDIDATE_INPUT_STALE")
    try:
        _, failures, policy_sha = evaluate_native_hard_rules(
            bundle["domain_listing"],
            bundle["domain_address"],
            target_format_code=promo["target_format_code"],
        )
    except DomainValidationError as exc:
        raise ScoreDeliveryDenied("SITESCORE_CURRENT_HARD_RULE_GATE_DENIED") from exc
    if policy_sha != gate_snapshot.get("policy_sha256") or failures:
        raise ScoreDeliveryDenied("SITESCORE_CURRENT_HARD_RULE_GATE_DENIED")
    verified_listing = _domain_listing_from_native_rows(
        clean_tenant,
        listing_row,
        bundle["revision_row"],
        bundle["domain_address"].address_id,
        domain_status="candidate",
    )
    object.__setattr__(verified_listing, "hard_rule_failures", list(failures))
    current_attrs = {key: value for key, value in vars(verified_listing).items() if key != "listing_status"}
    current_attrs["hard_rule_failures"] = list(failures)
    return verified_listing, bundle["domain_address"], current_attrs


def load_native_score_parents(
    engine: Any,
    tenant: str,
    *,
    intake_id: str,
    promotion_decision_id: str,
    candidate_site_id: str,
    job_id: str,
    lock_mode: int = 0,
    document_store: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], CandidateSiteDraft]:
    """Load and verify native SQL intake/promotion/candidate parents in canonical lock order."""
    from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied

    clean_tenant = _ensure_tenant_context(engine, tenant)
    iid = _valid_uuid(intake_id)
    pid = _valid_uuid(promotion_decision_id)
    cid = _valid_uuid(candidate_site_id)
    jid = _valid_uuid(job_id)
    if None in (iid, pid, cid, jid):
        raise ScoreDeliveryDenied("SITESCORE_PARENT_BINDING_DENIED")
    suffix = _LOCK_SUFFIX[lock_mode]
    promo_row = engine.query_one(
        "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? AND promotion_decision_id=?" + suffix,  # nosec B608
        (clean_tenant, pid),
    )
    if (
        promo_row is None
        or str(promo_row["intake_id"]) != iid
        or str(promo_row.get("candidate_site_id") or "") != cid
        or str(promo_row.get("site_score_job_id") or "") != jid
    ):
        raise ScoreDeliveryDenied("SITESCORE_PARENT_BINDING_DENIED")
    gate_snapshot = _json_value(promo_row.get("gate_snapshot"))
    if not isinstance(gate_snapshot, dict):
        raise ScoreDeliveryDenied("SITESCORE_PARENT_BINDING_DENIED")
    try:
        candidate = reconstruct_native_candidate_draft(
            engine, clean_tenant, cid, document_store=document_store, lock_mode=lock_mode
        )
    except (DomainValidationError, ValueError) as exc:
        raise ScoreDeliveryDenied("SITESCORE_PARENT_BINDING_DENIED") from exc
    if candidate is None or str(candidate.candidate_site.listing_id) != str(promo_row["listing_id"]):
        raise ScoreDeliveryDenied("SITESCORE_PARENT_BINDING_DENIED")
    revision_row = engine.query_one(
        "SELECT r.* FROM expansion.listings l JOIN expansion.listing_revisions r "
        "ON r.tenant_id=l.tenant_id AND r.listing_id=l.listing_id AND r.listing_revision_id=l.current_revision_id "
        "WHERE l.tenant_id=? AND l.listing_id=? FOR SHARE",
        (clean_tenant, promo_row["listing_id"]),
    )
    try:
        intake = load_native_intake_projection(
            engine, clean_tenant, iid, gate_snapshot=gate_snapshot, revision_row=revision_row, lock_mode=lock_mode
        )
    except (DomainValidationError, ValueError) as exc:
        raise ScoreDeliveryDenied("SITESCORE_PARENT_BINDING_DENIED") from exc
    if intake["resolved_listing_id"] != str(promo_row["listing_id"]):
        raise ScoreDeliveryDenied("SITESCORE_PARENT_BINDING_DENIED")
    audit_row = engine.query_one(
        "SELECT audit_event_id FROM audit.audit_events WHERE tenant_id=? "
        "AND resource_type='promotion_decision' AND resource_id=? AND result='SUCCEEDED' "
        "AND jsonb_typeof(after_value->'version')='number' AND after_value->>'version'=? "
        "AND after_value->>'status'=? ORDER BY sequence_no DESC LIMIT 1",
        (clean_tenant, pid, str(int(promo_row["version"])), promo_row["status"]),
    )
    promotion = _row_to_promotion_dict(
        promo_row,
        audit_event_id=str(audit_row["audit_event_id"]) if audit_row is not None else None,
    )
    return intake, promotion, candidate


def _instant(value) -> str | None:
    """Normalize one recorded aware instant to the shared command text form."""
    if value is None:
        return None
    at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if at.utcoffset() is None:
        raise IntakeWorkflowUnavailable("Recorded workflow time is not timezone-aware")
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_value(value):
    from json import loads

    return loads(value) if isinstance(value, str) else value


class _NativeWorkflowState(SimpleNamespace):
    """Request-local command surface for one locked native workflow.

    It mirrors the shared command store interface for exactly the recorded
    rows loaded in this SQL root. It is never flushed to durable_documents.
    """

    def canonical_audit_is_committed(self, audit) -> bool:
        return (
            any(audit is loaded for loaded in self.committed_audits)
            and audit.get("tenant_id") == self.intake["tenant_id"]
            and self.graph.audit_sink.is_committed(audit)
        )

    def canonical_decision_execution_is_committed(self, row, audit) -> bool:
        from shared.infrastructure.persistence.identity_decisions_sql import (
            CanonicalIdentityDecisionRepository,
        )

        proof = self.executed_decisions.get(row.get("decision_id"))
        if proof is None or not self.canonical_audit_is_committed(audit):
            return False
        decision, job = proof
        tenant = self.intake["tenant_id"]
        intake_id = self.intake["intake_id"]
        try:
            CanonicalIdentityDecisionRepository(self.graph).require_approval(tenant, decision)
            result = self.graph.execution_receipt(tenant, job, decision)
        except Exception:
            return False
        return (
            decision["status"] == "EXECUTED"
            and decision["version"] == row.get("version")
            and job["correlation_id"] == self.intake["correlation_id"]
            and result.get("audit_event_id") == audit.get("audit_event_id")
            and (
                result.get("intake_id") == intake_id
                if decision["decision_type"] in {"CREATE", "REVISE", "DUPLICATE"}
                else result.get("intake_id") in (None, intake_id)
            )
        )


class CanonicalIntakeRepository:
    def __init__(self, graph, workflow_policy=None, *, replay_store=None):
        self.graph, self.engine = graph, graph.engine
        self.workflow_policy = workflow_policy
        self.replay_store = replay_store

    def get(self, tenant: str, intake_id: str) -> dict:
        UUID(intake_id)
        with self.graph.transaction(tenant):
            return self.graph._one(
                "SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? AND deleted_at IS NULL",
                (tenant, intake_id),
            )

    def processing_history_span(self, tenant: str, intake_id: str, through: int | None = None) -> dict:
        """Bound the original transition rows, after the caller authorizes Intake.

        The HTTP owner retains the same SERIALIZABLE root and current-version
        fence. These are recorded sequences, never a reconstructed stage path.
        """
        with self.graph.transaction(tenant):
            return self.engine.query_one(
                "SELECT MIN(sequence_no) AS first_sequence,MAX(sequence_no) AS last_sequence "
                "FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=? "
                "AND (?::bigint IS NULL OR sequence_no<=?::bigint)", (tenant, intake_id, through, through))

    def processing_history(self, tenant: str, intake_id: str, *, through: int, position: int,
                           backward: bool, limit: int) -> list[dict]:
        """Project bounded metadata only, without raw reasons or command keys."""
        if type(limit) is not int or not 1 <= limit <= 26:
            raise ValueError("Native history read bound is required")
        comparison, order = ("<", "DESC") if backward else (">", "ASC")
        with self.graph.transaction(tenant):
            return self.engine.query(
                "SELECT transition_id,sequence_no,from_state,to_state,actor_subject_id,service_principal,"  # nosec B608 -- operator/order picked from two literals by bool; values bound as ?.
                "permission,reason_code,expected_version,resulting_version,job_id,source_policy_version,"
                "parser_release_id,source_snapshot_id,match_case_id,correlation_id,causation_id,occurred_at "
                "FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=? "
                f"AND sequence_no{comparison}? AND sequence_no<=? ORDER BY sequence_no {order} LIMIT ?",
                (tenant, intake_id, position, through, limit))

    def processing_evidence_position(self, tenant: str, intake_id: str, position, *, source_id, before) -> bool:
        """Validate a cursor's exact saved parent/reference, never a derived ID."""
        from modules.listing.application.native_intake_assisted_entry_request_proof import _time
        from modules.listing.application.native_intake_parser_runs import (
            CanonicalNativeParserRunRepository,
        )
        from shared.infrastructure.persistence.assisted_listing_intake import (
            NativeIntakeSnapshotMetadataRepository,
        )

        at, kind, reference = position
        with self.graph.transaction(tenant):
            if kind == "SNAPSHOT":
                row = NativeIntakeSnapshotMetadataRepository(self.graph).metadata_for_intake(
                    tenant, intake_id, reference, before=before)
                column = "stored_at"
            else:
                row = CanonicalNativeParserRunRepository(self.engine).metadata_for_intake(
                    tenant, intake_id, reference, before=before)
                column = "created_at"
            return row is not None and row["source_id"] == source_id and _time(row[column]) == at

    def processing_evidence(self, tenant: str, intake_id: str, *, through, position, backward, limit, before) -> list[dict]:
        """Merge bounded existing lifecycle records; no bodies or execution IO."""
        from modules.listing.application.native_intake_assisted_entry_request_proof import _time
        from modules.listing.application.native_intake_parser_runs import (
            CanonicalNativeParserRunRepository,
        )
        from shared.infrastructure.persistence.assisted_listing_intake import (
            NativeIntakeSnapshotMetadataRepository,
        )

        marker = None if position is None else (position[0].isoformat(), 0 if position[1] == "SNAPSHOT" else 1, position[2])
        with self.graph.transaction(tenant):
            runs = CanonicalNativeParserRunRepository(self.engine).metadata_page_for_intake(
                tenant, intake_id, through=through.isoformat(), position=marker, backward=backward, limit=limit, before=before)
            snapshots = NativeIntakeSnapshotMetadataRepository(self.graph).metadata_page_for_intake(
                tenant, intake_id, through=through.isoformat(), position=marker, backward=backward, limit=limit, before=before)
            records = [{"kind": "SNAPSHOT", "recorded_at": item["stored_at"], "metadata": item} for item in snapshots]
            records += [{"kind": "PARSER_RUN", "recorded_at": item["created_at"], "metadata": item} for item in runs]
            before.check_in_memory()
            records.sort(key=lambda item: (_time(item["recorded_at"]), 0 if item["kind"] == "SNAPSHOT" else 1,
                item["metadata"]["source_snapshot_id" if item["kind"] == "SNAPSHOT" else "parser_run_id"]), reverse=backward)
            return records[:limit]

    def parser_fields(self, intake, run_id, *, intake_version, run_version, before):
        """Read the selected actual run through its original complete proof.

        The caller first authorizes current HTTP view access and retains this
        same tenant SQL root. Recorded history never authorizes an action or
        supplies a saved Listing value.
        """
        from modules.listing.application.native_intake_parser_history import (
            NativeParserTerminalHistory,
        )
        from modules.listing.application.native_intake_parser_input import (
            NativeParserManualInputReader,
        )
        from modules.listing.application.native_intake_parser_job_proof import (
            parser_delivery_identity,
        )
        from modules.listing.application.native_intake_parser_policy import (
            JOB_TYPE,
            NativeParserConflict,
        )
        from modules.listing.application.native_intake_parser_runs import (
            CanonicalNativeParserRunRepository,
        )

        tenant, intake_id = intake["tenant_id"], intake["intake_id"]
        if intake["version"] != intake_version:
            raise IdentityConflictError("CANONICAL_INTAKE_VERSION_CONFLICT")
        metadata = CanonicalNativeParserRunRepository(self.engine).metadata_for_intake(
            tenant, intake_id, run_id, before=before)
        if metadata is None:
            raise IdentityNotFoundError("Canonical parser run not found")
        if metadata["source_id"] != intake["source_id"]:
            raise NativeParserConflict("NATIVE_PARSER_FIELD_LINEAGE_UNAVAILABLE")
        if metadata["version"] != run_version:
            raise IdentityConflictError("CANONICAL_PARSER_RUN_VERSION_CONFLICT")
        before()
        jobs = self.engine.query("SELECT job_id,status,version FROM workflow.jobs WHERE tenant_id=? "
            "AND job_type=? AND aggregate_type='parser_run' AND aggregate_id=? LIMIT 2", (tenant, JOB_TYPE, run_id))
        if len(jobs) != 1:
            raise NativeParserConflict("NATIVE_PARSER_FIELD_LINEAGE_UNAVAILABLE")
        before()
        delivery = self.graph.queue.get(jobs[0]["job_id"])
        if delivery is None:
            raise NativeParserConflict("NATIVE_PARSER_FIELD_LINEAGE_UNAVAILABLE")
        actual_tenant, generation = parser_delivery_identity(delivery)
        if actual_tenant != tenant:
            raise NativeParserConflict("NATIVE_PARSER_FIELD_LINEAGE_UNAVAILABLE")
        _, original, run = NativeParserTerminalHistory(self.graph).read_with_parent_input(
            delivery, tenant, generation, before=before)
        if (run["parser_run_id"] != run_id or run["version"] != run_version
                or run["intake_id"] != intake_id or run["source_snapshot_id"] != metadata["source_snapshot_id"]
                or original.lineage.source_id != intake["source_id"]):
            raise NativeParserConflict("NATIVE_PARSER_FIELD_LINEAGE_UNAVAILABLE")
        _, fields = NativeParserManualInputReader(self.graph).field_projection(
            original, run, before=before, include_observations=True)
        before()
        observed = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
        before.check_in_memory()
        return {"intake_id": intake_id, "intake_version": intake_version, "source_id": intake["source_id"],
            "parser_run_id": run_id, "parser_run_version": run_version, "status": run["status"],
            "parser_job_id": jobs[0]["job_id"], "parser_job_version": jobs[0]["version"],
            "job_status": jobs[0]["status"], "source_snapshot_id": run["source_snapshot_id"],
            "parser_release_id": run["parser_release_id"], "observed_at": observed, "fields": fields}

    def workflow(self, tenant: str, intake_id: str) -> dict | None:
        """Read recorded workflow facts after the caller authorizes its Intake.

        Older native rows without workflow remain unknown. A partial binding is
        unavailable; neither current policy nor a legacy document fills it in.
        """
        with self.graph.transaction(tenant):
            row = self.engine.query_one(
                "SELECT a.assignment_id,a.version AS assignment_version,a.status AS assignment_status,"
                "a.owner_subject_id,a.owner_role,a.queue_key,a.due_at AS assignment_due_at,"
                "s.tenant_id,s.assignment_id AS bound_assignment_id,s.sla_instance_id,s.version AS sla_version,s.state AS sla_state,"
                "s.policy_version,s.calendar_version,s.started_at,s.due_soon_at,s.due_at,s.breach_at "
                "FROM workflow.sla_instances s LEFT JOIN workflow.assignments a "
                "ON a.tenant_id=s.tenant_id AND a.intake_id=s.intake_id AND a.assignment_id=s.assignment_id "
                "WHERE s.tenant_id=? AND s.intake_id=?", (tenant, intake_id),
            )
            if row is None:
                if self.engine.query_one("SELECT 1 FROM workflow.assignments WHERE tenant_id=? AND intake_id=? LIMIT 1",
                                         (tenant, intake_id)) is not None:
                    raise IntakeWorkflowUnavailable("Native workflow binding is incomplete")
                return None
            if row["bound_assignment_id"] is None and all(row[key] is None for key in (
                "calendar_version", "due_soon_at", "breach_at",
            )):
                # The additive migration deliberately leaves this historical
                # binding unknown. Keep the authorized Intake detail readable
                # without substituting today's policy or an old document.
                return None
            schedule = schedule_from_record({"schedule": {key: row[key] for key in (
                "tenant_id", "policy_version", "calendar_version", "started_at", "due_soon_at", "due_at", "breach_at",
            )}})
            if (row["assignment_id"] is None or row["assignment_due_at"] is None
                    or datetime.fromisoformat(row["assignment_due_at"]) != schedule.due_at):
                raise IntakeWorkflowUnavailable("Native Assignment/SLA binding differs")
            return {key: value for key, value in row.items()
                    if key not in {"tenant_id", "assignment_due_at", "bound_assignment_id"}}

    def workflow_owner(self, tenant: str, *, intake_id: str | None = None, assignment_id: str | None = None,
                       sla_instance_id: str | None = None) -> str | None:
        """Return the owning native Intake only for an actual recorded native row.

        A UUID alone is not provenance. Absence means this resource is not a
        native workflow resource in the verified tenant; the caller keeps the
        retained legacy behaviour for it rather than guessing.
        """
        selected = [value for value in (intake_id, assignment_id, sla_instance_id) if value is not None]
        if len(selected) != 1:
            raise ValueError("Exactly one native workflow resource is required")
        UUID(selected[0])
        with self.graph.transaction(tenant):
            if intake_id is not None:
                row = self.engine.query_one("SELECT intake_id FROM intake.intakes WHERE tenant_id=? AND intake_id=? "
                    "AND deleted_at IS NULL", (tenant, intake_id))
            elif assignment_id is not None:
                row = self.engine.query_one("SELECT a.intake_id FROM workflow.assignments a JOIN intake.intakes i "
                    "ON i.tenant_id=a.tenant_id AND i.intake_id=a.intake_id AND i.deleted_at IS NULL "
                    "WHERE a.tenant_id=? AND a.assignment_id=?", (tenant, assignment_id))
            else:
                row = self.engine.query_one("SELECT s.intake_id FROM workflow.sla_instances s JOIN intake.intakes i "
                    "ON i.tenant_id=s.tenant_id AND i.intake_id=s.intake_id AND i.deleted_at IS NULL "
                    "WHERE s.tenant_id=? AND s.sla_instance_id=?", (tenant, sla_instance_id))
            return None if row is None else row["intake_id"]

    def workflow_clock(self, tenant: str) -> datetime:
        """Use the same database clock as the native SQL owners."""
        with self.graph.transaction(tenant):
            at = self.engine.query_one("SELECT clock_timestamp() AS at")["at"]
            return datetime.fromisoformat(at).astimezone(UTC)

    def _load_committed_audit(self, tenant: str, audit_event_id) -> dict | None:
        """Load one committed canonical audit through its original hash-verifying owner."""
        if not isinstance(audit_event_id, str):
            return None
        try:
            if str(UUID(audit_event_id)) != audit_event_id:
                return None
            loaded = self.graph.audit_sink.writer.get_event(tenant, audit_event_id)
        except Exception:
            return None
        if loaded is None or loaded.get("tenant_id") != tenant or loaded.get("audit_event_id") != audit_event_id:
            return None
        return {**loaded, "canonical_event_sha256": loaded["event_sha256"]}

    def _intake_committed_audit(self, tenant: str, intake: dict) -> dict | None:
        """Load the committed canonical audit recording this exact Intake version and state."""
        intake_id = intake["intake_id"]
        version = intake["version"]
        state = intake["processing_state"]
        rows = self.engine.query(
            "SELECT audit_event_id FROM audit.audit_events WHERE tenant_id=? "
            "AND resource_type='intake' AND resource_id=? AND result='SUCCEEDED' "
            "AND jsonb_typeof(after_value->'version')='number' AND after_value->>'version'=? "
            "AND (after_value->>'processing_state'=? OR after_value->>'state'=?) "
            "ORDER BY sequence_no DESC LIMIT 1",
            (tenant, intake_id, str(version), state, state),
        )
        if not rows:
            return None
        loaded = self._load_committed_audit(tenant, rows[0]["audit_event_id"])
        if loaded is None:
            return None
        after = loaded.get("after_value")
        recorded_state = (
            after["state"] if isinstance(after, dict) and "state" in after
            else after.get("processing_state") if isinstance(after, dict) else None
        )
        if (
            loaded.get("resource_type") != "intake"
            or loaded.get("resource_id") != intake_id
            or loaded.get("result") != "SUCCEEDED"
            or loaded.get("correlation_id") != intake["correlation_id"]
            or not isinstance(after, dict)
            or type(after.get("version")) is not int
            or after["version"] != version
            or recorded_state != state
            or ("intake_id" in after and after["intake_id"] != intake_id)
        ):
            return None
        return {**loaded, "intake_id": intake_id}

    def _rejected_decision_audit(self, tenant: str, intake: dict, decision: dict) -> dict | None:
        """Load the committed review rejection audit for this exact native decision."""
        after_graph = decision.get("after_graph")
        if (
            decision.get("tenant_id") != tenant
            or decision.get("intake_id") != intake["intake_id"]
            or decision.get("status") != "REJECTED"
            or decision.get("proposer_service_principal") is not None
            or not decision.get("proposer_subject_id")
            or not decision.get("reviewer_subject_id")
            or decision["reviewer_subject_id"] == decision["proposer_subject_id"]
            or not decision.get("reviewed_at")
            or not isinstance(after_graph, dict)
        ):
            return None
        review = after_graph.get("review")
        if (
            not isinstance(review, dict)
            or review.get("status") != "REJECTED"
            or review.get("reviewer_subject_id") != decision["reviewer_subject_id"]
            or not isinstance(review.get("reason"), str)
            or type(review.get("risk_acknowledged")) is not bool
        ):
            return None
        try:
            reviewed_at = _instant(decision["reviewed_at"])
            if reviewed_at is None or _instant(review.get("reviewed_at")) != reviewed_at:
                return None
        except (IntakeWorkflowUnavailable, ValueError, TypeError):
            return None
        loaded = self._load_committed_audit(tenant, review.get("audit_event_id"))
        if loaded is None:
            return None
        before = loaded.get("before_value")
        after = loaded.get("after_value")
        try:
            occurred_at = _instant(loaded.get("occurred_at"))
            audited_reviewed_at = _instant(after.get("reviewed_at")) if isinstance(after, dict) else None
        except (IntakeWorkflowUnavailable, ValueError, TypeError):
            return None
        if (
            loaded.get("action") != "identity.decision_rejected.v1"
            or loaded.get("result") != "SUCCEEDED"
            or loaded.get("resource_type") != "identity_decision"
            or loaded.get("resource_id") != decision["match_decision_id"]
            or loaded.get("actor_subject_id") != decision["reviewer_subject_id"]
            or loaded.get("service_principal") is not None
            or loaded.get("correlation_id") != intake["correlation_id"]
            or loaded.get("reason") != review["reason"]
            or occurred_at != reviewed_at
            or not isinstance(before, dict)
            or not isinstance(after, dict)
            or before.get("decision_id") != decision["match_decision_id"]
            or before.get("match_case_id") != decision["match_case_id"]
            or before.get("status") != "PENDING_REVIEW"
            or type(before.get("version")) is not int
            or before["version"] + 1 != decision["version"]
            or before.get("plan") != after_graph.get("plan")
            or after.get("decision_id") != decision["match_decision_id"]
            or after.get("match_case_id") != decision["match_case_id"]
            or after.get("status") != "REJECTED"
            or type(after.get("version")) is not int
            or after["version"] != decision["version"]
            or after.get("reviewer_subject_id") != decision["reviewer_subject_id"]
            or after.get("audit_event_id") != review["audit_event_id"]
            or after.get("reason") != review["reason"]
            or after.get("risk_acknowledged") is not review["risk_acknowledged"]
            or audited_reviewed_at != reviewed_at
            or after.get("plan") != after_graph.get("plan")
        ):
            return None
        return {**loaded, "intake_id": intake["intake_id"]}

    def _executed_decision_proof(self, tenant: str, intake: dict, decision: dict):
        """Retain the original stronger graph.execution_receipt proof for EXECUTED."""
        from shared.infrastructure.persistence.identity_decisions_sql import (
            CanonicalIdentityDecisionRepository,
        )

        if (
            decision.get("tenant_id") != tenant
            or decision.get("intake_id") != intake["intake_id"]
            or decision.get("status") != "EXECUTED"
        ):
            return None
        jobs = self.engine.query(
            "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_type=? "
            "AND aggregate_type='identity_decision' AND aggregate_id=? LIMIT 2",
            (tenant, self.graph.job_type, decision["match_decision_id"]),
        )
        if len(jobs) != 1:
            return None
        job = {
            **jobs[0],
            "payload": _json_value(jobs[0].get("payload")),
            "result": _json_value(jobs[0].get("result")),
        }
        if job.get("correlation_id") != intake["correlation_id"]:
            return None
        try:
            CanonicalIdentityDecisionRepository(self.graph).require_approval(tenant, decision)
            result = self.graph.execution_receipt(tenant, job, decision)
        except Exception:
            return None
        intake_id = intake["intake_id"]
        if (
            result.get("intake_id") != intake_id
            if decision["decision_type"] in {"CREATE", "REVISE", "DUPLICATE"}
            else result.get("intake_id") not in (None, intake_id)
        ):
            return None
        loaded = self._load_committed_audit(tenant, result.get("audit_event_id"))
        if (
            loaded is None
            or loaded.get("resource_type") != "identity_decision"
            or loaded.get("resource_id") != decision["match_decision_id"]
            or loaded.get("correlation_id") != intake["correlation_id"]
        ):
            return None
        return {**loaded, "intake_id": intake_id}, decision, job

    def workflow_command_state(self, tenant: str, intake_id: str) -> _NativeWorkflowState:
        """Lock one complete recorded native workflow into the shared command shape.

        The caller holds this graph root for the whole command, replay receipt
        and flush. Incomplete or historical rows without recorded facts stay
        unavailable; nothing is defaulted from current policy or documents.
        """
        UUID(intake_id)
        with self.graph.transaction(tenant):
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? "
                "AND deleted_at IS NULL", (tenant, intake_id))
            assignments = self.engine.query("SELECT * FROM workflow.assignments WHERE tenant_id=? AND intake_id=? "
                "ORDER BY assignment_id FOR UPDATE", (tenant, intake_id))
            slas = self.engine.query("SELECT * FROM workflow.sla_instances WHERE tenant_id=? AND intake_id=? "
                "FOR UPDATE", (tenant, intake_id))
            if len(assignments) != 1 or len(slas) != 1:
                raise IntakeWorkflowUnavailable("Native Assignment/SLA workflow is not recorded")
            assignment, sla = assignments[0], slas[0]
            if (sla["assignment_id"] != assignment["assignment_id"]
                    or any(sla[key] is None for key in ("calendar_version", "due_soon_at", "breach_at"))):
                raise IntakeWorkflowUnavailable("Historical native workflow binding is unavailable")
            intervals = self.engine.query("SELECT * FROM workflow.sla_pause_intervals WHERE tenant_id=? "
                "AND sla_instance_id=? ORDER BY paused_at,sla_pause_interval_id FOR UPDATE",
                (tenant, sla["sla_instance_id"]))
            if any(item["expected_resume_at"] is None for item in intervals):
                # Never infer an old interval's expectation from its actual
                # resume, the current SLA resume_at or today's calendar.
                raise IntakeWorkflowUnavailable("Historical pause expectation is unavailable")
            scope = {"tenant_id": tenant, **{key: intake[key] for key in (
                "brand_id", "region_id", "assigned_area_id", "heat_zone_id")}}
            pauses = [{"pause_interval_id": item["sla_pause_interval_id"], "sla_instance_id": sla["sla_instance_id"],
                "tenant_id": tenant, "paused_at": _instant(item["paused_at"]),
                "expected_resume_at": _instant(item["expected_resume_at"]),
                "resumed_at": _instant(item["resumed_at"]), "reason": item["reason"],
                "approved_by": item["approved_by"], "created_by": item["created_by"],
                "version": item["version"]} for item in intervals]
            active = [item for item in pauses if item["resumed_at"] is None]
            if active and (_instant(sla["paused_at"]) != active[0]["paused_at"]
                           or _instant(sla["resume_at"]) != active[0]["expected_resume_at"]):
                raise IntakeWorkflowUnavailable("Recorded active pause differs from its SLA")
            if not active and (sla["paused_at"] is not None or sla["resume_at"] is not None):
                raise IntakeWorkflowUnavailable("Recorded SLA pause has no interval")
            schedule = {"tenant_id": tenant, "policy_version": sla["policy_version"],
                "calendar_version": sla["calendar_version"], **{key: _instant(sla[key]) for key in (
                    "started_at", "due_soon_at", "due_at", "breach_at")}}
            sla_shape = {"sla_instance_id": sla["sla_instance_id"], "assignment_id": sla["assignment_id"],
                "tenant_id": tenant, "intake_id": intake_id, "state": sla["state"],
                "policy_version": sla["policy_version"], "calendar_version": sla["calendar_version"],
                **{key: schedule[key] for key in ("started_at", "due_soon_at", "due_at", "breach_at")},
                "completed_at": _instant(sla["completed_at"]), "schedule": schedule, "pause_intervals": pauses,
                "paused_duration_seconds": int(sum(
                    (datetime.fromisoformat(item["resumed_at"].replace("Z", "+00:00"))
                     - datetime.fromisoformat(item["paused_at"].replace("Z", "+00:00"))).total_seconds()
                    for item in pauses if item["resumed_at"] is not None)),
                "active_pause_interval_id": active[0]["pause_interval_id"] if active else None,
                "version": sla["version"], "correlation_id": intake["correlation_id"]}
            assignment_shape = {key: assignment[key] for key in (
                "assignment_id", "tenant_id", "intake_id", "status", "queue_key", "owner_subject_id",
                "owner_role", "previous_owner_subject_id", "assigned_by", "handoff_note", "version")}
            assignment_shape.update({"due_at": _instant(assignment["due_at"]),
                "created_at": _instant(assignment["created_at"]), "updated_at": _instant(assignment["updated_at"]),
                "correlation_id": intake["correlation_id"]})
            intake_shape = {"intake_id": intake_id, "tenant_id": tenant, "scope": scope,
                "correlation_id": intake["correlation_id"], "submitted_by": intake["submitter_subject_id"],
                "state": intake["processing_state"], "version": intake["version"],
                "assigned_to": assignment["owner_subject_id"], "due_at": assignment_shape["due_at"],
                "audit": [], "processing_history": []}
            committed = []
            executed_decisions = {}
            # The original completion evaluator reads actual committed work.
            # Only the canonical audits recorded by genuine native producers
            # for this exact current state and version can prove completion;
            # unsupported kinds and missing/corrupt proofs stay fail-closed.
            intake_audit = self._intake_committed_audit(tenant, intake)
            if intake_audit is not None:
                committed.append(intake_audit)
                intake_shape["audit_event_id"] = intake_audit["audit_event_id"]
            raw_decisions = self.engine.query(
                "SELECT d.*,c.intake_id FROM identity.match_decisions d "
                "JOIN identity.match_cases c ON c.tenant_id=d.tenant_id AND c.match_case_id=d.match_case_id "
                "WHERE d.tenant_id=? AND c.intake_id=? ORDER BY d.created_at,d.match_decision_id",
                (tenant, intake_id),
            )
            decisions = {}
            for raw in raw_decisions:
                decision = {
                    **raw,
                    "before_graph": _json_value(raw.get("before_graph")),
                    "after_graph": _json_value(raw.get("after_graph")),
                }
                row = {
                    "decision_id": decision["match_decision_id"],
                    "intake_id": decision["intake_id"],
                    "tenant_id": decision["tenant_id"],
                    "status": decision["status"],
                    "version": decision["version"],
                    "action": decision["decision_type"],
                }
                if decision["status"] == "REJECTED":
                    rejected_audit = self._rejected_decision_audit(tenant, intake, decision)
                    if rejected_audit is not None:
                        committed.append(rejected_audit)
                        row["audit_event_id"] = rejected_audit["audit_event_id"]
                elif decision["status"] == "EXECUTED":
                    executed = self._executed_decision_proof(tenant, intake, decision)
                    if executed is not None:
                        executed_audit, verified_decision, verified_job = executed
                        committed.append(executed_audit)
                        row["audit_event_id"] = executed_audit["audit_event_id"]
                        executed_decisions[row["decision_id"]] = (verified_decision, verified_job)
                decisions[row["decision_id"]] = row
            # Corrections have no native per-row canonical audit producer yet.
            # Rows are still loaded so unresolved sibling work blocks completion
            # while require_audit=true fails closed.
            corrections = self.engine.query("SELECT correction_id,intake_id,tenant_id,status,version "
                "FROM intake.human_corrections WHERE tenant_id=? AND intake_id=?", (tenant, intake_id))
            raw_promotions = self.engine.query(
                "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? AND intake_id=? "
                "ORDER BY created_at,promotion_decision_id",
                (tenant, intake_id),
            )
            promotions: dict[str, dict[str, Any]] = {}
            verified_completed_promotions: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
            workflow_outbox: dict[str, dict[str, Any]] = {}
            for raw_promo in raw_promotions:
                promo_row: dict[str, Any] = {
                    "promotion_decision_id": str(raw_promo["promotion_decision_id"]),
                    "intake_id": str(raw_promo["intake_id"]),
                    "tenant_id": str(raw_promo["tenant_id"]),
                    "listing_id": str(raw_promo["listing_id"]),
                    "property_id": str(raw_promo["property_id"]),
                    "target_format_code": raw_promo["target_format_code"],
                    "candidate_site_id": str(raw_promo["candidate_site_id"]) if raw_promo.get("candidate_site_id") else None,
                    "site_score_job_id": str(raw_promo["site_score_job_id"]) if raw_promo.get("site_score_job_id") else None,
                    "status": raw_promo["status"],
                    "version": int(raw_promo["version"]),
                }
                proof = self._native_promotion_proof(tenant, intake, raw_promo)
                if proof is not None:
                    promo_audit, promo_outbox, gate_snapshot = proof
                    committed.append(promo_audit)
                    promo_row["audit_event_id"] = promo_audit["audit_event_id"]
                    if promo_outbox is not None:
                        workflow_outbox[str(promo_outbox["event_id"])] = promo_outbox
                        promo_row["outbox_event_id"] = str(promo_outbox["event_id"])
                    if raw_promo["status"] == "COMPLETED":
                        verified_completed_promotions[promo_row["promotion_decision_id"]] = (raw_promo, gate_snapshot)
                promotions[promo_row["promotion_decision_id"]] = promo_row

            intake_jobs = self.engine.query(
                "SELECT job_id,aggregate_id AS intake_id,tenant_id,status,version,job_type "
                "FROM workflow.jobs WHERE tenant_id=? AND aggregate_type='intake' AND aggregate_id=?",
                (tenant, intake_id),
            )
            jobs_map: dict[str, dict[str, Any]] = {
                str(row["job_id"]): {
                    "job_id": str(row["job_id"]),
                    "intake_id": str(row["intake_id"]),
                    "tenant_id": str(row["tenant_id"]),
                    "status": row["status"],
                    "version": int(row["version"]),
                    "job_type": row["job_type"],
                }
                for row in intake_jobs
            }
            candidate_ids = [
                str(row["candidate_site_id"])
                for row in raw_promotions
                if row.get("candidate_site_id") is not None
            ]
            if candidate_ids:
                from shared.infrastructure.persistence.operator_domains import (
                    TenantScopedDocumentStore,
                )
                from shared.infrastructure.persistence.postgresql import PostgresDocumentStore

                scoped_docs = TenantScopedDocumentStore(PostgresDocumentStore(self.engine), tenant)
                score_jobs = self.engine.query(
                    "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_type='intake-sitescore' "
                    "AND aggregate_type='candidate_site' AND aggregate_id=ANY(?::uuid[]) "
                    "ORDER BY created_at,job_id",
                    (tenant, candidate_ids),
                )
                promo_by_candidate = {
                    str(row["candidate_site_id"]): row
                    for row in raw_promotions
                    if row.get("candidate_site_id") is not None
                }
                for raw_job in score_jobs:
                    job_id = str(raw_job["job_id"])
                    cand_id = str(raw_job["aggregate_id"])
                    linked_promo = promo_by_candidate.get(cand_id)
                    job_row: dict[str, Any] = {
                        "job_id": job_id,
                        "intake_id": intake_id,
                        "tenant_id": str(raw_job["tenant_id"]),
                        "status": raw_job["status"],
                        "version": int(raw_job["version"]),
                        "job_type": raw_job["job_type"],
                        "candidate_site_id": cand_id,
                        "promotion_decision_id": str(linked_promo["promotion_decision_id"]) if linked_promo else None,
                    }
                    if linked_promo is not None:
                        verified_pair = verified_completed_promotions.get(str(linked_promo["promotion_decision_id"]))
                        if verified_pair is not None:
                            job_proof = self._native_score_job_proof(
                                tenant, intake, verified_pair[0], verified_pair[1], raw_job, document_store=scoped_docs,
                            )
                            if job_proof is not None:
                                job_audit, acceptance, result_receipt = job_proof
                                committed.append(job_audit)
                                job_row["audit_event_id"] = job_audit["audit_event_id"]
                                job_row["worker_acceptance"] = deepcopy(acceptance)
                                job_row["result"] = deepcopy(result_receipt)
                    jobs_map[job_id] = job_row
            return _NativeWorkflowState(
                graph=self.graph,
                intake=intake, intakes={intake_id: intake_shape},
                assignments={assignment["assignment_id"]: assignment_shape},
                slas={sla["sla_instance_id"]: sla_shape},
                decisions=decisions,
                corrections={row["correction_id"]: row for row in corrections},
                promotions=promotions,
                jobs=jobs_map,
                workflow_audits={audit["audit_event_id"]: audit for audit in committed},
                committed_audits=tuple(committed),
                executed_decisions=executed_decisions,
                workflow_outbox=workflow_outbox,
                baseline={"assignments": deepcopy({assignment["assignment_id"]: assignment_shape}),
                          "slas": deepcopy({sla["sla_instance_id"]: sla_shape})})

    def persist_workflow_command(self, state: _NativeWorkflowState, *, actor: str, reason: str,
                                 idempotency_key: str, at: datetime) -> None:
        """Write one shared command's result to the original normalized rows.

        Assignment/SLA rows, transitions, pause intervals, canonical audit and
        outbox commit with the caller's replay receipt in this same SQL root.
        The native Intake row is the processing fence for its pipeline workers
        and is not changed by workflow commands; its Intake-resource evidence
        from the shared command therefore has no native write to describe.
        """
        tenant = state.intake["tenant_id"]
        intake_id = state.intake["intake_id"]
        scope = state.intakes[intake_id]["scope"]
        when = _instant(at)
        with self.graph.transaction(tenant):
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            for assignment_id, after in state.assignments.items():
                before = state.baseline["assignments"][assignment_id]
                if after["version"] == before["version"]:
                    continue
                if after["version"] <= before["version"] or after["intake_id"] != intake_id:
                    raise IntakeWorkflowUnavailable("Assignment command did not advance its version")
                if self.engine.execute(
                    "UPDATE workflow.assignments SET status=?,owner_subject_id=?,owner_role=?,"
                    "previous_owner_subject_id=?,assigned_by=?,handoff_note=?,queue_key=?,due_at=?,version=?,updated_at=? "
                    "WHERE tenant_id=? AND intake_id=? AND assignment_id=? AND version=?",
                    (after["status"], after["owner_subject_id"], after["owner_role"],
                     before["owner_subject_id"] if after["owner_subject_id"] != before["owner_subject_id"]
                     and before["owner_subject_id"] is not None else before["previous_owner_subject_id"],
                     actor if before["status"] == "UNASSIGNED" and after["status"] != "UNASSIGNED"
                     else before["assigned_by"], after.get("handoff_note"), after["queue_key"], after["due_at"],
                     after["version"], when, tenant, intake_id, assignment_id, before["version"]),
                ).rowcount != 1:
                    raise IdentityConflictError("NATIVE_ASSIGNMENT_VERSION_CONFLICT")
            for sla_id, after in state.slas.items():
                before = state.baseline["slas"][sla_id]
                if after["version"] == before["version"]:
                    continue
                if after["version"] <= before["version"]:
                    raise IntakeWorkflowUnavailable("SLA command did not advance its version")
                known = {item["pause_interval_id"]: item for item in before["pause_intervals"]}
                for item in after["pause_intervals"]:
                    prior = known.get(item["pause_interval_id"])
                    if prior is None:
                        UUID(item["pause_interval_id"])
                        self.engine.execute(
                            "INSERT INTO workflow.sla_pause_intervals (sla_pause_interval_id,tenant_id,sla_instance_id,"
                            "paused_at,expected_resume_at,resumed_at,reason,approved_by,created_by,version) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (item["pause_interval_id"], tenant, sla_id, item["paused_at"], item["expected_resume_at"],
                             item["resumed_at"], item["reason"], item["approved_by"], item["created_by"],
                             item["version"]))
                    elif item["resumed_at"] != prior["resumed_at"]:
                        if (prior["resumed_at"] is not None or item["version"] != prior["version"] + 1
                                or any(item[key] != prior[key] for key in (
                                    "paused_at", "expected_resume_at", "reason", "approved_by", "created_by"))):
                            raise IntakeWorkflowUnavailable("Recorded pause interval cannot be rewritten")
                        if self.engine.execute(
                            "UPDATE workflow.sla_pause_intervals SET resumed_at=?,version=? WHERE tenant_id=? "
                            "AND sla_instance_id=? AND sla_pause_interval_id=? AND version=? AND resumed_at IS NULL",
                            (item["resumed_at"], item["version"], tenant, sla_id, item["pause_interval_id"],
                             prior["version"]),
                        ).rowcount != 1:
                            raise IdentityConflictError("NATIVE_SLA_PAUSE_VERSION_CONFLICT")
                if {item["pause_interval_id"] for item in after["pause_intervals"]} < set(known):
                    raise IntakeWorkflowUnavailable("Recorded pause history cannot be removed")
                active = [item for item in after["pause_intervals"] if item["resumed_at"] is None]
                if self.engine.execute(
                    "UPDATE workflow.sla_instances SET state=?,due_soon_at=?,due_at=?,breach_at=?,paused_at=?,"
                    "resume_at=?,completed_at=?,version=? WHERE tenant_id=? AND intake_id=? AND sla_instance_id=? "
                    "AND version=?",
                    (after["state"], after["due_soon_at"], after["due_at"], after["breach_at"],
                     active[0]["paused_at"] if active else None, active[0]["expected_resume_at"] if active else None,
                     after.get("completed_at"), after["version"], tenant, intake_id, sla_id, before["version"]),
                ).rowcount != 1:
                    raise IdentityConflictError("NATIVE_SLA_VERSION_CONFLICT")
            for audit in state.workflow_audits.values():
                if any(audit is loaded for loaded in state.committed_audits):
                    continue
                kind, before, after = audit["resource_type"], audit["before_value"], audit["after_value"]
                if kind == "intake":
                    continue
                if kind == "assignment":
                    sequence = self.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next FROM "
                        "workflow.assignment_transitions WHERE tenant_id=? AND assignment_id=?",
                        (tenant, audit["resource_id"]))["next"]
                    self.engine.execute(
                        "INSERT INTO workflow.assignment_transitions (assignment_transition_id,tenant_id,assignment_id,"
                        "sequence_no,from_status,to_status,actor_subject_id,permission,reason,owner_before,owner_after,"
                        "expected_version,resulting_version,idempotency_key,correlation_id,occurred_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (str(uuid4()), tenant, audit["resource_id"], sequence, before["status"], after["status"],
                         audit["actor_subject_id"], audit["action"], reason,
                         before["owner_subject_id"], after["owner_subject_id"], before["version"], after["version"],
                         idempotency_key, audit["correlation_id"], when))
                elif kind == "sla_instance":
                    sequence = self.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next FROM "
                        "workflow.sla_transitions WHERE tenant_id=? AND sla_instance_id=?",
                        (tenant, audit["resource_id"]))["next"]
                    self.engine.execute(
                        "INSERT INTO workflow.sla_transitions (sla_transition_id,tenant_id,sla_instance_id,sequence_no,"
                        "from_state,to_state,actor_subject_id,permission,reason,due_at_before,due_at_after,"
                        "expected_version,resulting_version,idempotency_key,correlation_id,occurred_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (str(uuid4()), tenant, audit["resource_id"], sequence, before["state"], after["state"],
                         audit["actor_subject_id"], audit["action"], reason,
                         before["due_at"], after["due_at"], before["version"], after["version"],
                         idempotency_key, audit["correlation_id"], when))
                else:
                    raise IntakeWorkflowUnavailable("Workflow command produced unsupported evidence")
                self.graph.audit_sink.persist(audit, scope)
            for value in state.workflow_outbox.values():
                self.graph._outbox(DomainEvent(
                    **{**value, "occurred_at": datetime.fromisoformat(value["occurred_at"].replace("Z", "+00:00"))},
                    actor_ref=actor, policy_version=state.slas[next(iter(state.slas))]["policy_version"],
                    schema_ref=_WORKFLOW_EVENT_SCHEMAS[value["event_type"]],
                ))

    def _initialize_workflow(self, row: dict, *, reason: str, idempotency_key: str) -> None:
        """Persist the shared bootstrap in this native submission's SQL unit.

        This request-local assembly is never flushed to durable_documents. Its
        policy, initial facts and evidence come from the existing bootstrap.
        """
        state = SimpleNamespace(assignments={}, slas={}, workflow_audits={}, workflow_outbox={})
        tenant, intake_id, actor = row["tenant_id"], row["intake_id"], row["submitter_subject_id"]
        scope = {"tenant_id": tenant, **{key: row[key] for key in (
            "brand_id", "region_id", "assigned_area_id", "heat_zone_id",
        )}}
        context = {"intake_id": intake_id, "scope": scope, "submitted_by": actor,
                   "submitted_at": row["submitted_at"], "correlation_id": row["correlation_id"]}
        assignment_id, sla_id = initialize_intake_workflow(
            state, context, self.workflow_policy, record_submission=False,
        )
        assignment, sla = state.assignments[assignment_id], state.slas[sla_id]
        self.engine.execute(
            "INSERT INTO workflow.assignments (assignment_id,tenant_id,intake_id,status,queue_key,"
            "owner_subject_id,owner_role,due_at,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            tuple(assignment[key] for key in ("assignment_id", "tenant_id", "intake_id", "status", "queue_key",
                "owner_subject_id", "owner_role", "due_at", "version", "created_at", "updated_at")),
        )
        self.engine.execute(
            "INSERT INTO workflow.sla_instances (sla_instance_id,tenant_id,intake_id,assignment_id,policy_version,"
            "calendar_version,state,started_at,due_soon_at,due_at,breach_at,version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(sla[key] for key in ("sla_instance_id", "tenant_id", "intake_id", "assignment_id", "policy_version",
                "calendar_version", "state", "started_at", "due_soon_at", "due_at", "breach_at", "version")),
        )
        at, correlation = row["submitted_at"], row["correlation_id"]
        self.engine.execute(
            "INSERT INTO workflow.assignment_transitions (assignment_transition_id,tenant_id,assignment_id,"
            "sequence_no,from_status,to_status,actor_subject_id,service_principal,permission,reason,"
            "resulting_version,idempotency_key,correlation_id,occurred_at) "
            "VALUES (?,?,?,1,NULL,?,?,'intake_workflow','assignment.create',?,1,?,?,?)",
            (str(uuid4()), tenant, assignment_id, assignment["status"], actor, reason, idempotency_key, correlation, at),
        )
        self.engine.execute(
            "INSERT INTO workflow.sla_transitions (sla_transition_id,tenant_id,sla_instance_id,sequence_no,"
            "from_state,to_state,actor_subject_id,service_principal,permission,reason,due_at_after,"
            "resulting_version,idempotency_key,correlation_id,occurred_at) "
            "VALUES (?,?,?,1,NULL,?,?,'intake_workflow','sla.create',?,?,1,?,?,?)",
            (str(uuid4()), tenant, sla_id, sla["state"], actor, reason, sla["due_at"], idempotency_key, correlation, at),
        )
        for audit in state.workflow_audits.values():
            self.graph.audit_sink.persist(audit, scope)
        for value in state.workflow_outbox.values():
            self.graph._outbox(DomainEvent(
                **{**value, "occurred_at": datetime.fromisoformat(value["occurred_at"])},
                actor_ref=actor, policy_version=sla["policy_version"], schema_ref="#/payloads/SlaStateChangedV1",
            ))

    def page(self, tenant: str, *, scope, owner: str | None, state: str | None,
             before: tuple[str, str] | None, limit: int) -> list[dict]:
        """Project current native scope/ownership before applying a page limit.

        The route still authorizes every returned row, including the lookahead.
        A cursor is an untrusted position, never a tenant or permission grant.
        Native Intakes have no store axis, so a store-restricted principal has
        no representable row rather than receiving unscoped metadata.
        """
        from json import dumps

        with self.graph.transaction(tenant):
            return self.engine.query(
                "SELECT intake_id,tenant_id,submitter_subject_id,intake_method,processing_state,version,"
                "submitted_at,correlation_id,brand_id,region_id,assigned_area_id,heat_zone_id "
                "FROM intake.intakes WHERE tenant_id=? AND deleted_at IS NULL "
                "AND ?::boolean AND (?::uuid IS NULL OR submitter_subject_id=?::uuid) "
                "AND (?::boolean OR jsonb_exists(?::jsonb,brand_id::text)) "
                "AND (?::boolean OR jsonb_exists(?::jsonb,region_id::text)) "
                "AND (?::boolean OR jsonb_exists(?::jsonb,assigned_area_id::text)) "
                "AND (?::boolean OR jsonb_exists(?::jsonb,heat_zone_id::text)) "
                "AND (?::text IS NULL OR processing_state=?::text) "
                "AND (?::timestamptz IS NULL OR (submitted_at,intake_id)<(?::timestamptz,?::uuid)) "
                "ORDER BY submitted_at DESC,intake_id DESC LIMIT ?",
                (tenant, not scope.store_ids, owner, owner,
                 not scope.brand_ids, dumps(sorted(scope.brand_ids)),
                 not scope.region_ids, dumps(sorted(scope.region_ids)),
                 not scope.assigned_area_ids, dumps(sorted(scope.assigned_area_ids)),
                 not scope.heat_zone_ids, dumps(sorted(scope.heat_zone_ids)), state, state,
                 before[0] if before else None, before[0] if before else None, before[1] if before else None, limit),
            )

    def cancellation_context(self, principal: Principal, intake_id: str) -> dict:
        """Lock and authorize the current native row, including for replay."""
        UUID(intake_id)
        with self.graph.transaction(principal.tenant_id):
            row = self.graph._one(
                "SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? "
                "AND deleted_at IS NULL FOR UPDATE", (principal.tenant_id, intake_id),
            )
            authorize_intake_action(principal, "cancel",
                resource={**row, "submitter": row["submitter_subject_id"]})
            return row

    def cancel_authorization_wait(self, principal: Principal, intake_id: str, *,
                                  expected_version: int, reason: str, idempotency_key: str) -> dict:
        """Cancel a settled authorization wait without granting source access.

        The HTTP owner persists its idempotency receipt in this same SQL root.
        Job rows are read under SERIALIZABLE, never locked after the Intake.
        """
        if not reason.strip() or not idempotency_key:
            raise ValueError("Explicit cancellation intent is required")
        tenant, actor = principal.tenant_id, principal.subject_id
        UUID(actor)
        with self.graph.transaction(tenant):
            row = self.cancellation_context(principal, intake_id)
            if (row["processing_state"] != "AWAITING_SOURCE_AUTHORIZATION"
                    or row["source_policy_state"] != "AUTH_REQUIRED"
                    or row["version"] != expected_version):
                raise IdentityConflictError("CANNOT_CANCEL_AUTHORIZATION_WAIT")
            if (self.engine.query_one("SELECT 1 FROM identity.match_cases WHERE tenant_id=? AND intake_id=? LIMIT 1",
                                      (tenant, intake_id)) is not None
                    or self.engine.query_one("SELECT 1 FROM expansion.promotion_decisions WHERE tenant_id=? AND intake_id=? LIMIT 1",
                                             (tenant, intake_id)) is not None):
                raise IdentityConflictError("CANNOT_CANCEL_AUTHORIZATION_WAIT")
            jobs = self.engine.query(
                "SELECT j.job_type,j.status,d.job_id AS delivery_id,d.status AS delivery_status,d.delivery_state "
                "FROM workflow.jobs j LEFT JOIN durable_jobs d ON d.job_id::text=j.job_id::text "
                "WHERE j.tenant_id=? AND j.aggregate_type='intake' AND j.aggregate_id=?", (tenant, intake_id),
            )
            if (not any(j["job_type"] == "native-intake-source-policy" for j in jobs)
                    or any(j["status"] != "SUCCEEDED" or j["delivery_id"] is None
                           or j["delivery_status"] != JobStatus.SUCCEEDED.value or j["delivery_state"] is not None for j in jobs)):
                raise IdentityConflictError("AUTHORIZATION_WAIT_JOBS_NOT_SETTLED")
            if principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE):
                role = PrincipalRole.EXPANSION_MANAGER
            elif principal.has_role(Role.DATA_OWNER):
                role = PrincipalRole.DATA_STEWARD
            else:
                role = PrincipalRole.EXPANSION_STAFF
            aggregate = IntakeAggregate(id=intake_id, tenant_id=tenant, stage=IntakeStage(row["processing_state"]),
                version=row["version"], created_by=row["submitter_subject_id"], source_id=row["source_id"],
                owner_id=row["submitter_subject_id"])
            at = datetime.fromisoformat(self.engine.query_one("SELECT clock_timestamp() AS at")["at"]).astimezone(UTC)
            at = at.isoformat().replace("+00:00", "Z")
            IntakeStateMachine.transition(aggregate, IntakeStage.CANCELLED, TransitionContext(
                actor=Actor(actor, role, tenant), idempotency_key=idempotency_key,
                version_before=expected_version, reason=reason, correlation_id=row["correlation_id"],
                current_time=datetime.fromisoformat(at)))
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            transition_id, audit_id = str(uuid4()), str(uuid4())
            if self.engine.execute(
                "UPDATE intake.intakes SET processing_state='CANCELLED',version=?,last_transition_at=? "
                "WHERE tenant_id=? AND intake_id=? AND version=? AND processing_state='AWAITING_SOURCE_AUTHORIZATION' "
                "AND source_policy_state='AUTH_REQUIRED' AND deleted_at IS NULL",
                (aggregate.version, at, tenant, intake_id, expected_version),
            ).rowcount != 1:
                raise IdentityConflictError("CANNOT_CANCEL_AUTHORIZATION_WAIT")
            sequence = self.engine.query_one("SELECT COALESCE(MAX(sequence_no),0)+1 AS next "
                "FROM intake.intake_stage_transitions WHERE tenant_id=? AND intake_id=?", (tenant, intake_id))["next"]
            self.engine.execute(
                "INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
                "from_state,to_state,actor_subject_id,permission,reason_text,idempotency_key,expected_version,"
                "resulting_version,correlation_id,occurred_at) "
                "VALUES (?,?,?,?,'AWAITING_SOURCE_AUTHORIZATION','CANCELLED',?,'intake.cancel',?,?,?,?,?,?)",
                (transition_id, tenant, intake_id, sequence, actor, reason, idempotency_key,
                 expected_version, aggregate.version, row["correlation_id"], at),
            )
            event = DomainEvent(event_type="intake.state_changed", producer="listing_intake_service",
                tenant_id=tenant, aggregate_type="intake", aggregate_id=intake_id, aggregate_version=aggregate.version,
                partition_key=tenant + ":" + intake_id, correlation_id=row["correlation_id"], actor_ref=actor,
                occurred_at=datetime.fromisoformat(at), schema_ref="#/payloads/IntakeStateChangedV1",
                payload={"intake_id": intake_id, "from_state": "AWAITING_SOURCE_AUTHORIZATION", "to_state": "CANCELLED",
                         "transition_id": transition_id, "version": aggregate.version, "occurred_at": at})
            receipt = {"intake_id": intake_id, "processing_state": "CANCELLED", "version": aggregate.version,
                "cancelled_at": at, "correlation_id": row["correlation_id"], "transition_id": transition_id,
                "audit_event_id": audit_id, "outbox_event_id": event.event_id}
            self.graph.audit_sink.persist({"audit_event_id": audit_id, "tenant_id": tenant, "resource_type": "intake",
                "resource_id": intake_id, "action": "intake.cancelled.v1", "result": "SUCCEEDED",
                "actor_subject_id": actor, "correlation_id": row["correlation_id"], "occurred_at": at,
                "before_value": {"intake_id": intake_id, "processing_state": row["processing_state"], "version": expected_version},
                "after_value": receipt, "reason": reason},
                {"tenant_id": tenant, **{key: row[key] for key in ("brand_id", "region_id", "assigned_area_id", "heat_zone_id")}})
            self.graph._outbox(event)
            return receipt

    def submit(self, tenant: str, actor: str, *, method: str, original_url: str | None,
               scope: dict, reason: str, idempotency_key: str) -> dict:
        """Commit SUBMITTED, its transition, audit and event in one SQL unit.

        Admission is not retrieval, parsing, matching or execution. In particular
        the submitted URL is not asserted to be canonical, no source/policy is
        guessed, and no match case, Listing or worker-success receipt is made.
        The HTTP owner commits its idempotency receipt in this same root unit.
        """
        UUID(actor)
        if (method not in {"URL", "MANUAL"} or not reason.strip() or not idempotency_key
                or (method == "URL") != (original_url is not None)):
            raise ValueError("Explicit Intake method and submission intent are required")
        scope = {key: scope.get(key) for key in ("brand_id", "region_id", "assigned_area_id", "heat_zone_id")}
        for value in scope.values():
            if value is not None:
                UUID(value)
        with self.graph.transaction(tenant):
            intake_id, correlation, transition_id, audit_id = (str(uuid4()) for _ in range(4))
            row = self.engine.query_one(
                "INSERT INTO intake.intakes (intake_id,tenant_id,submitter_subject_id,intake_method,original_url,"
                "brand_id,region_id,assigned_area_id,heat_zone_id,processing_state,correlation_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,'SUBMITTED',?) RETURNING *",
                (intake_id, tenant, actor, method, original_url, scope["brand_id"], scope["region_id"],
                 scope["assigned_area_id"], scope["heat_zone_id"], correlation),
            )
            at, version = row["submitted_at"], row["version"]
            self.engine.execute(
                "INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
                "from_state,to_state,actor_subject_id,permission,reason_text,idempotency_key,resulting_version,"
                "correlation_id,occurred_at) VALUES (?,?,?,1,NULL,'SUBMITTED',?,'intake.create',?,?,?,?,?)",
                (transition_id, tenant, intake_id, actor, reason, idempotency_key, version, correlation, at),
            )
            receipt = {"intake_id": intake_id, "processing_state": row["processing_state"], "version": version,
                       "intake_method": method, "submitted_at": at, "correlation_id": correlation,
                       "transition_id": transition_id, "audit_event_id": audit_id}
            native_scope = {"tenant_id": tenant, **scope}
            self.graph.audit_sink.persist({
                "audit_event_id": audit_id, "tenant_id": tenant, "resource_type": "intake", "resource_id": intake_id,
                "action": "intake.create", "result": "SUCCEEDED", "actor_subject_id": actor,
                "correlation_id": correlation, "occurred_at": at, "before_value": None,
                "after_value": receipt, "reason": reason,
            }, native_scope)
            event = DomainEvent(
                event_type="intake.submitted", tenant_id=tenant, aggregate_type="intake", aggregate_id=intake_id,
                aggregate_version=version, partition_key=tenant + ":" + intake_id, correlation_id=correlation,
                producer="listing_intake_service", occurred_at=datetime.fromisoformat(at), actor_ref=actor,
                schema_ref="#/payloads/IntakeSubmittedV1",
                sensitive_fields=["payload.original_url"],
                payload={"intake_id": intake_id, "intake_method": method, "submitter_subject_id": actor,
                         "submitted_at": at, "scope": native_scope},
            )
            self.graph._outbox(event)
            self._initialize_workflow(row, reason=reason, idempotency_key=idempotency_key)
            return {**receipt, "outbox_event_id": event.event_id}

    def _promotion_latest_committed_audit(self, tenant: str, promotion_decision_id: str, *, version: int, status: str) -> dict[str, Any] | None:
        rows = self.engine.query(
            "SELECT audit_event_id FROM audit.audit_events WHERE tenant_id=? "
            "AND resource_type='promotion_decision' AND resource_id=? AND result='SUCCEEDED' "
            "AND jsonb_typeof(after_value->'version')='number' AND after_value->>'version'=? "
            "AND after_value->>'status'=? ORDER BY sequence_no DESC LIMIT 1",
            (tenant, promotion_decision_id, str(version), status),
        )
        if not rows:
            return None
        return self._load_committed_audit(tenant, rows[0]["audit_event_id"])

    def _native_promotion_proof(
        self, tenant: str, intake: dict[str, Any], promo: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]] | None:
        """Verify true committed native promotion, audit, outbox and revision binding."""
        promo_id = str(promo["promotion_decision_id"])
        intake_id = str(intake["intake_id"])
        listing_id = str(promo["listing_id"])
        property_id = str(promo["property_id"])
        status = promo["status"]
        version = int(promo["version"])
        gate_snapshot = _json_value(promo.get("gate_snapshot"))
        if (
            str(promo.get("tenant_id")) != tenant
            or str(promo.get("intake_id")) != intake_id
            or status not in {"REJECTED", "COMPLETED"}
            or not isinstance(gate_snapshot, dict)
            or str(intake.get("resolved_listing_id") or "") != listing_id
        ):
            return None
        listing = self.engine.query_one(
            "SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=?",
            (tenant, listing_id),
        )
        if (
            listing is None
            or str(listing.get("property_id") or "") != property_id
            or str(listing.get("current_revision_id") or "") != str(gate_snapshot.get("listing_revision_id") or "")
            or int(listing["version"]) != gate_snapshot.get("listing_version")
        ):
            return None

        if status == "REJECTED":
            if (
                not promo.get("proposer_subject_id")
                or not promo.get("reviewer_subject_id")
                or str(promo["reviewer_subject_id"]) == str(promo["proposer_subject_id"])
                or not promo.get("reviewed_at")
                or promo.get("candidate_site_id") is not None
                or promo.get("site_score_job_id") is not None
            ):
                return None
            loaded = self._promotion_latest_committed_audit(tenant, promo_id, version=version, status="REJECTED")
            if loaded is None:
                return None
            after = loaded.get("after_value")
            if (
                loaded.get("action") != "promotion.rejected"
                or loaded.get("resource_type") != "promotion_decision"
                or loaded.get("resource_id") != promo_id
                or loaded.get("actor_subject_id") != str(promo["reviewer_subject_id"])
                or loaded.get("service_principal") is not None
                or loaded.get("result") != "SUCCEEDED"
                or not isinstance(after, dict)
                or after.get("promotion_decision_id") != promo_id
                or after.get("intake_id") != intake_id
                or after.get("listing_id") != listing_id
                or after.get("status") != "REJECTED"
                or type(after.get("version")) is not int
                or after["version"] != version
                or after.get("reviewer_subject_id") != str(promo["reviewer_subject_id"])
            ):
                return None
            return {**loaded, "intake_id": intake_id}, None, gate_snapshot

        # status == "COMPLETED" (worker-accepted promotion)
        cand_id = str(promo["candidate_site_id"]) if promo.get("candidate_site_id") else None
        job_id = str(promo["site_score_job_id"]) if promo.get("site_score_job_id") else None
        if (
            intake["processing_state"] != "READY"
            or listing["lifecycle_state"] != "ACTIVE"
            or listing["legal_hold"]
            or not cand_id
            or not job_id
            or not promo.get("reviewer_subject_id")
            or str(promo["reviewer_subject_id"]) == str(promo["proposer_subject_id"])
        ):
            return None
        candidate = self.engine.query_one(
            "SELECT * FROM expansion.candidate_sites WHERE tenant_id=? AND candidate_site_id=?",
            (tenant, cand_id),
        )
        if (
            candidate is None
            or str(candidate["promotion_decision_id"]) != promo_id
            or str(candidate["source_listing_id"]) != listing_id
            or str(candidate["property_id"]) != property_id
            or candidate["target_format_code"] != promo["target_format_code"]
        ):
            return None
        job = self.engine.query_one(
            "SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? "
            "AND job_type='intake-sitescore' AND aggregate_type='candidate_site' AND aggregate_id=?",
            (tenant, job_id, cand_id),
        )
        if job is None:
            return None
        payload = _json_value(job.get("payload"))
        acceptance = payload.get("worker_acceptance") if isinstance(payload, dict) else None
        if (
            not isinstance(acceptance, dict)
            or acceptance.get("tenant_id") != tenant
            or acceptance.get("job_id") != job_id
            or acceptance.get("intake_id") != intake_id
            or acceptance.get("promotion_decision_id") != promo_id
            or acceptance.get("candidate_site_id") != cand_id
            or acceptance.get("promotion_version") != version
            or type(acceptance.get("accepted_fence")) is not int
            or not 0 < acceptance["accepted_fence"] <= int(job["fence_token"])
        ):
            return None
        loaded = self._load_committed_audit(tenant, acceptance.get("audit_event_id"))
        if loaded is None:
            return None
        after = loaded.get("after_value")
        if (
            loaded.get("resource_type") != "promotion_decision"
            or loaded.get("resource_id") != promo_id
            or loaded.get("action") != "candidate.promotion_completed"
            or loaded.get("service_principal") != "sitescore_worker"
            or loaded.get("result") != "SUCCEEDED"
            or loaded.get("correlation_id") != str(job["correlation_id"])
            or not isinstance(after, dict)
            or after.get("tenant_id") != tenant
            or after.get("promotion_decision_id") != promo_id
            or after.get("intake_id") != intake_id
            or after.get("listing_id") != listing_id
            or after.get("candidate_site_id") != cand_id
            or after.get("site_score_job_id") != job_id
            or after.get("status") != "COMPLETED"
            or type(after.get("version")) is not int
            or after["version"] != version
        ):
            return None
        outbox_row = self.engine.query_one(
            "SELECT event_id,event_type,aggregate_type,aggregate_id,aggregate_version,correlation_id "
            "FROM workflow.outbox_events WHERE tenant_id=? AND aggregate_type='promotion_decision' "
            "AND aggregate_id=? AND aggregate_version=? AND event_type='candidate.promotion_completed'",
            (tenant, promo_id, version),
        )
        if outbox_row is None or str(outbox_row["correlation_id"]) != str(job["correlation_id"]):
            return None
        return {**loaded, "intake_id": intake_id}, outbox_row, gate_snapshot

    def _native_score_job_proof(
        self,
        tenant: str,
        intake: dict[str, Any],
        promo: dict[str, Any],
        gate_snapshot: dict[str, Any],
        job: dict[str, Any],
        *,
        document_store: Any,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        """Verify committed score SUCCEEDED job, canonical audit and immutable report."""
        if job.get("status") != "SUCCEEDED":
            return None
        job_id = str(job["job_id"])
        intake_id = str(intake["intake_id"])
        promo_id = str(promo["promotion_decision_id"])
        cand_id = str(promo["candidate_site_id"])
        job_version = int(job["version"])
        payload = _json_value(job.get("payload"))
        result = _json_value(job.get("result"))
        acceptance = payload.get("worker_acceptance") if isinstance(payload, dict) else None
        if (
            str(job.get("tenant_id")) != tenant
            or str(job.get("aggregate_id")) != cand_id
            or str(promo.get("site_score_job_id") or "") != job_id
            or not isinstance(payload, dict)
            or payload.get("parent_owner") != "native_sql"
            or payload.get("intake_id") != intake_id
            or payload.get("promotion_decision_id") != promo_id
            or payload.get("candidate_site_id") != cand_id
            or not isinstance(acceptance, dict)
            or not isinstance(result, dict)
            or not all(result.get(key) for key in ("report_id", "report_version", "sitescore_run_id", "prediction_run_id", "model_version"))
            or type(result.get("report_version")) is not int
            or result["report_version"] < 1
        ):
            return None
        delivery = self.engine.query_one(
            "SELECT status FROM durable_jobs WHERE job_id=?",
            (job_id,),
        )
        if delivery is None or delivery["status"] != JobStatus.SUCCEEDED.value:
            return None
        # Immutable report & run artifacts must actually exist in the tenant store.
        report = document_store.get("sitescore.reports", str(result["report_id"]))
        sitescore_run = document_store.get("sitescore.sitescore_runs", str(result["sitescore_run_id"]))
        prediction_run = document_store.get("sitescore.prediction_runs", str(result["prediction_run_id"]))
        if (
            report is None
            or report.report_id != result["report_id"]
            or report.candidate_site_id != cand_id
            or report.report_version != result["report_version"]
            or report.sitescore_run_id != result["sitescore_run_id"]
            or report.model_version != result["model_version"]
            or sitescore_run is None
            or sitescore_run.candidate_site_id != cand_id
            or sitescore_run.prediction_run_id != result["prediction_run_id"]
            or prediction_run is None
            or prediction_run.prediction_run_id != result["prediction_run_id"]
        ):
            return None
        rows = self.engine.query(
            "SELECT audit_event_id FROM audit.audit_events WHERE tenant_id=? "
            "AND resource_type='job' AND resource_id=? AND action='sitescore.completed' "
            "AND service_principal='sitescore_worker' AND result='SUCCEEDED' "
            "AND jsonb_typeof(after_value->'version')='number' AND after_value->>'version'=? "
            "AND after_value->>'status'='SUCCEEDED' ORDER BY sequence_no DESC LIMIT 1",
            (tenant, job_id, str(job_version)),
        )
        if not rows:
            return None
        loaded = self._load_committed_audit(tenant, rows[0]["audit_event_id"])
        if loaded is None:
            return None
        after = loaded.get("after_value")
        audited_result = after.get("result") if isinstance(after, dict) else None
        if (
            loaded.get("resource_type") != "job"
            or loaded.get("resource_id") != job_id
            or loaded.get("action") != "sitescore.completed"
            or loaded.get("service_principal") != "sitescore_worker"
            or loaded.get("result") != "SUCCEEDED"
            or loaded.get("correlation_id") != str(job["correlation_id"])
            or not isinstance(after, dict)
            or after.get("job_id") != job_id
            or after.get("tenant_id") != tenant
            or after.get("intake_id") != intake_id
            or after.get("promotion_decision_id") != promo_id
            or after.get("candidate_site_id") != cand_id
            or after.get("status") != "SUCCEEDED"
            or type(after.get("version")) is not int
            or after["version"] != job_version
            or not isinstance(audited_result, dict)
            or any(audited_result.get(key) != result.get(key) for key in (
                "report_id", "report_version", "sitescore_run_id", "prediction_run_id", "model_version"
            ))
        ):
            return None
        return {**loaded, "intake_id": intake_id}, acceptance, result

    def promotion_owner(
        self,
        tenant: str,
        *,
        intake_id: str | None = None,
        promotion_decision_id: str | None = None,
        candidate_site_id: str | None = None,
        listing_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Identify actual tenant-qualified normalized SQL ownership for promotion/candidate paths."""
        selected = [v for v in (intake_id, promotion_decision_id, candidate_site_id, listing_id) if v is not None]
        if len(selected) != 1:
            raise ValueError("Exactly one native promotion/candidate resource is required")
        clean_id = _valid_uuid(selected[0])
        if clean_id is None:
            return None
        with self.graph.transaction(tenant):
            if promotion_decision_id is not None:
                row = self.engine.query_one(
                    "SELECT promotion_decision_id,intake_id,listing_id,property_id,candidate_site_id,site_score_job_id "
                    "FROM expansion.promotion_decisions WHERE tenant_id=? AND promotion_decision_id=?",
                    (tenant, clean_id),
                )
                return None if row is None else {"owner": "native_sql", **{k: str(v) if v is not None else None for k, v in row.items()}}
            if candidate_site_id is not None:
                row = self.engine.query_one(
                    "SELECT candidate_site_id,promotion_decision_id,source_listing_id AS listing_id,property_id "
                    "FROM expansion.candidate_sites WHERE tenant_id=? AND candidate_site_id=?",
                    (tenant, clean_id),
                )
                return None if row is None else {"owner": "native_sql", **{k: str(v) if v is not None else None for k, v in row.items()}}
            if listing_id is not None:
                row = self.engine.query_one(
                    "SELECT listing_id,property_id FROM expansion.listings WHERE tenant_id=? AND listing_id=?",
                    (tenant, clean_id),
                )
                return None if row is None else {"owner": "native_sql", **{k: str(v) if v is not None else None for k, v in row.items()}}
            if is_native_sql_owned(self.engine, tenant, intake_id=clean_id):
                row = self.engine.query_one(
                    "SELECT intake_id,resolved_listing_id FROM intake.intakes WHERE tenant_id=? AND intake_id=? AND deleted_at IS NULL",
                    (tenant, clean_id),
                )
                return None if row is None else {
                    "owner": "native_sql",
                    "intake_id": str(row["intake_id"]),
                    "listing_id": str(row["resolved_listing_id"]) if row.get("resolved_listing_id") else None,
                }
            return None

    def get_promotion(self, tenant: str, promotion_decision_id: str) -> dict[str, Any] | None:
        pid = _valid_uuid(promotion_decision_id)
        if pid is None:
            return None
        with self.graph.transaction(tenant):
            row = self.engine.query_one(
                "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? AND promotion_decision_id=?",
                (tenant, pid),
            )
            if row is None:
                return None
            audit = self._promotion_latest_committed_audit(
                tenant, pid, version=int(row["version"]), status=row["status"],
            )
            return _row_to_promotion_dict(row, audit_event_id=audit["audit_event_id"] if audit else None)

    def get_promotion_for_intake(self, tenant: str, intake_id: str) -> dict[str, Any] | None:
        iid = _valid_uuid(intake_id)
        if iid is None:
            return None
        with self.graph.transaction(tenant):
            row = self.engine.query_one(
                "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? AND intake_id=? "
                "ORDER BY created_at DESC,version DESC,promotion_decision_id DESC LIMIT 1",
                (tenant, iid),
            )
            if row is None:
                return None
            pid = str(row["promotion_decision_id"])
            audit = self._promotion_latest_committed_audit(
                tenant, pid, version=int(row["version"]), status=row["status"],
            )
            return _row_to_promotion_dict(row, audit_event_id=audit["audit_event_id"] if audit else None)

    def list_promotions(self, tenant: str, *, intake_id: str | None = None) -> list[dict[str, Any]]:
        with self.graph.transaction(tenant):
            if intake_id is not None:
                iid = _valid_uuid(intake_id)
                if iid is None:
                    return []
                rows = self.engine.query(
                    "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? AND intake_id=? "
                    "ORDER BY created_at,promotion_decision_id",
                    (tenant, iid),
                )
            else:
                rows = self.engine.query(
                    "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? "
                    "ORDER BY created_at,promotion_decision_id",
                    (tenant,),
                )
            result = []
            for row in rows:
                pid = str(row["promotion_decision_id"])
                audit = self._promotion_latest_committed_audit(
                    tenant, pid, version=int(row["version"]), status=row["status"],
                )
                result.append(_row_to_promotion_dict(row, audit_event_id=audit["audit_event_id"] if audit else None))
            return result

    def _native_promotion_adapters(
        self,
        tenant: str,
        intake_row: dict[str, Any],
        *,
        gate_snapshot: dict[str, Any] | None = None,
    ):
        from modules.listing.application.intake_score_admission import validate_score_admission
        from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied

        engine = self.engine
        graph = self.graph

        class _NativeOutboxAdapter:
            def save(self, event: DomainEvent) -> None:
                graph._outbox(event)

        class _NativeIntakeAdapter:
            def __init__(self) -> None:
                self._cached: dict[str, Any] | None = None

            def get_listing_intake(self, target_intake_id: str) -> dict[str, Any] | None:
                if str(target_intake_id) != str(intake_row["intake_id"]):
                    return None
                if self._cached is None:
                    rev = None
                    if intake_row.get("resolved_listing_id") is not None:
                        rev = engine.query_one(
                            "SELECT r.* FROM expansion.listings l JOIN expansion.listing_revisions r "
                            "ON r.tenant_id=l.tenant_id AND r.listing_id=l.listing_id AND r.listing_revision_id=l.current_revision_id "
                            "WHERE l.tenant_id=? AND l.listing_id=? FOR SHARE",
                            (tenant, intake_row["resolved_listing_id"]),
                        )
                    self._cached = load_native_intake_projection(
                        engine, tenant, str(intake_row["intake_id"]),
                        gate_snapshot=gate_snapshot, revision_row=rev, lock_mode=2,
                    )
                return deepcopy(self._cached)

        class _NativeListingAdapter:
            def __init__(self, intake_adapter: _NativeIntakeAdapter) -> None:
                self._intake_adapter = intake_adapter
                self._bundles: dict[str, dict[str, Any]] = {}

            def _load(self, listing_id: str) -> dict[str, Any]:
                expected_addr = gate_snapshot.get("address_id") if isinstance(gate_snapshot, dict) else None
                bundle = load_native_listing_bundle(
                    engine, tenant, listing_id,
                    expected_address_id=str(expected_addr) if expected_addr else None,
                    lock_mode=2, domain_status=None,
                )
                self._bundles[str(listing_id)] = bundle
                return bundle

            def get_listing(self, listing_id: str) -> dict[str, Any] | None:
                bundle = self._load(listing_id)
                listing = bundle["domain_listing"]
                addr = bundle["domain_address"]
                d: dict[str, Any] = {
                    "id": listing.listing_id,
                    "listing_id": listing.listing_id,
                    "listingId": listing.listing_id,
                    "source_listing_id": listing.source_listing_id,
                    "source_id": listing.source_id,
                    "status": listing.listing_status,
                    "listing_status": listing.listing_status,
                    "address_id": listing.address_id,
                    "address": addr.normalized_address or addr.raw_address,
                    "address_raw": addr.raw_address,
                    "rent_amount": listing.rent_amount,
                    "rentPerMonth": listing.rent_amount,
                    "currency": listing.currency,
                    "area_ping": listing.area_ping,
                    "areaPing": listing.area_ping,
                    "floor": listing.floor,
                    "frontage_m": listing.frontage_m,
                    "depth_m": listing.depth_m,
                    "corner_flag": listing.corner_flag,
                    "parking_flag": listing.parking_flag,
                    "utility_electricity_flag": listing.utility_electricity_flag,
                    "utility_drainage_flag": listing.utility_drainage_flag,
                    "utility_gas_flag": listing.utility_gas_flag,
                    "confidence": listing.effective_confidence,
                    "snapshot_id": listing.snapshot_id,
                    "datasetSnapshotId": listing.snapshot_id or None,
                    "geocode_confidence": addr.geocode_confidence,
                    "geocodeConfidence": addr.geocode_confidence,
                    "h3_res_9": addr.h3_res_9,
                    "h3Index": addr.h3_res_9,
                    "h3_index": addr.h3_res_9,
                    "hz": addr.h3_res_9,
                    "heatZoneId": addr.h3_res_9,
                    "latitude": addr.latitude,
                    "longitude": addr.longitude,
                    "property_id": str(bundle["listing_row"]["property_id"]),
                }
                for attr in (
                    "listing_obs_id", "prior_90d_cell_net_revenue", "prior_90d_cell_transaction_count",
                    "prior_90d_cell_store_count", "feature_snapshot_time", "rent_benchmark_median",
                    "rent_benchmark_p25", "rent_benchmark_p75", "rent_benchmark_sample_count", "rent_benchmark_id",
                ):
                    if hasattr(listing, attr):
                        d[attr] = getattr(listing, attr)
                return d

            def get_domain_listing(self, listing_id: str) -> Listing | None:
                bundle = self._bundles.get(str(listing_id)) or self._load(listing_id)
                return bundle["domain_listing"]

            def get_address(self, address_id: str) -> AddressLocation | None:
                for bundle in self._bundles.values():
                    if bundle["domain_address"].address_id == str(address_id):
                        return bundle["domain_address"]
                _, addr = load_native_address(engine, tenant, address_id=str(address_id), lock_mode=1)
                return addr

            def get_native_gate_context(self, listing_id: str) -> dict[str, Any]:
                bundle = self._bundles.get(str(listing_id)) or self._load(listing_id)
                intake_proj = self._intake_adapter.get_listing_intake(str(intake_row["intake_id"]))
                if intake_proj is None:
                    raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, "NATIVE_INTAKE_UNAVAILABLE")
                domain_listing = bundle["domain_listing"]
                probe = SimpleNamespace(
                    listing=domain_listing,
                    dataset_snapshot_id=domain_listing.snapshot_id or None,
                )
                try:
                    admission = validate_score_admission(engine, intake_proj, probe)
                except ScoreDeliveryDenied as exc:
                    raise DomainValidationError(DenialCode.SOURCE_POLICY_DENIED, str(exc)) from exc
                now_iso = _instant(engine.query_one("SELECT clock_timestamp() AS at")["at"])
                listing_sql = bundle["listing_row"]
                rev_sql = bundle["revision_row"]
                prop_sql = bundle["property_row"]
                addr_sql = bundle["address_row"]
                geo_sql = bundle["geo_row"]
                versions = {
                    "intake_id": intake_proj["intake_id"],
                    "intake_version": intake_proj["version"],
                    "listing_id": str(listing_sql["listing_id"]),
                    "listing_version": int(listing_sql["version"]),
                    "listing_revision_id": str(rev_sql["listing_revision_id"]),
                    "listing_revision_no": int(rev_sql["revision_no"]),
                    "property_id": str(prop_sql["property_id"]),
                    "property_version": int(prop_sql["version"]),
                    "address_id": str(addr_sql["address_id"]),
                    "address_revision": int(addr_sql["revision"]),
                    "geo_cell_id": str(geo_sql["geo_cell_id"]),
                    "geo_version": bundle["geo_version"],
                    "h3_res_9": bundle["domain_address"].h3_res_9,
                    "source_id": admission["source_id"],
                    "source_policy_version": admission["source_policy_version"],
                    "source_snapshot_id": admission["source_snapshot_id"],
                    "source_snapshot_version": admission["source_snapshot_version"],
                    "parser_run_id": admission.get("parser_run_id"),
                    "parser_run_version": admission.get("parser_run_version"),
                    "parser_release_id": admission.get("parser_release_id"),
                    "parser_release_version": admission.get("parser_release_version"),
                    "parser_input_schema_version": admission.get("parser_input_schema_version"),
                    "parser_output_schema_version": admission.get("parser_output_schema_version"),
                }
                return {
                    "domain_listing": domain_listing,
                    "domain_address": bundle["domain_address"],
                    "versions": versions,
                    "evaluated_at": now_iso,
                }

            def list_candidates(self) -> list[CandidateSiteDraft]:
                return list_native_candidate_drafts(engine, tenant)

            def save_candidate(self, candidate: CandidateSiteDraft) -> None:
                save_native_candidate_cas(engine, tenant, candidate)

            def save_listing(self, listing: Any) -> None:
                if hasattr(listing, "get"):
                    lid = str(listing.get("listing_id") or listing.get("id") or "")
                    cid = listing.get("candidateId") or listing.get("candidate_site_id")
                    pid = listing.get("promotionDecisionId") or listing.get("promotion_decision_id")
                else:
                    lid = str(listing.listing_id)
                    cid = getattr(listing, "candidate_site_id", None)
                    pid = getattr(listing, "promotion_decision_id", None)
                bundle = self._bundles.get(lid)
                prove_native_listing_candidate_relation(
                    engine,
                    tenant,
                    listing_id=lid,
                    candidate_site_id=str(cid) if cid else None,
                    promotion_decision_id=str(pid) if pid else None,
                    expected_listing_version=int(bundle["listing_row"]["version"]) if bundle else None,
                    expected_revision_id=str(bundle["listing_row"]["current_revision_id"]) if bundle else None,
                )

        class _NativePromotionAdapter:
            def __init__(self) -> None:
                self._persisted_versions: dict[str, int] = {}

            def get_promotion(self, promotion_decision_id: str) -> dict[str, Any] | None:
                pid = _valid_uuid(promotion_decision_id)
                if pid is None:
                    return None
                row = engine.query_one(
                    "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? AND promotion_decision_id=? FOR UPDATE",
                    (tenant, pid),
                )
                if row is None:
                    return None
                self._persisted_versions[pid] = int(row["version"])
                return _row_to_promotion_dict(row)

            def list_promotions(self) -> list[dict[str, Any]]:
                rows = engine.query(
                    "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? ORDER BY created_at,promotion_decision_id",
                    (tenant,),
                )
                return [_row_to_promotion_dict(row) for row in rows]

            def save_promotion(self, promo: dict[str, Any]) -> None:
                pid = str(promo["promotion_decision_id"])
                existing_version = self._persisted_versions.get(pid)
                if existing_version is None:
                    row = engine.query_one(
                        "SELECT version FROM expansion.promotion_decisions WHERE tenant_id=? AND promotion_decision_id=? FOR UPDATE",
                        (tenant, pid),
                    )
                    if row is not None:
                        existing_version = int(row["version"])
                if existing_version is None:
                    try:
                        engine.execute(
                            "INSERT INTO expansion.promotion_decisions (promotion_decision_id,tenant_id,intake_id,"
                            "listing_id,property_id,target_format_code,status,proposer_subject_id,reason,"
                            "risk_acknowledged,gate_snapshot,version,created_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?::jsonb,?,?)",
                            (
                                pid, tenant, str(promo["intake_id"]), str(promo["listing_id"]),
                                str(promo["property_id"]), promo["target_format_code"], promo["status"],
                                str(promo["proposer_subject_id"]), promo["reason"],
                                bool(promo.get("risk_acknowledged", False)),
                                json.dumps(promo["gate_snapshot"], sort_keys=True, separators=(",", ":"), default=str),
                                int(promo["version"]),
                                promo["created_at"],
                            ),
                        )
                    except Exception as exc:
                        if (
                            getattr(exc, "sqlstate", None) == "23505"
                            and getattr(getattr(exc, "diag", None), "constraint_name", None) == "ux_active_promotion_request"
                        ):
                            raise DomainValidationError(DenialCode.DEPENDENCY_CONFLICT, "ACTIVE_PROMOTION_CONFLICT") from exc
                        raise
                    self._persisted_versions[pid] = int(promo["version"])
                    return

                reviewer = promo.get("reviewer_subject_id") or promo.get("reviewer")
                reviewed_at = promo.get("reviewed_at")
                reason_val = promo.get("review_reason") or promo.get("reason")
                updated = engine.execute(
                    "UPDATE expansion.promotion_decisions SET status=?,reviewer_subject_id=?,reviewed_at=?,"
                    "reason=?,risk_acknowledged=?,candidate_site_id=?,site_score_job_id=?,version=? "
                    "WHERE tenant_id=? AND promotion_decision_id=? AND version=?",
                    (
                        promo["status"], reviewer, reviewed_at, reason_val,
                        bool(promo.get("risk_acknowledged", False)),
                        promo.get("candidate_site_id"), promo.get("site_score_job_id"),
                        int(promo["version"]), tenant, pid, existing_version,
                    ),
                )
                if updated.rowcount != 1:
                    raise IdentityConflictError("NATIVE_PROMOTION_VERSION_CONFLICT")
                self._persisted_versions[pid] = int(promo["version"])

        intake_adapter = _NativeIntakeAdapter()
        return _NativePromotionAdapter(), _NativeListingAdapter(intake_adapter), intake_adapter, _NativeOutboxAdapter()

    def _replay_store(self, explicit: Any) -> Any:
        return explicit if explicit is not None else self.replay_store

    def _read_native_replay(
        self, replay_store: Any, tenant: str, actor_id: str, operation: str, key: str, resource_id: str, digest: str,
    ) -> dict[str, Any] | None:
        prior = replay_store.read_command_replay(tenant, actor_id, operation, key, resource_id)
        if prior is None:
            return None
        if prior[0] != digest:
            raise IdentityConflictError("IDEMPOTENCY_KEY_REUSED")
        return deepcopy(prior[1])

    def _write_native_replay(
        self, replay_store: Any, tenant: str, actor_id: str, operation: str, key: str, resource_id: str,
        digest: str, receipt: dict[str, Any], status_code: int,
    ) -> None:
        from shared.infrastructure.persistence.assisted_listing_intake import _STATE_COLLECTIONS

        replay_key = replay_store.command_replay_key(tenant, actor_id, operation, key, resource_id)
        replay_store._store.put(
            _STATE_COLLECTIONS["replays"],
            f"{tenant}:{replay_key}",
            replay_store._wrap(tenant, replay_key, (digest, deepcopy(receipt), status_code)),
        )

    def request_promotion(
        self,
        tenant: str,
        actor_id: str,
        *,
        intake_id: str,
        expected_intake_version: int,
        target_format_code: str,
        reason: str,
        gate_snapshot_sha256: str,
        idempotency_key: str,
        correlation_id: str | None = None,
        actor_role: PrincipalRole = PrincipalRole.EXPANSION_STAFF,
        replay_store: Any = None,
        request_digest: str | None = None,
        return_replay_status: bool = False,
    ) -> dict[str, Any] | tuple[dict[str, Any], bool]:
        """Request native SQL candidate promotion inside one SERIALIZABLE Graph UoW."""
        from modules.listing.application.promotion import PromotionService

        UUID(tenant)
        UUID(actor_id)
        UUID(intake_id)
        if not reason or not reason.strip() or not idempotency_key:
            raise ValueError("Explicit promotion reason and idempotency_key are required")
        active_replay = self._replay_store(replay_store)
        with self.graph.transaction(tenant):
            if active_replay is not None and request_digest is not None:
                prior = self._read_native_replay(
                    active_replay, tenant, actor_id, "requestCandidatePromotion",
                    idempotency_key, intake_id, request_digest,
                )
                if prior is not None:
                    return (prior, True) if return_replay_status else prior
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            intake_row = self.graph._one(
                "SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? AND deleted_at IS NULL FOR UPDATE",
                (tenant, intake_id),
            )
            if int(intake_row["version"]) != expected_intake_version:
                raise IdentityConflictError("CANONICAL_INTAKE_VERSION_CONFLICT")
            if intake_row["processing_state"] != "READY":
                raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "WORKFLOW_STATE_DENIED")
            if intake_row["legal_hold"]:
                raise DomainValidationError(DenialCode.LEGAL_HOLD_CONFLICT, "LEGAL_HOLD_CONFLICT")
            promo_repo, listing_repo, intake_repo, outbox_repo = self._native_promotion_adapters(tenant, intake_row)
            service = PromotionService(
                promotion_repository=promo_repo,
                listing_repository=listing_repo,
                intake_repository=intake_repo,
                outbox_repository=outbox_repo,
            )
            corr = correlation_id or str(intake_row["correlation_id"])
            UUID(corr)
            promo_record = service.request_promotion(
                intake_id=intake_id,
                target_format_code=target_format_code,
                reason=reason,
                gate_snapshot_sha256=gate_snapshot_sha256,
                context=TransitionContext(
                    actor=Actor(actor_id=actor_id, role=actor_role, tenant_id=tenant),
                    idempotency_key=idempotency_key,
                    correlation_id=corr,
                ),
            )
            audit_id = str(uuid4())
            promo_record["audit_event_id"] = audit_id
            at = _instant(self.engine.query_one("SELECT clock_timestamp() AS at")["at"])
            scope = {
                "tenant_id": tenant,
                **{k: str(intake_row[k]) if intake_row.get(k) else None for k in (
                    "brand_id", "region_id", "assigned_area_id", "heat_zone_id"
                )},
            }
            self.graph.audit_sink.persist(
                {
                    "audit_event_id": audit_id,
                    "tenant_id": tenant,
                    "resource_type": "promotion_decision",
                    "resource_id": promo_record["promotion_decision_id"],
                    "decision_id": promo_record["promotion_decision_id"],
                    "action": "intake.promotion_requested",
                    "result": "SUCCEEDED",
                    "actor_subject_id": actor_id,
                    "correlation_id": corr,
                    "occurred_at": at,
                    "source_snapshot_id": promo_record["gate_snapshot"].get("source_snapshot_id"),
                    "parser_run_id": promo_record["gate_snapshot"].get("parser_run_id"),
                    "before_value": None,
                    "after_value": deepcopy(promo_record),
                    "reason": reason,
                },
                scope,
            )
            if active_replay is not None and request_digest is not None:
                self._write_native_replay(
                    active_replay, tenant, actor_id, "requestCandidatePromotion",
                    idempotency_key, intake_id, request_digest, promo_record, 202,
                )
            return (promo_record, False) if return_replay_status else promo_record

    def review_promotion(
        self,
        tenant: str,
        actor_id: str,
        *,
        promotion_decision_id: str,
        expected_version: int,
        decision: str,
        reason: str,
        risk_acknowledged: bool,
        idempotency_key: str,
        correlation_id: str | None = None,
        actor_role: PrincipalRole = PrincipalRole.EXPANSION_MANAGER,
        score_dispatcher: Any = None,
        score_queue_hook: Any = None,
        replay_store: Any = None,
        request_digest: str | None = None,
        return_replay_status: bool = False,
    ) -> dict[str, Any] | tuple[dict[str, Any], bool]:
        """Review native SQL promotion, create candidate and reserve first-score job atomically."""
        from modules.listing.application.promotion import PromotionService

        UUID(tenant)
        UUID(actor_id)
        UUID(promotion_decision_id)
        if not reason or not reason.strip() or not risk_acknowledged or not idempotency_key:
            raise DomainValidationError(
                DenialCode.RISK_ACKNOWLEDGEMENT_REQUIRED,
                "Explicit review reason, risk_acknowledged and idempotency_key are required",
            )
        active_replay = self._replay_store(replay_store)
        with self.graph.transaction(tenant):
            if active_replay is not None and request_digest is not None:
                prior = self._read_native_replay(
                    active_replay, tenant, actor_id, "reviewPromotionDecision",
                    idempotency_key, promotion_decision_id, request_digest,
                )
                if prior is not None:
                    return (prior, True) if return_replay_status else prior
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            promo_row = self.graph._one(
                "SELECT * FROM expansion.promotion_decisions WHERE tenant_id=? AND promotion_decision_id=? FOR UPDATE",
                (tenant, promotion_decision_id),
            )
            if promo_row["status"] != "PENDING_REVIEW":
                raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "WORKFLOW_STATE_DENIED")
            if int(promo_row["version"]) != expected_version:
                raise IdentityConflictError("NATIVE_PROMOTION_VERSION_CONFLICT")
            if str(promo_row["proposer_subject_id"]) == actor_id:
                raise DomainValidationError(DenialCode.SELF_REVIEW_DENIED, "SELF_REVIEW_DENIED")
            intake_row = self.graph._one(
                "SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? AND deleted_at IS NULL FOR UPDATE",
                (tenant, promo_row["intake_id"]),
            )
            if intake_row["processing_state"] != "READY" or intake_row["legal_hold"]:
                raise DomainValidationError(DenialCode.WORKFLOW_STATE_DENIED, "WORKFLOW_STATE_DENIED")
            before_promo = _row_to_promotion_dict(promo_row)
            gate_snapshot = before_promo["gate_snapshot"]
            promo_repo, listing_repo, intake_repo, outbox_repo = self._native_promotion_adapters(
                tenant, intake_row, gate_snapshot=gate_snapshot,
            )
            service = PromotionService(
                promotion_repository=promo_repo,
                listing_repository=listing_repo,
                intake_repository=intake_repo,
                outbox_repository=outbox_repo,
                score_queue_hook=score_queue_hook,
                score_dispatcher=score_dispatcher if decision == "APPROVE" else None,
            )
            corr = correlation_id or str(intake_row["correlation_id"])
            UUID(corr)
            updated = service.review_promotion(
                promotion_decision_id=promotion_decision_id,
                decision=decision,
                reason=reason,
                risk_acknowledged=risk_acknowledged,
                context=TransitionContext(
                    actor=Actor(actor_id=actor_id, role=actor_role, tenant_id=tenant),
                    idempotency_key=idempotency_key,
                    correlation_id=corr,
                ),
            )
            updated["reviewer_subject_id"] = actor_id
            audit_id = str(uuid4())
            updated["audit_event_id"] = audit_id
            at = _instant(self.engine.query_one("SELECT clock_timestamp() AS at")["at"])
            scope = {
                "tenant_id": tenant,
                **{k: str(intake_row[k]) if intake_row.get(k) else None for k in (
                    "brand_id", "region_id", "assigned_area_id", "heat_zone_id"
                )},
            }
            self.graph.audit_sink.persist(
                {
                    "audit_event_id": audit_id,
                    "tenant_id": tenant,
                    "resource_type": "promotion_decision",
                    "resource_id": promotion_decision_id,
                    "decision_id": promotion_decision_id,
                    "action": "promotion." + updated["status"].lower(),
                    "result": "SUCCEEDED",
                    "actor_subject_id": actor_id,
                    "correlation_id": corr,
                    "occurred_at": at,
                    "source_snapshot_id": gate_snapshot.get("source_snapshot_id"),
                    "parser_run_id": gate_snapshot.get("parser_run_id"),
                    "before_value": before_promo,
                    "after_value": deepcopy(updated),
                    "reason": reason,
                },
                scope,
            )
            if updated["status"] == "SCORE_QUEUED" and score_dispatcher is not None:
                score_dispatcher.seal_enqueue(updated["site_score_job_id"], tenant)
            if active_replay is not None and request_digest is not None:
                self._write_native_replay(
                    active_replay, tenant, actor_id, "reviewPromotionDecision",
                    idempotency_key, promotion_decision_id, request_digest, updated, 200,
                )
            return (updated, False) if return_replay_status else updated
