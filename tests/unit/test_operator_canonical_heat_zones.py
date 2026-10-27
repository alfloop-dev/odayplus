"""Canonical source, selection and full-axis boundaries; explicit test facts."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from modules.heatzone.domain.scoring import HeatZoneScoreResult, HeatZoneState
from modules.opsboard.application.network_heat_zones import project_canonical_heat_zones
from shared.auth import Principal, Role, Scope

TENANT = "00000000-0000-0000-0000-000000000001"


def zone_result(identity=None, *, rank=1):
    now = datetime(2026, 10, 16, 9, tzinfo=UTC)
    return HeatZoneScoreResult(
        heat_zone_id=identity or str(uuid4()),
        h3_index="8929a1d4d67ffff",
        h3_resolution=9,
        score=60,
        priority_rank=rank,
        unmet_demand_score=0.6,
        format_fit_score=0.6,
        cannibalization_risk_score=0.2,
        rent_feasibility_score=0.6,
        listing_availability_score=0.6,
        confidence=0.8,
        state=HeatZoneState.STILL_EXPANDABLE,
        feature_snapshot_time=now,
        prediction_origin_time=now,
        last_scored_at=now,
        model_version="synthetic-recorded-test-v1",
        feature_version="synthetic-recorded-test-v1",
        source_snapshot_ids=(str(uuid4()),),
        reasons=("Explicit synthetic measured test input",),
        admin_city="Synthetic",
        admin_district="Native zone",
    )


def source(rows, **updates):
    return {
        "tenantId": TENANT,
        "state": "available",
        "source": "heatzone_store.list_scores",
        "persistenceMode": "postgresql",
        "rows": rows,
        **updates,
    }


def principal(role=Role.SITE_REVIEWER, **scope):
    return Principal(str(uuid4()), frozenset({role}), Scope(tenant_id=TENANT, **scope))


def snapshot():
    return {
        "source": "api",
        "heatZones": [{"id": "legacy-zone-must-not-return"}],
        "listings": [{"id": "visible-listing", "tenantId": TENANT}],
        "candidates": [],
        "assistedIntakes": [],
        "listingSources": [],
        "siteReviews": [],
        "counts": {"listings": 1, "heatZones": 1},
        "expansionSteps": [],
    }


def test_native_projection_selects_recorded_first_zone_and_preserves_other_rows_without_write():
    first, second = zone_result(), zone_result(rank=2)
    original = snapshot()
    before = deepcopy(original)
    actual = project_canonical_heat_zones(
        original,
        source([second.to_dict(), first.to_dict()]),
        principal=principal(),
        selected_id=None,
    )
    assert original == before
    assert actual["selectedHeatZoneId"] == first.heat_zone_id
    assert [row["id"] for row in actual["heatZones"]] == [first.heat_zone_id, second.heat_zone_id]
    row = actual["heatZones"][0]
    assert row["demandGap"] is row["competitionIndex"] is row["cannibalizationRisk"] is None
    assert row["recordedMap"]["geometry"] is None
    assert row["recordedMap"]["sourceSnapshotIds"] == list(first.source_snapshot_ids)
    assert row["recordedMap"]["score"] == first.score
    assert "mapEvidence" not in row
    assert row["recordedMap"]["id"] == row["id"]
    assert row["recordedMap"]["h3"] == first.h3_index
    assert row["recordedMap"]["rank"] == first.priority_rank
    assert row["recordedMap"]["poiCount"] is None
    assert actual["listings"] == original["listings"] and actual["counts"]["listings"] == 1
    selected = project_canonical_heat_zones(
        original,
        source([first.to_dict(), second.to_dict()]),
        principal=principal(),
        selected_id=second.heat_zone_id,
    )
    assert selected["selectedHeatZoneId"] == second.heat_zone_id


@pytest.mark.parametrize("role", [Role.SITE_REVIEWER, Role.EXECUTIVE, Role.OPERATOR_VIEWER])
def test_zone_scope_filters_before_counts_and_selection_for_every_reader_role(role):
    allowed, denied = zone_result(rank=2), zone_result()
    actual = project_canonical_heat_zones(
        snapshot(),
        source([denied.to_dict(), allowed.to_dict()]),
        principal=principal(role, heat_zone_ids=frozenset({allowed.heat_zone_id})),
        selected_id=None,
    )
    assert [row["id"] for row in actual["heatZones"]] == [allowed.heat_zone_id]
    assert (
        actual["selectedHeatZoneId"] == allowed.heat_zone_id and actual["counts"]["heatZones"] == 1
    )
    assert denied.heat_zone_id not in str(actual)


@pytest.mark.parametrize("role", [Role.SITE_REVIEWER, Role.OPERATOR_VIEWER])
@pytest.mark.parametrize("axis", ["brand_ids", "region_ids", "store_ids", "assigned_area_ids"])
def test_missing_aggregate_axis_is_not_inferred_from_principal_or_visible_listing(role, axis):
    actual = project_canonical_heat_zones(
        snapshot(),
        source([zone_result().to_dict()]),
        principal=principal(role, **{axis: frozenset({str(uuid4())})}),
        selected_id=None,
    )
    assert actual["heatZones"] == [] and actual["selectedHeatZoneId"] is None
    assert actual["counts"]["heatZones"] == 0
    assert actual["listings"] == snapshot()["listings"]


@pytest.mark.parametrize(
    "bad",
    [
        "tenant",
        "source",
        "mode",
        "unavailable",
        "duplicate",
        "score-nan",
        "no-model",
        "no-lineage",
        "bad-h3",
        "bad-time",
        "abstained-numeric",
        "abstained-null",
        "row-tenant",
        "seed",
        "nested-fixture",
    ],
)
def test_unavailable_invalid_or_nonproduction_source_never_restores_legacy_zone(bad):
    row = zone_result().to_dict()
    envelope = source([row])
    if bad == "tenant":
        envelope["tenantId"] = str(uuid4())
    elif bad == "source":
        envelope["source"] = "other-owner"
    elif bad == "mode":
        envelope["persistenceMode"] = "memory"
    elif bad == "unavailable":
        envelope["state"] = "unavailable"
    elif bad == "duplicate":
        envelope["rows"].append(deepcopy(row))
    elif bad == "score-nan":
        row["score"] = float("nan")
    elif bad == "no-model":
        row["model_version"] = ""
    elif bad == "no-lineage":
        row["source_snapshot_ids"] = []
    elif bad == "bad-h3":
        row["h3_index"] = "h3-demo"
    elif bad == "bad-time":
        row["last_scored_at"] = "2026-10-16"
    elif bad in {"abstained-numeric", "abstained-null"}:
        # This existing source is V2 HeatZoneScoreResult, whose enum has no
        # ABSTAINED. A V3 result cannot silently become an operational V2 row.
        row["state"] = "ABSTAINED"
        row["score"] = 55 if bad == "abstained-numeric" else None
    elif bad == "row-tenant":
        row["scope"] = {"tenant_id": str(uuid4())}
    elif bad == "seed":
        row["source"] = "seed-r4"
    elif bad == "nested-fixture":
        row["metadata"] = {"is_fixture": True}
    actual = project_canonical_heat_zones(
        snapshot(), envelope, principal=principal(), selected_id=None
    )
    assert actual["heatZones"] == [] and actual["heatZoneAvailability"]["state"] == "unavailable"
    assert actual["listings"] == snapshot()["listings"]


def test_business_copy_does_not_become_a_metadata_marker():
    row = zone_result().to_dict()
    row["reasons"] = ["Demographic study and seed funding are business copy"]
    actual = project_canonical_heat_zones(
        snapshot(), source([row]), principal=principal(), selected_id=None
    )
    assert len(actual["heatZones"]) == 1
