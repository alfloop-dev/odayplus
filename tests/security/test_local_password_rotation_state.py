"""Affirmative credential evidence, separate from original acceptance cohorts."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.api.oday_api.security.dependencies import _enforce_password_rotation
from shared.auth import Principal
from shared.identity.store import InMemoryIdentityStore, SqlIdentityStore


def principal(provider="local_password"):
    return Principal(subject_id=str(uuid4()), attributes={"provider": provider})


@pytest.mark.parametrize("value", [None, "", "false", "true", 0, 1, [], {}, object()])
def test_malformed_checker_result_never_authorizes(value):
    boundary = SimpleNamespace(_identity_store=SimpleNamespace(password_must_change=lambda _: value))
    with pytest.raises(HTTPException) as error:
        _enforce_password_rotation(principal(), boundary)
    assert (error.value.status_code, error.value.detail) == (503, "IDENTITY_STORE_UNAVAILABLE")


@pytest.mark.parametrize("store", [None, object(), SimpleNamespace(password_must_change=False)])
def test_missing_supported_checker_refuses(store):
    with pytest.raises(HTTPException) as error:
        _enforce_password_rotation(principal(), SimpleNamespace(_identity_store=store))
    assert error.value.status_code == 503


def test_unavailable_checker_refuses():
    def unavailable(_):
        raise RuntimeError("test-owned unavailable storage")
    with pytest.raises(HTTPException) as error:
        _enforce_password_rotation(principal(), SimpleNamespace(
            _identity_store=SimpleNamespace(password_must_change=unavailable)))
    assert error.value.status_code == 503


def test_explicit_supported_memory_state_and_verified_denial_actor():
    actor = principal()
    store = InMemoryIdentityStore()
    boundary = SimpleNamespace(_identity_store=store)
    with pytest.raises(HTTPException) as error:
        _enforce_password_rotation(actor, boundary)
    assert error.value.status_code == 503
    store.set_password_must_change(actor.subject_id, True)
    with pytest.raises(HTTPException) as error:
        _enforce_password_rotation(actor, boundary)
    assert error.value.status_code == 403
    assert error.value.detail == "PASSWORD_CHANGE_REQUIRED"
    assert error.value._verified_principal is actor
    store.set_password_must_change(actor.subject_id, False)
    assert _enforce_password_rotation(actor, boundary) is None
    with pytest.raises(ValueError):
        store.set_password_must_change(actor.subject_id, "false")


def test_oidc_does_not_require_local_password_evidence():
    assert _enforce_password_rotation(principal("oidc"), object()) is None


class Cursor:
    def __init__(self, row):
        self.row = row
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, sql, params):
        assert "identity.password_credentials" in sql
    def fetchone(self):
        return self.row


@contextmanager
def connection(row):
    yield SimpleNamespace(cursor=lambda: Cursor(row))


@pytest.mark.parametrize("value,expected", [(False, False), (True, True), (0, False), (1, True)])
@pytest.mark.parametrize("mapping", [False, True])
def test_sql_native_and_supported_sqlite_boolean_encodings(value, expected, mapping):
    row = {"must_change": value} if mapping else (value,)
    store = SqlIdentityStore(connection_factory=lambda: connection(row))
    assert store.password_must_change(uuid4()) is expected


@pytest.mark.parametrize("row", [None, (), (None,), ("false",), ("0",), (2,), (0.0,), ({},)])
def test_sql_missing_or_malformed_credential_refuses(row):
    store = SqlIdentityStore(connection_factory=lambda: connection(row))
    with pytest.raises((ValueError, IndexError, KeyError)):
        store.password_must_change(uuid4())


def test_sql_unavailable_connection_is_not_rotated():
    @contextmanager
    def unavailable():
        yield None
    with pytest.raises(RuntimeError):
        SqlIdentityStore(connection_factory=unavailable).password_must_change(uuid4())
