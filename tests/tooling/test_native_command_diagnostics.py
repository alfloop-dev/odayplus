"""Diagnostic forwarding cannot change outcomes or disclose request data."""
import json
from types import SimpleNamespace

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
    assert capsys.readouterr().err == "Native SQL diagnostic unavailable: OSError\n"


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
    assert metric["diagnostic_error_classes"] == ["OSError", "OSError"]
