"""Actual PostgreSQL isolation, immutable writes and corrupt-row refusal.

All financial parameters below are synthetic engineering inputs. Persisting
them supplies no source admission, financial approval or production evidence.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest

from modules.site_economics.application.service import SiteEconomicsService
from modules.site_economics.infrastructure.durable import (
    DurableSiteEconomicsRepository,
    SiteEconomicsPersistenceError,
)
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from tests.integration.test_intake_registry_calendar_postgresql import _bundle, _upgrade
from tests.site_economics_fixtures import synthetic_g2_registry

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def owners(intake_blank_db, monkeypatch, tmp_path):
    _upgrade(intake_blank_db, "head")
    apply_upgrade_to_database(intake_blank_db.url())
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", (tmp_path / "owned-worm").as_uri())
    bundles = []
    try:
        bundles.extend(_bundle(intake_blank_db, monkeypatch) for _ in range(2))
        tenant = str(uuid4())
        documents = [
            TenantScopedDocumentStore(PostgresDocumentStore(bundle.engine), tenant)
            for bundle in bundles
        ]
        assert documents[0].engine is not documents[1].engine
        assert all(store.engine.dialect == "postgresql" for store in documents)
        yield documents, [DurableSiteEconomicsRepository(store) for store in documents]
    finally:
        for bundle in reversed(bundles):
            bundle.engine.close()


def _document(store):
    return SiteEconomicsService(registry=synthetic_g2_registry()).evaluate_site(
        site_id="synthetic-financial-site", tenant_id=store.tenant_id,
        area_ping=25.0, monthly_rent=45_000.0,
    )


def test_committed_document_survives_independent_owner_and_mutated_read(owners):
    documents, (first, second) = owners
    document = _document(documents[0])
    document.metadata["synthetic-input"] = {"revision": 1}
    expected_metadata = deepcopy(document.metadata)
    first.save(document)
    document.metadata["synthetic-input"]["revision"] = 999
    loaded = second.get_by_document_id(document.document_id)
    assert loaded.metadata["synthetic-input"] == {"revision": 1}
    loaded.metadata.clear()
    assert first.get_by_document_id(document.document_id).metadata == expected_metadata


def test_identical_replay_does_not_add_or_replace_a_version(owners):
    documents, (first, second) = owners
    document = _document(documents[0])
    first.save(document)
    second.save(document)
    assert first.list_by_site_id(document.site_id) == [document]
    assert second.get_latest_by_site_id(document.site_id) == document


def test_later_different_payload_cannot_rewrite_a_committed_identity(owners):
    documents, (first, second) = owners
    document = _document(documents[0])
    first.save(document)
    changed = replace(document, metadata={"synthetic-replacement": True})
    with pytest.raises(SiteEconomicsPersistenceError, match="IMMUTABLE_ID_CONFLICT"):
        second.save(changed)
    assert first.get_by_document_id(document.document_id) == document
    assert second.list_by_site_id(document.site_id) == [document]


def test_two_independent_writers_cannot_replace_one_immutable_identity(owners):
    documents, repositories = owners
    document = _document(documents[0])
    alternatives = [replace(document, metadata={"synthetic-writer": i}) for i in range(2)]
    barrier = Barrier(2)

    def write(index):
        barrier.wait(timeout=10)
        try:
            repositories[index].save(alternatives[index])
            return "saved", index
        except SiteEconomicsPersistenceError as error:
            return str(error), index

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(2)))
    assert sorted(status for status, _ in results) == [
        "SITE_ECONOMICS_IMMUTABLE_ID_CONFLICT", "saved",
    ]
    winner = next(index for status, index in results if status == "saved")
    assert repositories[0].get_by_document_id(document.document_id) == alternatives[winner]
    assert repositories[1].list_by_site_id(document.site_id) == [alternatives[winner]]


def test_same_ids_are_tenant_partitioned_and_foreign_payload_is_refused(owners):
    documents, (first, _) = owners
    other_store = TenantScopedDocumentStore(
        PostgresDocumentStore(documents[1].engine), str(uuid4()),
    )
    other = DurableSiteEconomicsRepository(other_store)
    document = _document(documents[0])
    first.save(document)
    assert other.get_by_document_id(document.document_id) is None
    assert other.list_by_site_id(document.site_id) == []
    with pytest.raises(SiteEconomicsPersistenceError, match="DOCUMENT_INVALID"):
        other.save(document)
    foreign = replace(document, tenant_id=other_store.tenant_id, metadata={"other": True})
    other.save(foreign)
    assert first.get_by_document_id(document.document_id) == document
    assert other.get_by_document_id(document.document_id) == foreign


def test_latest_uses_evaluation_instant_not_last_write_or_offset_spelling(owners):
    documents, (first, second) = owners
    document = _document(documents[0])
    newest = replace(document, document_id="synthetic-new", evaluated_at="2026-10-16T09:00:00+08:00")
    older = replace(document, document_id="synthetic-old", evaluated_at="2026-10-16T00:30:00+00:00")
    first.save(newest)
    second.save(older)
    assert first.list_by_site_id(document.site_id) == [older, newest]
    assert second.get_latest_by_site_id(document.site_id) == newest


@pytest.mark.parametrize("corruption", ["tenant", "identity", "contract", "naive", "nonfinite"])
def test_corrupt_stored_rows_fail_closed(owners, corruption):
    documents, (_, reader) = owners
    document = _document(documents[0])
    if corruption == "tenant":
        corrupt = replace(document, tenant_id=str(uuid4()))
    elif corruption == "identity":
        corrupt = replace(document, document_id="different-synthetic-identity")
    elif corruption == "contract":
        corrupt = replace(document, contract_version="unrecognized-version")
    elif corruption == "naive":
        corrupt = replace(document, evaluated_at="2026-10-16T00:00:00")
    else:
        corrupt = replace(document, metadata={"invalid-number": float("nan")})
    documents[0].put(
        reader.COLLECTION, document.document_id, corrupt, group_key=document.site_id,
    )
    with pytest.raises(SiteEconomicsPersistenceError):
        reader.get_by_document_id(document.document_id)
    with pytest.raises(SiteEconomicsPersistenceError):
        reader.list_by_site_id(document.site_id)


def test_outer_transaction_rollback_removes_document_and_group_entry(owners):
    documents, (first, second) = owners
    document = _document(documents[0])
    with pytest.raises(RuntimeError, match="synthetic owner rollback"):
        with documents[0].engine.transaction():
            first.save(document)
            raise RuntimeError("synthetic owner rollback")
    assert second.get_by_document_id(document.document_id) is None
    assert second.list_by_site_id(document.site_id) == []


@pytest.mark.parametrize("corruption", [
    "horizon_string", "horizon_bool", "npv_string", "discount_bool",
    "month_string", "cashflow_string", "assumptions_string", "scenario_string",
    "payback_bool", "metadata_key", "schedule_list", "decision_string", "censoring_string",
])
def test_original_nested_types_are_required_on_save_and_after_storage(owners, corruption):
    documents, (writer, reader) = owners
    document = _document(documents[0])
    if corruption == "horizon_string":
        corrupt = replace(document, horizon_months="sixty")
    elif corruption == "horizon_bool":
        corrupt = replace(document, horizon_months=True)
    elif corruption == "npv_string":
        corrupt = replace(document, metrics=replace(document.metrics, levered_npv="not-a-number"))
    elif corruption == "discount_bool":
        corrupt = replace(document, annual_discount_rate=True)
    elif corruption in ("month_string", "cashflow_string"):
        first = document.monthly_schedule[0]
        changes = {"month": "one"} if corruption == "month_string" else {"net_levered_cash_flow": "zero"}
        corrupt = replace(document, monthly_schedule=(replace(first, **changes), *document.monthly_schedule[1:]))
    elif corruption == "assumptions_string":
        corrupt = replace(document, assumptions=replace(document.assumptions, area_ping="twenty-five"))
    elif corruption == "scenario_string":
        key = next(iter(document.scenarios))
        scenarios = dict(document.scenarios)
        scenarios[key] = replace(scenarios[key], levered_npv="invalid")
        corrupt = replace(document, scenarios=scenarios)
    elif corruption == "payback_bool":
        outcome = replace(document.metrics.simple_payback, is_censored=1)
        corrupt = replace(document, metrics=replace(document.metrics, simple_payback=outcome))
    elif corruption == "metadata_key":
        corrupt = replace(document, metadata={1: "would silently become a JSON string key"})
    elif corruption == "schedule_list":
        corrupt = replace(document, monthly_schedule=list(document.monthly_schedule))
    elif corruption == "decision_string":
        corrupt = replace(document, decision=replace(document.decision, recommendation="GO"))
    else:
        outcome = replace(document.metrics.simple_payback, censoring_type="NOT_CENSORED")
        corrupt = replace(document, metrics=replace(document.metrics, simple_payback=outcome))
    with pytest.raises(SiteEconomicsPersistenceError, match="DOCUMENT_INVALID"):
        writer.save(corrupt)
    assert reader.get_by_document_id(document.document_id) is None
    assert reader.list_by_site_id(document.site_id) == []
    documents[0].put(reader.COLLECTION, document.document_id, corrupt, group_key=document.site_id)
    with pytest.raises(SiteEconomicsPersistenceError, match="DOCUMENT_INVALID"):
        reader.get_by_document_id(document.document_id)
    with pytest.raises(SiteEconomicsPersistenceError, match="DOCUMENT_INVALID"):
        reader.list_by_site_id(document.site_id)


def test_legitimate_zero_boolean_metadata_and_optional_none_remain_unchanged(owners):
    documents, (writer, reader) = owners
    document = _document(documents[0])
    document = replace(
        document,
        metrics=replace(document.metrics, levered_npv=0, levered_irr=None),
        decision=replace(document.decision, confidence_score=0.0),
        metadata={"zero": 0, "enabled": False, "optional": None},
    )
    writer.save(document)
    loaded = reader.get_by_document_id(document.document_id)
    assert loaded == document
    assert loaded.metrics.levered_npv == 0 and type(loaded.metrics.levered_npv) is int
    assert loaded.metrics.levered_irr is None
    assert loaded.decision.confidence_score == 0.0
    assert loaded.metadata == {"zero": 0, "enabled": False, "optional": None}


def test_ancillary_costs_and_full_snapshot_survive_independent_postgresql_owner(owners):
    from modules.site_economics import CapitalAssetKind, CapitalAssetSpec, TargetFormatRegistry
    from modules.site_economics.application.context_inputs import content_sha256
    from tests.site_economics_fixtures import synthetic_g2_format

    documents, (writer, reader) = owners
    asset = CapitalAssetSpec(asset_id="SYNTHETIC-PG-PAYMENT", asset_kind=CapitalAssetKind.PAYMENT_SYSTEM,
        version="1.0.0-engineering", quantity=2, unit_capex=1234, useful_life_months=2,
        residual_value_ratio=0.25, monthly_fixed_operating_cost=37)
    spec = replace(synthetic_g2_format(), capital_assets=(asset,))
    registry = TargetFormatRegistry()
    registry.register(spec)
    document = SiteEconomicsService(registry=registry).evaluate_site(
        site_id="synthetic-ancillary-pg-site", area_ping=25, monthly_rent=0,
        tenant_id=documents[0].tenant_id)
    writer.save(document)
    loaded = reader.get_by_document_id(document.document_id)
    assert loaded == document
    assert loaded.assumptions.total_equipment_capex == spec.machine_mix.total_equipment_capex + 2468
    assert loaded.metadata["target_format_snapshot"]["capital_assets"] == [asset.to_dict()]
    assert loaded.metadata["target_format_sha256"] == content_sha256(spec.to_dict())
    assert loaded.metadata["production_admission"] == "not_established_by_simulation"
    loaded.metadata["target_format_snapshot"]["capital_assets"][0]["unit_capex"] = 999
    assert reader.get_by_document_id(document.document_id) == document
    assert writer.get_by_document_id(document.document_id) == document


def test_camera_furniture_snapshot_hash_and_boundaries_survive_independent_postgresql_owner(owners):
    from modules.site_economics import (
        CapitalAssetKind,
        CapitalAssetSpec,
        SiteEconomicsDocument,
        TargetFormatRegistry,
        TargetFormatSpec,
    )
    from modules.site_economics.application.context_inputs import content_sha256
    from tests.site_economics_fixtures import synthetic_g2_format

    # Arbitrary engineering inputs, never a quote, approved lifetime/residual,
    # insurance-bundle allocation or registration of an actual production G2.
    assets = (
        CapitalAssetSpec(asset_id="SYNTHETIC-PG-CAMERA", asset_kind=CapitalAssetKind.CAMERA_SYSTEM,
            version="1.0.0-engineering", quantity=2, unit_capex=1200, useful_life_months=2,
            residual_value_ratio=0.25, monthly_fixed_operating_cost=7),
        CapitalAssetSpec(asset_id="SYNTHETIC-PG-FURNITURE", asset_kind=CapitalAssetKind.FURNITURE,
            version="1.0.0-engineering", quantity=3, unit_capex=800, useful_life_months=3,
            residual_value_ratio=0.5, monthly_fixed_operating_cost=11),
    )
    documents, (writer, reader) = owners
    spec = replace(synthetic_g2_format(), format_version="2.3.0-camera-furniture-engineering",
        capital_assets=assets)
    registry = TargetFormatRegistry()
    registry.register(spec)
    document = SiteEconomicsService(registry=registry).evaluate_site(
        site_id="synthetic-camera-furniture-pg", area_ping=25, monthly_rent=0,
        tenant_id=documents[0].tenant_id, horizon_months=4)
    expected_hash = content_sha256(spec.to_dict())
    expected_digest = document.digest
    writer.save(document)
    loaded = reader.get_by_document_id(document.document_id)
    assert loaded == document and loaded.digest == expected_digest
    assert loaded.assumptions.total_equipment_capex == spec.machine_mix.total_equipment_capex + 4800
    assert loaded.metadata["target_format_snapshot"]["capital_assets"] == [asset.to_dict() for asset in assets]
    assert loaded.metadata["target_format_sha256"] == expected_hash
    assert TargetFormatSpec.from_dict(loaded.metadata["target_format_snapshot"]) == spec
    assert loaded.metadata["production_admission"] == "not_established_by_simulation"
    assert SiteEconomicsDocument.from_json(loaded.to_json()).digest == expected_digest
    reader.save(loaded)  # Same payload remains an idempotent immutable replay.
    assert reader.list_by_site_id(document.site_id) == [document]
    loaded.metadata["target_format_snapshot"]["capital_assets"][1]["unit_capex"] = 999
    with pytest.raises(SiteEconomicsPersistenceError, match="IMMUTABLE_ID_CONFLICT"):
        reader.save(loaded)
    assert writer.get_by_document_id(document.document_id).digest == expected_digest
    assert reader.get_by_document_id(document.document_id) == document
    foreign = DurableSiteEconomicsRepository(TenantScopedDocumentStore(
        PostgresDocumentStore(documents[1].engine), str(uuid4())))
    assert foreign.get_by_document_id(document.document_id) is None
    assert foreign.list_by_site_id(document.site_id) == []
    with pytest.raises(SiteEconomicsPersistenceError, match="DOCUMENT_INVALID"):
        foreign.save(document)
    assert reader.get_latest_by_site_id(document.site_id).digest == expected_digest
