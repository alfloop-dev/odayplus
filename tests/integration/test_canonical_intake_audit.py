"""Actual canonical table/RLS/transaction tests, with synthetic principals."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

import pytest

from shared.audit.canonical_postgresql import CanonicalAuditFact, CanonicalPostgresAuditLog
from shared.audit.integrity import AuditImmutabilityError, AuditIntegrityError
from shared.infrastructure.persistence.postgresql import PostgresEngine

pytestmark = pytest.mark.requires_live_env
TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"
ACTOR = "00000000-0000-0000-0000-000000000101"


@pytest.fixture
def canonical_audit(intake_db):
    # A real application role with no bypass-RLS, UPDATE, or DELETE grant.
    role = "audit_test_" + uuid4().hex
    password = "synthetic-audit-only"
    with intake_db.connect() as conn:
        conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS")
        conn.execute(f"GRANT USAGE ON SCHEMA audit, intake TO {role}")
        conn.execute(f"GRANT SELECT, INSERT ON audit.audit_events, intake.intakes TO {role}")
    params = intake_db.server.admin_params
    host = str(params.get("host") or "127.0.0.1")
    port = str(params.get("port") or "5432")
    if host.startswith("/"):
        url = f"postgresql://{role}:{password}@/{intake_db.dbname}?host={quote(host, safe='')}&port={port}"
    else:
        url = f"postgresql://{role}:{password}@{host}:{port}/{intake_db.dbname}"
    engine = PostgresEngine(url, bootstrap=False, validate_schema=False)
    try:
        yield CanonicalPostgresAuditLog(engine), intake_db, url
    finally:
        engine.close()
        with intake_db.connect() as conn:
            conn.execute(f"DROP OWNED BY {role}")
            conn.execute(f"DROP ROLE {role}")


def _fact(tenant=TENANT_A, **changes):
    occurred = datetime.now(UTC)
    return CanonicalAuditFact(
        audit_event_id=str(uuid4()),
        tenant_id=tenant,
        event_type="intake.correct.apply",
        action="intake.correct.apply",
        resource_type="correction",
        resource_id=str(uuid4()),
        result="SUCCEEDED",
        correlation_id=str(uuid4()),
        actor_subject_id=ACTOR,
        actor_role="expansion_user",
        occurred_at=occurred,
        # Explicit synthetic test policy, not a production retention default.
        retained_until=occurred + timedelta(days=90),
        before_value={"classification": "CONFIDENTIAL", "effective": 11},
        after_value={"classification": "CONFIDENTIAL", "effective": 12},
        **changes,
    )


def test_actual_table_chain_survives_restart_and_does_not_claim_worm(canonical_audit):
    log, _, url = canonical_audit
    fact = _fact()
    first = log.append(fact)
    assert first["audit_event_id"] == fact.audit_event_id and first["actor_subject_id"] == ACTOR
    assert first["after_value"]["classification"] == "CONFIDENTIAL"
    assert first["sequence_no"] == 1 and first["worm_object_uri"] is None
    second = log.append(_fact())
    assert second["previous_event_sha256"] == first["event_sha256"]
    restarted = PostgresEngine(url, bootstrap=False, validate_schema=False)
    try:
        other = CanonicalPostgresAuditLog(restarted)
        assert other.list_events(TENANT_A) == [first, second]
        assert other.verify_chain(TENANT_A).ok
        assert other.append(fact) == first
    finally:
        restarted.close()


@pytest.mark.parametrize("value", [None, "null", "11", ["null", 12], {"a": True, "b": 1.5}])
def test_json_fact_types_round_trip_without_hash_ambiguity(canonical_audit, value):
    log, _, _ = canonical_audit
    fact = replace(_fact(), after_value=value)
    assert log.append(fact)["after_value"] == value
    assert log.append(fact)["after_value"] == value
    assert log.verify_chain(TENANT_A).ok


def test_foreign_tenant_cannot_be_read_or_changed_inside_bound_transaction(canonical_audit):
    log, _, _ = canonical_audit
    log.append(_fact(TENANT_A))
    assert log.list_events(TENANT_B) == []
    with log.engine.transaction():
        log.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (TENANT_A,))
        assert log.engine.query("SELECT tenant_id FROM audit.audit_events") == [
            {"tenant_id": TENANT_A}
        ]
        with pytest.raises(PermissionError):
            log.append(_fact(TENANT_B))
    assert log.list_events(TENANT_B) == []


def test_business_and_audit_rollback_together_and_id_can_retry(canonical_audit):
    log, _, _ = canonical_audit
    fact = _fact()
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        with log.engine.transaction():
            log.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (TENANT_A,))
            log.engine.execute(
                "INSERT INTO intake.intakes (tenant_id,submitter_subject_id,intake_method,processing_state,correlation_id) VALUES (?,?,'MANUAL','SUBMITTED',?)",
                (TENANT_A, ACTOR, str(uuid4())),
            )
            log.append(fact)
            raise RuntimeError("synthetic commit failure")
    assert log.list_events(TENANT_A) == []
    with log.engine.transaction():
        log.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (TENANT_A,))
        assert log.engine.query("SELECT * FROM intake.intakes") == []
    assert log.append(fact)["sequence_no"] == 1


def test_replay_cannot_overwrite_facts_or_mask_sensitive_values(canonical_audit):
    log, _, _ = canonical_audit
    fact = _fact()
    first = log.append(fact)
    with pytest.raises(AuditImmutabilityError):
        log.append(replace(fact, after_value={"effective": 99}))
    with pytest.raises(ValueError, match="sensitive values"):
        log.append(replace(_fact(), result="DENIED"))
    assert log.list_events(TENANT_A) == [first]


def test_application_role_cannot_update_or_delete_audit_records(canonical_audit):
    import psycopg

    log, _, _ = canonical_audit
    log.append(_fact())
    for mutation in (
        "UPDATE audit.audit_events SET reason='changed'",
        "DELETE FROM audit.audit_events",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with log.engine.transaction():
                log.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (TENANT_A,))
                log.engine.execute(mutation)
    assert len(log.list_events(TENANT_A)) == 1


def test_corrupt_or_legacy_tail_is_not_silently_adopted(canonical_audit):
    log, db, _ = canonical_audit
    first = log.append(_fact())
    with db.connect() as conn:
        conn.execute(
            "UPDATE audit.audit_events SET after_value = '{\"effective\":99}'::jsonb WHERE audit_event_id=%s",
            (first["audit_event_id"],),
        )
    assert not log.verify_chain(TENANT_A).ok
    with pytest.raises(AuditIntegrityError):
        log.append(_fact())
    assert len(log.list_events(TENANT_A)) == 1


def test_concurrent_connections_produce_one_contiguous_tenant_chain(canonical_audit):
    log, _, url = canonical_audit

    def append_one(_):
        engine = PostgresEngine(url, bootstrap=False, validate_schema=False)
        try:
            return CanonicalPostgresAuditLog(engine).append(_fact())["sequence_no"]
        finally:
            engine.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        sequences = list(pool.map(append_one, range(8)))
    assert sorted(sequences) == list(range(1, 9))
    assert log.verify_chain(TENANT_A).ok


@pytest.mark.parametrize(
    "invalid",
    [
        "tenant",
        "missing_identity",
        "missing_principal",
        "bad_service",
        "naive_time",
        "retention",
        "nan",
    ],
)
def test_invalid_facts_leave_no_audit_or_success_receipt(canonical_audit, invalid):
    log, _, _ = canonical_audit
    fact = _fact()
    if invalid == "tenant":
        fact = replace(fact, tenant_id="tenant-a")
    elif invalid == "missing_identity":
        fact = replace(fact, audit_event_id=None)
    elif invalid == "missing_principal":
        fact = replace(fact, actor_subject_id=None)
    elif invalid == "bad_service":
        fact = replace(fact, service_principal=12)
    elif invalid == "naive_time":
        fact = replace(fact, occurred_at=datetime.now())
    elif invalid == "retention":
        fact = replace(fact, retained_until=fact.occurred_at)
    else:
        fact = replace(fact, after_value=float("nan"))
    with pytest.raises(ValueError):
        log.append(fact)
    assert log.list_events(TENANT_A) == []
