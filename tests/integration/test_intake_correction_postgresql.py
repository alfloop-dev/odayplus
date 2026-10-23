"""Real PG16 commits/restarts with explicitly synthetic actors and parser state."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from tests.intake_workflow_fixture import (
    SyntheticIntakeAuditRetention,
    SyntheticIntakeWorkflowPolicy,
)
from tests.integration.test_assisted_listing_postgresql_runtime import (
    ACTOR_ID,
    REVIEWER_ID,
    TENANT_ID,
    _configure_production,
    _database_url,
    _headers,
    _install_canonical_runtime,
    _live_provider_validation,
)

pytestmark = pytest.mark.requires_live_env
COLLECTIONS = (
    "intakes",
    "corrections",
    "decisions",
    "replays",
    "workflow_audits",
    "workflow_outbox",
)


@pytest.fixture
def postgres_case(intake_blank_db, monkeypatch, tmp_path):
    url = _database_url(intake_blank_db)
    apply_upgrade_to_database(url)
    _install_canonical_runtime(url)
    _configure_production(monkeypatch, tmp_path, url)
    app = _app()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/intakes/url",
            json={
                "original_url": "https://example.invalid/pg-correction",
                "scope": {"tenant_id": TENANT_ID},
            },
            headers=_headers(ACTOR_ID, key=f"synthetic-submit-{uuid4()}"),
        )
        assert response.status_code == 202, response.text
        iid = response.json()["intake_id"]
        store = app.state.assisted_intake_store
        store.refresh(TENANT_ID)
        # Isolate correction storage; no claim that the parser produced this row.
        intake = store.intakes[iid]
        intake.update(
            state="NEEDS_REVIEW", source_snapshot_id=str(uuid4()), parser_run_id=str(uuid4())
        )
        intake["fields"] = [
            dict(
                field_path=path,
                classification="INTERNAL",
                masked=False,
                parsed=10,
                normalized=11,
                corrected=None,
                effective=11,
                confidence=0.91,
            )
            for path in ("floor", "rent_amount")
        ]
        store.flush()
        yield app, client, iid
    app.state.persistence_bundle.engine.close()


def _app():
    return create_app(
        external_provider_validation=_live_provider_validation(),
        assisted_intake_audit_retention=SyntheticIntakeAuditRetention(), assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy(),
    )


def _correct(case, path, key):
    app, client, iid = case
    app.state.assisted_intake_store.refresh(TENANT_ID)
    version = app.state.assisted_intake_store.intakes[iid]["version"]
    return client.post(
        f"/api/v1/intakes/{iid}/corrections",
        json={
            "field_path": path,
            "corrected_value": 12,
            "reason": "Synthetic original document",
            "risk_acknowledged": True,
        },
        headers={**_headers(ACTOR_ID, key=key), "If-Match": f'W/"{version}"'},
    )


def _review(case, cid, key):
    _, client, _ = case
    return client.post(
        f"/api/v1/identity-decisions/{cid}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "Synthetic independent review",
            "risk_acknowledged": True,
        },
        headers={**_headers(REVIEWER_ID, key=key), "If-Match": 'W/"1"'},
    )


def _snapshot(store):
    store.refresh(TENANT_ID)
    return deepcopy({name: getattr(store, name) for name in COLLECTIONS})


def test_correction_lineage_and_real_audits_survive_new_postgresql_connection(postgres_case):
    app, _, iid = postgres_case
    ordinary = _correct(postgres_case, "floor", f"synthetic-ordinary-{uuid4()}")
    assert ordinary.status_code == 201, ordinary.text
    proposal = _correct(postgres_case, "rent_amount", f"synthetic-proposal-{uuid4()}")
    assert proposal.status_code == 201, proposal.text
    cid = proposal.json()["correction_id"]
    reviewed = _review(postgres_case, cid, f"synthetic-review-{uuid4()}")
    assert reviewed.status_code == 200, reviewed.text
    before = _snapshot(app.state.assisted_intake_store)
    restarted = _app()
    try:
        with TestClient(restarted) as client:
            detail = client.get(f"/api/v1/intakes/{iid}", headers=_headers(ACTOR_ID))
            assert detail.status_code == 200, detail.text
            assert all(
                f["parsed"] == 10 and f["normalized"] == 11 and f["effective"] == 12
                for f in detail.json()["fields"]
            )
            store = restarted.state.assisted_intake_store
            assert _snapshot(store) == before
            for receipt in (ordinary.json(), proposal.json(), reviewed.json()):
                assert receipt["audit_event_id"] in store.workflow_audits
            assert store.corrections[cid]["reviewed_by"] == REVIEWER_ID
            assert store.decisions[cid]["status"] == "APPROVED"
            store.refresh(str(uuid4()))
            assert all(not getattr(store, name) for name in COLLECTIONS)
    finally:
        restarted.state.persistence_bundle.engine.close()


@pytest.mark.parametrize("command", ["ordinary", "proposal", "review"])
def test_partial_sql_write_failure_rolls_back_correction_and_can_retry(
    postgres_case, monkeypatch, command
):
    app, _, _ = postgres_case
    store = app.state.assisted_intake_store
    cid = (
        _correct(postgres_case, "rent_amount", f"synthetic-proposal-{uuid4()}").json()[
            "correction_id"
        ]
        if command == "review"
        else None
    )
    key = f"synthetic-failed-sql-{uuid4()}"

    def execute():
        return (
            _review(postgres_case, cid, key)
            if cid
            else _correct(postgres_case, "rent_amount" if command == "proposal" else "floor", key)
        )

    before = _snapshot(store)
    original_put = store._store.put
    calls = 0

    def crash(*args, **kwargs):
        nonlocal calls
        calls += 1
        original_put(*args, **kwargs)
        if calls == 2:
            raise RuntimeError("synthetic crash after two SQL writes")

    with monkeypatch.context() as patch:
        patch.setattr(store._store, "put", crash)
        failed = execute()
    assert failed.status_code == 500 and calls == 2, failed.text
    assert _snapshot(store) == before
    retried = execute()
    assert retried.status_code in {200, 201}, retried.text
    assert retried.headers["Idempotency-Replayed"] == "false"
