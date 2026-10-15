"""Real HTTP/router resolution must not observe the full snapshot twice."""
from fastapi.testclient import TestClient

from modules.opsboard.application.network_scoring import NetworkScoringService
from tests.integration.test_operator_canonical_wiring import (
    BASE,
    CanonicalHarness,
    _headers,
    _seed_candidate,
)
from tests.unit.test_network_candidate_report_projection import _report


def test_real_router_resolves_fresh_snapshot_once_per_request_and_keeps_tenant_scope(tmp_path, monkeypatch):
    harness = CanonicalHarness(tmp_path / "owned-http-single-refresh.sqlite3")
    candidate_id = _seed_candidate(harness, "tenant-a")
    first = harness.sitescore("tenant-a").save_report(_report(candidate_id, "recorded-v1"))
    calls = []
    original = NetworkScoringService._refresh_canonical
    def observed_refresh(service):
        calls.append(service._tenant_id)
        return original(service)
    monkeypatch.setattr(NetworkScoringService, "_refresh_canonical", observed_refresh)
    try:
        with TestClient(harness.app()) as client:
            response = client.get(f"{BASE}/network-scoring", headers=_headers("tenant-a"))
            assert response.status_code == 200
            assert calls == ["tenant-a"]
            assert response.json()["scorecards"][0]["reportId"] == first.report_id
            newer = harness.sitescore("tenant-a").save_report(_report(candidate_id, "recorded-v2", run="2"))
            second = client.get(f"{BASE}/network-scoring", headers=_headers("tenant-a"))
            assert second.status_code == 200
            assert calls == ["tenant-a", "tenant-a"]
            assert second.json()["scorecards"][0]["reportId"] == newer.report_id
            foreign = client.get(f"{BASE}/network-scoring", headers=_headers("tenant-b"))
            assert foreign.status_code == 200
            assert foreign.json()["candidates"] == foreign.json()["scorecards"] == []
            assert calls == ["tenant-a", "tenant-a", "tenant-b"]
        assert harness.sitescore("tenant-a").get_report(first.report_id) == first
        assert harness.runtime.calls == []
    finally:
        harness.close()
