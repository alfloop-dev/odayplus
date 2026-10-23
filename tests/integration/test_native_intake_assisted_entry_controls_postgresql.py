"""Real current-human SQL roots and shared cross-purpose storage capacity."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from modules.listing.application.native_intake_requester_authority import (
    NativeIntakeRequesterAuthorityUnavailable,
    current_native_assisted_principal,
)
from modules.listing.application.native_intake_retrieval_policy import NativeIntakeRetrievalConflict
from modules.listing.application.native_intake_retrieval_quota import (
    NativeIntakeRetrievalQuota,
    NativeRetrievalQuotaExceeded,
)
from shared.auth import Role
from tests.integration.test_assisted_listing_identity import canonical_graph as canonical_graph
from tests.integration.test_native_intake_requester_authority_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_retrieval_controls_postgresql import (
    quota_case as quota_case,
)

pytestmark = pytest.mark.requires_live_env


def current(case):
    graph, ids, _, intake = case
    return current_native_assisted_principal(graph.engine, ids["actor"], intake,
        risk_summary="Identity proposals remain unapplied pending review", risk_acknowledged=True,
        before=lambda: None)


def test_current_staff_needs_no_added_clearance_and_identity_reads_cannot_commit_outer_root(authority):
    graph, ids, db, _ = authority
    with db.connect() as conn:
        conn.execute("UPDATE identity.account_roles SET role='expansion_user'")
        conn.execute("UPDATE identity.account_scopes SET clearance='PUBLIC'")

    class AbortHumanCommand(Exception):
        pass

    with pytest.raises(AbortHumanCommand):
        with graph.transaction(ids["tenant"]):
            connection = graph.engine._local.connection
            tx = graph.engine.query_one("SELECT txid_current() AS tx")
            graph.engine.execute("UPDATE intake.intakes SET version=version+1 WHERE intake_id=?", (ids["intake"],))
            principal = current(authority)
            assert principal.subject_id == ids["actor"] and principal.has_role(Role.EXPANSION_USER)
            assert graph.engine._local.connection is connection and not connection.closed
            assert graph.engine.query_one("SELECT txid_current() AS tx") == tx
            with db.connect() as observer:
                assert observer.execute("SELECT version FROM intake.intakes WHERE intake_id=%s", (ids["intake"],)).fetchone()[0] == 17
            raise AbortHumanCommand
    with db.connect() as observer:
        assert observer.execute("SELECT version FROM intake.intakes WHERE intake_id=%s", (ids["intake"],)).fetchone()[0] == 17


@pytest.mark.parametrize("role", ["site_reviewer", "executive", "data_owner"])
def test_permitted_non_submitter_is_the_actual_initiating_human(authority, role):
    graph, ids, db, intake = authority
    original = str(uuid4())
    with db.connect() as conn:
        conn.execute("UPDATE identity.account_roles SET role=%s", (role,))
        conn.execute("UPDATE intake.intakes SET submitter_subject_id=%s WHERE intake_id=%s", (original, ids["intake"]))
    intake["submitter_subject_id"] = original
    with graph.transaction(ids["tenant"]):
        principal = current(authority)
        assert principal.subject_id == ids["actor"] != original


def test_staff_cannot_borrow_another_original_submitters_command(authority):
    graph, ids, db, intake = authority
    original = str(uuid4())
    with db.connect() as conn:
        conn.execute("UPDATE identity.account_roles SET role='expansion_user'")
        conn.execute("UPDATE intake.intakes SET submitter_subject_id=%s WHERE intake_id=%s", (original, ids["intake"]))
    intake["submitter_subject_id"] = original
    with graph.transaction(ids["tenant"]):
        with pytest.raises(HTTPException) as error:
            current(authority)
    assert error.value.status_code == 403


@pytest.mark.parametrize("sql", [
    "UPDATE identity.accounts SET status='disabled'",
    "UPDATE identity.accounts SET tenant_id=gen_random_uuid()",
    "DELETE FROM identity.account_roles", "UPDATE identity.account_roles SET role='platform_admin'",
    "DELETE FROM identity.account_scopes", "UPDATE identity.account_scopes SET modules='[\"priceops\"]'::jsonb",
    "UPDATE identity.account_scopes SET brand_ids='[\"foreign\"]'::jsonb",
    "UPDATE identity.account_scopes SET region_ids='[\"foreign\"]'::jsonb",
    "UPDATE identity.account_scopes SET store_ids='[\"foreign\"]'::jsonb",
    "UPDATE identity.account_scopes SET assigned_area_ids='[\"foreign\"]'::jsonb",
    "UPDATE identity.account_scopes SET heat_zone_ids='[\"foreign\"]'::jsonb",
])
def test_current_human_revocation_is_not_replaced_by_prior_role_or_scope(authority, sql):
    graph, ids, db, _ = authority
    with graph.transaction(ids["tenant"]):
        assert current(authority).subject_id == ids["actor"]
    with db.connect() as conn:
        conn.execute(sql)
    with graph.transaction(ids["tenant"]):
        with pytest.raises((HTTPException, PermissionError)):
            current(authority)


def test_current_human_requires_current_serializable_tenant_root(authority):
    graph, ids, _, _ = authority
    with pytest.raises(NativeIntakeRequesterAuthorityUnavailable, match="CURRENT_TRANSACTION_REQUIRED"):
        current(authority)
    with graph.engine.transaction():
        with pytest.raises(NativeIntakeRequesterAuthorityUnavailable, match="SERIALIZABLE_REQUIRED"):
            current(authority)
    with graph.transaction(str(uuid4())):
        with pytest.raises(PermissionError, match="TRANSACTION_TENANT_CONFLICT"):
            current(authority)
    with graph.transaction(ids["tenant"]):
        assert current(authority).subject_id == ids["actor"]


def storage(case, *, tenant=None, source_id=None, limit=2, source_limit=2):
    graph, ids, _, source = case
    with graph.transaction(tenant or ids["tenant"]):
        return NativeIntakeRetrievalQuota(graph.engine).reserve_storage(source_id or source["source_id"],
            global_limit=limit, source_limit=source_limit, before=lambda: None)


def http(case, *, tenant=None, limit=2):
    graph, ids, _, source = case
    with graph.transaction(tenant or ids["tenant"]):
        return NativeIntakeRetrievalQuota(graph.engine).reserve(source, global_limit=limit,
            charge_rate=True, before=lambda: None)


def test_real_manual_only_source_with_zero_http_budgets_can_reserve_only_storage(quota_case):
    graph, ids, db, source = quota_case
    with db.connect() as conn:
        conn.execute("UPDATE intake.source_registry SET retrieval_mode='ASSISTED_ENTRY_ONLY',"
            "requests_per_minute=0,concurrent_requests=0 WHERE source_id=%s", (source["source_id"],))
    source.update(requests_per_minute=0, concurrent_requests=0)
    reservation = storage(quota_case)
    assert reservation.rate_reservation_id is None
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT count(*) AS n FROM intake.native_retrieval_rate_reservations")["n"] == 0
        slot = graph._one("SELECT * FROM intake.native_retrieval_inflight_slots WHERE reservation_id=?", (reservation.reservation_id,))
        assert set(slot) == {"reservation_id", "source_id", "admitted_at", "expires_at"}
        assert 11.9 <= (datetime.fromisoformat(slot["expires_at"]) - datetime.fromisoformat(slot["admitted_at"])).total_seconds() <= 12.1
    # The existing retrieval interface still rejects those same zero budgets.
    with pytest.raises(NativeIntakeRetrievalConflict, match="QUOTA_LIMITS_REJECTED"):
        http(quota_case)


@pytest.mark.parametrize("storage_first", [True, False])
def test_both_purposes_count_all_tenants_in_the_same_global_pool(quota_case, storage_first):
    if storage_first:
        storage(quota_case)
        http(quota_case, tenant=str(uuid4()))
    else:
        http(quota_case)
        storage(quota_case, tenant=str(uuid4()))
    with pytest.raises(NativeRetrievalQuotaExceeded, match="CONCURRENCY_FULL"):
        storage(quota_case, tenant=str(uuid4()), source_id="different-manual-source")
    with pytest.raises(NativeRetrievalQuotaExceeded, match="CONCURRENCY_FULL"):
        http(quota_case, tenant=str(uuid4()))


def test_unreaped_storage_retains_slot_and_release_does_not_erase_http_charge(quota_case):
    graph, ids, _, _ = quota_case
    http(quota_case)
    reservation = storage(quota_case)
    with graph.transaction(ids["tenant"]):
        quota = NativeIntakeRetrievalQuota(graph.engine)
        with pytest.raises(NativeIntakeRetrievalConflict, match="REAP_PROOF_REQUIRED"):
            quota.release(reservation, process_reaped=False, before=lambda: None)
        assert graph.engine.query_one("SELECT count(*) AS n FROM intake.native_retrieval_inflight_slots")["n"] == 2
        quota.release(reservation, process_reaped=True, before=lambda: None)
        assert graph.engine.query_one("SELECT count(*) AS n FROM intake.native_retrieval_inflight_slots")["n"] == 1
        assert graph.engine.query_one("SELECT count(*) AS n FROM intake.native_retrieval_rate_reservations")["n"] == 1


@pytest.mark.parametrize("limit,source_limit", [(0, 1), (101, 1), (True, 1), (2, 0), (2, True), (1, 2)])
def test_storage_config_is_strict_and_bounded_before_reservation(quota_case, limit, source_limit):
    graph, ids, _, _ = quota_case
    with pytest.raises(NativeIntakeRetrievalConflict, match="STORAGE_LIMITS_REJECTED"):
        storage(quota_case, limit=limit, source_limit=source_limit)
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT count(*) AS n FROM intake.native_retrieval_inflight_slots")["n"] == 0


@pytest.mark.parametrize("sql", [
    "ALTER TABLE intake.native_retrieval_inflight_slots ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE intake.native_retrieval_inflight_slots ADD COLUMN tenant_id uuid",
    "ALTER TABLE intake.native_retrieval_inflight_slots ADD FOREIGN KEY (reservation_id) REFERENCES workflow.jobs(job_id)",
])
def test_storage_refuses_changed_global_shape_or_business_reverse_link(quota_case, sql):
    _, _, db, _ = quota_case
    with db.connect() as conn:
        conn.execute(sql)
    with pytest.raises(NativeIntakeRetrievalConflict, match="GLOBAL_QUOTA"):
        storage(quota_case)


def test_racing_manual_and_http_tenants_cannot_overbook_one_slot(quota_case):
    barrier = threading.Barrier(2)

    def attempt(manual):
        barrier.wait(timeout=3)
        try:
            if manual:
                return storage(quota_case, tenant=str(uuid4()), limit=1, source_limit=1)
            return http(quota_case, tenant=str(uuid4()), limit=1)
        except Exception as error:
            assert isinstance(error, NativeRetrievalQuotaExceeded) or getattr(error, "sqlstate", None) in {"40001", "40P01"}
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [True, False]))
    assert sum(r is not None for r in results) == 1
    graph, ids, _, _ = quota_case
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT count(*) AS n FROM intake.native_retrieval_inflight_slots")["n"] == 1
