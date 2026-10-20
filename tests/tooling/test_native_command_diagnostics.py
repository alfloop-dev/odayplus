"""Diagnostic forwarding cannot change outcomes or disclose request data."""
import functools
import inspect
import json
from datetime import UTC, datetime
from types import FunctionType, SimpleNamespace

import pytest

from tests.integration import _native_command_diagnostics as diagnostics
from tests.integration._native_command_diagnostics import (
    NativeCommandDiagnostics,
    diagnostics_requested,
)


def test_sql_keyword_arguments_results_and_anonymous_counts_are_preserved(tmp_path):
    output = tmp_path / "profile.jsonl"
    recorder = NativeCommandDiagnostics(output, "tests/integration/test_native_intake_case.py::case[private-param]")
    params, row, calls = ("private-bind",), {"total": 1, "source_total": 1, "secret": "private-row"}, []

    class Engine:
        def query_one(self, *, sql, parameters):
            calls.append((sql, parameters))
            return row

    engine = Engine()
    Engine.query_one = recorder.sql(Engine.query_one)

    class Owner:
        def execute(self):
            return engine.query_one(sql="SELECT 'private-literal'", parameters=params)

    Owner.execute = recorder.operation(Owner.execute, "Owner.execute")
    assert Owner().execute() is row and calls == [("SELECT 'private-literal'", params)]
    assert calls[0][1] is params
    raw = output.read_text()
    assert all(value not in raw for value in ("private-bind", "private-row", "private-literal", "private-param"))
    metric = json.loads(raw)
    assert metric["quota_count_samples"] == [{"total": 1, "source_total": 1}]
    assert sum(q["count"] for q in metric["sql"].values()) == 1
    assert next(iter(metric["sql"].values()))["source_callsite"]


def test_exact_exception_and_stage_error_survive_without_message_disclosure(tmp_path):
    output = tmp_path / "profile.jsonl"
    recorder = NativeCommandDiagnostics(output, "tests/integration/test_native_intake_case.py::case")
    original_error = ValueError("private-error-message")

    class Owner:
        def reserve(self):
            raise original_error

        def execute(self):
            return self.reserve()

    Owner.reserve = recorder.stage(Owner.reserve, "Owner.reserve")
    Owner.execute = recorder.operation(Owner.execute, "Owner.execute")
    with pytest.raises(ValueError) as caught:
        Owner().execute()
    assert caught.value is original_error
    raw = output.read_text()
    assert "private-error-message" not in raw
    metric = json.loads(raw)
    assert metric["error_class"] == "ValueError"
    assert metric["stage_errors"] == [{"stage": "Owner.reserve", "error_class": "ValueError"}]
    assert metric["stages"]["Owner.reserve"]["count"] == 1


def test_nested_engine_methods_are_forwarded_without_double_counting(tmp_path):
    output = tmp_path / "profile.jsonl"
    recorder = NativeCommandDiagnostics(output, "native-case")
    row = {"actual": object()}

    class Engine:
        def execute(self, sql):
            return row

        def query_one(self, sql):
            return self.execute(sql)

    engine = Engine()
    Engine.execute = recorder.sql(Engine.execute)
    Engine.query_one = recorder.sql(Engine.query_one)

    class Owner:
        def execute(self):
            return engine.query_one("SELECT native_value")

    Owner.execute = recorder.operation(Owner.execute, "Owner.execute")
    assert Owner().execute() is row
    metric = json.loads(output.read_text())
    assert len(metric["sql"]) == 1
    assert next(iter(metric["sql"].values()))["count"] == 1


@pytest.mark.parametrize("fail", [False, True])
def test_diagnostic_write_failure_cannot_replace_the_original_outcome(tmp_path, capsys, fail):
    recorder = NativeCommandDiagnostics(tmp_path / "profile.jsonl", "native-case")
    result, original_error = object(), ValueError("original-private-message")

    def unavailable(metric):
        raise OSError("private-diagnostic-path")

    recorder._write = unavailable

    class Owner:
        def execute(self):
            if fail:
                raise original_error
            return result

    Owner.execute = recorder.operation(Owner.execute, "Owner.execute")
    if fail:
        with pytest.raises(ValueError) as caught:
            Owner().execute()
        assert caught.value is original_error
    else:
        assert Owner().execute() is result
    assert capsys.readouterr().err == "Native SQL diagnostic unavailable: DIAGNOSTIC_FAILURE\n"


def test_query_groups_and_count_samples_remain_bounded(tmp_path):
    output = tmp_path / "profile.jsonl"
    recorder = NativeCommandDiagnostics(output, "native-case")

    class Engine:
        def query_one(self, sql, value):
            return {"total": value, "source_total": value}

    engine = Engine()
    Engine.query_one = recorder.sql(Engine.query_one)

    class Owner:
        def execute(self):
            for value in range(80):
                result = engine.query_one(f"SELECT {value}", value)
            return result

    Owner.execute = recorder.operation(Owner.execute, "Owner.execute")
    assert Owner().execute() == {"total": 79, "source_total": 79}
    metric = json.loads(output.read_text())
    assert len(metric["sql"]) == 64 and metric["sql_overflow_count"] == 16
    assert len(metric["quota_count_samples"]) == 8
    assert metric["quota_count_samples"][-1] == {"total": 79, "source_total": 79}


@pytest.mark.parametrize("node,value,expected", [
    ("tests/integration/test_native_intake_case.py::case", "1", True),
    ("tests/integration/test_native_matching_case_command_postgresql.py::case", "1", True),
    ("tests/integration/test_native_matching_case_command_postgresql.py::case", None, False),
    ("tests/security/test_native_matching_case_command_postgresql.py::case", "1", False),
    ("tests/integration/test_native_intake_case.py::case", None, False),
    ("tests/integration/test_native_intake_case.py::case", "0", False),
    ("tests/integration/test_other.py::case", "1", False),
    ("tests/security/test_native_intake_case.py::case", "1", False),
])
def test_explicit_opt_in_is_limited_to_native_integration_nodes(node, value, expected):
    assert diagnostics_requested(node, value) is expected


def test_engine_wall_thread_cpu_and_observation_are_distinct_windows(tmp_path, monkeypatch):
    # Replace only this recorder's clock reference, never the shared time
    # module or any business/SQL/root clock. This is a forwarding unit test.
    wall = iter((0.0, 10.0, 12.0, 20.0, 24.0, 25.0))
    cpu = iter((100.0, 100.25))
    monkeypatch.setattr(diagnostics, "time", SimpleNamespace(
        perf_counter=lambda: next(wall), thread_time=lambda: next(cpu)))
    path = tmp_path / "profile.jsonl"
    recorder = NativeCommandDiagnostics(path, "native-case")
    value, calls = object(), []
    class Engine:
        def query_one(self, sql, parameters):
            calls.append((sql, parameters))
            return value
    engine = Engine()
    Engine.query_one = recorder.sql(Engine.query_one)
    class Owner:
        def execute(self):
            return engine.query_one("SELECT private_value", ("private-parameter",))
    Owner.execute = recorder.operation(Owner.execute, "Owner.execute")
    assert Owner().execute() is value
    assert calls == [("SELECT private_value", ("private-parameter",))]
    metric = json.loads(path.read_text())
    item = next(iter(metric["sql"].values()))
    assert item["engine_wall_seconds"] == item["max_engine_wall_seconds"] == 2.0
    assert item["thread_cpu_seconds"] == item["max_thread_cpu_seconds"] == .25
    assert item["thread_cpu_samples"] == 1
    assert item["observation_seconds"] == item["max_observation_seconds"] == 12.0
    # Existing fields retain their previous, observation-inclusive meaning.
    assert item["seconds"] == item["max_seconds"] == 10.0
    assert "private_value" not in path.read_text() and "private-parameter" not in path.read_text()


@pytest.mark.parametrize("fail", [False, True])
def test_unavailable_cpu_clock_preserves_exact_engine_result_or_exception(tmp_path, monkeypatch, fail):
    real_time = diagnostics.time
    def unavailable():
        raise OSError("private-clock-error")
    monkeypatch.setattr(diagnostics, "time", SimpleNamespace(
        perf_counter=real_time.perf_counter, thread_time=unavailable))
    path = tmp_path / "profile.jsonl"
    recorder = NativeCommandDiagnostics(path, "native-case")
    value, original_error, calls = object(), ValueError("private-business-error"), []
    class Engine:
        def query_one(self, sql):
            calls.append(sql)
            if fail:
                raise original_error
            return value
    engine = Engine()
    Engine.query_one = recorder.sql(Engine.query_one)
    class Owner:
        def execute(self):
            return engine.query_one("SELECT private_value")
    Owner.execute = recorder.operation(Owner.execute, "Owner.execute")
    if fail:
        with pytest.raises(ValueError) as caught:
            Owner().execute()
        assert caught.value is original_error
    else:
        assert Owner().execute() is value
    assert calls == ["SELECT private_value"]
    raw = path.read_text()
    assert "private-clock-error" not in raw and "private-business-error" not in raw
    metric = json.loads(raw)
    item = next(iter(metric["sql"].values()))
    assert item["count"] == 1 and item["thread_cpu_samples"] == 0
    assert metric["diagnostic_error_classes"] == ["DIAGNOSTIC_FAILURE", "DIAGNOSTIC_FAILURE"]


def _memory_recorder(tmp_path):
    recorder = NativeCommandDiagnostics(tmp_path / "profile.jsonl", "native-case[private-case-value]")
    records = []
    recorder._write = records.append
    return recorder, records


def _raised(call):
    try:
        call()
    except Exception as error:
        return error
    raise AssertionError("synthetic call did not raise")


def _raise(error):
    raise error


@pytest.mark.parametrize("call_style", ["positional", "keyword", "mixed", "defaults"])
@pytest.mark.parametrize("returns_none", [False, True])
def test_consumed_error_forwarding_keeps_refs_call_shape_and_result(tmp_path, call_style, returns_none):
    recorder, records = _memory_recorder(tmp_path)
    instance, delivery, fence, leases = object(), object(), object(), object()
    error = _raised(lambda: _raise(ValueError("private-input-error")))
    result, calls = (None if returns_none else object()), []
    if call_style == "positional":
        args, kwargs = (delivery, error), {"business_fence": fence, "leases": leases}
    elif call_style == "keyword":
        args, kwargs = (), {"delivery": delivery, "error": error, "business_fence": fence, "leases": leases}
    elif call_style == "mixed":
        args, kwargs = (delivery,), {"error": error, "business_fence": fence, "leases": leases}
    else:
        args, kwargs = (delivery, error), {}

    def negative(self, *forwarded_args, **forwarded_kwargs):
        calls.append((self, forwarded_args, forwarded_kwargs))
        # Witness is present before replay/settlement can consume the error.
        assert recorder.local.metric["handled_errors"][0]["error_class"] == "ValueError"
        return result

    observed = recorder.negative(negative)
    operation = recorder.operation(lambda self: observed(self, *args, **kwargs), "Owner.execute")
    assert operation(instance) is result
    assert len(calls) == 1 and calls[0][0] is instance
    assert len(calls[0][1]) == len(args)
    assert all(actual is expected for actual, expected in zip(calls[0][1], args, strict=True))
    assert calls[0][2].keys() == kwargs.keys()
    assert all(calls[0][2][key] is value for key, value in kwargs.items())
    assert len(records) == 1 and "error_class" not in records[0]
    witness = records[0]["handled_errors"][0]
    assert witness["boundary"] == "MANUAL_NEGATIVE_INPUT"
    assert witness["reason_site"] == witness["reason_provenance"] == "UNCLASSIFIED"
    assert "private-" not in json.dumps(records)
    assert recorder.local.metric is None


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("local_unavailable", [False, True])
def test_negative_without_active_operation_forwards_without_observation(
        tmp_path, monkeypatch, fail, local_unavailable):
    recorder, records = _memory_recorder(tmp_path)
    result, error, calls = object(), ValueError("private-error"), []
    cause, context = RuntimeError("private-cause"), RuntimeError("private-context")
    error.__cause__, error.__context__ = cause, context
    delivery, fence, leases = object(), object(), object()

    class UnavailableLocal:
        @property
        def metric(self):
            raise KeyboardInterrupt("private-local-error")

    if local_unavailable:
        recorder.local = UnavailableLocal()

    def forbidden(*args, **kwargs):
        pytest.fail("inactive observer touched metadata or clock")

    monkeypatch.setattr(diagnostics, "_error_snapshot", forbidden)
    monkeypatch.setattr(diagnostics, "time", SimpleNamespace(perf_counter=forbidden))

    def negative(self, delivery, error, *, business_fence=None, leases=None):
        calls.append((self, delivery, error, business_fence, leases))
        if fail:
            raise error
        return result

    instance = object()
    wrapped = recorder.negative(negative)

    def call():
        return wrapped(instance, delivery, error, business_fence=fence, leases=leases)

    if fail:
        assert _raised(call) is error
        assert error.__cause__ is cause and error.__context__ is context
        assert error.__suppress_context__ is True
    else:
        assert call() is result
    assert len(calls) == 1
    assert all(actual is expected for actual, expected in zip(
        calls[0], (instance, delivery, error, fence, leases), strict=True))
    assert records == []


@pytest.mark.parametrize("same_error", [False, True])
def test_incoming_error_and_later_escape_preserve_identity_cause_and_context(tmp_path, same_error):
    recorder, records = _memory_recorder(tmp_path)
    incoming = _raised(lambda: _raise(ValueError("private-incoming")))
    outgoing = incoming if same_error else RuntimeError("private-replay")
    cause, context = LookupError("private-cause"), LookupError("private-context")
    outgoing.__cause__, outgoing.__context__ = cause, context
    outgoing.__suppress_context__ = True
    calls = []

    def negative(self, delivery, error, *, business_fence=None, leases=None):
        calls.append(error)
        assert recorder.local.metric["handled_errors"][0]["boundary"] == "MANUAL_NEGATIVE_INPUT"
        raise outgoing

    observed = recorder.negative(negative)
    operation = recorder.operation(lambda self: observed(self, object(), incoming), "Owner.execute")
    assert _raised(lambda: operation(object())) is outgoing
    assert len(calls) == 1 and calls[0] is incoming
    assert outgoing.__cause__ is cause and outgoing.__context__ is context
    assert outgoing.__suppress_context__ is True
    assert records[0]["handled_errors"][0]["error_class"] == "ValueError"
    assert records[0]["error_class"] == ("ValueError" if same_error else "RuntimeError")
    assert records[0]["boundary"] == "OPERATION_ESCAPE"
    assert "private-" not in json.dumps(records)


@pytest.mark.parametrize("site", ["command_memory", "source_sql", "parser_sql", "parser_memory"])
def test_real_bound_deadline_throw_sites_use_only_synthetic_clocks_and_engine(tmp_path, monkeypatch, site):
    from modules.listing.application import native_intake_pending_commands as commands

    recorder, records = _memory_recorder(tmp_path)
    at = datetime(2026, 1, 1, tzinfo=UTC)
    now, calls = [10.0], []
    monkeypatch.setattr(commands, "time", SimpleNamespace(monotonic=lambda: now[0]))

    def query_one(sql, parameters):
        calls.append((sql, parameters))
        return {"remaining_ms": 0}

    engine = SimpleNamespace(dialect="postgresql", _local=SimpleNamespace(connection=object()),
                             query_one=query_one)
    if site in {"command_memory", "parser_memory"}:
        cls = (diagnostics.NativeIntakeCommandBudget if site == "command_memory"
               else diagnostics.NativeParserCommandBudget)
        budget = cls(SimpleNamespace(_clock=lambda: at))
        assert budget._monotonic_deadline == 10.750
        assert (budget.deadline - at).total_seconds() == .750
        now[0] = 10.750
        call = budget.check_in_memory
        reason = "COMMAND_MEMORY_DEADLINE" if site == "command_memory" else "PARSER_MEMORY_DEADLINE"
    elif site == "source_sql":
        owner = object.__new__(diagnostics.NativeIntakeSourcePolicyOwner)
        owner.engine = engine

        def call():
            return owner._admission_budget(at)

        reason = "SOURCE_SQL_DEADLINE"
    else:
        def call():
            return diagnostics.NativeParserSQLClock(engine)._admission_budget(at)

        reason = "PARSER_SQL_DEADLINE"
    error = _raised(call)
    observed = recorder.negative(lambda self, delivery, error: None)
    assert recorder.operation(lambda self: observed(self, object(), error), "Owner.execute")(object()) is None
    witness = records[0]["handled_errors"][0]
    assert witness["reason_site"] == reason
    assert witness["reason_provenance"] == "TRUSTED_CLASS_AND_BOUND_THROW_SITE"
    assert witness["error_frames_no_values"][-1]["symbol"] != "UNKNOWN_FRAME"
    assert type(witness["error_frames_no_values"][-1]["line"]) is int
    assert witness["error_frames_no_values"][-1]["line"] > 0
    # The same classifier also applies at the existing operation escape.
    escaping = _raised(lambda: recorder.operation(lambda self: call(), "Owner.request")(object()))
    assert type(escaping) is type(error)
    assert records[1]["boundary"] == "OPERATION_ESCAPE"
    assert records[1]["reason_site"] == reason
    if site.endswith("sql"):
        assert len(calls) == 2
        assert all(parameters == (at.isoformat(),) for _, parameters in calls)
        assert all("statement_timeout" in sql and "lock_timeout" in sql for sql, _ in calls)
    else:
        assert calls == []
        assert budget._monotonic_deadline == 10.750


def test_trust_requires_original_code_exact_class_and_actual_final_frame(monkeypatch):
    from modules.listing.application import native_intake_pending_commands as commands

    original = commands.NativeIntakeCommandBudget.check_in_memory
    conflict = diagnostics.NativeAssistedEntryConflict
    budget = object.__new__(commands.NativeIntakeCommandBudget)
    budget._monotonic_deadline = 0.0
    monkeypatch.setattr(commands, "time", SimpleNamespace(monotonic=lambda: 1.0))

    def witness(call):
        return diagnostics._error_snapshot(_raised(call), "MANUAL_NEGATIVE_INPUT")

    # A copied code object can compare equal; only the captured object is trusted.
    clone = FunctionType(original.__code__.replace(), original.__globals__)
    assert witness(lambda: clone(budget))["reason_site"] == "UNCLASSIFIED"

    def replacement(self):
        raise conflict("NATIVE_COMMAND_ROOT_DEADLINE_EXPIRED")

    replacement.__wrapped__ = original
    monkeypatch.setattr(commands.NativeIntakeCommandBudget, "check_in_memory", replacement)
    assert witness(budget.check_in_memory)["reason_site"] == "UNCLASSIFIED"
    # Captured trust survives mutation of the class attribute without trusting it.
    assert witness(lambda: original(budget))["reason_site"] == "COMMAND_MEMORY_DEADLINE"

    # A genuine budget frame earlier in the traceback is not the throw site.
    def failing_clock():
        raise conflict("NATIVE_COMMAND_ROOT_DEADLINE_EXPIRED")

    monkeypatch.setattr(commands, "time", SimpleNamespace(monotonic=failing_clock))
    earlier = witness(lambda: original(budget))
    assert earlier["reason_site"] == "UNCLASSIFIED"
    assert any(frame["symbol"] == "NativeIntakeCommandBudget.check_in_memory"
               for frame in earlier["error_frames_no_values"])
    assert earlier["error_frames_no_values"][-1] == {"symbol": "UNKNOWN_FRAME", "line": 0}

    monkeypatch.setattr(commands, "time", SimpleNamespace(monotonic=lambda: 1.0))
    # Wrong known class, then an unknown subclass, raised by the real code object.
    for wrong_type, label in [(ValueError, "ValueError"),
                              (diagnostics.NativeParserConflict, "NativeParserConflict"),
                              (type("private-subclass", (conflict,), {}), "OTHER_EXCEPTION")]:
        monkeypatch.setattr(commands, "NativeAssistedEntryConflict", wrong_type)
        wrong = witness(lambda: original(budget))
        assert wrong["reason_site"] == "UNCLASSIFIED"
        assert wrong["error_class"] == label


def test_bounded_walk_does_not_classify_an_unreached_trusted_throw(monkeypatch):
    from modules.listing.application import native_intake_pending_commands as commands

    budget = object.__new__(commands.NativeIntakeCommandBudget)
    budget._monotonic_deadline = 0.0
    monkeypatch.setattr(commands, "time", SimpleNamespace(monotonic=lambda: 1.0))

    def deep(depth):
        if depth:
            return deep(depth - 1)
        budget.check_in_memory()

    error = _raised(lambda: deep(80))
    witness = diagnostics._error_snapshot(error, "MANUAL_NEGATIVE_INPUT")
    assert witness["error_class"] == "NativeAssistedEntryConflict"
    assert witness["reason_site"] == witness["reason_provenance"] == "UNCLASSIFIED"
    assert len(witness["error_frames_no_values"]) == 6
    assert witness["error_frames_no_values"][-1] == {"symbol": "UNKNOWN_FRAME", "line": 0}


def test_unknown_error_metadata_is_never_read_or_exported(tmp_path, monkeypatch, capsys):
    import linecache
    import traceback

    recorder, records = _memory_recorder(tmp_path)
    touched = []

    def forbidden(*args, **kwargs):
        touched.append("forbidden")
        raise AssertionError("private-accessor")

    class HostileMeta(type):
        def __getattribute__(cls, name):
            if name in {"__name__", "__qualname__", "__module__"}:
                return forbidden()
            return super().__getattribute__(name)

    class HostileError(ValueError, metaclass=HostileMeta):
        __str__ = forbidden
        __repr__ = forbidden

        def __getattribute__(self, name):
            if name in {"args", "code", "detail", "__dict__", "__traceback__", "__cause__", "__context__"}:
                return forbidden()
            return super().__getattribute__(name)

    HostileError.__name__ = "private-dynamic-class"
    secret = 'Bearer private-token https://user:pass@host/\r\n"\u202e'
    error = HostileError(secret, {"private-row": secret})
    error.__cause__, error.__context__ = ValueError(secret), ValueError(secret)
    monkeypatch.setattr(linecache, "getline", forbidden)
    monkeypatch.setattr(linecache, "getlines", forbidden)
    monkeypatch.setattr(traceback, "extract_tb", forbidden)
    throw = FunctionType(_raise.__code__.replace(
        co_filename="/private-path/secret.py", co_name="private-function",
        co_qualname="private-qualname"), _raise.__globals__)
    assert _raised(lambda: throw(error)) is error
    observed = recorder.negative(lambda self, delivery, error: _raise(error))
    operation = recorder.operation(lambda self: observed(self, {"private-delivery": secret}, error), "Owner.execute")
    assert _raised(lambda: operation(object())) is error
    assert touched == []
    raw = json.dumps(records)
    assert "private-" not in raw and "Bearer" not in raw and "https:" not in raw
    assert capsys.readouterr().err == ""
    for witness in (records[0], records[0]["handled_errors"][0]):
        assert witness["error_class"] == "OTHER_EXCEPTION"
        assert witness["reason_site"] == "UNCLASSIFIED"
        assert all(frame == {"symbol": "UNKNOWN_FRAME", "line": 0}
                   for frame in witness["error_frames_no_values"])


@pytest.mark.parametrize("failure", ["extract", "classify", "start_clock", "finish_clock", "timestamp", "encode", "write"])
@pytest.mark.parametrize("outcome", ["none", "result", "escape"])
def test_observer_failures_preserve_original_business_outcome(tmp_path, monkeypatch, capsys, failure, outcome):
    recorder, records = _memory_recorder(tmp_path)
    incoming = ValueError("private-input")
    business_error = RuntimeError("private-business")
    cause, context = ValueError("private-cause"), ValueError("private-context")
    business_error.__cause__, business_error.__context__ = cause, context
    value, calls, active = object(), [], []

    def unavailable(*args, **kwargs):
        raise KeyboardInterrupt("private-observer-failure")

    if failure == "extract":
        monkeypatch.setattr(diagnostics, "_error_snapshot", unavailable)
    elif failure == "classify":
        monkeypatch.setattr(diagnostics, "_error_class", unavailable)
    elif failure in {"start_clock", "finish_clock"}:
        clock_calls = []

        def clock():
            clock_calls.append(None)
            if len(clock_calls) == (1 if failure == "start_clock" else 2):
                unavailable()
            return 1.0

        monkeypatch.setattr(diagnostics, "time", SimpleNamespace(perf_counter=clock))
    elif failure == "timestamp":
        monkeypatch.setattr(diagnostics, "datetime", SimpleNamespace(now=unavailable))
    elif failure == "encode":
        recorder._write = NativeCommandDiagnostics._write.__get__(recorder)
        monkeypatch.setattr(diagnostics, "json", SimpleNamespace(dumps=unavailable))
    else:
        recorder._write = unavailable

    def negative(self, delivery, error, *, business_fence=None, leases=None):
        calls.append(error)
        active.append(recorder.local.metric)
        if outcome == "escape":
            raise business_error
        return None if outcome == "none" else value

    observed = recorder.negative(negative)
    operation = recorder.operation(lambda self: observed(self, object(), incoming), "Owner.execute")
    if outcome == "escape":
        assert _raised(lambda: operation(object())) is business_error
        assert business_error.__cause__ is cause and business_error.__context__ is context
        assert business_error.__suppress_context__ is True
    else:
        assert operation(object()) is (None if outcome == "none" else value)
    assert len(calls) == 1 and calls[0] is incoming
    assert recorder.local.metric is None
    if failure in {"encode", "write"}:
        assert capsys.readouterr().err == "Native SQL diagnostic unavailable: DIAGNOSTIC_FAILURE\n"
    else:
        assert records == active
        assert set(records[0]["diagnostic_error_classes"]) == {"DIAGNOSTIC_FAILURE"}
        assert capsys.readouterr().err == ""
    assert "private-" not in json.dumps(active)


def test_handled_frames_overflow_and_existing_error_caps_remain_bounded(tmp_path, monkeypatch):
    recorder, records = _memory_recorder(tmp_path)
    error = ValueError("private-deep-error")
    original_snapshot, observations = diagnostics._error_snapshot, []

    def counted(error, boundary):
        observations.append(boundary)
        return original_snapshot(error, boundary)

    monkeypatch.setattr(diagnostics, "_error_snapshot", counted)

    def deep(depth):
        if depth:
            return deep(depth - 1)
        raise error

    assert _raised(lambda: deep(80)) is error
    observed = recorder.negative(lambda self, delivery, error: None)
    stage = recorder.stage(lambda self: _raise(error), "Owner.reserve")

    def run(self):
        for _ in range(12):
            assert observed(self, object(), error) is None
            assert _raised(lambda: stage(self)) is error
            recorder._observation_error(recorder.local.metric, error)

    recorder.operation(run, "Owner.execute")(object())
    metric = records[0]
    assert len(metric["handled_errors"]) == 2
    assert metric["handled_errors_overflow"] is True
    assert observations == ["MANUAL_NEGATIVE_INPUT"] * 2
    assert len(metric["stage_errors"]) == 8
    assert metric["diagnostic_error_classes"] == ["DIAGNOSTIC_FAILURE"] * 4
    assert all(len(item["error_frames_no_values"]) == 6 for item in metric["handled_errors"])
    assert all(item["reason_site"] == "UNCLASSIFIED" for item in metric["handled_errors"])
    # Serialization also rejects accidental retention of exceptions/frames.
    assert "private-" not in json.dumps(metric)
    recorder.operation(lambda self: observed(self, object(), ValueError()), "Owner.execute")(object())
    assert len(records[1]["handled_errors"]) == 1
    assert "handled_errors_overflow" not in records[1]
    assert "stage_errors" not in records[1] and "diagnostic_error_classes" not in records[1]


def test_full_profile_drops_diagnostic_without_changing_result(tmp_path):
    output = tmp_path / "profile.jsonl"
    cap = 64 * 1024 * 1024
    with output.open("wb") as stream:
        stream.truncate(cap)
    recorder = NativeCommandDiagnostics(output, "native-case")
    result = object()
    observed = recorder.negative(lambda self, delivery, error: result)
    operation = recorder.operation(lambda self: observed(self, object(), ValueError()), "Owner.execute")
    assert operation(object()) is result
    assert output.stat().st_size == cap


def test_handled_errors_stay_with_owner_recorder_across_nested_operations(tmp_path):
    recorder, records = _memory_recorder(tmp_path)
    outer, outer_records = _memory_recorder(tmp_path)
    error, result = ValueError("private-error"), object()
    observed = recorder.negative(lambda self, delivery, error: result)
    inner = recorder.operation(lambda self: observed(self, object(), error), "Owner.inner")
    owner = recorder.operation(lambda self: inner(self), "Owner.execute")
    historical = outer.operation(lambda self: owner(self), "Historical.outer")
    assert historical(object()) is result
    assert len(records) == len(outer_records) == 1
    assert records[0]["operation"] == "Owner.execute"
    assert len(records[0]["handled_errors"]) == 1
    assert "handled_errors" not in outer_records[0]
    assert recorder.local.metric is outer.local.metric is None


@pytest.mark.parametrize("node,value,enabled", [
    ("tests/integration/test_native_intake_case.py::case", "1", True),
    ("tests/integration/test_native_intake_case.py::case", None, False),
    ("tests/integration/test_native_intake_case.py::case", "0", False),
    ("tests/integration/test_other.py::case", "1", False),
    ("tests/security/test_native_intake_case.py::case", "1", False),
])
def test_real_fixture_installs_negative_on_same_opt_in_owner_recorder(tmp_path, monkeypatch, node, value, enabled):
    from modules.listing.application.native_intake_assisted_entry import (
        NativeIntakeAssistedEntryOwner,
    )
    from modules.listing.application.native_intake_assisted_entry_settlement import (
        NativeAssistedEntrySettlement,
    )

    recorder, records = _memory_recorder(tmp_path)
    constructions, calls = [], []
    result, incoming = object(), ValueError("private-error")
    settlement = object.__new__(NativeAssistedEntrySettlement)

    def construct(output, case):
        constructions.append((output, case))
        return recorder

    def negative(self, delivery, error, *, business_fence=None, leases=None):
        calls.append(error)
        return result

    def execute(self):
        return settlement.negative(object(), incoming)

    with monkeypatch.context() as patch:
        patch.setattr(diagnostics, "NativeCommandDiagnostics", construct)
        patch.setattr(NativeAssistedEntrySettlement, "negative", negative)
        patch.setattr(NativeIntakeAssistedEntryOwner, "execute", execute)
        if value is None:
            patch.delenv("ODP_NATIVE_SQL_PROFILE", raising=False)
        else:
            patch.setenv("ODP_NATIVE_SQL_PROFILE", value)
        request = SimpleNamespace(node=SimpleNamespace(nodeid=node), config=SimpleNamespace(rootpath=tmp_path))
        fixture = diagnostics.native_command_diagnostics.__wrapped__(request, patch)
        next(fixture)
        try:
            if enabled:
                assert NativeAssistedEntrySettlement.negative is not negative
            else:
                assert NativeAssistedEntrySettlement.negative is negative
            owner = object.__new__(NativeIntakeAssistedEntryOwner)
            assert owner.execute() is result
        finally:
            with pytest.raises(StopIteration):
                next(fixture)
        assert len(calls) == 1 and calls[0] is incoming
        assert len(constructions) == len(records) == int(enabled)
        if enabled:
            assert records[0]["operation"] == "NativeIntakeAssistedEntryOwner.execute"
            assert len(records[0]["handled_errors"]) == 1
            assert records[0]["sql"] == {} and records[0]["stages"] == {}


def test_shared_sql_and_stage_keep_both_scopes_with_one_factual_window(tmp_path, monkeypatch):
    outer, outer_records = _memory_recorder(tmp_path)
    inner, inner_records = _memory_recorder(tmp_path)
    wall_calls, cpu_calls, hashes, calls = [], [], [], []
    real_sha256 = diagnostics.hashlib.sha256

    def wall():
        wall_calls.append(len(wall_calls))
        return float(wall_calls[-1])

    def cpu():
        cpu_calls.append(len(cpu_calls))
        return float(cpu_calls[-1])

    def hashed(value):
        hashes.append(value)
        return real_sha256(value)

    monkeypatch.setattr(diagnostics, "time", SimpleNamespace(perf_counter=wall, thread_time=cpu))
    monkeypatch.setattr(diagnostics, "hashlib", SimpleNamespace(sha256=hashed))
    row, parameters = {"total": 3, "source_total": 2, "secret": "private-row"}, ("private-bind",)

    class Engine:
        def execute(self, *, statement, parameters):
            calls.append((statement, parameters))
            return row

        def query_one(self, *, statement, parameters):
            return self.execute(statement=statement, parameters=parameters)

    original = Engine.query_one
    for name in ("execute", "query_one"):
        wrapped = outer.sql(getattr(Engine, name))
        # Repeated installation of either active recorder must not duplicate it.
        setattr(Engine, name, inner.sql(outer.sql(inner.sql(wrapped))))
    assert inspect.signature(Engine.query_one) == inspect.signature(original)
    assert Engine.query_one.__name__ == original.__name__
    assert Engine.query_one.__module__ == original.__module__
    engine = Engine()

    def budget(self, *, statement, parameters):
        return engine.query_one(statement=statement, parameters=parameters)

    stage = outer.stage(budget, "Budget.check")
    stage = inner.stage(outer.stage(inner.stage(stage, "Budget.check"), "Budget.check"), "Budget.check")
    assert inspect.signature(stage) == inspect.signature(budget)
    root = inner.operation(lambda self: stage(self, statement="SELECT private_value", parameters=parameters),
                           "Root._intent")
    assisted = outer.operation(lambda self: root(self), "Assisted.execute")
    assert assisted(object()) is row
    assert calls == [("SELECT private_value", parameters)] and calls[0][1] is parameters
    assert hashes == [b"SELECT private_value"]
    assert len(cpu_calls) == 2 and len(wall_calls) == 10
    assert len(outer_records) == len(inner_records) == 1
    a, b = outer_records[0], inner_records[0]
    assert a["operation"] == "Assisted.execute" and b["operation"] == "Root._intent"
    assert a["case_number"] != b["case_number"]
    assert a["sql"] == b["sql"] and len(a["sql"]) == 1
    item = next(iter(a["sql"].values()))
    assert item["count"] == item["thread_cpu_samples"] == 1
    assert item["engine_wall_seconds"] == item["max_engine_wall_seconds"] == 1.0
    assert item["thread_cpu_seconds"] == item["max_thread_cpu_seconds"] == 1.0
    assert item["seconds"] == item["max_seconds"] == 2.0
    assert item["observation_seconds"] == item["max_observation_seconds"] == 2.0
    assert a["stages"] == b["stages"] == {"Budget.check": {"count": 1, "inclusive_seconds": 5.0}}
    assert a["quota_count_samples"] == b["quota_count_samples"] == [{"total": 3, "source_total": 2}]
    assert a["sql"] is not b["sql"]
    assert a["quota_count_samples"][0] is not b["quota_count_samples"][0]
    assert "private-" not in json.dumps([a, b]) and "private_value" not in json.dumps([a, b])


def test_fanout_preserves_outer_and_separate_nested_root_operations(tmp_path):
    outer, outer_records = _memory_recorder(tmp_path)
    inner, inner_records = _memory_recorder(tmp_path)
    result, calls = object(), []

    def query(self, sql):
        calls.append(sql)
        return result

    sql = inner.sql(outer.sql(query))
    stage = inner.stage(outer.stage(lambda self: sql(self, "SELECT value"), "Budget.check"), "Budget.check")
    intent = inner.operation(lambda self: stage(self), "Root._intent")
    finish = inner.operation(lambda self: stage(self), "Root._finish")

    def execute(self):
        assert intent(self) is result
        assert finish(self) is result
        return stage(self)

    assert outer.operation(execute, "Assisted.execute")(object()) is result
    assert calls == ["SELECT value"] * 3
    assert len(outer_records) == 1 and len(inner_records) == 2
    assert [m["operation"] for m in inner_records] == ["Root._intent", "Root._finish"]
    assert next(iter(outer_records[0]["sql"].values()))["count"] == 3
    assert outer_records[0]["stages"]["Budget.check"]["count"] == 3
    for metric in inner_records:
        assert next(iter(metric["sql"].values()))["count"] == 1
        assert metric["stages"]["Budget.check"]["count"] == 1
    assert outer.local.metric is inner.local.metric is None


@pytest.mark.parametrize("active", ["first", "second", "neither"])
@pytest.mark.parametrize("fail", [False, True])
def test_shared_dispatch_with_one_or_zero_active_recorders(tmp_path, monkeypatch, active, fail):
    first, first_records = _memory_recorder(tmp_path)
    second, second_records = _memory_recorder(tmp_path)
    result, error, calls, observations = object(), ValueError("private-business"), [], []

    def query(self, *, statement):
        calls.append(statement)
        if fail:
            raise error
        return result

    sql = second.sql(first.sql(query))
    stage = second.stage(first.stage(sql, "Budget.check"), "Budget.check")
    def call(self):
        return stage(self, statement="SELECT private_value")

    if active == "first":
        call = first.operation(call, "First.execute")
    elif active == "second":
        call = second.operation(call, "Second.execute")
    else:
        def forbidden(*args, **kwargs):
            observations.append("unexpected")
            pytest.fail("inactive dispatch performed observation")

        monkeypatch.setattr(diagnostics, "time", SimpleNamespace(perf_counter=forbidden, thread_time=forbidden))
        monkeypatch.setattr(diagnostics, "hashlib", SimpleNamespace(sha256=forbidden))
        monkeypatch.setattr(diagnostics, "_callsite", forbidden)
        monkeypatch.setattr(diagnostics, "_error_class", forbidden)
    if fail:
        assert _raised(lambda: call(object())) is error
    else:
        assert call(object()) is result
    assert calls == ["SELECT private_value"]
    assert observations == []
    assert len(first_records) == int(active == "first")
    assert len(second_records) == int(active == "second")
    for metric in first_records + second_records:
        assert next(iter(metric["sql"].values()))["count"] == 1
        assert metric["stages"]["Budget.check"]["count"] == 1
        if fail:
            assert metric["stage_errors"] == [{"stage": "Budget.check", "error_class": "ValueError"}]
    assert "private" not in json.dumps(first_records + second_records)


@pytest.mark.parametrize("kind", ["sql", "stage"])
@pytest.mark.parametrize("fault", ["none", "before", "engine", "after"])
def test_copied_native_metadata_does_not_bypass_foreign_adapter(tmp_path, kind, fault):
    first, first_records = _memory_recorder(tmp_path)
    second, second_records = _memory_recorder(tmp_path)
    result, error, order = object(), ValueError("private-fault"), []
    cause, context = LookupError("private-cause"), LookupError("private-context")
    error.__cause__, error.__context__ = cause, context

    def query(self, *, statement):
        order.append("engine")
        if fault == "engine":
            raise error
        return result

    def wrap(recorder, original):
        return recorder.sql(original) if kind == "sql" else recorder.stage(original, "Budget.check")

    native = wrap(first, query)

    @functools.wraps(native)
    def adapter(self, *args, **kwargs):
        order.append("before")
        if fault == "before":
            raise error
        value = native(self, *args, **kwargs)
        order.append("after")
        if fault == "after":
            raise error
        return value

    wrapped = wrap(second, adapter)
    operation = first.operation(second.operation(
        lambda self: wrapped(self, statement="SELECT private_value"), "Second.execute"), "First.execute")
    if fault == "none":
        assert operation(object()) is result
    else:
        assert _raised(lambda: operation(object())) is error
        assert error.__cause__ is cause and error.__context__ is context
        assert error.__suppress_context__ is True
    assert order == ({"before": ["before"], "engine": ["before", "engine"]}.get(
        fault, ["before", "engine", "after"]))
    a, b = first_records[0], second_records[0]
    if kind == "sql":
        assert sum(item["count"] for item in a["sql"].values()) == int(fault != "before")
        assert sum(item["count"] for item in b["sql"].values()) == 1
    else:
        assert a["stages"].get("Budget.check", {}).get("count", 0) == int(fault != "before")
        assert b["stages"]["Budget.check"]["count"] == 1
        assert len(a.get("stage_errors", [])) == int(fault == "engine")
        assert len(b.get("stage_errors", [])) == int(fault != "none")
    assert "private" not in json.dumps([a, b])


def test_different_stage_labels_keep_inclusive_nesting(tmp_path, monkeypatch):
    first, first_records = _memory_recorder(tmp_path)
    second, second_records = _memory_recorder(tmp_path)
    ticks, calls, value = iter(range(8)), [], object()
    monkeypatch.setattr(diagnostics, "time", SimpleNamespace(perf_counter=lambda: next(ticks)))

    def work(self):
        calls.append("work")
        return value

    stages = second.stage(first.stage(work, "First.check"), "Second.check")
    operation = first.operation(second.operation(stages, "Second.execute"), "First.execute")
    assert operation(object()) is value and calls == ["work"]
    assert first_records[0]["stages"] == {"First.check": {"count": 1, "inclusive_seconds": 1.0}}
    assert second_records[0]["stages"] == {"Second.check": {"count": 1, "inclusive_seconds": 3.0}}


@pytest.mark.parametrize("kind", ["sql", "stage"])
def test_restoring_previous_wrapper_restores_recorder_membership(tmp_path, kind):
    first, first_records = _memory_recorder(tmp_path)
    second, second_records = _memory_recorder(tmp_path)
    value, calls = object(), []

    def query(self, statement):
        calls.append(statement)
        return value

    def wrap(recorder, original):
        return recorder.sql(original) if kind == "sql" else recorder.stage(original, "Budget.check")

    original_installation = wrap(first, query)
    shared_installation = wrap(second, original_installation)
    for installed in (shared_installation, original_installation):
        def call_installed(self, installed=installed):
            return installed(self, "SELECT value")

        operation = first.operation(second.operation(
            call_installed, "Second.execute"), "First.execute")
        assert operation(object()) is value
    field = "sql" if kind == "sql" else "stages"
    assert len(calls) == 2
    assert all(sum(item["count"] for item in metric[field].values()) == 1 for metric in first_records)
    assert sum(item["count"] for item in second_records[0][field].values()) == 1
    assert second_records[1][field] == {}


def test_sql_depth_belongs_to_each_active_recorder(tmp_path):
    outer, outer_records = _memory_recorder(tmp_path)
    inner, inner_records = _memory_recorder(tmp_path)
    value, calls = object(), []

    class Engine:
        def execute(self, sql):
            calls.append(sql)
            return value

        def query_one(self, sql):
            # A second scope begins inside the first scope's engine call.
            return inner.operation(lambda self: self.execute(sql), "Root._intent")(self)

    for name in ("execute", "query_one"):
        setattr(Engine, name, inner.sql(outer.sql(getattr(Engine, name))))
    engine = Engine()
    operation = outer.operation(lambda self: engine.query_one("SELECT value"), "Assisted.execute")
    assert operation(object()) is value and calls == ["SELECT value"]
    for metric in (outer_records[0], inner_records[0]):
        assert len(metric["sql"]) == 1 and next(iter(metric["sql"].values()))["count"] == 1
    assert outer.local.sql_depth is inner.local.sql_depth is False


@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_shared_failure_keeps_exact_exception_and_each_stage_error(tmp_path, error_type):
    first, first_records = _memory_recorder(tmp_path)
    second, second_records = _memory_recorder(tmp_path)
    error, calls = error_type("private-business"), []
    cause, context = ValueError("private-cause"), ValueError("private-context")
    error.__cause__, error.__context__ = cause, context

    def query(self, sql):
        calls.append(sql)
        raise error

    sql = second.sql(first.sql(query))
    stage = second.stage(first.stage(sql, "Budget.check"), "Budget.check")
    operation = first.operation(second.operation(
        lambda self: stage(self, "SELECT private_value"), "Root._intent"), "Assisted.execute")
    with pytest.raises(error_type) as caught:
        operation(object())
    assert caught.value is error and calls == ["SELECT private_value"]
    assert error.__cause__ is cause and error.__context__ is context
    assert error.__suppress_context__ is True
    label = "ValueError" if error_type is ValueError else "OTHER_EXCEPTION"
    for metric in first_records + second_records:
        assert next(iter(metric["sql"].values()))["count"] == 1
        assert metric["stages"]["Budget.check"]["count"] == 1
        assert metric["stage_errors"] == [{"stage": "Budget.check", "error_class": label}]
        assert metric["error_class"] == label and metric["boundary"] == "OPERATION_ESCAPE"
    assert first.local.sql_depth is second.local.sql_depth is False
    assert "private" not in json.dumps(first_records + second_records)


def test_shared_observers_enforce_family_sample_and_error_caps_independently(tmp_path, monkeypatch):
    first, first_records = _memory_recorder(tmp_path)
    second, second_records = _memory_recorder(tmp_path)
    real_time, calls, error = diagnostics.time, [], ValueError("private-stage-error")

    def unavailable():
        raise OSError("private-cpu-error")

    monkeypatch.setattr(diagnostics, "time", SimpleNamespace(
        perf_counter=real_time.perf_counter, thread_time=unavailable))

    def query(self, sql, value):
        calls.append(value)
        return {"total": value, "source_total": value, "secret": "private-row"}

    sql = second.sql(first.sql(query))
    stage = second.stage(first.stage(lambda self: _raise(error), "Budget.check"), "Budget.check")

    def run(self, start, stop):
        for value in range(start, stop):
            result = sql(self, f"SELECT private_value_{value}", value)
            assert _raised(lambda: stage(self)) is error
        return result

    shared = second.operation(lambda self: run(self, 64, 144), "Root._intent")

    def execute(self):
        run(self, 0, 64)
        return shared(self)

    assert first.operation(execute, "Assisted.execute")(object())["total"] == 143
    assert calls == list(range(144))
    a, b = first_records[0], second_records[0]
    assert len(a["sql"]) == len(b["sql"]) == 64
    assert a["sql_overflow_count"] == 80 and b["sql_overflow_count"] == 16
    assert set(a["sql"]).isdisjoint(b["sql"])
    expected = [{"total": value, "source_total": value} for value in range(136, 144)]
    assert a["quota_count_samples"] == b["quota_count_samples"] == expected
    assert a["stages"]["Budget.check"]["count"] == 144
    assert b["stages"]["Budget.check"]["count"] == 80
    for metric in (a, b):
        assert len(metric["stage_errors"]) == 8
        assert metric["diagnostic_error_classes"] == ["DIAGNOSTIC_FAILURE"] * 4
        assert all(item["thread_cpu_samples"] == 0 for item in metric["sql"].values())
    assert "private" not in json.dumps([a, b])


@pytest.mark.parametrize("broken_field", ["sql", "stages", "quota_count_samples", "stage_errors"])
@pytest.mark.parametrize("bad_observer", ["first", "second"])
def test_one_observer_accumulator_fault_does_not_starve_another(
        tmp_path, broken_field, bad_observer):
    first, first_records = _memory_recorder(tmp_path)
    second, second_records = _memory_recorder(tmp_path)
    error, calls = ValueError("private-business"), []

    def unavailable(*args, **kwargs):
        raise KeyboardInterrupt("private-accumulator")

    class UnavailableMap(dict):
        __contains__ = unavailable
        setdefault = unavailable

    class UnavailableList(list):
        append = unavailable

    def query(self, sql):
        calls.append(sql)
        return {"total": 1, "source_total": 1, "secret": "private-row"}

    sql = second.sql(first.sql(query))
    stage = second.stage(first.stage(lambda self: _raise(error), "Budget.check"), "Budget.check")
    bad = first if bad_observer == "first" else second

    def run(self):
        bad.local.metric[broken_field] = (UnavailableMap() if broken_field in {"sql", "stages"}
                                          else UnavailableList())
        for _ in range(6):
            result = sql(self, "SELECT private_value")
            assert _raised(lambda: stage(self)) is error
        return result

    operation = first.operation(second.operation(run, "Root._intent"), "Assisted.execute")
    assert operation(object())["total"] == 1 and calls == ["SELECT private_value"] * 6
    good_records, bad_records = ((second_records, first_records) if bad_observer == "first"
                                else (first_records, second_records))
    good = good_records[0]
    assert next(iter(good["sql"].values()))["count"] == 6
    assert good["stages"]["Budget.check"]["count"] == 6
    assert good["quota_count_samples"] == [{"total": 1, "source_total": 1}] * 6
    assert good["stage_errors"] == [{"stage": "Budget.check", "error_class": "ValueError"}] * 6
    assert "diagnostic_error_classes" not in good
    assert bad_records[0]["diagnostic_error_classes"] == ["DIAGNOSTIC_FAILURE"] * 4
    assert "private" not in json.dumps(first_records + second_records)


@pytest.mark.parametrize("failure", ["wall", "cpu", "hash", "callsite", "quota_read", "classify"])
@pytest.mark.parametrize("fail", [False, True])
def test_shared_observation_faults_preserve_forwarding_and_report_to_each_scope(
        tmp_path, monkeypatch, failure, fail):
    first, first_records = _memory_recorder(tmp_path)
    second, second_records = _memory_recorder(tmp_path)
    error, calls, real_time = ValueError("private-business"), [], diagnostics.time

    def unavailable(*args, **kwargs):
        raise KeyboardInterrupt("private-diagnostic")

    class UnavailableCounts(dict):
        get = unavailable

    result = (UnavailableCounts(total=1, source_total=1) if failure == "quota_read" else object())
    if failure in {"wall", "cpu"}:
        monkeypatch.setattr(diagnostics, "time", SimpleNamespace(
            perf_counter=unavailable if failure == "wall" else real_time.perf_counter,
            thread_time=unavailable if failure == "cpu" else real_time.thread_time))
    elif failure == "hash":
        monkeypatch.setattr(diagnostics, "hashlib", SimpleNamespace(sha256=unavailable))
    elif failure == "callsite":
        monkeypatch.setattr(diagnostics, "_callsite", unavailable)
    elif failure == "classify":
        monkeypatch.setattr(diagnostics, "_error_class", unavailable)

    def query(self, *, sql):
        calls.append(sql)
        if fail:
            raise error
        return result

    sql = second.sql(first.sql(query))
    stage = second.stage(first.stage(sql, "Budget.check"), "Budget.check")
    operation = first.operation(second.operation(
        lambda self: stage(self, sql="SELECT private_value"), "Root._intent"), "Assisted.execute")
    if fail:
        assert _raised(lambda: operation(object())) is error
    else:
        assert operation(object()) is result
    assert calls == ["SELECT private_value"]
    assert first.local.sql_depth is second.local.sql_depth is False
    for metric in first_records + second_records:
        assert metric["stages"]["Budget.check"]["count"] == 1
        if failure not in {"hash", "callsite"}:
            assert next(iter(metric["sql"].values()))["count"] == 1
        diagnostic_failed = not ((failure == "quota_read" and fail) or (failure == "classify" and not fail))
        if diagnostic_failed:
            assert 1 <= len(metric["diagnostic_error_classes"]) <= 4
            assert set(metric["diagnostic_error_classes"]) == {"DIAGNOSTIC_FAILURE"}
        else:
            assert "diagnostic_error_classes" not in metric
    assert "private" not in json.dumps(first_records + second_records)


@pytest.mark.parametrize("fail", [False, True])
def test_one_recorder_output_failure_keeps_other_scope_and_original_outcome(tmp_path, capsys, fail):
    first, first_records = _memory_recorder(tmp_path)
    second, second_records = _memory_recorder(tmp_path)
    result, error, calls = object(), ValueError("private-business"), []

    def unavailable(metric):
        raise KeyboardInterrupt("private-output-path")

    second._write = unavailable

    def query(self, sql):
        calls.append(sql)
        if fail:
            raise error
        return result

    sql = second.sql(first.sql(query))
    stage = second.stage(first.stage(sql, "Budget.check"), "Budget.check")
    operation = first.operation(second.operation(
        lambda self: stage(self, "SELECT private_value"), "Root._intent"), "Assisted.execute")
    if fail:
        assert _raised(lambda: operation(object())) is error
    else:
        assert operation(object()) is result
    assert calls == ["SELECT private_value"]
    assert second_records == [] and len(first_records) == 1
    assert next(iter(first_records[0]["sql"].values()))["count"] == 1
    assert first_records[0]["stages"]["Budget.check"]["count"] == 1
    assert capsys.readouterr().err == "Native SQL diagnostic unavailable: DIAGNOSTIC_FAILURE\n"
    assert "private" not in json.dumps(first_records)


def test_shared_recorders_respect_full_output_bound(tmp_path):
    output, cap = tmp_path / "profile.jsonl", 64 * 1024 * 1024
    with output.open("wb") as stream:
        stream.truncate(cap)
    first = NativeCommandDiagnostics(output, "first-case")
    second = NativeCommandDiagnostics(output, "second-case")
    result, calls = object(), []

    def query(self, sql):
        calls.append(sql)
        return result

    sql = second.sql(first.sql(query))
    operation = first.operation(second.operation(
        lambda self: sql(self, "SELECT value"), "Root._intent"), "Assisted.execute")
    assert operation(object()) is result and calls == ["SELECT value"]
    assert output.stat().st_size == cap
