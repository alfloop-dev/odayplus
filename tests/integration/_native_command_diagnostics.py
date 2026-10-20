"""Opt-in forwarding diagnostics for native PostgreSQL integration cases.

Only query hashes, source call sites, durations and anonymous quota counts are
retained. Arguments, SQL text, row payloads and exception messages are excluded.
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import itertools
import json
import os
import sys
import threading
import time
import weakref
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import pytest

from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_identity import (
    NativeIntakeIdentityConflict,
    NativeIntakeIdentityOwner,
)
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import (
    NativeParserCommandBudget,
    NativeParserSQLClock,
)
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_source_policy import (
    NativeIntakeSourcePolicyConflict,
    NativeIntakeSourcePolicyOwner,
)

_LOCK = threading.Lock()
_CASES = itertools.count(1)
_MAX_BYTES = 64 * 1024 * 1024
_MAX_QUERIES = 64
_MAX_TRACEBACK_SCAN = 64

# Capture the explicitly read bound functions before fixture/test mutation.
# No invocation, source lookup, name matching or __wrapped__ traversal grants
# trust. Code objects must match by identity (even equal copies are unknown).
_THROW_SITES = (
    (NativeIntakeIdentityOwner.exact_snapshot.__code__, NativeIntakeIdentityConflict,
     "EXACT_SOURCE_PROOF_CHECK", "NativeIntakeIdentityOwner.exact_snapshot"),
    (NativeIntakeIdentityOwner._existing_target.__code__, NativeIntakeIdentityConflict,
     "EXACT_GRAPH_PROOF_CHECK", "NativeIntakeIdentityOwner._existing_target"),
    (NativeIntakeCommandBudget.check_in_memory.__code__, NativeAssistedEntryConflict,
     "COMMAND_MEMORY_DEADLINE", "NativeIntakeCommandBudget.check_in_memory"),
    (NativeIntakeSourcePolicyOwner._admission_budget.__code__, NativeIntakeSourcePolicyConflict,
     "SOURCE_SQL_DEADLINE", "NativeIntakeSourcePolicyOwner._admission_budget"),
    (NativeParserSQLClock._admission_budget.__code__, NativeParserConflict,
     "PARSER_SQL_DEADLINE", "NativeParserSQLClock._admission_budget"),
    (NativeParserCommandBudget.check_in_memory.__code__, NativeParserConflict,
     "PARSER_MEMORY_DEADLINE", "NativeParserCommandBudget.check_in_memory"),
)
_ERROR_CLASSES = (
    (NativeIntakeIdentityConflict, "NativeIntakeIdentityConflict"),
    (NativeAssistedEntryConflict, "NativeAssistedEntryConflict"),
    (NativeIntakeSourcePolicyConflict, "NativeIntakeSourcePolicyConflict"),
    (NativeParserConflict, "NativeParserConflict"),
    (ValueError, "ValueError"), (OSError, "OSError"), (RuntimeError, "RuntimeError"),
    (TypeError, "TypeError"), (AssertionError, "AssertionError"),
)


def _error_class(error):
    for expected, label in _ERROR_CLASSES:
        if type(error) is expected:
            return label
    return "OTHER_EXCEPTION"


def _error_snapshot(error, boundary):
    frames = deque(maxlen=6)
    reason = "UNCLASSIFIED"
    # Bypass exception-defined accessors; never inspect args, cause, context,
    # source lines, locals, filenames or runtime class/function names.
    trace = BaseException.__traceback__.__get__(error)
    try:
        for _ in range(_MAX_TRACEBACK_SCAN):
            if trace is None:
                break
            symbol, line = "UNKNOWN_FRAME", 0
            for code, expected, site, trusted_symbol in _THROW_SITES:
                if trace.tb_frame.f_code is code:
                    if type(trace.tb_lineno) is int:
                        symbol, line = trusted_symbol, trace.tb_lineno
                        if trace.tb_next is None and type(error) is expected:
                            reason = site
                    break
            frames.append({"symbol": symbol, "line": line})
            trace = trace.tb_next
        if trace is not None:
            # A bounded walk that did not reach the actual throw cannot prove
            # a reason, even if it encountered a trusted budget frame.
            frames.append({"symbol": "UNKNOWN_FRAME", "line": 0})
        return {"boundary": boundary, "error_class": _error_class(error),
                "reason_site": reason,
                "reason_provenance": ("TRUSTED_CLASS_AND_BOUND_THROW_SITE"
                                      if reason != "UNCLASSIFIED" else "UNCLASSIFIED"),
                "error_frames_no_values": list(frames)}
    finally:
        del trace


def diagnostics_requested(nodeid: str, value: str | None) -> bool:
    return value == "1" and nodeid.startswith(("tests/integration/test_native_intake_",
        "tests/integration/test_native_matching_case_command_postgresql.py::"))


def _callsite() -> list[str]:
    frame = sys._getframe(2)
    try:
        result = []
        for _ in range(3):
            if frame is None:
                break
            result.append(f"{Path(frame.f_code.co_filename).name}:{frame.f_code.co_name}:{frame.f_lineno}")
            frame = frame.f_back
        return result
    finally:
        del frame


class _ObservationDispatch:
    """Share only a directly supplied, identity-checked native wrapper.

    Registrations are immutable per returned wrapper: restoring a previous
    installation also restores its original recorder coverage. functools
    metadata copied onto a foreign adapter cannot authenticate that adapter.
    """

    def __init__(self, original, recorder, kind, label=None):
        previous = getattr(original, "_native_observation_dispatch", None)
        if (type(previous) is _ObservationDispatch and previous.wrapper is not None
                and previous.wrapper() is original
                and previous.kind == kind and previous.label == label):
            self.original = previous.original
            self.recorders = previous.recorders
            if not any(item is recorder for item in self.recorders):
                self.recorders += (recorder,)
        else:
            self.original, self.recorders = original, (recorder,)
        self.kind, self.label = kind, label
        self.wrapper = None

    def bind(self, wrapper):
        self.wrapper = weakref.ref(wrapper)
        wrapper._native_observation_dispatch = self
        return wrapper

    def active(self):
        observers = []
        for recorder in self.recorders:
            metric = None
            try:
                metric = getattr(recorder.local, "metric", None)
                if metric is None:
                    continue
                if self.kind == "sql":
                    if getattr(recorder.local, "sql_depth", False):
                        continue
                    recorder.local.sql_depth = True
                observers.append((recorder, metric))
            except BaseException:
                recorder._observation_error(metric)
        return observers

    @staticmethod
    def error(observers):
        for recorder, metric in observers:
            recorder._observation_error(metric)

    @staticmethod
    def clock(observers, *, cpu=False):
        try:
            return time.thread_time() if cpu else time.perf_counter()
        except BaseException:
            _ObservationDispatch.error(observers)
            return None


class NativeCommandDiagnostics:
    def __init__(self, output: Path, case: str):
        self.output = output
        self.case = case.split("[", 1)[0][:240]
        self.case_number = next(_CASES)
        self.local = threading.local()

    def _write(self, metric):
        encoded = json.dumps(metric, sort_keys=True, allow_nan=False) + "\n"
        with _LOCK:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            size = self.output.stat().st_size if self.output.exists() else 0
            if size + len(encoded.encode()) > _MAX_BYTES:
                return
            with self.output.open("a", encoding="utf-8") as out:
                out.write(encoded)
                out.flush()

    @staticmethod
    def _observation_error(metric, error=None):
        try:
            errors = metric.setdefault("diagnostic_error_classes", [])
            if len(errors) < 4:
                errors.append("DIAGNOSTIC_FAILURE")
        except BaseException:
            pass

    def negative(self, original):
        @functools.wraps(original)
        def observed(instance, *args, **kwargs):
            try:
                metric = getattr(self.local, "metric", None)
            except BaseException:
                metric = None
            if metric is None:
                return original(instance, *args, **kwargs)
            try:
                errors = metric.setdefault("handled_errors", [])
                if len(errors) >= 2:
                    metric["handled_errors_overflow"] = True
                else:
                    error = args[1] if len(args) > 1 else kwargs.get("error")
                    errors.append(_error_snapshot(error, "MANUAL_NEGATIVE_INPUT"))
            except BaseException:
                self._observation_error(metric)
            return original(instance, *args, **kwargs)
        return observed

    def _thread_cpu(self, metric):
        try:
            return time.thread_time()
        except Exception as error:
            self._observation_error(metric, error)
            return None

    def operation(self, original, label):
        @functools.wraps(original)
        def measured(instance, *args, **kwargs):
            if getattr(self.local, "metric", None) is not None:
                return original(instance, *args, **kwargs)
            metric = {"case": self.case, "case_number": self.case_number, "operation": label,
                      "sql": {}, "stages": {}, "quota_count_samples": []}
            self.local.metric = metric
            started = None
            try:
                started = time.perf_counter()
            except BaseException:
                self._observation_error(metric)
            try:
                return original(instance, *args, **kwargs)
            except BaseException as error:
                try:
                    metric.update(_error_snapshot(error, "OPERATION_ESCAPE"))
                except BaseException:
                    self._observation_error(metric)
                raise
            finally:
                self.local.metric = None
                try:
                    if started is not None:
                        metric["elapsed_seconds"] = time.perf_counter() - started
                    metric["completed_at"] = datetime.now(UTC).isoformat()
                except BaseException:
                    self._observation_error(metric)
                try:
                    self._write(metric)
                except BaseException:
                    # Diagnostic storage must not replace a business result or
                    # the exact original exception, including deadline errors.
                    try:
                        print("Native SQL diagnostic unavailable: DIAGNOSTIC_FAILURE", file=sys.stderr)
                    except BaseException:
                        pass
        return measured

    def sql(self, original):
        dispatch = _ObservationDispatch(original, self, "sql")
        parameters = list(inspect.signature(dispatch.original).parameters)
        statement_parameter = parameters[1] if len(parameters) > 1 else None

        @functools.wraps(original)
        def measured(instance, *args, **kwargs):
            observers = dispatch.active()
            if not observers:
                return dispatch.original(instance, *args, **kwargs)
            cpu_started = dispatch.clock(observers, cpu=True)
            started, result = dispatch.clock(observers), None
            try:
                return_value = dispatch.original(instance, *args, **kwargs)
                result = return_value
                return return_value
            finally:
                # One raw forwarded engine window replaces nested observer
                # windows for exact native dispatches. Foreign adapters stay
                # inside their original forwarding order. Old profiles remain
                # evidence, not comparable proof of latency improvement/cause.
                finished = dispatch.clock(observers)
                cpu_finished = dispatch.clock(observers, cpu=True)
                for recorder, metric in observers:
                    try:
                        recorder.local.sql_depth = False
                    except BaseException:
                        recorder._observation_error(metric)
                key, callsite, items = None, None, []
                try:
                    statement = args[0] if args else kwargs.get(statement_parameter, "")
                    key = hashlib.sha256(str(statement).encode()).hexdigest()
                except BaseException:
                    dispatch.error(observers)
                for recorder, metric in observers:
                    try:
                        if key is None:
                            continue
                        if key not in metric["sql"] and len(metric["sql"]) < _MAX_QUERIES:
                            if callsite is None:
                                callsite = _callsite()
                            metric["sql"][key] = {
                                "count": 0, "seconds": 0.0, "max_seconds": 0.0,
                                "source_callsite": list(callsite),
                                "engine_wall_seconds": 0.0, "max_engine_wall_seconds": 0.0,
                                "thread_cpu_seconds": 0.0, "max_thread_cpu_seconds": 0.0,
                                "thread_cpu_samples": 0,
                                "observation_seconds": 0.0, "max_observation_seconds": 0.0}
                        if key in metric["sql"]:
                            items.append((recorder, metric, metric["sql"][key]))
                        else:
                            metric["sql_overflow_count"] = metric.get("sql_overflow_count", 0) + 1
                    except BaseException:
                        recorder._observation_error(metric)
                elapsed_at = dispatch.clock(observers) if items else None
                for recorder, metric, item in items:
                    try:
                        item["count"] += 1
                        if started is not None and elapsed_at is not None:
                            elapsed = elapsed_at - started
                            item["seconds"] += elapsed
                            item["max_seconds"] = max(item["max_seconds"], elapsed)
                        if started is not None and finished is not None:
                            wall = finished - started
                            item["engine_wall_seconds"] += wall
                            item["max_engine_wall_seconds"] = max(item["max_engine_wall_seconds"], wall)
                        if cpu_started is not None and cpu_finished is not None:
                            cpu = cpu_finished - cpu_started
                            item["thread_cpu_seconds"] += cpu
                            item["max_thread_cpu_seconds"] = max(item["max_thread_cpu_seconds"], cpu)
                            item["thread_cpu_samples"] += 1
                    except BaseException:
                        recorder._observation_error(metric)
                sample = None
                try:
                    if (isinstance(result, dict)
                            and all(type(result.get(k)) is int for k in ("total", "source_total"))):
                        sample = {k: result[k] for k in ("total", "source_total")}
                except BaseException:
                    dispatch.error(observers)
                if sample is not None:
                    for recorder, metric in observers:
                        try:
                            samples = metric["quota_count_samples"]
                            samples.append(dict(sample))
                            del samples[:-8]
                        except BaseException:
                            recorder._observation_error(metric)
                observation_at = dispatch.clock(observers) if items else None
                if finished is not None and observation_at is not None:
                    # Shared observation includes fanout, excluding these last
                    # accumulator writes. Overlapping recorder scopes must not
                    # be summed as independent work or command SLO evidence.
                    observation = observation_at - finished
                    for recorder, metric, item in items:
                        try:
                            item["observation_seconds"] += observation
                            item["max_observation_seconds"] = max(item["max_observation_seconds"], observation)
                        except BaseException:
                            recorder._observation_error(metric)
        return dispatch.bind(measured)

    def stage(self, original, label):
        dispatch = _ObservationDispatch(original, self, "stage", label)

        @functools.wraps(original)
        def measured(instance, *args, **kwargs):
            observers = dispatch.active()
            if not observers:
                return dispatch.original(instance, *args, **kwargs)
            started = dispatch.clock(observers)
            error_class = None
            try:
                return dispatch.original(instance, *args, **kwargs)
            except BaseException as error:
                try:
                    error_class = _error_class(error)
                except BaseException:
                    dispatch.error(observers)
                raise
            finally:
                finished = dispatch.clock(observers)
                for recorder, metric in observers:
                    if error_class is not None:
                        try:
                            errors = metric.setdefault("stage_errors", [])
                            if len(errors) < 8:
                                errors.append({"stage": label, "error_class": error_class})
                        except BaseException:
                            recorder._observation_error(metric)
                    try:
                        item = metric["stages"].setdefault(label, {"count": 0, "inclusive_seconds": 0.0})
                        item["count"] += 1
                        if started is not None and finished is not None:
                            item["inclusive_seconds"] += finished - started
                    except BaseException:
                        recorder._observation_error(metric)
        return dispatch.bind(measured)


@pytest.fixture(autouse=True)
def native_command_diagnostics(request, monkeypatch):
    if not diagnostics_requested(request.node.nodeid, os.environ.get("ODP_NATIVE_SQL_PROFILE")):
        yield
        return
    from modules.listing.application.native_intake_assisted_entry import (
        NativeIntakeAssistedEntryOwner,
    )
    from modules.listing.application.native_intake_assisted_entry_request import (
        NativeAssistedEntryRequestOwner,
    )
    from modules.listing.application.native_intake_assisted_entry_settlement import (
        NativeAssistedEntrySettlement,
    )
    from modules.listing.application.native_intake_assisted_entry_source import (
        NativeAssistedEntrySourceProof,
    )
    from modules.listing.application.native_intake_pending_commands import (
        NativeIntakeCommandBudget,
        NativeIntakePendingCommands,
    )
    from modules.listing.application.native_intake_retrieval import NativeIntakeRetrievalOwner
    from modules.listing.application.native_intake_retrieval_io import NativeIntakeRetrievalIO
    from modules.listing.application.native_intake_retrieval_quota import NativeIntakeRetrievalQuota
    from modules.listing.application.native_intake_source_policy_recheck import (
        NativeIntakeSourcePolicyRecheckOwner,
    )
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    recorder = NativeCommandDiagnostics(
        request.config.rootpath / ".odp_data/ci/native-intake-command-profile.jsonl", request.node.nodeid)
    monkeypatch.setattr(NativeAssistedEntrySettlement, "negative",
                        recorder.negative(NativeAssistedEntrySettlement.negative))
    for name in ("query", "query_one", "execute"):
        monkeypatch.setattr(PostgresEngine, name, recorder.sql(getattr(PostgresEngine, name)))
    for cls, name in [(NativeIntakeCommandBudget, "__call__"), (NativeIntakePendingCommands, "require_clear"),
            (NativeAssistedEntrySourceProof, "parent"), (NativeIntakeSourcePolicyRecheckOwner, "_waiting"),
            (NativeIntakeSourcePolicyRecheckOwner, "_request_event"),
            (NativeIntakeSourcePolicyRecheckOwner, "_root_identity"),
            (NativeIntakeRetrievalQuota, "reserve"), (NativeIntakeRetrievalQuota, "reserve_storage"),
            (NativeIntakeRetrievalQuota, "release"), (NativeIntakeRetrievalIO, "_intent"),
            (NativeIntakeRetrievalIO, "_finish")]:
        monkeypatch.setattr(cls, name, recorder.stage(getattr(cls, name), cls.__name__ + "." + name))
    for cls, name in [(NativeAssistedEntryRequestOwner, "request"), (NativeIntakeSourcePolicyRecheckOwner, "request"),
            (NativeIntakeSourcePolicyRecheckOwner, "admit_event"),
            (NativeIntakeIdentityOwner, "confirm_exact_case"),
            (NativeIntakeSourcePolicyRecheckOwner, "execute"), (NativeIntakeRetrievalOwner, "execute"),
            (NativeIntakeAssistedEntryOwner, "execute")]:
        monkeypatch.setattr(cls, name, recorder.operation(getattr(cls, name), cls.__name__ + "." + name))
    yield
