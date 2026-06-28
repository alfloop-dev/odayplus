from __future__ import annotations

import uuid
from datetime import date, datetime, time

from shared.domain import (
    AddressLocation,
    Alert,
    Approval,
    AuditEvent,
    Brand,
    CandidateSite,
    CompetitorStore,
    DataSnapshot,
    Decision,
    ForecastOutput,
    Listing,
    Machine,
    Poi,
    Store,
    Tenant,
    Transaction,
    WorkOrder,
)


def test_tenant_instantiation_and_defaults() -> None:
    """Test Tenant dataclass instantiation and default values."""
    tenant = Tenant(tenant_name="ODay Group")
    assert tenant.tenant_name == "ODay Group"
    assert tenant.status == "active"
    assert isinstance(tenant.created_at, datetime)
    # Check that UUID is generated
    assert uuid.UUID(tenant.tenant_id)


def test_brand_instantiation_and_types() -> None:
    """Test Brand dataclass instantiation and field constraints."""
    brand = Brand(
        tenant_id=str(uuid.uuid4()),
        brand_code="ODAY",
        brand_name="ODay Laundry",
        brand_type="owned",
    )
    assert brand.brand_code == "ODAY"
    assert brand.brand_type in ["owned", "franchise", "competitor", "external"]
    assert brand.status == "active"


def test_address_location_instantiation() -> None:
    """Test AddressLocation fields and defaults."""
    address = AddressLocation(
        raw_address="台北市信義區信義路五段7號",
        normalized_address="台北市信義區信義路五段7號",
        city="台北市",
        district="信義區",
        latitude=25.033964,
        longitude=121.564468,
        geocode_precision="rooftop",
        geocode_confidence=0.99,
        h3_res_9="89263064c2fffff",
    )
    assert address.city == "台北市"
    assert address.latitude == 25.033964
    assert address.geocode_precision in ["rooftop", "street", "district", "manual"]
    assert 0.0 <= address.geocode_confidence <= 1.0
    assert address.manual_override_flag is False


def test_store_instantiation_and_scd2() -> None:
    """Test Store instantiation, dates, and SCD2 fields."""
    store = Store(
        tenant_id=str(uuid.uuid4()),
        brand_id=str(uuid.uuid4()),
        source_store_id="S001",
        store_name="Xinyi Store",
        opened_on=date(2026, 6, 26),
        service_start_time=time(8, 0),
        service_end_time=time(22, 0),
    )
    assert store.store_status == "planned"
    assert store.ownership_type == "owned"
    assert store.opened_on == date(2026, 6, 26)
    assert store.closed_on is None
    assert store.service_start_time == time(8, 0)
    assert store.service_end_time == time(22, 0)
    assert isinstance(store.effective_from, datetime)
    assert store.effective_to == datetime(9999, 12, 31, 23, 59, 59)
    assert store.is_current is True


def test_machine_instantiation_and_types() -> None:
    """Test Machine field properties and default constraints."""
    machine = Machine(
        store_id=str(uuid.uuid4()),
        source_machine_id="M01",
        machine_family="washer",
        capacity_kg=15.0,
        capacity_band="medium",
        installed_on=date(2026, 6, 26),
    )
    assert machine.machine_family in ["washer", "dryer", "combo", "payment_terminal", "other"]
    assert machine.capacity_kg == 15.0
    assert machine.capacity_band in ["small", "medium", "large", "xlarge"]
    assert machine.machine_status == "active"


def test_transaction_instantiation_and_amounts() -> None:
    """Test Transaction event fields and amount logic."""
    transaction = Transaction(
        source_transaction_id="TX10023",
        store_id=str(uuid.uuid4()),
        gross_amount=150.0,
        discount_amount=20.0,
        net_amount=130.0,
        currency="TWD",
        payment_method="LINE Pay",
        source_system="POS",
    )
    assert transaction.gross_amount == 150.0
    assert transaction.discount_amount == 20.0
    assert transaction.net_amount == 130.0
    assert transaction.currency == "TWD"
    assert transaction.payment_method == "LINE Pay"
    assert transaction.transaction_status == "succeeded"
    assert isinstance(transaction.event_time, datetime)
    assert isinstance(transaction.observation_time, datetime)
    assert isinstance(transaction.ingested_at, datetime)


def test_work_order_instantiation() -> None:
    """Test WorkOrder status, severity, and values."""
    order = WorkOrder(
        store_id=str(uuid.uuid4()),
        issue_type="failure",
        issue_subtype="water_leakage",
        opened_at=datetime.now(),
        severity="high",
        cost_amount=2500.0,
    )
    assert order.status == "open"
    assert order.severity in ["low", "medium", "high", "critical"]
    assert order.cost_amount == 2500.0
    assert order.closed_at is None


def test_listing_instantiation_and_utility_flags() -> None:
    """Test Listing attributes and utility flags."""
    listing = Listing(
        source_listing_id="LST998",
        source_id="591",
        rent_amount=45000.0,
        area_ping=25.5,
        corner_flag=True,
        parking_flag=False,
        utility_electricity_flag=True,
        utility_drainage_flag=True,
        utility_gas_flag=False,
        confidence=0.85,
    )
    assert listing.listing_status == "active"
    assert listing.rent_amount == 45000.0
    assert listing.area_ping == 25.5
    assert listing.corner_flag is True
    assert listing.parking_flag is False
    assert listing.utility_electricity_flag is True
    assert listing.utility_drainage_flag is True
    assert listing.utility_gas_flag is False
    assert listing.confidence == 0.85


def test_candidate_site_instantiation() -> None:
    """Test CandidateSite fields."""
    site = CandidateSite(
        listing_id=str(uuid.uuid4()),
        address_id=str(uuid.uuid4()),
        target_format_code="ODAY_G2",
        created_by="agent-antigravity",
    )
    assert site.site_status == "new"
    assert site.target_format_code == "ODAY_G2"
    assert site.created_by == "agent-antigravity"
    assert isinstance(site.created_at, datetime)


def test_decision_and_approvals() -> None:
    """Test Decision and associated Approval links."""
    decision_id = str(uuid.uuid4())
    decision = Decision(
        decision_id=decision_id,
        decision_type="site_go_wait_reject",
        entity_type="candidate_site",
        entity_id=str(uuid.uuid4()),
        recommendation="go",
        policy_version_id="POL-V1.0",
        created_by="system",
    )
    assert decision.decision_status == "proposed"
    assert decision.recommendation == "go"

    approval = Approval(
        decision_id=decision_id,
        approver_id="user-manager-01",
        approval_status="approved",
        approved_at=datetime.now(),
        comment="Looks great, proceed with leasing.",
    )
    assert approval.decision_id == decision_id
    assert approval.approval_status == "approved"
    assert approval.comment == "Looks great, proceed with leasing."


def test_audit_event_instantiation() -> None:
    """Test AuditEvent fields and properties."""
    audit = AuditEvent(
        actor_id="user-manager-01",
        actor_type="user",
        action="approve",
        entity_type="decision",
        entity_id=str(uuid.uuid4()),
        before_hash="a1b2c3d4",
        after_hash="e5f6g7h8",
        ip_address="192.168.1.100",
        correlation_id="corr-xyz-12345",
    )
    assert audit.actor_id == "user-manager-01"
    assert audit.actor_type == "user"
    assert audit.action == "approve"
    assert audit.before_hash == "a1b2c3d4"
    assert audit.after_hash == "e5f6g7h8"
    assert audit.ip_address == "192.168.1.100"
    assert audit.correlation_id == "corr-xyz-12345"
    assert isinstance(audit.occurred_at, datetime)


def test_additional_canonical_entities() -> None:
    """Test other data structures like CompetitorStore, Poi, ForecastOutput, etc."""
    competitor = CompetitorStore(
        brand_name="WashMore",
        store_name="Xinyi Branch",
        estimated_capacity=10.0,
        distance_to_nearest_oday_m=120.5,
    )
    assert competitor.estimated_capacity == 10.0
    assert competitor.distance_to_nearest_oday_m == 120.5

    poi = Poi(
        source_poi_id="POI-999",
        poi_name="Taipei 101",
        poi_category="Tourism",
        confidence=1.0,
    )
    assert poi.poi_name == "Taipei 101"
    assert poi.confidence == 1.0

    forecast = ForecastOutput(
        store_id=str(uuid.uuid4()),
        prediction_run_id=str(uuid.uuid4()),
        horizon_days=28,
        p50=180000.00,
        trajectory_class="growing",
    )
    assert forecast.horizon_days == 28
    assert forecast.p50 == 180000.00
    assert forecast.trajectory_class == "growing"

    alert = Alert(
        store_id=str(uuid.uuid4()),
        alert_level="orange",
        alert_reason_code="revenue_gap",
        evidence_json={"actual_revenue": 10000, "forecasted_revenue": 15000},
    )
    assert alert.alert_level == "orange"
    assert alert.evidence_json["actual_revenue"] == 10000

    snapshot = DataSnapshot(
        snapshot_type="training",
        source_id="POS-Transactions",
        storage_uri="gs://laundromat-snapshots/training/tx_v1.parquet",
        schema_version="1.0.0",
        row_count=150000,
        quality_score=0.98,
        created_by_run_id="run-pipeline-09",
    )
    assert snapshot.snapshot_type == "training"
    assert snapshot.row_count == 150000
    assert snapshot.quality_score == 0.98
