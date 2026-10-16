"""Normal app + actual fresh aggregate/native tenant HeatZone PostgreSQL source."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.heatzone.workers import HeatZoneBatchScoreResult
from shared.auth import Scope
from shared.infrastructure.persistence.operator_domains import DurableOperatorDomainStateRepository
from tests.intake_registry_fixture import TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_operator_intake_workflow_postgresql import _headers, bridge_database
from tests.unit.test_operator_canonical_heat_zones import zone_result

pytestmark = pytest.mark.requires_live_env
assert bridge_database
BASE = "/api/v1/operator/network-listings"


def test_fresh_normal_app_reads_only_canonical_tenant_zones_without_aggregate_backfill(
    bridge_database, monkeypatch
):
    from apps.api.oday_api.security import dependencies

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        first, second, foreign = zone_result(), zone_result(rank=2), zone_result()
        other = str(uuid4())
        for tenant, scores in [(TENANT, (second, first)), (other, (foreign,))]:
            bundle.heatzone_store_for_tenant(tenant).put(
                HeatZoneBatchScoreResult(
                    job_id=str(uuid4()),
                    status="succeeded",
                    scores=scores,
                    completed_at=datetime.now(UTC),
                    tenant_id=tenant,
                )
            )
        store = bundle.assisted_intake_store._store
        state_owner = DurableOperatorDomainStateRepository(store, "network-listings")
        before = state_owner.load(TENANT)
        assert before is None or not before.get("heatZones")
        client = TestClient(create_app(persistence=bundle))
        response = client.get(BASE, headers=_headers())
        assert response.status_code == 200, response.text
        actual = response.json()
        assert [row["id"] for row in actual["heatZones"]] == [
            first.heat_zone_id,
            second.heat_zone_id,
        ]
        assert actual["selectedHeatZoneId"] == first.heat_zone_id
        assert actual["counts"]["heatZones"] == 2 and foreign.heat_zone_id not in response.text
        selected = client.get(
            BASE, params={"selectedHeatZoneId": second.heat_zone_id}, headers=_headers()
        )
        assert (
            selected.status_code == 200
            and selected.json()["selectedHeatZoneId"] == second.heat_zone_id
        )
        assert state_owner.load(TENANT) == before
        original = dependencies.principal_from_headers

        def scoped(headers, **kwargs):
            principal = original(headers, **kwargs)
            return replace(
                principal,
                scope=Scope(
                    tenant_id=principal.tenant_id, heat_zone_ids=frozenset({second.heat_zone_id})
                ),
            )

        monkeypatch.setattr(dependencies, "principal_from_headers", scoped)
        bounded = client.get(BASE, headers=_headers())
        assert bounded.status_code == 200, bounded.text
        assert [row["id"] for row in bounded.json()["heatZones"]] == [second.heat_zone_id]
        assert bounded.json()["counts"]["heatZones"] == 1
        assert bounded.json()["selectedHeatZoneId"] == second.heat_zone_id

        def missing_axis(headers, **kwargs):
            principal = original(headers, **kwargs)
            return replace(
                principal,
                scope=Scope(tenant_id=principal.tenant_id, brand_ids=frozenset({str(uuid4())})),
            )

        monkeypatch.setattr(dependencies, "principal_from_headers", missing_axis)
        withheld = client.get(BASE, headers=_headers())
        assert withheld.status_code == 200, withheld.text
        assert withheld.json()["heatZones"] == [] and withheld.json()["counts"]["heatZones"] == 0
    finally:
        bundle.engine.close()
