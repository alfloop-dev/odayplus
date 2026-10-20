"""Actual native selection/pagination under explicitly synthetic grants."""

import base64
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from shared.auth import Role, Scope
from tests.integration.test_canonical_intakes_postgresql import canonical_graph as canonical_graph
from tests.integration.test_canonical_intakes_postgresql import native_api as native_api

pytestmark = pytest.mark.requires_live_env


def seed(case, **values):
    graph, ids, _, _ = case
    row = {"id": str(uuid4()), "tenant": ids["tenant"], "actor": ids["actor"],
           "brand": None, "region": None, "area": None, "zone": None, "state": "SUBMITTED", **values}
    with graph.transaction(row["tenant"]):
        graph.engine.execute(
            "INSERT INTO intake.intakes (intake_id,tenant_id,submitter_subject_id,intake_method,original_url,"
            "brand_id,region_id,assigned_area_id,heat_zone_id,processing_state,correlation_id,submitted_at) "
            "VALUES (?,?,?,'URL','https://synthetic.example/private-value',?,?,?,?,?,?,'2026-10-01T00:00:00Z')",
            (row["id"], row["tenant"], row["actor"], row["brand"], row["region"], row["area"], row["zone"],
             row["state"], str(uuid4())),
        )
    return row["id"]


def get(client, **params):
    return client.get("/api/v1/canonical-intakes", params={"processing_state": "SUBMITTED", **params})


def test_selection_audit_stale_serializable_chain_rolls_back_and_allows_explicit_refresh(
        canonical_graph, native_api, monkeypatch):
    from shared.audit.events import AuditEvent
    from shared.infrastructure.persistence.audit_log import DurableAuditLog
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    graph, ids, _, database = canonical_graph
    intake_id = seed(canonical_graph)
    client, _, _ = native_api
    audit = DurableAuditLog(graph.engine)
    other_engine = PostgresEngine(database.url(), bootstrap=False, validate_schema=False)
    other_audit = DurableAuditLog(other_engine)
    original_record, conflicted = audit.record, []
    concurrent = AuditEvent(event_type="security.access", actor=ids["reviewer"], action="view",
        resource=f"intake/{intake_id}", outcome="allowed", correlation_id=str(uuid4()))

    def commit_other_writer_after_native_snapshot(event):
        if not conflicted and getattr(graph.engine._local, "connection", None) is not None:
            assert graph.engine.query_one("SHOW transaction_isolation")["transaction_isolation"] == "serializable"
            other_audit.record(concurrent)
            conflicted.append(event.event_id)
        return original_record(event)

    monkeypatch.setattr(audit, "record", commit_other_writer_after_native_snapshot)
    monkeypatch.setattr(client.app.state, "audit_log", audit)
    try:
        response = get(client)
        assert response.status_code == 409, response.text
        events = audit.list_events()
        assert conflicted and conflicted[0] not in {event.event_id for event in events}
        assert concurrent.event_id in {event.event_id for event in events}
        assert audit.verify_chain().ok
        refreshed = get(client)
        assert refreshed.status_code == 200, refreshed.text
        assert intake_id in {row["intake_id"] for row in refreshed.json()["items"]}
        assert audit.verify_chain().ok
        with database.connect() as connection:
            assert connection.execute("SELECT processing_state,version FROM intake.intakes WHERE intake_id=%s",
                (intake_id,)).fetchone() == ("SUBMITTED", 1)
    finally:
        other_engine.close()


@pytest.mark.parametrize("merged", [False, True])
def test_native_detail_reads_actual_listing_resolution_without_private_fields(canonical_graph, native_api, merged):
    from tests.integration.test_canonical_identity_execution_postgresql import _queued

    graph, ids, _, database = canonical_graph
    if merged:
        _queued(canonical_graph).run(canonical_graph[2])
    intake_id = seed(canonical_graph, state="READY")
    with database.connect() as connection:
        connection.execute("UPDATE intake.intakes SET resolved_listing_id=%s WHERE intake_id=%s", (ids["listing"], intake_id))
    client, caller, _ = native_api
    caller["principal"] = replace(caller["principal"], roles=frozenset({Role.SITE_REVIEWER}))
    response = client.get(f"/api/v1/canonical-intakes/{intake_id}")
    assert response.status_code == 200, response.text
    assert response.json()["resolution"] == {"listing_id": ids["listing"], "listing_version": 13,
        "lifecycle_state": "ACTIVE", "current_revision_id": None, "property_id_at_creation": ids["source"],
        "effective_property_id": ids["target"] if merged else ids["source"], "property_version": 12 if merged else 7, "fields": []}
    assert "original_url" not in response.text and "private-value" not in response.text
    assert "effective_values" not in response.text and "raw_object_uri" not in response.text
    assert response.headers["etag"] == 'W/"1"'
    resources = {event.resource for event in client.app.state.audit_log.list_events()}
    assert "listing/" + ids["listing"] in resources
    assert "listing/" + (ids["target"] if merged else ids["source"]) in resources


@pytest.mark.parametrize("clearance", ["PUBLIC", "CONFIDENTIAL", "HIGHLY_RESTRICTED"])
def test_saved_revision_without_field_classification_stays_masked(canonical_graph, native_api, clearance):
    from shared.auth import DataClassification

    _, ids, _, database = canonical_graph
    revision_id = str(uuid4())
    intake_id = seed(canonical_graph, state="READY")
    with database.connect() as connection:
        connection.execute("INSERT INTO expansion.listing_revisions "
            "(listing_revision_id,tenant_id,listing_id,revision_no,revision_kind,normalized_values,effective_values,material_fingerprint) "
            "VALUES(%s,%s,%s,1,'CREATED',%s::jsonb,%s::jsonb,%s)",
            (revision_id, ids["tenant"], ids["listing"], json.dumps({"address": "parser-only-private-marker"}),
             json.dumps({"address": "actual-saved-private-marker", "rent": 54321}), "f" * 64))
        connection.execute("UPDATE expansion.listings SET current_revision_id=%s WHERE listing_id=%s", (revision_id, ids["listing"]))
        connection.execute("UPDATE intake.intakes SET resolved_listing_id=%s WHERE intake_id=%s", (ids["listing"], intake_id))
    client, caller, _ = native_api
    caller["principal"] = replace(caller["principal"], roles=frozenset({Role.SITE_REVIEWER}),
        scope=replace(caller["principal"].scope, clearance=DataClassification[clearance]))
    response = client.get(f"/api/v1/canonical-intakes/{intake_id}")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    fields = response.json()["resolution"]["fields"]
    assert {field["field_path"] for field in fields} == {"address", "rent"}
    assert all(field["effective"] is None and field["classification"] is None
        and field["masked"] and field["mask_reason_code"] == "FIELD_MASKED" for field in fields)
    assert "actual-saved-private-marker" not in response.text and "parser-only-private-marker" not in response.text


@pytest.mark.parametrize("merged", [False, True])
def test_native_detail_uses_current_property_region_for_listing_scope(canonical_graph, native_api, merged):
    from tests.integration.test_canonical_identity_execution_postgresql import _queued

    _, ids, _, database = canonical_graph
    if merged:
        _queued(canonical_graph).run(canonical_graph[2])
    allowed, outside = str(uuid4()), str(uuid4())
    current_property = ids["target"] if merged else ids["source"]
    intake_id = seed(canonical_graph, state="READY", region=allowed)
    with database.connect() as connection:
        connection.execute("UPDATE intake.intakes SET resolved_listing_id=%s WHERE intake_id=%s", (ids["listing"], intake_id))
        connection.execute("UPDATE identity.properties SET region_id=%s WHERE property_id=%s", (outside, ids["source"]))
        connection.execute("UPDATE identity.properties SET region_id=%s WHERE property_id=%s", (allowed, current_property))
    client, caller, _ = native_api
    caller["principal"] = replace(caller["principal"], roles=frozenset({Role.SITE_REVIEWER}),
        scope=replace(caller["principal"].scope, region_ids=frozenset({allowed})))
    path = f"/api/v1/canonical-intakes/{intake_id}"
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["resolution"]["effective_property_id"] == current_property
    with database.connect() as connection:
        connection.execute("UPDATE identity.properties SET region_id=%s WHERE property_id=%s", (outside, current_property))
    response = client.get(path)
    assert response.status_code == 403
    assert ids["listing"] not in response.text and current_property not in response.text


@pytest.mark.parametrize("axis,column", [("brand_ids", "brand_id"),
    ("assigned_area_ids", "assigned_area_id"), ("heat_zone_ids", "heat_zone_id")])
def test_native_resolution_never_borrows_other_axes_from_intake(canonical_graph, native_api, axis, column):
    _, ids, _, database = canonical_graph
    allowed, region = str(uuid4()), str(uuid4())
    intake_id = seed(canonical_graph, state="READY", region=region)
    with database.connect() as connection:
        # column is one of this test's fixed schema names, never request data.
        connection.execute(f"UPDATE intake.intakes SET resolved_listing_id=%s,{column}=%s WHERE intake_id=%s",
            (ids["listing"], allowed, intake_id))
        connection.execute("UPDATE identity.properties SET region_id=%s WHERE property_id=%s", (region, ids["source"]))
    client, caller, _ = native_api
    caller["principal"] = replace(caller["principal"], roles=frozenset({Role.SITE_REVIEWER}),
        scope=Scope(tenant_id=ids["tenant"], region_ids=frozenset({region}), **{axis: frozenset({allowed})}))
    response = client.get(f"/api/v1/canonical-intakes/{intake_id}")
    assert response.status_code == 403, response.text
    assert ids["listing"] not in response.text and ids["source"] not in response.text


def test_native_detail_reauthorizes_resolution_beyond_owned_intake(canonical_graph, native_api):
    graph, ids, _, database = canonical_graph
    allowed = str(uuid4())
    intake_id = seed(canonical_graph, state="READY", region=allowed)
    with database.connect() as connection:
        connection.execute("UPDATE intake.intakes SET resolved_listing_id=%s WHERE intake_id=%s", (ids["listing"], intake_id))
        connection.execute("UPDATE identity.properties SET region_id=%s WHERE property_id=%s",
            (str(uuid4()), ids["source"]))
    client, caller, _ = native_api
    caller["principal"] = replace(caller["principal"], roles=frozenset({Role.SITE_REVIEWER}),
        scope=replace(caller["principal"].scope, region_ids=frozenset({allowed})))
    # The parent is readable; the target requires its own authorization.
    assert any(row["intake_id"] == intake_id for row in client.get("/api/v1/canonical-intakes").json()["items"])
    response = client.get(f"/api/v1/canonical-intakes/{intake_id}")
    assert response.status_code == 403, response.text
    assert ids["listing"] not in response.text and ids["source"] not in response.text


def test_native_selection_paginates_only_authorized_rows_and_never_returns_private_url(canonical_graph, native_api):
    client, _, _ = native_api
    own = [seed(canonical_graph) for _ in range(5)]
    for _ in range(4):
        seed(canonical_graph, actor=str(uuid4()))
        seed(canonical_graph, tenant=str(uuid4()))
    found, cursors = [], []
    cursor = None
    while True:
        response = get(client, page_size=2, **({"cursor": cursor} if cursor else {}))
        assert response.status_code == 200, response.text
        assert "original_url" not in response.text and "private-value" not in response.text
        page = response.json()
        found.extend(item["intake_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert cursor not in cursors
        cursors.append(cursor)
        decoded = json.loads(base64.urlsafe_b64decode(cursor))
        assert decoded["intake_id"] == page["items"][-1]["intake_id"]
    assert found == sorted(own, reverse=True) and len(cursors) == 2


@pytest.mark.parametrize("axis,column", [
    ("brand_ids", "brand"), ("region_ids", "region"),
    ("assigned_area_ids", "area"), ("heat_zone_ids", "zone"),
])
def test_sql_projects_restricted_axis_before_limit_and_lookahead(canonical_graph, native_api, axis, column):
    client, caller, _ = native_api
    allowed = str(uuid4())
    expected = {seed(canonical_graph, **{column: allowed}) for _ in range(2)}
    for _ in range(4):
        seed(canonical_graph, **{column: str(uuid4())})
        seed(canonical_graph)
    principal = caller["principal"]
    caller["principal"] = replace(principal, scope=replace(principal.scope, **{axis: frozenset({allowed})}))
    first = get(client, page_size=1)
    assert first.status_code == 200, first.text
    second = get(client, page_size=1, cursor=first.json()["next_cursor"])
    assert second.status_code == 200, second.text
    items = first.json()["items"] + second.json()["items"]
    assert {item["intake_id"] for item in items} == expected
    assert second.json()["next_cursor"] is None


def test_native_intakes_without_store_scope_are_unreadable_to_store_restricted_principal(canonical_graph, native_api):
    client, caller, _ = native_api
    seed(canonical_graph)
    caller["principal"] = replace(caller["principal"], scope=replace(caller["principal"].scope,
        store_ids=frozenset({str(uuid4())})))
    response = get(client)
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("role", [Role.SITE_REVIEWER, Role.DATA_OWNER, Role.OPERATOR_VIEWER])
def test_current_read_grants_allow_other_owners_only_inside_scope(canonical_graph, native_api, role):
    client, caller, _ = native_api
    region = str(uuid4())
    expected = seed(canonical_graph, actor=str(uuid4()), region=region)
    seed(canonical_graph, actor=str(uuid4()), region=str(uuid4()))
    caller["principal"] = replace(caller["principal"], roles=frozenset({role}),
        scope=replace(caller["principal"].scope, region_ids=frozenset({region})))
    response = get(client)
    assert response.status_code == 200, response.text
    assert [row["intake_id"] for row in response.json()["items"]] == [expected]


@pytest.mark.parametrize("revocation", ["role", "scope", "actor", "tenant", "anonymous"])
def test_cursor_never_preserves_previous_authority(canonical_graph, native_api, revocation):
    client, caller, _ = native_api
    for _ in range(3):
        seed(canonical_graph)
    first = get(client, page_size=1)
    assert first.status_code == 200 and first.json()["next_cursor"]
    principal = caller["principal"]
    if revocation == "role":
        caller["principal"] = replace(principal, roles=frozenset({Role.PLATFORM_ADMIN}))
    elif revocation == "scope":
        caller["principal"] = replace(principal, scope=replace(principal.scope, region_ids=frozenset({str(uuid4())})))
    elif revocation == "actor":
        caller["principal"] = replace(principal, subject_id=str(uuid4()))
    elif revocation == "tenant":
        caller["principal"] = replace(principal, scope=Scope(tenant_id=str(uuid4())))
    else:
        caller["principal"] = replace(principal, authenticated=False)
    response = get(client, cursor=first.json()["next_cursor"])
    if revocation in {"scope", "actor"}:
        assert response.status_code == 200 and response.json() == {"items": [], "next_cursor": None}
    else:
        assert response.status_code == {"role": 403, "tenant": 422, "anonymous": 401}[revocation], response.text


@pytest.mark.parametrize("cursor", ["!", "e30=", base64.urlsafe_b64encode(b'{"version":2}').decode()])
def test_invalid_cursor_fails_instead_of_claiming_an_empty_collection(native_api, cursor):
    client, _, _ = native_api
    response = get(client, cursor=cursor)
    assert response.status_code == 422, response.text


def test_cursor_is_bound_to_its_state_filter(canonical_graph, native_api):
    client, _, _ = native_api
    for _ in range(2):
        seed(canonical_graph)
    first = get(client, page_size=1)
    response = get(client, processing_state="NEEDS_REVIEW", cursor=first.json()["next_cursor"])
    assert response.status_code == 422, response.text


def test_forging_a_cursor_position_cannot_grant_foreign_ownership_or_tenant(canonical_graph, native_api):
    _, ids, _, _ = canonical_graph
    client, _, _ = native_api
    own = {seed(canonical_graph) for _ in range(2)}
    other = seed(canonical_graph, actor=str(uuid4()))
    foreign_tenant = str(uuid4())
    seed(canonical_graph, tenant=foreign_tenant)
    value = {"version": 1, "tenant_id": ids["tenant"], "processing_state": "SUBMITTED",
             "submitted_at": "2099-01-01T00:00:00Z", "intake_id": other}

    def encode():
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()

    response = get(client, cursor=encode())
    assert response.status_code == 200, response.text
    assert {row["intake_id"] for row in response.json()["items"]} == own
    value["tenant_id"] = foreign_tenant
    assert get(client, cursor=encode()).status_code == 422


@pytest.mark.parametrize("role", [Role.PLATFORM_ADMIN, Role.OPERATIONS_MANAGER, Role.MARKETING_MANAGER])
def test_empty_collection_still_requires_actual_view_permission(native_api, role):
    client, caller, _ = native_api
    caller["principal"] = replace(caller["principal"], roles=frozenset({role}))
    response = get(client)
    assert response.status_code == 403, response.text
