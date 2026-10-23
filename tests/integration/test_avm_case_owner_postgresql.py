"""Two genuine PostgreSQL engines; original native AVM reads after row lock.

The numerical fixture uses declared POC valuation. This proves SQL concurrency,
not production model readiness, external archive delivery or human approval.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest

from modules.avm.application.valuation import AVMService
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableAVMRepository
from tests.integration.test_avm_valuation import _valuation_payload
from tests.integration.test_intake_registry_calendar_postgresql import _bundle, _upgrade

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def owners(intake_blank_db, monkeypatch, tmp_path):
    _upgrade(intake_blank_db, "head")
    apply_upgrade_to_database(intake_blank_db.url())
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", (tmp_path / "owned-avm-worm").as_uri())
    first = _bundle(intake_blank_db, monkeypatch)
    second = None
    try:
        second = _bundle(intake_blank_db, monkeypatch)
        assert first.engine is not second.engine
        assert first.engine.dialect == second.engine.dialect == "postgresql"
        # Both actual PG bundles are composed before switching only the
        # synthetic valuation model inputs back to this engineering POC lane.
        # _bundle enables live-data mode; storage and lock owners stay genuine.
        monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
        monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
        monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
        tenant = str(uuid4())
        repositories = [DurableAVMRepository(TenantScopedDocumentStore(PostgresDocumentStore(bundle.engine), tenant)) for bundle in (first, second)]
        services = [AVMService(repository=repo, runtime_mode="poc") for repo in repositories]
        case = services[0].create_case(_valuation_payload(), created_by="declared-owner-author", correlation_id="owned-PG-synthetic-create")
        services[0].normalize(case.case_id, actor="declared-owner-author", correlation_id="owned-PG-normalize")
        services[0].value(case.case_id, actor="declared-owner-author", correlation_id="owned-PG-value")
        yield intake_blank_db, first, second, repositories, services, case.case_id
    finally:
        if second is not None:
            second.engine.close()
        first.engine.close()


def _wait_for_owned_backend_lock(database, pid):
    deadline = monotonic() + 5
    with database.connect() as connection:
        while monotonic() < deadline:
            row = connection.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()
            if row is not None and row[0] == "Lock":
                return True
            sleep(0.01)
    return False


@pytest.mark.parametrize("operation", ["approve", "export"])
def test_native_AVM_first_read_waits_and_keeps_latest_report_or_all_export_entries(owners, monkeypatch, operation):
    database, first, second, (first_repo, second_repo), (first_native, second_native), identity = owners
    before_report = first_repo.latest_report(identity)
    if operation == "export":
        first_native.approve_finance(identity, actor="declared-second-finance", reason="Original recorded engineering finance approval", correlation_id="owned-PG-approval")
        first_native.build_dataroom(identity, actor="declared-second-finance", correlation_id="owned-PG-room")
    started, actual_read = Event(), Event()
    observed = {}
    original_get = second_repo.get_case

    def observe_real_case_read(case_id):
        actual_read.set()
        return original_get(case_id)

    monkeypatch.setattr(second_repo, "get_case", observe_real_case_read)

    def native_command():
        with second.engine.transaction():
            observed["pid"] = second.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
            started.set()
            if operation == "approve":
                return second_native.approve_finance(identity, actor="declared-second-finance", reason="Read actual post-wait report", correlation_id="owned-PG-waiting-approval")
            return second_native.export_dataroom(identity, actor="declared-second-finance", reason="Second actual export", correlation_id="owned-PG-waiting-export")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with first_repo.case_transaction(identity):
            if operation == "approve":
                current = first_repo.get_case(identity)
                first_repo.save_case(replace(current, valuation_input=replace(current.valuation_input, forecast_gm_next_12m=1_400_000)))
                changed_report = first_native.value(identity, actor="declared-owner-author", correlation_id="owned-PG-new-report")
                assert changed_report.valuation_version == before_report.valuation_version + 1
            else:
                first_export = first_native.export_dataroom(identity, actor="declared-second-finance", reason="First actual export", correlation_id="owned-PG-first-export")
            pending = executor.submit(native_command)
            assert started.wait(5)
            assert _wait_for_owned_backend_lock(database, observed["pid"])
            assert not actual_read.is_set(), "native AVM read before the real owner row lock"
        result = pending.result(timeout=20)
    assert actual_read.is_set()
    if operation == "approve":
        assert result.report_id == changed_report.report_id
        assert result.valuation_version == changed_report.valuation_version
        assert result.finance_approval.actor_id == "declared-second-finance"
        assert first_repo.latest_report(identity) == result
    else:
        assert len(result.export_audit) == 2
        assert result.export_audit[0] == first_export.export_audit[0]
        assert result.export_audit[0]["export_id"] != result.export_audit[1]["export_id"]
        assert first_repo.get_dataroom(identity) == result
