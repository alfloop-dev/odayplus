"""Diagnostic forwarding cannot change outcomes or disclose request data."""
import json

import pytest

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
