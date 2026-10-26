"""PostgreSQL coverage for the deployment-only first administrator bootstrap.

Task: ODP-DEV-ADMIN-RELEASE-READINESS-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §7.2

Runs against a real PostgreSQL 16 (``INTAKE_TEST_DATABASE_URL`` or the bundled
``pgserver``) with the identity schema migration and the runtime audit table.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from shared.identity.bootstrap import (
    BOOTSTRAP_AUDIT_EVENT,
    STATUS_CREATED,
    STATUS_NOOP,
    BootstrapRefused,
    bootstrap_first_admin,
    main,
    request_from_env,
)

IDENTITY_MIGRATION = Path("infra/db/migrations/000011_identity_schema.sql")
SECRET = "Correct-Horse-Battery-7319-staple"
TENANT = "6f1c2c1e-3a51-4b8e-9d55-1a3b7e0c9f10"


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "ODP_IDENTITY_BOOTSTRAP_SECRET": SECRET,
        "ODP_IDENTITY_BOOTSTRAP_EXPIRES_AT": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        "ODP_IDENTITY_BOOTSTRAP_TENANT_ID": TENANT,
        "ODP_IDENTITY_BOOTSTRAP_USERNAME": "first.admin",
        "ODP_IDENTITY_BOOTSTRAP_EMAIL": "first.admin@example.invalid",
        "ODP_IDENTITY_BOOTSTRAP_DISPLAY_NAME": "First Admin",
    }
    env.update(overrides)
    return env


@pytest.fixture
def identity_db(intake_blank_db: Any) -> Any:
    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(IDENTITY_MIGRATION.read_text(encoding="utf-8"))
    return intake_blank_db


def _engine(db: Any) -> Any:
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    return PostgresEngine(db.url(), bootstrap=True, validate_schema=False)


@pytest.fixture
def engine(identity_db: Any) -> Any:
    eng = _engine(identity_db)
    try:
        yield eng
    finally:
        eng.close()


def _audit(engine: Any) -> Any:
    from shared.infrastructure.persistence.audit_log import DurableAuditLog

    return DurableAuditLog(engine)


def _rows(db: Any, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with db.connect(autocommit=True) as conn:
        return list(conn.execute(sql, params).fetchall())


def _audit_text(db: Any) -> str:
    rows = _rows(db, "SELECT row_to_json(e)::text FROM odp_runtime.durable_audit_events e")
    return "\n".join(r[0] for r in rows)


def test_creates_single_must_change_platform_admin_with_audit(identity_db: Any, engine: Any) -> None:
    from shared.identity import CredentialService

    result = bootstrap_first_admin(engine, _audit(engine), request_from_env(_env()))

    assert result.status == STATUS_CREATED
    accounts = _rows(identity_db, "SELECT account_id::text, tenant_id::text, username, status, created_by FROM identity.accounts")
    assert accounts == [(result.account_id, TENANT, "first.admin", "active", "identity-bootstrap")]
    assert _rows(identity_db, "SELECT role FROM identity.account_roles") == [("platform_admin",)]
    (phc, must_change, algorithm), = _rows(
        identity_db, "SELECT phc_hash, must_change, algorithm FROM identity.password_credentials"
    )
    assert must_change is True and algorithm == "argon2id"
    assert phc.startswith("$argon2id$") and SECRET not in phc
    assert CredentialService().verify_password(phc, SECRET)

    events = _rows(
        identity_db,
        "SELECT event_type, actor, metadata_json::text, event_hash FROM odp_runtime.durable_audit_events",
    )
    assert len(events) == 1
    event_type, actor, metadata, event_hash = events[0]
    assert (event_type, actor) == (BOOTSTRAP_AUDIT_EVENT, "identity-bootstrap")
    assert json.loads(metadata)["account_id"] == result.account_id
    assert event_hash  # hash-chained like every other durable audit event
    assert _audit(engine).verify_chain().ok
    # No credential material in the audit row or the receipt.
    assert SECRET not in _audit_text(identity_db) and phc not in _audit_text(identity_db)
    receipt = json.dumps(result.to_receipt())
    assert SECRET not in receipt and "$argon2id$" not in receipt
    assert SECRET not in repr(request_from_env(_env()))


def test_rerun_is_a_noop_and_never_resets_the_password(identity_db: Any, engine: Any) -> None:
    first = bootstrap_first_admin(engine, _audit(engine), request_from_env(_env()))
    before = _rows(identity_db, "SELECT phc_hash, must_change FROM identity.password_credentials")

    again = bootstrap_first_admin(
        engine,
        _audit(engine),
        request_from_env(_env(ODP_IDENTITY_BOOTSTRAP_SECRET="Another-Different-Secret-4410")),
    )

    assert first.status == STATUS_CREATED and again.status == STATUS_NOOP
    assert again.account_id is None
    assert _rows(identity_db, "SELECT count(*) FROM identity.accounts") == [(1,)]
    assert _rows(identity_db, "SELECT phc_hash, must_change FROM identity.password_credentials") == before
    assert _rows(identity_db, "SELECT count(*) FROM odp_runtime.durable_audit_events") == [(1,)]


def test_any_active_account_in_any_tenant_makes_bootstrap_a_noop(identity_db: Any, engine: Any) -> None:
    _rows(
        identity_db,
        "INSERT INTO identity.accounts (tenant_id, username, email, status, created_by) "
        "VALUES (%s, 'existing', 'existing@example.invalid', 'active', 'invite') RETURNING account_id",
        (str(uuid4()),),
    )

    result = bootstrap_first_admin(engine, _audit(engine), request_from_env(_env()))

    assert result.status == STATUS_NOOP
    assert _rows(identity_db, "SELECT count(*) FROM identity.accounts") == [(1,)]
    assert _rows(identity_db, "SELECT count(*) FROM identity.account_roles") == [(0,)]


def test_refuses_to_overwrite_an_inactive_account_with_the_same_username(
    identity_db: Any, engine: Any
) -> None:
    _rows(
        identity_db,
        "INSERT INTO identity.accounts (tenant_id, username, email, status, created_by) "
        "VALUES (%s, 'First.Admin', 'other@example.invalid', 'disabled', 'invite') RETURNING account_id",
        (TENANT,),
    )

    with pytest.raises(BootstrapRefused) as exc:
        bootstrap_first_admin(engine, _audit(engine), request_from_env(_env()))

    assert exc.value.code == "BOOTSTRAP_ACCOUNT_EXISTS"
    assert _rows(identity_db, "SELECT username, status FROM identity.accounts") == [("First.Admin", "disabled")]
    assert _rows(identity_db, "SELECT count(*) FROM identity.password_credentials") == [(0,)]
    assert _rows(identity_db, "SELECT count(*) FROM odp_runtime.durable_audit_events") == [(0,)]


def test_audit_failure_rolls_back_account_credential_and_role(identity_db: Any, engine: Any) -> None:
    class BrokenAudit:
        def record(self, event: Any) -> Any:
            raise RuntimeError("audit sink unavailable")

    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        bootstrap_first_admin(engine, BrokenAudit(), request_from_env(_env()))

    for table in ("accounts", "password_credentials", "account_roles"):
        assert _rows(identity_db, f"SELECT count(*) FROM identity.{table}") == [(0,)]  # noqa: S608

    # A later, healthy run still succeeds: the failed attempt left no state.
    assert bootstrap_first_admin(engine, _audit(engine), request_from_env(_env())).status == STATUS_CREATED


def test_concurrent_runs_create_exactly_one_admin(identity_db: Any) -> None:
    engines = [_engine(identity_db), _engine(identity_db)]
    barrier = threading.Barrier(len(engines))
    results: list[Any] = []
    errors: list[BaseException] = []
    request = request_from_env(_env())

    def run(eng: Any) -> None:
        try:
            barrier.wait(timeout=10)
            results.append(
                bootstrap_first_admin(eng, _audit(eng), request, hash_password=lambda _s: "$argon2id$v=19$m=65536,t=3,p=1$c2FsdA$aGFzaA")
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(eng,)) for eng in engines]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        for eng in engines:
            eng.close()

    assert errors == []
    assert sorted(r.status for r in results) == [STATUS_CREATED, STATUS_NOOP]
    assert _rows(identity_db, "SELECT count(*) FROM identity.accounts") == [(1,)]
    assert _rows(identity_db, "SELECT count(*) FROM identity.account_roles") == [(1,)]
    assert _rows(identity_db, "SELECT count(*) FROM odp_runtime.durable_audit_events") == [(1,)]


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"ODP_IDENTITY_BOOTSTRAP_SECRET": ""}, "BOOTSTRAP_INPUT_MISSING"),
        ({"ODP_IDENTITY_BOOTSTRAP_EXPIRES_AT": ""}, "BOOTSTRAP_INPUT_MISSING"),
        ({"ODP_IDENTITY_BOOTSTRAP_EXPIRES_AT": "tomorrow"}, "BOOTSTRAP_EXPIRY_MALFORMED"),
        ({"ODP_IDENTITY_BOOTSTRAP_EXPIRES_AT": "2026-10-04T00:00:00"}, "BOOTSTRAP_EXPIRY_MALFORMED"),
        (
            {"ODP_IDENTITY_BOOTSTRAP_EXPIRES_AT": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
            "BOOTSTRAP_SECRET_EXPIRED",
        ),
        (
            {"ODP_IDENTITY_BOOTSTRAP_EXPIRES_AT": (datetime.now(UTC) + timedelta(hours=73)).isoformat()},
            "BOOTSTRAP_EXPIRY_TOO_LONG",
        ),
        ({"ODP_IDENTITY_BOOTSTRAP_TENANT_ID": "tenant-default"}, "BOOTSTRAP_INPUT_MALFORMED"),
        ({"ODP_IDENTITY_BOOTSTRAP_USERNAME": "a b"}, "BOOTSTRAP_INPUT_MALFORMED"),
        ({"ODP_IDENTITY_BOOTSTRAP_EMAIL": "not-an-email"}, "BOOTSTRAP_INPUT_MALFORMED"),
        ({"ODP_IDENTITY_BOOTSTRAP_SECRET": "short"}, "BOOTSTRAP_SECRET_REJECTED"),
        ({"ODP_IDENTITY_BOOTSTRAP_SECRET": "first.admin-first.admin"}, "BOOTSTRAP_SECRET_REJECTED"),
    ],
)
def test_malformed_missing_or_expired_input_fails_closed_without_echoing_secret(
    overrides: dict[str, str], code: str
) -> None:
    env = _env(**overrides)
    with pytest.raises(BootstrapRefused) as exc:
        request_from_env(env)
    assert exc.value.code == code
    secret = env["ODP_IDENTITY_BOOTSTRAP_SECRET"]
    if secret:
        assert secret not in str(exc.value)


def test_refuses_non_postgresql_engines() -> None:
    class SqliteLike:
        dialect = "sqlite"

    with pytest.raises(BootstrapRefused) as exc:
        bootstrap_first_admin(SqliteLike(), object(), request_from_env(_env()))
    assert exc.value.code == "BOOTSTRAP_REQUIRES_POSTGRESQL"


def test_cli_refuses_when_runtime_audit_schema_is_absent(
    identity_db: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    # The entrypoint never creates schema: without the audit table it refuses.
    assert main([], _env(ODP_IDENTITY_DATABASE_URL=identity_db.url())) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "BOOTSTRAP_SCHEMA_MISSING"
    assert _rows(identity_db, "SELECT count(*) FROM identity.accounts") == [(0,)]


def test_cli_prints_identifier_receipt_only_and_is_idempotent(
    identity_db: Any, engine: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    env = _env(ODP_IDENTITY_DATABASE_URL=identity_db.url())

    assert main([], env) == 0
    first = capsys.readouterr()
    assert main([], env) == 0
    second = capsys.readouterr()

    receipt = json.loads(first.out)
    assert receipt["status"] == STATUS_CREATED and receipt["must_change"] is True
    assert receipt["role"] == "platform_admin" and receipt["audit_event"] == BOOTSTRAP_AUDIT_EVENT
    assert json.loads(second.out)["status"] == STATUS_NOOP
    for stream in (first.out, first.err, second.out, second.err):
        assert SECRET not in stream and "$argon2id$" not in stream


def test_cli_refuses_without_database_and_without_secret(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([], _env()) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "BOOTSTRAP_DATABASE_MISSING"
    assert main([], _env(ODP_IDENTITY_BOOTSTRAP_SECRET="")) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "BOOTSTRAP_INPUT_MISSING"
