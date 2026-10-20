"""Mock transport tests for invocation-local assisted-entry history rows.

These fixtures are lookup data, not canonical Jobs, Intakes, or historical
proofs. No SQL executes here. Loading/consuming this data proves neither real
admission, authorization, performance, nor child-process reaping. The original
proof callers still own all business predicates and must reject a None lookup.
Product imports and the real parser budget/clock classes are not stubbed.
"""

from copy import deepcopy
from datetime import UTC, datetime
from itertools import combinations
from time import monotonic
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from modules.listing.application.native_intake_assisted_entry_history_rows import (
    read_success_history_rows,
)
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import (
    NativeParserCommandBudget,
    NativeParserSQLClock,
)
from shared.jobs.queue import JobFenceRejectedError


def _identifier(label):
    return str(uuid5(NAMESPACE_URL, "history-row-transport-only/" + label))


TENANT = _identifier("tenant")
FOREIGN_TENANT = _identifier("foreign-tenant")
REQUEST = _identifier("request")
AUDIT_IDS = {
    "request": str(uuid5(UUID(REQUEST), "native-assisted-entry-request-audit-v1")),
    "admission": _identifier("admission-audit"),
    "acceptance": _identifier("acceptance-audit"),
    "completion": _identifier("completion-audit"),
}
EVENT_IDS = {
    "request": REQUEST,
    "state": _identifier("state-event"),
    "snapshot": _identifier("snapshot-event"),
}
ROW_ROLES = (
    *(("audit", role) for role in AUDIT_IDS),
    *(("event", role) for role in EVENT_IDS),
)
CONSUMPTION = (
    ("root", "settings"),
    ("event", "request"),
    ("audit", "request"),
    ("audit", "admission"),
    ("audit", "acceptance"),
    ("audit", "completion"),
    ("reason", "request"),
    ("event", "state"),
    ("event", "snapshot"),
)


class MockTransportEngine:
    """Record calls and return adversarial data without evaluating SQL.

    In particular, do not filter by the supplied tenant or IDs: the actual
    loader must reject extra/foreign rows even when this double returns them.
    Budget responses are canned, not evidence of PostgreSQL timeout behavior.
    """

    dialect = "postgresql"

    def __init__(self):
        self._local = SimpleNamespace(connection=object())
        self.calls = []
        self.after_read = None
        self.remaining_ms = 500
        self.settings = {"isolation": "serializable", "tenant": TENANT}
        self.rows = {
            "audits": [
                {"tenant_id": TENANT, "audit_event_id": identifier,
                 "reason": "transport-only " + role}
                for role, identifier in AUDIT_IDS.items()
            ],
            "events": [
                {"tenant_id": TENANT, "event_id": identifier,
                 "payload": {"transport_only": role}}
                for role, identifier in EVENT_IDS.items()
            ],
        }

    def _respond(self, kind, params, value):
        self.calls.append((kind, deepcopy(params)))
        if self.after_read is not None:
            self.after_read(kind)
        return deepcopy(value)

    def query_one(self, sql, params=()):
        # Route by purpose; assertions below concern outcomes and parameters,
        # not the implementation's exact SQL spelling or source text.
        if "remaining_ms" in sql:
            return self._respond("budget", params, {"remaining_ms": self.remaining_ms})
        if "clock_timestamp" in sql:
            return self._respond("clock", params, {"now": datetime(2026, 10, 18, tzinfo=UTC)})
        if "current_setting" in sql:
            return self._respond("settings", params, self.settings)
        pytest.fail("Transport attempted an undeclared single-row query")

    def query(self, sql, params=()):
        if "workflow.outbox_events" in sql:
            return self._respond("events", params, self.rows["events"])
        if "audit.audit_events" in sql:
            return self._respond("audits", params, self.rows["audits"])
        pytest.fail("Transport attempted an undeclared row-set query")


class TransportCase:
    def __init__(self):
        self.engine = MockTransportEngine()
        self.graph = SimpleNamespace(engine=self.engine)
        # Deliberately incomplete lookup inputs; never passed to a full proof
        # caller and never represented as a real admitted/successful job.
        self.job = {"tenant_id": TENANT}
        self.payload = {
            "request_id": REQUEST,
            "admission_receipt": {"audit_event_id": AUDIT_IDS["admission"]},
            "acceptance_receipt": {"audit_event_id": AUDIT_IDS["acceptance"]},
        }
        self.result = {
            "audit_event_id": AUDIT_IDS["completion"],
            "outbox_event_id": EVENT_IDS["state"],
            "snapshot_created_event_id": EVENT_IDS["snapshot"],
        }
        self.before = NativeParserCommandBudget(NativeParserSQLClock(self.engine))

    def load(self):
        return read_success_history_rows(
            self.graph, self.job, self.payload, self.result, self.before,
        )

    def set_role_id(self, kind, role, identifier):
        if role == "request":
            self.payload["request_id"] = identifier
        elif kind == "audit" and role in {"admission", "acceptance"}:
            self.payload[role + "_receipt"]["audit_event_id"] = identifier
        elif kind == "audit":
            self.result["audit_event_id"] = identifier
        else:
            key = "outbox_event_id" if role == "state" else "snapshot_created_event_id"
            self.result[key] = identifier


@pytest.fixture
def case():
    return TransportCase()


def _consume(rows, step):
    kind, role = step
    if kind == "root":
        return rows.root(TENANT)
    if kind == "reason":
        return rows.request_reason()
    column, identifiers = ("audit_event_id", AUDIT_IDS) if kind == "audit" else ("event_id", EVENT_IDS)
    return getattr(rows, kind)(role, tenant_id=TENANT, **{column: identifiers[role]})


def _consume_all(rows):
    for step in CONSUMPTION:
        value = _consume(rows, step)
        if step[0] != "root":
            assert value is not None


def _deadlines(before):
    return before.deadline, before._monotonic_deadline


def _expire(before):
    # Only shorten this fixture's original bound; never replace a validator
    # or extend a deadline to make a positive lookup pass.
    before._monotonic_deadline = monotonic() - 1


@pytest.mark.parametrize("callback_kind", ["function", "callable-object", "budget-subclass"])
def test_unfamiliar_callback_falls_back_without_sql_or_callback(case, callback_kind):
    def forbidden():
        pytest.fail("Fallback invoked an unfamiliar callback")

    class UnfamiliarCallback:
        source = case.before.source

        def __call__(self):
            forbidden()

        def check_in_memory(self):
            forbidden()

    class OtherBudget(NativeParserCommandBudget):
        def __call__(self):
            forbidden()

    if callback_kind == "function":
        before = forbidden
    elif callback_kind == "callable-object":
        before = UnfamiliarCallback()
    else:
        before = OtherBudget(case.before.source)
    calls = deepcopy(case.engine.calls)
    # Empty inputs also establish that fallback does not inspect lookup data.
    assert read_success_history_rows(case.graph, {}, {}, {}, before) is None
    assert case.engine.calls == calls


@pytest.mark.parametrize("source_kind", ["duck-clock", "clock-subclass", "foreign-engine"])
def test_unfamiliar_source_falls_back_without_sql(case, source_kind):
    class OtherClock(NativeParserSQLClock):
        pass

    class DuckClock:
        engine = case.engine

        def _clock(self):
            return datetime(2026, 10, 18, tzinfo=UTC)

        def _admission_budget(self, deadline):
            pytest.fail("Fallback invoked an unfamiliar source")

    foreign_engine = MockTransportEngine()
    source = {
        "duck-clock": lambda: DuckClock(),
        "clock-subclass": lambda: OtherClock(case.engine),
        "foreign-engine": lambda: NativeParserSQLClock(foreign_engine),
    }[source_kind]()
    before = NativeParserCommandBudget(source)
    calls, foreign_calls = deepcopy(case.engine.calls), deepcopy(foreign_engine.calls)
    assert read_success_history_rows(case.graph, {}, {}, {}, before) is None
    assert case.engine.calls == calls
    assert foreign_engine.calls == foreign_calls


def test_trusted_budget_loads_exact_unordered_sets_and_consumes_without_sql(case):
    original = _deadlines(case.before)
    case.engine.rows["events"].reverse()
    case.engine.rows["audits"].reverse()
    rows = case.load()
    assert rows is not None
    for kind, identifiers in (("events", EVENT_IDS), ("audits", AUDIT_IDS)):
        requests = [params for purpose, params in case.engine.calls if purpose == kind]
        assert len(requests) == 1
        tenant, requested_ids = requests[0]
        assert tenant == TENANT
        assert len(requested_ids) == len(set(requested_ids)) == len(identifiers)
        assert set(requested_ids) == set(identifiers.values())
    calls = deepcopy(case.engine.calls)
    for step in CONSUMPTION:
        value = _consume(rows, step)
        if step[0] == "audit" or step[0] == "reason":
            assert value["reason"] == "transport-only " + step[1]
        elif step[0] == "event":
            assert value["payload"] == {"transport_only": step[1]}
    rows.finish()
    assert case.engine.calls == calls
    assert _deadlines(case.before) == original
    deadlines = [params for kind, params in calls if kind == "budget"]
    assert deadlines == [(original[0].isoformat(),)] * 3
    assert sum(kind == "clock" for kind, _ in calls) == 1


@pytest.mark.parametrize("setting,value", [
    ("isolation", "read committed"), ("isolation", "repeatable read"),
    ("isolation", None), ("tenant", FOREIGN_TENANT), ("tenant", ""), ("tenant", None),
])
def test_root_setting_mismatch_refuses_before_any_history_rows(case, setting, value):
    case.engine.settings[setting] = value
    with pytest.raises(JobFenceRejectedError):
        case.load()
    assert not any(kind in {"events", "audits"} for kind, _ in case.engine.calls)


@pytest.mark.parametrize("change", ["dialect", "local", "connection"])
def test_missing_sql_root_is_rejected_without_another_engine_call(case, change):
    if change == "dialect":
        case.engine.dialect = "sqlite"
    elif change == "local":
        case.engine._local = None
    else:
        case.engine._local.connection = None
    calls = deepcopy(case.engine.calls)
    with pytest.raises(JobFenceRejectedError):
        case.load()
    assert case.engine.calls == calls


@pytest.mark.parametrize("kind,role", ROW_ROLES)
@pytest.mark.parametrize("damage", ["missing", "duplicate", "extra", "foreign", "replacement", "alias"])
def test_every_role_rejects_incomplete_duplicate_extra_foreign_or_aliased_rows(case, kind, role, damage):
    table, column, identifiers = (
        ("audits", "audit_event_id", AUDIT_IDS) if kind == "audit" else ("events", "event_id", EVENT_IDS)
    )
    data = case.engine.rows[table]
    target = next(row for row in data if row[column] == identifiers[role])
    if damage == "missing":
        data.remove(target)
    elif damage == "duplicate":
        data.append(deepcopy(target))
    elif damage == "extra":
        data.append({**target, column: _identifier("extra-row")})
    elif damage == "foreign":
        target["tenant_id"] = FOREIGN_TENANT
    elif damage == "replacement":
        target[column] = _identifier("unexpected-replacement")
    else:
        # Same cardinality, with one expected role replaced by another role's
        # very same mapping; the mock must not deduplicate on our behalf.
        other = next(row for row in data if row is not target)
        data[data.index(target)] = other
    with pytest.raises(JobFenceRejectedError):
        case.load()


@pytest.mark.parametrize("location", ["tenant", "request", "admission", "acceptance", "completion", "state", "snapshot"])
@pytest.mark.parametrize("bad_id", [
    None, 7, "not-a-uuid", "00000000-0000-0000-0000-000000000000",
    REQUEST.upper(), REQUEST.replace("-", ""), "{" + REQUEST + "}", " " + REQUEST,
])
def test_every_input_identifier_must_be_nonzero_canonical_uuid_before_sql(case, location, bad_id):
    if location == "tenant":
        case.job["tenant_id"] = bad_id
    else:
        kind = "event" if location in {"request", "state", "snapshot"} else "audit"
        case.set_role_id(kind, location, bad_id)
    calls = deepcopy(case.engine.calls)
    with pytest.raises(ValueError):
        case.load()
    assert case.engine.calls == calls


@pytest.mark.parametrize("kind,first,second", [
    *(("audit", first, second) for first, second in combinations(AUDIT_IDS, 2)),
    *(("event", first, second) for first, second in combinations(EVENT_IDS, 2)),
])
def test_distinct_roles_cannot_share_an_identifier_in_the_same_table(case, kind, first, second):
    identifiers = AUDIT_IDS if kind == "audit" else EVENT_IDS
    case.set_role_id(kind, second, identifiers[first])
    calls = deepcopy(case.engine.calls)
    with pytest.raises(JobFenceRejectedError):
        case.load()
    assert case.engine.calls == calls


@pytest.mark.parametrize("step", CONSUMPTION)
def test_each_role_is_consumable_once_only(case, step):
    rows = case.load()
    if step[0] == "reason":
        assert _consume(rows, ("audit", "request")) is not None
    _consume(rows, step)
    calls = deepcopy(case.engine.calls)
    with pytest.raises(JobFenceRejectedError):
        _consume(rows, step)
    assert case.engine.calls == calls


@pytest.mark.parametrize("kind", ["audit", "event"])
@pytest.mark.parametrize("role", ["unknown", "SELECT * FROM audit.audit_events", "root", "reason"])
def test_unknown_roles_never_fall_through_to_generic_sql(case, kind, role):
    rows = case.load()
    calls = deepcopy(case.engine.calls)
    with pytest.raises(JobFenceRejectedError):
        getattr(rows, kind)(role)
    assert case.engine.calls == calls
    _consume_all(rows)
    rows.finish()


@pytest.mark.parametrize("method", ["query", "query_one", "execute"])
def test_transport_exposes_no_generic_query_replay(case, method):
    rows = case.load()
    calls = deepcopy(case.engine.calls)
    with pytest.raises(AttributeError):
        getattr(rows, method)("SELECT * FROM audit.audit_events", ())
    assert case.engine.calls == calls


@pytest.mark.parametrize("kind,role", ROW_ROLES)
def test_mismatched_lookup_returns_none_and_cannot_be_retried(case, kind, role):
    rows = case.load()
    calls = deepcopy(case.engine.calls)
    assert getattr(rows, kind)(role, tenant_id=FOREIGN_TENANT) is None
    # None is a failure for the caller, not permission to retry this role with
    # weaker predicates. Consumption alone never upgrades failed predicates.
    with pytest.raises(JobFenceRejectedError):
        _consume(rows, (kind, role))
    assert case.engine.calls == calls


def test_failed_request_audit_cannot_unlock_its_reason(case):
    rows = case.load()
    assert rows.audit("request", tenant_id=FOREIGN_TENANT) is None
    with pytest.raises(JobFenceRejectedError):
        rows.request_reason()


@pytest.mark.parametrize("kind,role", [("audit", "admission"), ("event", "state")])
def test_finish_rejects_a_failed_lookup_even_with_every_role_consumed(case, kind, role):
    rows = case.load()
    for step in CONSUMPTION:
        if step == (kind, role):
            assert getattr(rows, kind)(role, tenant_id=FOREIGN_TENANT) is None
        else:
            _consume(rows, step)
    with pytest.raises(JobFenceRejectedError):
        rows.finish()


def test_absent_column_cannot_match_an_expected_SQL_null(case):
    rows = case.load()
    assert rows.audit("request", service_principal=None) is None
    with pytest.raises(JobFenceRejectedError):
        rows.request_reason()


def test_foreign_root_cannot_consume_the_tenant_root(case):
    rows = case.load()
    with pytest.raises(JobFenceRejectedError):
        rows.root(FOREIGN_TENANT)
    rows.root(TENANT)


def test_request_reason_requires_the_request_audit_and_is_available_once(case):
    rows = case.load()
    calls = deepcopy(case.engine.calls)
    with pytest.raises(JobFenceRejectedError):
        rows.request_reason()
    _consume(rows, ("event", "request"))
    _consume(rows, ("audit", "admission"))
    with pytest.raises(JobFenceRejectedError):
        rows.request_reason()
    request_audit = _consume(rows, ("audit", "request"))
    assert rows.request_reason() == request_audit
    with pytest.raises(JobFenceRejectedError):
        rows.request_reason()
    assert case.engine.calls == calls


@pytest.mark.parametrize("omitted", CONSUMPTION)
def test_finish_refuses_each_incomplete_consumption_set(case, omitted):
    rows = case.load()
    calls = deepcopy(case.engine.calls)
    with pytest.raises(JobFenceRejectedError):
        rows.finish()
    for step in CONSUMPTION:
        if step == omitted:
            continue
        if omitted == ("audit", "request") and step == ("reason", "request"):
            with pytest.raises(JobFenceRejectedError):
                rows.request_reason()
            continue
        _consume(rows, step)
    with pytest.raises(JobFenceRejectedError):
        rows.finish()
    _consume(rows, omitted)
    if omitted == ("audit", "request"):
        rows.request_reason()
    rows.finish()
    assert case.engine.calls == calls


def test_finish_refuses_a_consumption_superset_even_when_all_roles_were_taken(case):
    rows = case.load()
    _consume_all(rows)
    # Deliberate state corruption tests exact-set accounting; no product
    # function or validator is replaced to manufacture an accepted result.
    rows.consumed.add(("audit", "undeclared"))
    with pytest.raises(JobFenceRejectedError):
        rows.finish()


@pytest.mark.parametrize("phase", ["settings", "events", "audits"])
def test_connection_replacement_during_load_is_rejected(case, phase):
    def replace_connection(kind):
        if kind == phase:
            case.engine._local.connection = object()

    case.engine.after_read = replace_connection
    with pytest.raises(JobFenceRejectedError):
        case.load()
    assert case.engine.calls[-1][0] == phase


@pytest.mark.parametrize("step", (*CONSUMPTION, ("finish", "all")))
@pytest.mark.parametrize("connection", [None, "replacement"])
def test_connection_loss_or_replacement_refuses_every_consumer(case, step, connection):
    rows = case.load()
    if step[0] == "finish":
        _consume_all(rows)
    elif step[0] == "reason":
        _consume(rows, ("audit", "request"))
    case.engine._local.connection = None if connection is None else object()
    calls = deepcopy(case.engine.calls)
    with pytest.raises(JobFenceRejectedError):
        if step[0] == "finish":
            rows.finish()
        else:
            _consume(rows, step)
    assert case.engine.calls == calls


def test_expired_original_budget_refuses_load_without_sql_or_deadline_reset(case):
    _expire(case.before)
    original, calls = _deadlines(case.before), deepcopy(case.engine.calls)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        case.load()
    assert _deadlines(case.before) == original
    assert case.engine.calls == calls


@pytest.mark.parametrize("phase", ["budget", "settings", "events", "audits"])
def test_monotonic_expiry_during_load_propagates_before_the_next_read(case, phase):
    original_sql_deadline = case.before.deadline
    expired_deadlines = []

    def expire_after_read(kind):
        if kind == phase:
            _expire(case.before)
            expired_deadlines.append(_deadlines(case.before))

    case.engine.after_read = expire_after_read
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        case.load()
    assert case.engine.calls[-1][0] == phase
    assert len(expired_deadlines) == 1
    assert _deadlines(case.before) == expired_deadlines[0]
    assert case.before.deadline == original_sql_deadline


@pytest.mark.parametrize("after_phase", [None, "settings", "events"])
def test_database_budget_refusal_propagates_at_each_load_boundary(case, after_phase):
    original = _deadlines(case.before)
    if after_phase is None:
        case.engine.remaining_ms = 0
    else:
        def expire_database_budget(kind):
            if kind == after_phase:
                case.engine.remaining_ms = 0

        case.engine.after_read = expire_database_budget
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        case.load()
    assert case.engine.calls[-1] == ("budget", (original[0].isoformat(),))
    row_reads = [kind for kind, _ in case.engine.calls if kind in {"events", "audits"}]
    assert row_reads == (["events"] if after_phase == "events" else [])
    assert _deadlines(case.before) == original


@pytest.mark.parametrize("step", (*CONSUMPTION, ("finish", "all")))
def test_expiry_at_every_consumer_preserves_original_budget_and_issues_no_sql(case, step):
    rows = case.load()
    if step[0] == "finish":
        _consume_all(rows)
    elif step[0] == "reason":
        _consume(rows, ("audit", "request"))
    _expire(case.before)
    original, calls = _deadlines(case.before), deepcopy(case.engine.calls)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        if step[0] == "finish":
            rows.finish()
        else:
            _consume(rows, step)
    assert _deadlines(case.before) == original
    assert case.engine.calls == calls


def test_each_invocation_reloads_rows_and_has_independent_consumption(case):
    original = _deadlines(case.before)
    first = case.load()
    _consume_all(first)
    first.finish()
    case.engine.rows["audits"][0]["reason"] = "changed transport-only reason"
    second = case.load()
    assert second is not first
    second.root(TENANT)
    assert second.audit("request", tenant_id=TENANT)["reason"] == "changed transport-only reason"
    assert second.request_reason()["reason"] == "changed transport-only reason"
    for kind in ("settings", "events", "audits"):
        assert sum(purpose == kind for purpose, _ in case.engine.calls) == 2
    assert sum(kind == "clock" for kind, _ in case.engine.calls) == 1
    assert _deadlines(case.before) == original


def test_a_completed_invocation_cannot_mask_missing_rows_in_the_next_load(case):
    rows = case.load()
    _consume_all(rows)
    rows.finish()
    case.engine.rows["audits"].pop()
    with pytest.raises(JobFenceRejectedError):
        case.load()


def test_a_second_invocation_cannot_restart_the_expired_original_budget(case):
    rows = case.load()
    _consume_all(rows)
    rows.finish()
    _expire(case.before)
    original, calls = _deadlines(case.before), deepcopy(case.engine.calls)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        case.load()
    assert _deadlines(case.before) == original
    assert case.engine.calls == calls
