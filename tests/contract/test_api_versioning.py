"""Every product operation is versioned, with a working alias (ODP-PGAP-API-001)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.api.versioning import API_V1_PREFIX, alias_paths, versioned_paths

# Probes are infrastructure, not product contract: they are wired into deploy
# manifests and load balancers, so they stay unversioned by design.
UNVERSIONED_BY_DESIGN = {"/healthz", "/health", "/platform/health", "/platform/version", "/readiness"}

DOC_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def test_every_product_operation_is_served_under_api_v1() -> None:
    """No product route may exist only on an unversioned path.

    This is the regression guard for criterion 1: a router added later without
    ``mount_versioned`` fails here rather than shipping unversioned.
    """
    app = create_app()
    schema_paths = set(app.openapi()["paths"])
    unversioned = {
        path
        for path in schema_paths
        if not path.startswith(API_V1_PREFIX)
        and path not in UNVERSIONED_BY_DESIGN
        and path not in DOC_PATHS
    }
    assert unversioned == set(), (
        f"these product paths are not versioned: {sorted(unversioned)}. "
        "Mount the router with shared.api.versioning.mount_versioned."
    )


def test_alias_and_versioned_surfaces_are_exactly_paired() -> None:
    """Every versioned path has an alias and vice versa -- no half-mounted router."""
    app = create_app()
    assert alias_paths(app) == [p[len(API_V1_PREFIX) :] for p in versioned_paths(app)]
    assert len(versioned_paths(app)) > 100, "sanity: the whole surface should be versioned"


def test_versioned_and_alias_paths_return_identical_bodies() -> None:
    """The alias must serve the request, not redirect or diverge.

    A 307 would drop the body on mutations, so the alias is a real mount; this
    asserts the two mounts stay the same handler.
    """
    client = TestClient(create_app())
    versioned = client.get("/api/v1/audit/events", headers={"x-correlation-id": "corr-ver-1"})
    alias = client.get("/audit/events", headers={"x-correlation-id": "corr-ver-1"})

    assert versioned.status_code == alias.status_code == 200
    assert versioned.json() == alias.json()


def test_alias_responses_advertise_deprecation_and_successor() -> None:
    client = TestClient(create_app())
    response = client.get("/audit/events")

    assert response.headers["Deprecation"] == "true"
    assert response.headers["Link"] == '</api/v1/audit/events>; rel="successor-version"'


def test_versioned_responses_are_not_marked_deprecated() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/audit/events")

    assert "Deprecation" not in response.headers


def test_health_probes_are_not_marked_deprecated() -> None:
    """Probes have no versioned successor, so marking them would be a lie."""
    client = TestClient(create_app())
    for path in sorted(UNVERSIONED_BY_DESIGN):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "Deprecation" not in response.headers, path


def test_aliases_are_absent_from_the_openapi_artifact() -> None:
    """Aliases must not leak into the schema, or the generated client would
    target a deprecated path."""
    app = create_app()
    schema_paths = set(app.openapi()["paths"])
    for alias in alias_paths(app):
        assert alias not in schema_paths, f"deprecated alias {alias} leaked into the schema"
