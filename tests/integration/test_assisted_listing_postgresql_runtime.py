from __future__ import annotations

import copy
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.auth import Role, Scope
from shared.identity.store import Account
from shared.infrastructure.persistence.assisted_listing_intake import (
    ALL_TABLES,
    AssistedIntakePersistenceConflict,
    apply_upgrade_to_database,
)
from shared.infrastructure.persistence.factory import build_persistence
from shared.infrastructure.persistence.postgresql import (
    _REQUIRED_RELATIONS,
    PostgresEngine,
)

pytestmark = pytest.mark.requires_live_env

TENANT_ID = "00000000-0000-0000-0000-000000000001"
ACTOR_ID = "00000000-0000-0000-0000-000000000101"
REVIEWER_ID = "00000000-0000-0000-0000-000000000102"
OWNER_ID = "00000000-0000-0000-0000-000000000105"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _database_url(database) -> str:
    params = database.server.admin_params
    user = quote(str(params.get("user") or "postgres"), safe="")
    password = str(params.get("password") or "")
    credentials = user + (f":{quote(password, safe='')}" if password else "")
    host = str(params.get("host") or "127.0.0.1")
    port = str(params.get("port") or "5432")
    dbname = quote(database.dbname, safe="")
    if host.startswith("/"):
        return (
            f"postgresql://{credentials}@/{dbname}"
            f"?host={quote(host, safe='')}&port={port}"
        )
    return (
        f"postgresql://{credentials}@{host}:{port}/{dbname}"
    )


# The relations this helper stands in for. `PostgresEngine` refuses to boot
# unless every name in `_REQUIRED_RELATIONS` resolves; the ones under
# `odp_runtime.` are created by `apply_upgrade_to_database`, and everything
# else has to come from here. Naming the split explicitly lets
# `_install_canonical_runtime` assert its own coverage instead of trusting that
# whoever adds a required relation also remembers this file -- which is exactly
# what did not happen for `workflow.decision_policies`, added to
# `_REQUIRED_RELATIONS` by ODP-FORECAST-ALERT-POLICY-001 while this fixture
# stayed as it was. Nothing caught it because the marker expression that this
# branch splits kept these tests out of CI entirely.
_INTAKE_MIGRATION_SCHEMA = "odp_runtime"

_CANONICAL_STUB_DDL = """
    CREATE SCHEMA IF NOT EXISTS core;
    CREATE SCHEMA IF NOT EXISTS workflow;
    CREATE TABLE IF NOT EXISTS core.tenants (
        tenant_id UUID PRIMARY KEY,
        tenant_name TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    );
    CREATE TABLE IF NOT EXISTS core.brands (brand_id UUID PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS core.address_locations (
        address_id UUID PRIMARY KEY
    );
    CREATE TABLE IF NOT EXISTS core.stores (store_id UUID PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS core.machines (machine_id UUID PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS core.transactions (
        transaction_id UUID PRIMARY KEY
    );
    CREATE TABLE IF NOT EXISTS core.machine_cycles (cycle_id UUID PRIMARY KEY);
    -- Boot only needs the relation to resolve; the columns the decision-policy
    -- registry governs belong to its own tests, not to assisted-listing intake.
    CREATE TABLE IF NOT EXISTS workflow.decision_policies (
        policy_version_id VARCHAR(100) PRIMARY KEY
    );
"""

_CANONICAL_STUB_RELATIONS = frozenset(
    re.findall(
        r"CREATE TABLE IF NOT EXISTS ([a-z_]+\.[a-z_]+)",
        _CANONICAL_STUB_DDL,
    )
)


def _install_canonical_runtime(database_url: str) -> None:
    expected = {
        relation
        for relation in _REQUIRED_RELATIONS
        if not relation.startswith(f"{_INTAKE_MIGRATION_SCHEMA}.")
    }
    uncovered = expected - _CANONICAL_STUB_RELATIONS
    if uncovered:
        raise AssertionError(
            "PostgresEngine requires relations this fixture does not create: "
            + ", ".join(sorted(uncovered))
            + ". Add them to _CANONICAL_STUB_DDL."
        )

    engine = PostgresEngine(database_url, validate_schema=False)
    try:
        engine.execute(_CANONICAL_STUB_DDL)
    finally:
        engine.close()


def _live_provider_validation() -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        errors=(),
        mode=SimpleNamespace(value="live"),
    )


def _headers(subject_id: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "x-subject-id": subject_id,
        "x-tenant-id": TENANT_ID,
        "x-roles": "site_reviewer,data_owner,expansion_user",
        "x-operator-role": "expansion-manager",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _configure_production(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    database_url: str,
) -> None:
    monkeypatch.setenv("ODP_DEPLOY_ENV", "production")
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_PERSISTENCE", "postgresql")
    monkeypatch.setenv("ODAY_DATABASE_URL", database_url)
    monkeypatch.setenv(
        "ODP_INTAKE_CURSOR_SIGNING_KEY",
        "production-test-cursor-signing-key-0001",
    )
    monkeypatch.setenv(
        "ODP_AUDIT_WORM_LOCAL_PATH",
        str(tmp_path / "worm"),
    )
    monkeypatch.setenv(
        "MLFLOW_TRACKING_URI",
        f"sqlite:///{tmp_path / 'mlflow.sqlite3'}",
    )


def test_full_assisted_intake_stack_applies_and_validates(
    intake_blank_db,
) -> None:
    database_url = _database_url(intake_blank_db)

    result = apply_upgrade_to_database(database_url)

    assert result.steps == (
        "001_baseline.sql",
        "002_consistency.sql",
        "003_promotion_state.sql",
        "004_tenant_rls_lineage.sql",
    )
    assert result.required_tables == ALL_TABLES
    with intake_blank_db.connect() as connection:
        missing = [
            relation
            for relation in ALL_TABLES
            if connection.execute(
                "SELECT to_regclass(%s)",
                (relation,),
            ).fetchone()[0]
            is None
        ]
    assert missing == []


@pytest.mark.requires_postgis  # canonical baseline DDL declares CREATE EXTENSION postgis
def test_full_stack_composes_after_canonical_migration_and_is_idempotent(
    intake_blank_db,
) -> None:
    database_url = _database_url(intake_blank_db)
    canonical_sql = (
        REPO_ROOT
        / "infra/db/migrations/000001_baseline_canonical_schema.sql"
    ).read_text(encoding="utf-8")
    with intake_blank_db.connect() as connection:
        connection.execute(canonical_sql)

    first = apply_upgrade_to_database(database_url)
    repeated = apply_upgrade_to_database(database_url)

    assert repeated == first
    with intake_blank_db.connect() as connection:
        marker = connection.execute(
            """
            SELECT manifest_sha256
            FROM odp_runtime.assisted_intake_schema_migrations
            WHERE migration_name = '001-004'
            """
        ).fetchone()
        assert marker is not None
        assert marker[0] == first.manifest_sha256
        for relation, columns in {
            "expansion.listings": {
                "tenant_id",
                "property_id",
                "lifecycle_state",
                "current_revision_id",
                "version",
            },
            "expansion.candidate_sites": {
                "tenant_id",
                "property_id",
                "promotion_decision_id",
                "status",
                "version",
            },
            "audit.audit_events": {
                "tenant_id",
                "sequence_no",
                "resource_type",
                "event_sha256",
                "legal_hold",
            },
        }.items():
            schema, table = relation.split(".", maxsplit=1)
            installed = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    """,
                    (schema, table),
                ).fetchall()
            }
            assert columns <= installed


def test_production_api_boots_with_postgresql_and_fails_on_missing_intake_schema(
    intake_blank_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    database_url = _database_url(intake_blank_db)
    _install_canonical_runtime(database_url)
    _configure_production(monkeypatch, tmp_path, database_url)

    with pytest.raises(
        RuntimeError,
        match="Assisted Listing Intake schema is incomplete",
    ):
        create_app(external_provider_validation=_live_provider_validation())

    apply_upgrade_to_database(database_url)
    app = create_app(
        external_provider_validation=_live_provider_validation(),
    )
    try:
        assert app.state.persistence_bundle.mode == "postgresql"
        assert app.state.persistence_bundle.is_production is True
        assert app.state.assisted_intake_store is not None
    finally:
        app.state.persistence_bundle.engine.close()


def test_assisted_intake_http_state_survives_postgresql_restart(
    intake_blank_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    database_url = _database_url(intake_blank_db)
    apply_upgrade_to_database(database_url)
    _install_canonical_runtime(database_url)
    _configure_production(monkeypatch, tmp_path, database_url)

    app = create_app(
        external_provider_validation=_live_provider_validation(),
    )
    # Use the actual identity schema/store for assignment recipients. Explicit
    # test accounts, not production/Operator provisioning or login acceptance.
    with intake_blank_db.connect(autocommit=True) as connection:
        connection.execute((REPO_ROOT / "infra/db/migrations/000011_identity_schema.sql").read_text())
    identities = app.state.persistence_bundle.identity_store
    for subject, role in ((OWNER_ID, Role.DATA_OWNER), (REVIEWER_ID, Role.SITE_REVIEWER)):
        identities.save_account(Account(UUID(subject), UUID(TENANT_ID), subject, f"{subject}@example.invalid"))
        identities.set_account_roles(subject, [role])
        identities.set_account_scope(subject, Scope(tenant_id=TENANT_ID))
    client = TestClient(app)
    submit_key = f"production-submit-{uuid4()}"
    submitted = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.com/listings/production-1",
            "scope": {"tenant_id": TENANT_ID},
        },
        headers=_headers(ACTOR_ID, key=submit_key),
    )
    assert submitted.status_code == 202, submitted.text
    receipt = submitted.json()

    assigned = client.put(
        f"/api/v1/intakes/{receipt['intake_id']}/assignment",
        json={
            "owner_subject_id": OWNER_ID,
            "owner_role": "data-steward",
            "due_at": "2026-07-30T12:00:00Z",
            "reason": "Production persistence verification",
        },
        headers={
            **_headers(ACTOR_ID, key=f"production-assign-{uuid4()}"),
            "If-Match": 'W/"1"',
        },
    )
    assert assigned.status_code == 200, assigned.text

    state = app.state.assisted_intake_store
    state.refresh(TENANT_ID)
    state.intakes[receipt["intake_id"]]["state"] = "NEEDS_REVIEW"
    # Explicit SLA provisioning fixture: the API still has no approved SLA
    # creation endpoint. The actions below, persistence and restart are real;
    # this does not prove Operator provisioning or its shared read-model.
    sla_id = str(uuid4())
    state.slas[sla_id] = {
        "sla_instance_id": sla_id,
        "intake_id": receipt["intake_id"],
        "tenant_id": TENANT_ID,
        "state": "ON_TRACK",
        "due_at": "2026-07-30T12:00:00Z",
        "paused_duration_seconds": 17,
        "version": 23,
        "audit_event_id": str(uuid4()),
        "correlation_id": str(uuid4()),
    }
    state.flush()
    pause_body = {
        "reason": "Awaiting independent source feedback",
        "expected_resume_at": "2027-01-10T12:00:00Z",
    }
    pause_headers = {
        **_headers(ACTOR_ID, key=f"production-sla-pause-{uuid4()}"),
        "If-Match": 'W/"23"',
    }
    paused = client.post(
        f"/api/v1/sla-instances/{sla_id}/actions/pause",
        json=pause_body, headers=pause_headers,
    )
    assert paused.status_code == 200, paused.text
    corrected = client.post(
        f"/api/v1/intakes/{receipt['intake_id']}/corrections",
        json={
            "field_path": "address",
            "corrected_value": "台北市信義區測試路 1 號",
            "reason": "Identity correction restart verification",
            "risk_acknowledged": True,
        },
        headers={
            **_headers(ACTOR_ID, key=f"production-correction-{uuid4()}"),
            "If-Match": 'W/"2"',
        },
    )
    assert corrected.status_code == 201, corrected.text
    correction = corrected.json()
    transfer_path = f"/api/v1/assignments/{assigned.json()['assignment_id']}/actions/transfer"
    transfer_body = {
        "target_owner_subject_id": REVIEWER_ID, "target_owner_role": "reviewer",
        "reason": "Route to the independent reviewer",
        "handoff_note": "Check the original source correction evidence",
    }
    transfer_headers = {
        **_headers(ACTOR_ID, key=f"production-transfer-{uuid4()}"),
        "If-Match": assigned.headers["ETag"],
    }
    directory_path = f"/api/v1/assignments/{assigned.json()['assignment_id']}/transfer-targets"
    directory = client.get(directory_path, headers=_headers(ACTOR_ID))
    assert directory.status_code == 200, directory.text
    assert directory.headers["Cache-Control"] == "no-store"
    assert directory.json() == {
        "assignment_id": assigned.json()["assignment_id"], "assignment_version": assigned.json()["version"],
        "items": [{"id": REVIEWER_ID, "name": REVIEWER_ID, "role": "site-reviewer"}],
    }
    target_account = identities.find_account_by_id(REVIEWER_ID)
    identities.save_account(replace(target_account, status="disabled"))
    assert client.get(directory_path, headers=_headers(ACTOR_ID)).json()["items"] == []
    identities.save_account(target_account)
    assert client.get(directory_path, headers=_headers(ACTOR_ID)).json() == directory.json()
    transferred = client.post(transfer_path, json=transfer_body, headers=transfer_headers)
    assert transferred.status_code == 200, transferred.text
    assert transferred.json()["owner_subject_id"] == REVIEWER_ID
    app.state.persistence_bundle.engine.close()

    restarted = create_app(
        external_provider_validation=_live_provider_validation(),
    )
    restarted_client = TestClient(restarted)
    try:
        detail = restarted_client.get(
            f"/api/v1/intakes/{receipt['intake_id']}",
            headers=_headers(ACTOR_ID),
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["assignment_id"] == assigned.json()["assignment_id"]
        assert detail.json()["assignment_version"] == transferred.json()["version"]
        assert detail.json()["assignment_status"] == "TRANSFERRED"
        assert detail.json()["assigned_to"] == REVIEWER_ID
        # Read fresh SQL identity state on retries, not the original recipient
        # cache or the UUID payload. Revocation denies without business mutation.
        identities = restarted.state.persistence_bundle.identity_store
        target_account = identities.find_account_by_id(REVIEWER_ID)
        identities.save_account(replace(target_account, status="disabled"))
        state = restarted.state.assisted_intake_store
        state.refresh(TENANT_ID)
        before = copy.deepcopy((state.intakes, state.assignments, state.replays))
        denied = restarted_client.post(transfer_path, json=transfer_body, headers=transfer_headers)
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "ASSIGNMENT_SCOPE_DENIED"
        state.refresh(TENANT_ID)
        assert (state.intakes, state.assignments, state.replays) == before
        identities.save_account(target_account)
        transfer_replay = restarted_client.post(transfer_path, json=transfer_body, headers=transfer_headers)
        assert transfer_replay.status_code == 200, transfer_replay.text
        assert transfer_replay.json() == transferred.json()
        assert transfer_replay.headers["ETag"] == transferred.headers["ETag"]
        assert detail.json()["sla_instance_id"] == sla_id
        assert detail.json()["sla_state"] == "PAUSED"
        assert detail.json()["sla_version"] == paused.json()["version"] == 24
        # Lost-response replay must retain the same interval and receipt after
        # restart; it must not reopen the pause or count elapsed time twice.
        pause_replay = restarted_client.post(
            f"/api/v1/sla-instances/{sla_id}/actions/pause",
            json=pause_body, headers=pause_headers,
        )
        assert pause_replay.status_code == 200, pause_replay.text
        assert pause_replay.json() == paused.json()
        resumed = restarted_client.post(
            f"/api/v1/sla-instances/{sla_id}/actions/resume",
            json={"reason": "Independent feedback received"},
            headers={
                **_headers(ACTOR_ID, key=f"production-sla-resume-{uuid4()}"),
                "If-Match": 'W/"24"',
            },
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["state"] == "ON_TRACK"
        assert resumed.json()["version"] == 25
        restarted.state.assisted_intake_store.refresh(TENANT_ID)
        sla = restarted.state.assisted_intake_store.slas[sla_id]
        assert len(sla["pause_intervals"]) == 1
        interval = sla["pause_intervals"][0]
        assert interval["reason"] == pause_body["reason"]
        assert interval["expected_resume_at"] == pause_body["expected_resume_at"]
        assert interval["pause_interval_id"] == paused.json()["active_pause_interval_id"]
        assert interval["resume_reason"] == "Independent feedback received"
        assert interval["resumed_by_subject_id"] == ACTOR_ID
        assert interval["ended_at"] is not None
        assert sla["paused_duration_seconds"] >= 17
        # Independently reload the completed interval using another DB-backed
        # store, not the API replica's already-mutated in-memory snapshot.
        reloaded = build_persistence(mode="postgresql")
        try:
            reloaded.assisted_intake_store.refresh(TENANT_ID)
            assert reloaded.assisted_intake_store.slas[sla_id] == sla
            assignment_id = assigned.json()["assignment_id"]
            assert reloaded.assisted_intake_store.assignments[assignment_id]["owner_subject_id"] == REVIEWER_ID
            assert reloaded.assisted_intake_store.assignments[assignment_id]["version"] == transferred.json()["version"]
            assert reloaded.assisted_intake_store.intakes[receipt["intake_id"]]["assigned_to"] == REVIEWER_ID
        finally:
            reloaded.engine.close()

        job = restarted_client.get(
            f"/api/v1/jobs/{receipt['job_id']}/receipt",
            headers=_headers(ACTOR_ID),
        )
        assert job.status_code == 200, job.text
        assert job.json()["job_id"] == receipt["job_id"]

        decision = restarted_client.get(
            f"/api/v1/identity-decisions/{correction['correction_id']}",
            headers=_headers(ACTOR_ID),
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["status"] == "PENDING_REVIEW"

        reviewed = restarted_client.post(
            (
                "/api/v1/identity-decisions/"
                f"{correction['correction_id']}/actions/review"
            ),
            json={
                "decision": "APPROVE",
                "reason": "Independent reviewer confirms durable correction",
                "risk_acknowledged": True,
            },
            headers={
                **_headers(
                    REVIEWER_ID,
                    key=f"production-review-{uuid4()}",
                ),
                "If-Match": 'W/"1"',
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["status"] == "APPROVED"
    finally:
        restarted.state.persistence_bundle.engine.close()


def test_assisted_intake_rejects_stale_multi_instance_write(
    intake_blank_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _database_url(intake_blank_db)
    apply_upgrade_to_database(database_url)
    _install_canonical_runtime(database_url)
    monkeypatch.setenv("ODAY_DATABASE_URL", database_url)

    first = build_persistence(mode="postgresql")
    second = build_persistence(mode="postgresql")
    intake_id = str(uuid4())
    try:
        first.assisted_intake_store.refresh(TENANT_ID)
        first.assisted_intake_store.intakes[intake_id] = {
            "intake_id": intake_id,
            "state": "SUBMITTED",
            "version": 1,
        }
        first.assisted_intake_store.flush()

        first.assisted_intake_store.refresh(TENANT_ID)
        second.assisted_intake_store.refresh(TENANT_ID)
        first.assisted_intake_store.intakes[intake_id]["state"] = "PARSING"
        second.assisted_intake_store.intakes[intake_id]["state"] = "FAILED"

        first.assisted_intake_store.flush()
        with pytest.raises(
            AssistedIntakePersistenceConflict,
            match="Concurrent Assisted Intake update",
        ):
            second.assisted_intake_store.flush()

        second.assisted_intake_store.refresh(TENANT_ID)
        assert second.assisted_intake_store.intakes[intake_id]["state"] == "PARSING"
    finally:
        first.engine.close()
        second.engine.close()
