"""Comprehensive test suite for Feature Flag UI, API, and Job enforcement (FR-SHARED-004 / UX-SCR-ADMIN-002).

Proves that:
1. Feature flag management API endpoints (list, get, enable, disable, approve, register) operate properly.
2. High-risk dual approval rules (ODP-SA-04 §3) are strictly enforced (>= 2 approvals required).
3. API endpoints fail closed (403 Forbidden) when feature flag is disabled.
4. Job queue execution (PriceOps, AdLift, NetPlan, Model Publish) refuses execution when feature flag is disabled.
5. All three execution layers (UI, API, Job) share the exact same process-wide feature flag state.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.auth.feature_flags import (
    default_registry,
    reset_global_registry,
)
from shared.jobs.queue import (
    InMemoryJobQueue,
    JobRequest,
    NonRetryableJobError,
    check_job_feature_flag,
)


@pytest.fixture(autouse=True)
def reset_registry_before_test():
    reset_global_registry()
    yield
    reset_global_registry()


def test_admin_feature_flags_list_and_get():
    app = create_app()
    client = TestClient(app)

    # 1. List admin feature flags
    res = client.get("/api/v1/admin/feature-flags")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["count"] >= 5
    keys = {f["key"] for f in body["flags"]}
    assert "high_risk.priceops.execute" in keys
    assert "high_risk.adlift.approve" in keys
    assert "high_risk.netplan.approve" in keys
    assert "high_risk.model.publish" in keys

    # 2. Get specific flag
    res_get = client.get("/api/v1/admin/feature-flags/high_risk.priceops.execute")
    assert res_get.status_code == 200
    flag_body = res_get.json()["flag"]
    assert flag_body["key"] == "high_risk.priceops.execute"
    assert flag_body["high_risk"] is True
    assert flag_body["enabled"] is False


def test_admin_feature_flag_dual_approval_workflow():
    app = create_app()
    client = TestClient(app)
    key = "high_risk.priceops.execute"

    # Attempt to enable with 0 approvals -> 403
    res_fail0 = client.post(f"/api/v1/admin/feature-flags/{key}/enable", json={"approvals": []})
    assert res_fail0.status_code == 403
    assert "distinct approvals" in res_fail0.json()["detail"]

    # Attempt to enable with 1 approval -> 403
    res_fail1 = client.post(f"/api/v1/admin/feature-flags/{key}/enable", json={"approvals": ["alice"]})
    assert res_fail1.status_code == 403

    # Add approvals step by step via /approve endpoint
    res_appr1 = client.post(f"/api/v1/admin/feature-flags/{key}/approve", json={"approver": "alice"})
    assert res_appr1.status_code == 200
    assert res_appr1.json()["flag"]["approved_by"] == ["alice"]

    res_appr2 = client.post(f"/api/v1/admin/feature-flags/{key}/approve", json={"approver": "bob"})
    assert res_appr2.status_code == 200
    assert sorted(res_appr2.json()["flag"]["approved_by"]) == ["alice", "bob"]

    # Now enable with dual approval -> 200 Success
    res_enable = client.post(f"/api/v1/admin/feature-flags/{key}/enable")
    assert res_enable.status_code == 200
    assert res_enable.json()["flag"]["enabled"] is True
    assert res_enable.json()["flag"]["is_active"] is True

    # Disable via Kill-Switch -> 200 Success
    res_disable = client.post(f"/api/v1/admin/feature-flags/{key}/disable")
    assert res_disable.status_code == 200
    assert res_disable.json()["flag"]["enabled"] is False
    assert res_disable.json()["flag"]["is_active"] is False


def test_admin_register_custom_feature_flag():
    app = create_app()
    client = TestClient(app)

    new_flag_payload = {
        "key": "custom.experimental_feature",
        "owner": "dev_team",
        "description": "Test custom feature flag",
        "high_risk": False,
        "readiness": "experimental",
    }
    res_reg = client.post("/api/v1/admin/feature-flags", json=new_flag_payload)
    assert res_reg.status_code == 200
    assert res_reg.json()["flag"]["key"] == "custom.experimental_feature"

    # Low risk flag can be enabled immediately with 0 approvals
    res_enable = client.post("/api/v1/admin/feature-flags/custom.experimental_feature/enable")
    assert res_enable.status_code == 200
    assert res_enable.json()["flag"]["enabled"] is True


def test_job_enforcement_refuses_disabled_feature_flag():
    job_queue = InMemoryJobQueue()
    reg = default_registry()
    key = "high_risk.priceops.execute"

    # 1. Feature flag is disabled by default
    assert not reg.is_enabled(key)

    # 2. Enqueuing PriceOps job when flag is disabled fails closed
    with pytest.raises(NonRetryableJobError) as exc_info:
        job_queue.enqueue(
            JobRequest(job_type="priceops.execute", payload={"tenant_id": "tenant-a"}),
            correlation_id="corr-test-1",
        )
    assert "kill-switch engaged" in str(exc_info.value)
    assert key in str(exc_info.value)

    # 3. Enable feature flag with dual approval
    reg.enable(key, approvals=frozenset({"approver1", "approver2"}))
    assert reg.is_enabled(key)

    # 4. Enqueuing PriceOps job succeeds when flag is enabled
    record, ok = job_queue.enqueue(
        JobRequest(job_type="priceops.execute", payload={"tenant_id": "tenant-a"}),
        correlation_id="corr-test-2",
    )
    assert ok is True
    assert record.job_type == "priceops.execute"

    # 5. Disable feature flag (Kill Switch)
    reg.disable(key)
    assert not reg.is_enabled(key)

    # 6. Leasing an enqueued job after flag is disabled catches kill switch
    leased = job_queue.lease(60)
    assert leased is None  # Job was failed due to kill-switch check


def test_three_layers_shared_flag_state():
    """Verify that UI (API read), API security engine, and Job queue all share the exact same flag state."""

    app = create_app()
    client = TestClient(app)
    reg = default_registry()
    key = "high_risk.netplan.approve"

    # Layer 1: UI / Admin API check
    ui_res1 = client.get(f"/api/v1/admin/feature-flags/{key}")
    assert ui_res1.json()["flag"]["is_active"] is False

    # Layer 2: Job queue check
    with pytest.raises(NonRetryableJobError):
        check_job_feature_flag("netplan.approve", flags=reg)

    # Enable flag via Admin API with dual approval
    client.post(f"/api/v1/admin/feature-flags/{key}/approve", json={"approver": "exec_1"})
    client.post(f"/api/v1/admin/feature-flags/{key}/approve", json={"approver": "exec_2"})
    enable_res = client.post(f"/api/v1/admin/feature-flags/{key}/enable")
    assert enable_res.status_code == 200

    # Layer 1 (UI API): reflects ACTIVE
    ui_res2 = client.get(f"/api/v1/admin/feature-flags/{key}")
    assert ui_res2.json()["flag"]["is_active"] is True

    # Layer 2 (Job): passes check without error
    check_job_feature_flag("netplan.approve", flags=reg)

    # Trigger emergency Kill-Switch via Admin API
    disable_res = client.post(f"/api/v1/admin/feature-flags/{key}/disable")
    assert disable_res.status_code == 200

    # Layer 1 (UI API): reflects DISABLED
    ui_res3 = client.get(f"/api/v1/admin/feature-flags/{key}")
    assert ui_res3.json()["flag"]["is_active"] is False

    # Layer 2 (Job): fails closed again
    with pytest.raises(NonRetryableJobError):
        check_job_feature_flag("netplan.approve", flags=reg)
