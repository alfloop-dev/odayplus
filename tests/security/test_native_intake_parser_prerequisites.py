"""Parser roots retain original deadlines and refuse implicit pooled work."""
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from modules.listing.application.native_intake_parser_input import NativeParserManualInputReader
from modules.listing.application.native_intake_parser_job_proof import NativeParserJobProof
from modules.listing.application.native_intake_parser_policy import (
    SIGNING_KEY_ENV,
    NativeParserConflict,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_parser_prerequisites import (
    NativeParserAdmissionPrerequisites,
    NativeParserCommandBudget,
    NativeParserSQLClock,
    native_parser_run_id,
)
from modules.listing.application.native_intake_parser_runs import CanonicalNativeParserRunRepository
from tests.security.test_native_intake_parser_policy import TENANT
from tests.security.test_native_intake_parser_policy import configured as configured
from tests.security.test_native_parser_artifacts import artifact_case as artifact_case


class RootGuardedJob(dict):
    """Fail if a negative root case reaches any later evidence lookup."""

    def __getitem__(self, key):
        if key != "tenant_id":
            pytest.fail("Root assertion did not reject before parser evidence")
        return super().__getitem__(key)


class RecordingEngine:
    """Recording driver only; these tests execute no PostgreSQL statements."""
    dialect = "postgresql"

    def __init__(self):
        self._local = SimpleNamespace(connection=object())
        self.now, self.calls = datetime.now(UTC), []
        self.remaining_ms, self.settings = 749, []

    def query_one(self, sql, params=()):
        self.calls.append((sql, params))
        if sql == "SELECT clock_timestamp() AS now":
            return {"now": self.now.isoformat()}
        assert "WITH observed AS MATERIALIZED (SELECT clock_timestamp() AS observed_at)" in sql
        assert "set_config('statement_timeout'" in sql and "set_config('lock_timeout'" in sql
        # Canned server result only; the actual SQL expression and conditional
        # GUC effects are checked separately against PostgreSQL.
        value = str(self.remaining_ms) + "ms" if self.remaining_ms >= 1 else None
        if value is not None:
            self.settings.append((value, value))
        return {"remaining_ms": self.remaining_ms, "statement_timeout": value, "lock_timeout": value}


def test_parser_budget_uses_current_sql_clock_without_resetting_original_either_deadline():
    engine = RecordingEngine()
    started = time.monotonic()
    before = NativeParserCommandBudget(NativeParserSQLClock(engine))
    initialized = time.monotonic()
    sql_deadline, monotonic_deadline = before.deadline, before._monotonic_deadline
    assert started + .750 <= monotonic_deadline <= initialized + .750
    before()
    engine.remaining_ms -= 10
    before()
    assert before.deadline == sql_deadline and before._monotonic_deadline == monotonic_deadline
    assert [s for s, _ in engine.calls].count("SELECT clock_timestamp() AS now") == 1
    assert [p for s, p in engine.calls if "set_config" in s] == [(sql_deadline.isoformat(),)] * 2
    settings = engine.settings
    assert len(settings) == 2 and all(p[0] == p[1] for p in settings)
    values = [int(p[0][:-2]) for p in settings]
    assert 1 <= values[1] < values[0] <= 749


def test_fresh_sql_clock_expiry_never_writes_timeouts_or_starts_another_root():
    engine = RecordingEngine()
    before = NativeParserCommandBudget(NativeParserSQLClock(engine))
    original = before.deadline, before._monotonic_deadline
    engine.remaining_ms = -1251
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        before()
    assert (before.deadline, before._monotonic_deadline) == original
    assert len(engine.calls) == 2 and engine.settings == []


def test_expired_monotonic_root_retains_parser_error_family_before_another_sql_read():
    engine = RecordingEngine()
    before = NativeParserCommandBudget(NativeParserSQLClock(engine))
    # Explicitly expire this synthetic object's bound; no shared clock patch
    # or extension of any real command/root deadline is involved.
    before._monotonic_deadline = time.monotonic() - 1
    calls = list(engine.calls)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        before()
    assert engine.calls == calls


def test_manual_reader_preserves_original_parser_root_deadline_error_identity():
    engine = RecordingEngine()
    reader = NativeParserManualInputReader(SimpleNamespace(engine=engine, queue=object()))
    intake = {"tenant_id": str(uuid4()), "intake_id": str(uuid4()), "processing_state": "PARSING",
        "intake_method": "URL", "version": 2, "deleted_at": None, "resolved_listing_id": None, "completed_at": None}
    original = NativeParserConflict("NATIVE_PARSER_ROOT_DEADLINE_EXPIRED")

    def expired():
        raise original

    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$") as caught:
        reader.read(intake, before=expired)
    assert caught.value is original and engine.calls == []


@pytest.mark.parametrize("method", ["admission", "acceptance", "get"])
def test_parser_owners_preserve_expired_original_budget_without_another_sql_read(method):
    engine = RecordingEngine()
    graph = SimpleNamespace(engine=engine, queue=object())
    before = NativeParserCommandBudget(NativeParserSQLClock(engine))
    original_deadline = before.deadline
    before._monotonic_deadline = time.monotonic() - 1
    calls = list(engine.calls)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        if method == "get":
            CanonicalNativeParserRunRepository(engine).get(str(uuid4()), str(uuid4()), before=before)
        else:
            getattr(NativeParserJobProof(graph), method)(RootGuardedJob(tenant_id=str(uuid4())), {}, before=before)
    assert engine.calls == calls and before.deadline == original_deadline


@pytest.mark.parametrize("method", ["admission", "acceptance", "get"])
@pytest.mark.parametrize("change", ["foreign_budget", "invalid_budget", "non_postgres", "no_local", "no_connection"])
def test_parser_owners_refuse_unowned_roots_before_any_sql_read(method, change):
    engine = RecordingEngine()
    graph = SimpleNamespace(engine=engine, queue=object())
    before = NativeParserCommandBudget(NativeParserSQLClock(engine))
    if change == "foreign_budget":
        before = NativeParserCommandBudget(NativeParserSQLClock(RecordingEngine()))
    elif change == "invalid_budget":
        def before():
            pytest.fail("Invalid budget reached SQL")
    elif change == "non_postgres":
        engine.dialect = "sqlite"
    elif change == "no_local":
        engine._local = None
    else:
        engine._local.connection = None
    calls = list(engine.calls)
    error = "NATIVE_PARSER_RUN_CAS_REJECTED" if method == "get" else "NATIVE_PARSER_ADMISSION_PROOF_REJECTED"
    with pytest.raises(NativeParserConflict, match="^" + error + "$"):
        if method == "get":
            CanonicalNativeParserRunRepository(engine).get(str(uuid4()), str(uuid4()), before=before)
        else:
            getattr(NativeParserJobProof(graph), method)(RootGuardedJob(tenant_id=str(uuid4())), {}, before=before)
    assert engine.calls == calls


@pytest.mark.parametrize("change", ["non_postgres", "no_local", "no_connection"])
def test_missing_owned_sql_root_does_not_checkout_or_query_a_pool(change):
    engine = RecordingEngine()
    if change == "non_postgres":
        engine.dialect = "sqlite"
    elif change == "no_local":
        engine._local = None
    else:
        engine._local.connection = None
    with pytest.raises(NativeParserUnavailable, match="^NATIVE_PARSER_CURRENT_SQL_ROOT_REQUIRED$"):
        NativeParserSQLClock(engine)._clock()
    with pytest.raises(NativeParserUnavailable, match="^NATIVE_PARSER_CURRENT_SQL_ROOT_REQUIRED$"):
        NativeParserSQLClock(engine)._admission_budget(datetime.now(UTC) + timedelta(seconds=1))
    assert engine.calls == []


def test_pre_admission_refuses_an_unowned_root_before_policy_or_catalog_reads(artifact_case):
    engine = RecordingEngine()
    engine._local.connection = None
    graph = SimpleNamespace(engine=engine, queue=object())
    registry = artifact_case[0]
    prerequisites = NativeParserAdmissionPrerequisites(graph, registry)
    with pytest.raises(NativeParserUnavailable, match="^NATIVE_PARSER_CURRENT_SQL_ROOT_REQUIRED$"):
        prerequisites.verify(None, None, generation=1, before=lambda: pytest.fail("unowned callback"))
    assert engine.calls == []


def test_missing_signing_capability_rejects_before_any_lineage_or_sql_boundary(configured, artifact_case, monkeypatch):
    engine = RecordingEngine()
    graph = SimpleNamespace(engine=engine, queue=object())
    prerequisites = NativeParserAdmissionPrerequisites(graph, artifact_case[0])
    monkeypatch.delenv(SIGNING_KEY_ENV)
    with pytest.raises(NativeParserUnavailable, match="^NATIVE_PARSER_SIGNING_KEY_UNAVAILABLE$"):
        prerequisites.verify({"tenant_id": TENANT}, str(uuid4()), generation=1,
            before=lambda: pytest.fail("Missing capability reached a SQL boundary"))
    assert engine.calls == []


def test_same_canonical_run_identity_never_aliases_other_tenant_snapshot_or_release():
    tenant, snapshot, release = (str(uuid4()) for _ in range(3))
    run = native_parser_run_id(tenant, snapshot, release)
    assert native_parser_run_id(tenant, snapshot, release) == run
    others = {native_parser_run_id(str(uuid4()), snapshot, release),
        native_parser_run_id(tenant, str(uuid4()), release), native_parser_run_id(tenant, snapshot, str(uuid4()))}
    assert run not in others and len(others) == 3
