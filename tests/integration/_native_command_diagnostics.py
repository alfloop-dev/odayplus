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
import traceback
from datetime import UTC, datetime
from pathlib import Path

import pytest

_LOCK = threading.Lock()
_CASES = itertools.count(1)
_MAX_BYTES = 64 * 1024 * 1024
_MAX_QUERIES = 64


def diagnostics_requested(nodeid: str, value: str | None) -> bool:
    return value == "1" and nodeid.startswith("tests/integration/test_native_intake_")


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
    def _observation_error(metric, error):
        errors = metric.setdefault("diagnostic_error_classes", [])
        if len(errors) < 4:
            errors.append(type(error).__name__)

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
            started = time.perf_counter()
            try:
                return original(instance, *args, **kwargs)
            except Exception as error:
                metric["error_class"] = type(error).__name__
                metric["error_frames_no_values"] = [
                    f"{Path(f.filename).name}:{f.name}:{f.lineno}"
                    for f in traceback.extract_tb(error.__traceback__)[-6:]]
                raise
            finally:
                self.local.metric = None
                try:
                    metric["elapsed_seconds"] = time.perf_counter() - started
                    metric["completed_at"] = datetime.now(UTC).isoformat()
                    self._write(metric)
                except Exception as error:
                    # Diagnostic storage must not replace a business result or
                    # the exact original exception, including deadline errors.
                    try:
                        print("Native SQL diagnostic unavailable: " + type(error).__name__, file=sys.stderr)
                    except Exception:
                        pass
        return measured

    def sql(self, original):
        parameters = list(inspect.signature(original).parameters)
        statement_parameter = parameters[1] if len(parameters) > 1 else None

        @functools.wraps(original)
        def measured(instance, *args, **kwargs):
            metric = getattr(self.local, "metric", None)
            if metric is None or getattr(self.local, "sql_depth", False):
                return original(instance, *args, **kwargs)
            self.local.sql_depth = True
            cpu_started = self._thread_cpu(metric)
            started, result = time.perf_counter(), None
            try:
                return_value = original(instance, *args, **kwargs)
                result = return_value
                return return_value
            finally:
                # Capture the forwarded engine window before hash/callsite
                # observation. No clock, root deadline or GC setting changes.
                finished = time.perf_counter()
                cpu_finished = self._thread_cpu(metric)
                self.local.sql_depth = False
                try:
                    statement = args[0] if args else kwargs.get(statement_parameter, "")
                    key = hashlib.sha256(str(statement).encode()).hexdigest()
                    if key not in metric["sql"] and len(metric["sql"]) < _MAX_QUERIES:
                        metric["sql"][key] = {"count": 0, "seconds": 0.0, "max_seconds": 0.0,
                                              "source_callsite": _callsite(),
                                              "engine_wall_seconds": 0.0, "max_engine_wall_seconds": 0.0,
                                              "thread_cpu_seconds": 0.0, "max_thread_cpu_seconds": 0.0,
                                              "thread_cpu_samples": 0,
                                              "observation_seconds": 0.0, "max_observation_seconds": 0.0}
                    if key in metric["sql"]:
                        item = metric["sql"][key]
                        elapsed = time.perf_counter() - started
                        item["count"] += 1
                        item["seconds"] += elapsed
                        item["max_seconds"] = max(item["max_seconds"], elapsed)
                        wall = finished - started
                        item["engine_wall_seconds"] += wall
                        item["max_engine_wall_seconds"] = max(item["max_engine_wall_seconds"], wall)
                        if cpu_started is not None and cpu_finished is not None:
                            cpu = cpu_finished - cpu_started
                            item["thread_cpu_seconds"] += cpu
                            item["max_thread_cpu_seconds"] = max(item["max_thread_cpu_seconds"], cpu)
                            item["thread_cpu_samples"] += 1
                    else:
                        metric["sql_overflow_count"] = metric.get("sql_overflow_count", 0) + 1
                    samples = metric["quota_count_samples"]
                    if (isinstance(result, dict)
                            and all(type(result.get(k)) is int for k in ("total", "source_total"))):
                        samples.append({k: result[k] for k in ("total", "source_total")})
                        del samples[:-8]
                    if key in metric["sql"]:
                        # This is the measured observation interval; the last
                        # two accumulator writes themselves are not included.
                        observation = time.perf_counter() - finished
                        item["observation_seconds"] += observation
                        item["max_observation_seconds"] = max(item["max_observation_seconds"], observation)
                except Exception as error:
                    self._observation_error(metric, error)
        return measured

    def stage(self, original, label):
        @functools.wraps(original)
        def measured(instance, *args, **kwargs):
            metric = getattr(self.local, "metric", None)
            if metric is None:
                return original(instance, *args, **kwargs)
            started = time.perf_counter()
            try:
                return original(instance, *args, **kwargs)
            except Exception as error:
                errors = metric.setdefault("stage_errors", [])
                if len(errors) < 8:
                    errors.append({"stage": label, "error_class": type(error).__name__})
                raise
            finally:
                try:
                    item = metric["stages"].setdefault(label, {"count": 0, "inclusive_seconds": 0.0})
                    item["count"] += 1
                    item["inclusive_seconds"] += time.perf_counter() - started
                except Exception as error:
                    self._observation_error(metric, error)
        return measured


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
    for name in ("query", "query_one", "execute"):
        monkeypatch.setattr(PostgresEngine, name, recorder.sql(getattr(PostgresEngine, name)))
    for cls, name in [(NativeIntakeCommandBudget, "__call__"), (NativeIntakePendingCommands, "require_clear"),
            (NativeAssistedEntrySourceProof, "parent"), (NativeIntakeSourcePolicyRecheckOwner, "_waiting"),
            (NativeIntakeRetrievalQuota, "reserve"), (NativeIntakeRetrievalQuota, "reserve_storage"),
            (NativeIntakeRetrievalQuota, "release"), (NativeIntakeRetrievalIO, "_intent"),
            (NativeIntakeRetrievalIO, "_finish")]:
        monkeypatch.setattr(cls, name, recorder.stage(getattr(cls, name), cls.__name__ + "." + name))
    for cls, name in [(NativeAssistedEntryRequestOwner, "request"), (NativeIntakeSourcePolicyRecheckOwner, "request"),
            (NativeIntakeSourcePolicyRecheckOwner, "execute"), (NativeIntakeRetrievalOwner, "execute"),
            (NativeIntakeAssistedEntryOwner, "execute")]:
        monkeypatch.setattr(cls, name, recorder.operation(getattr(cls, name), cls.__name__ + "." + name))
    yield
