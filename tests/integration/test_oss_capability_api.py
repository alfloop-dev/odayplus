from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.auth import Role
from tests.integration._authz import auth_headers


def test_learninghub_exposes_installed_oss_engine_versions() -> None:
    client = TestClient(create_app())

    response = client.get(
        "/api/v1/learninghub/oss-capabilities",
        headers=auth_headers(Role.MODEL_OWNER),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["unavailable_count"] == 0
    assert payload["count"] >= 11
    assert all(item["packages"] for item in payload["items"])


def test_learninghub_oss_capabilities_are_not_public() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/learninghub/oss-capabilities")

    assert response.status_code == 403
