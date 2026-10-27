"""Current synthetic identity grants, borrowed SQL roots and real rollback."""

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from modules.listing.application.native_intake_requester_authority import (
    NativeIntakeRequesterAuthorityUnavailable,
    _ReadConnection,
    current_native_recheck_principal,
)
from shared.auth import Role
from tests.integration.test_assisted_listing_identity import canonical_graph as canonical_graph

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def authority(canonical_graph):
    graph, ids, _, db = canonical_graph
    with db.connect() as conn:
        conn.execute((Path(__file__).resolve().parents[2] /
                      "infra/db/migrations/000011_identity_schema.sql").read_text())
        conn.execute("INSERT INTO identity.accounts (account_id,tenant_id,username,email,status,created_by) "
                     "VALUES (%s,%s,'synthetic-requester','synthetic@example.invalid','active','synthetic-fixture')",
                     (ids["actor"], ids["tenant"]))
        conn.execute("INSERT INTO identity.account_roles (account_id,role,granted_by) "
                     "VALUES (%s,'data_owner','synthetic-fixture')", (ids["actor"],))
        conn.execute("INSERT INTO identity.account_scopes (account_id,modules,clearance) "
                     "VALUES (%s,'[\"listing\"]'::jsonb,'CONFIDENTIAL')", (ids["actor"],))
    with graph.transaction(ids["tenant"]):
        intake = graph._one("SELECT * FROM intake.intakes WHERE intake_id=? AND tenant_id=?",
                            (ids["intake"], ids["tenant"]))
    return graph, ids, db, intake


def resolve(case, *, before=lambda: None):
    graph, ids, _, intake = case
    return current_native_recheck_principal(graph.engine, ids["actor"], intake, before=before)


def test_identity_reads_preserve_owned_connection_and_outer_rollback(authority):
    graph, ids, db, _ = authority

    class RollBackSyntheticRoot(Exception):
        pass

    with pytest.raises(RollBackSyntheticRoot):
        with graph.transaction(ids["tenant"]):
            connection = graph.engine._local.connection
            transaction = graph.engine.query_one("SELECT txid_current() AS tx")
            graph.engine.execute("UPDATE intake.intakes SET version=version+1 WHERE intake_id=?", (ids["intake"],))
            principal = resolve(authority)
            assert principal.subject_id == ids["actor"] and principal.tenant_id == ids["tenant"]
            assert principal.roles == frozenset({Role.DATA_OWNER})
            assert principal.scope.modules == frozenset({"listing"})
            assert graph.engine._local.connection is connection and not connection.closed
            assert graph.engine.query_one("SELECT txid_current() AS tx") == transaction
            assert graph.engine.query_one("SELECT version FROM intake.intakes WHERE intake_id=?", (ids["intake"],))["version"] == 18
            with db.connect() as observer:
                assert observer.execute("SELECT version FROM intake.intakes WHERE intake_id=%s", (ids["intake"],)).fetchone()[0] == 17
            raise RollBackSyntheticRoot
    with db.connect() as observer:
        assert observer.execute("SELECT version FROM intake.intakes WHERE intake_id=%s", (ids["intake"],)).fetchone()[0] == 17


@pytest.mark.parametrize("sql", [
    "UPDATE identity.accounts SET status='disabled'",
    "UPDATE identity.accounts SET status='locked'",
    "UPDATE identity.accounts SET status='invited'",
    "UPDATE identity.accounts SET tenant_id=gen_random_uuid()",
    "DELETE FROM identity.accounts",
    "DELETE FROM identity.account_roles",
    "UPDATE identity.account_roles SET role='platform_admin'",
    "UPDATE identity.account_roles SET role='site_reviewer'",
    "DELETE FROM identity.account_scopes",
    "UPDATE identity.account_scopes SET modules='[\"priceops\"]'::jsonb",
    "UPDATE identity.account_scopes SET clearance='PUBLIC'",
    "UPDATE identity.account_scopes SET clearance='invalid'",
    "UPDATE identity.account_scopes SET brand_ids='[\"other-brand\"]'::jsonb",
    "UPDATE identity.account_scopes SET region_ids='[\"other-region\"]'::jsonb",
    "UPDATE identity.account_scopes SET store_ids='[\"other-store\"]'::jsonb",
    "UPDATE identity.account_scopes SET assigned_area_ids='[\"other-area\"]'::jsonb",
    "UPDATE identity.account_scopes SET heat_zone_ids='[\"other-zone\"]'::jsonb",
    "UPDATE identity.account_scopes SET brand_ids='{}'::jsonb",
    "UPDATE identity.account_scopes SET region_ids='null'::jsonb",
    "UPDATE identity.account_scopes SET store_ids='[null]'::jsonb",
    "UPDATE identity.account_scopes SET assigned_area_ids='[7]'::jsonb",
    "UPDATE identity.account_scopes SET heat_zone_ids='[\"  \"]'::jsonb",
    "UPDATE identity.account_scopes SET modules='[true]'::jsonb",
])
def test_committed_revocation_and_missing_or_malformed_grants_fail_closed(authority, sql):
    graph, ids, db, _ = authority
    with graph.transaction(ids["tenant"]):
        assert resolve(authority).has_role(Role.DATA_OWNER)
    with db.connect() as conn:
        conn.execute(sql)
    # Every accepted read starts a new current root; the previous principal
    # and its once-valid grant cannot be reused as command authority.
    with graph.transaction(ids["tenant"]):
        with pytest.raises((PermissionError, HTTPException)):
            resolve(authority)


def test_adapter_requires_current_serializable_tenant_root(authority):
    graph, ids, _, _ = authority
    with pytest.raises(NativeIntakeRequesterAuthorityUnavailable, match="CURRENT_TRANSACTION_REQUIRED"):
        resolve(authority)
    with graph.engine.transaction():
        with pytest.raises(NativeIntakeRequesterAuthorityUnavailable, match="SERIALIZABLE_REQUIRED"):
            resolve(authority)
    with graph.transaction(str(uuid4())):
        with pytest.raises(PermissionError, match="TRANSACTION_TENANT_CONFLICT"):
            resolve(authority)
    with graph.transaction(ids["tenant"]):
        assert resolve(authority).has_role(Role.DATA_OWNER)


@pytest.mark.parametrize("sql", [
    "UPDATE identity.accounts SET status='active'",
    "SELECT set_config('app.tenant_id','foreign',true)",
    "SELECT nextval('synthetic_sequence')",
    "SELECT role FROM identity.account_roles WHERE account_id = %s; DELETE FROM identity.accounts",
])
def test_borrowed_proxy_cannot_execute_writes_or_arbitrary_selects(authority, sql):
    graph, ids, _, _ = authority
    with graph.transaction(ids["tenant"]):
        proxy = _ReadConnection(graph.engine._local.connection, lambda: None)
        assert all(not hasattr(proxy, name) for name in ("commit", "rollback", "close", "__enter__"))
        with proxy.cursor() as cursor:
            with pytest.raises(NativeIntakeRequesterAuthorityUnavailable, match="READ_ONLY_REQUIRED"):
                cursor.execute(sql, ())
        assert resolve(authority).has_role(Role.DATA_OWNER)


def test_expired_read_budget_aborts_without_releasing_or_committing_root(authority):
    graph, ids, db, _ = authority

    class ExpiredSyntheticBudget(Exception):
        pass

    def expired():
        raise ExpiredSyntheticBudget

    with pytest.raises(ExpiredSyntheticBudget):
        with graph.transaction(ids["tenant"]):
            graph.engine.execute("UPDATE intake.intakes SET version=99 WHERE intake_id=?", (ids["intake"],))
            resolve(authority, before=expired)
    with db.connect() as conn:
        assert conn.execute("SELECT version FROM intake.intakes WHERE intake_id=%s", (ids["intake"],)).fetchone()[0] == 17
