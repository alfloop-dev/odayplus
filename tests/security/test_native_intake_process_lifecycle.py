"""Lifecycle capability tests, authored for Root to execute after integration.

Synthetic cases exercise control flow only; their doubles are not OS proof.
Real owned-child cases use actual wait/PID/group checks before the independent
fixture finalizer. No SQL or network is needed by these cases.
"""

from __future__ import annotations

import copy
import gc
import inspect
import os
import pickle
import signal
import subprocess
import sys
import threading
import time
import traceback
import weakref
from collections.abc import Callable
from contextvars import Context, copy_context

import pytest

from modules.external_data.application import native_intake_process as native
from modules.external_data.application import native_intake_process_lifecycle as lifecycle
from tests.security.test_native_intake_process_cleanup import _SyntheticIO

_PRIVATE = "private-lifecycle-request-credential-error"
_CODE = "NATIVE_PROCESS_COMPLETION_UNAVAILABLE"


def _assert_unavailable(observation):
    with pytest.raises(lifecycle.NativeProcessCompletionUnavailable) as caught:
        lifecycle._consume_native_process_completion(observation)
    assert type(caught.value) is lifecycle.NativeProcessCompletionUnavailable
    assert caught.value.args == (_CODE,)
    assert caught.value.code == str(caught.value) == _CODE
    assert _PRIVATE not in "".join(traceback.format_exception(caught.value))


def _preflight_rejection():
    # Actual run, rejected before any possible Popen; no synthetic run scope.
    with pytest.raises(native.NativeIORejected, match="^NATIVE_IO_DEADLINE_REJECTED$"):
        native.NativeIntakeProcessRunner().run({}, deadline=True, guard=lambda: None)


@pytest.fixture
def synthetic(monkeypatch):
    return _SyntheticIO(monkeypatch)


def test_public_signature_has_no_observation_or_process_claim_parameter():
    signature = inspect.signature(native.NativeIntakeProcessRunner.run)
    parameters = signature.parameters
    assert list(parameters) == ["self", "request", "deadline", "guard", "credential"]
    assert parameters["self"].kind is parameters["request"].POSITIONAL_OR_KEYWORD
    assert parameters["request"].kind is parameters["request"].POSITIONAL_OR_KEYWORD
    for name in ("deadline", "guard", "credential"):
        assert parameters[name].kind is parameters[name].KEYWORD_ONLY
    for name in ("self", "request", "deadline", "guard"):
        assert parameters[name].default is inspect.Parameter.empty
    assert parameters["credential"].default is None
    assert inspect.get_annotations(native.NativeIntakeProcessRunner.run, eval_str=True) == {
        "request": dict, "deadline": float, "guard": Callable[[], None],
        "credential": str | None, "return": dict,
    }


@pytest.mark.parametrize("outcome", ("success", "ordinary", "rejected", "unreaped", "interrupt", "exit"))
def test_synthetic_inactive_wrapper_forwards_exact_references_and_outcome(monkeypatch, outcome):
    instance = native.NativeIntakeProcessRunner()
    request, deadline, guard, credential, result = {}, object(), object(), object(), {}
    error = {
        "success": None, "ordinary": RuntimeError(_PRIVATE),
        "rejected": native.NativeIORejected("NATIVE_IO_LEASE_LOST", status=403),
        "unreaped": native.NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED"),
        "interrupt": KeyboardInterrupt(), "exit": SystemExit(7),
    }[outcome]
    calls = []

    def body(self, forwarded, **kwargs):
        calls.append(kwargs)
        assert self is instance and forwarded is request
        assert kwargs["deadline"] is deadline and kwargs["guard"] is guard
        assert kwargs["credential"] is credential and kwargs["_invocation"] is None
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(native.NativeIntakeProcessRunner, "_run_owned", body)
    if error is None:
        assert instance.run(request, deadline=deadline, guard=guard, credential=credential) is result
    else:
        with pytest.raises(type(error)) as caught:
            instance.run(request, deadline=deadline, guard=guard, credential=credential)
        assert caught.value is error
    assert len(calls) == 1
    assert lifecycle._current.get() is None


@pytest.mark.parametrize("observed", (False, True))
def test_synthetic_fixed_command_environment_caps_and_credentials_preserved(synthetic, observed):
    if observed:
        with lifecycle._observe_native_process() as observation:
            result = synthetic.run(credential=_PRIVATE)
        assert lifecycle._consume_native_process_completion(observation) == "REAPED"
    else:
        result = synthetic.run(credential=_PRIVATE)
    assert result == {"accepted": True}
    assert native.MAX_SECONDS == 10 and native.CLEANUP_SECONDS == 1
    assert native.MAX_INPUT == native.MAX_OUTPUT == 750_000
    assert native.MAX_STDERR == 65_536
    args, settings = synthetic.launches[0]
    assert args == ([sys.executable, "-I", "-S", str(native._CHILD), "--credential-fd", "601"],)
    assert settings == {
        "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "env": {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONUNBUFFERED": "1"},
        "close_fds": True, "pass_fds": (601,), "start_new_session": True,
    }
    assert _PRIVATE not in repr((args, settings))
    assert synthetic.events.count("reap_attempt") == 1
    assert synthetic.closes["read_fd"] == synthetic.closes["write_fd"] == 1


@pytest.mark.parametrize("phase", ("deadline", "request", "credential", "guard", "json", "length", "pipe", "command"))
def test_synthetic_actual_preflight_without_popen_is_never_launched(monkeypatch, synthetic, phase):
    request, credential, deadline = {"operation": "http_hop"}, None, time.monotonic() + 2
    original = RuntimeError(_PRIVATE)

    def refuse(*args):
        raise original

    def guard():
        return None

    expected = native.NativeIORejected
    if phase == "deadline":
        deadline = True
    elif phase == "request":
        request["timeout_seconds"] = 1
    elif phase == "credential":
        credential = _PRIVATE
    elif phase == "guard":
        guard, expected = refuse, RuntimeError
    elif phase == "json":
        request["private"] = object()
        expected = TypeError
    elif phase == "length":
        request["private"] = "x" * native.MAX_INPUT
    elif phase == "pipe":
        request, credential = {"operation": "gcs_bucket"}, _PRIVATE
        monkeypatch.setattr(native.os, "pipe", refuse)
    else:
        monkeypatch.setattr(native.NativeIntakeProcessRunner, "_command", refuse)
    with lifecycle._observe_native_process() as observation:
        with pytest.raises(expected) as caught:
            native.NativeIntakeProcessRunner().run(request, credential=credential,
                deadline=deadline, guard=guard)
    if phase == "guard":
        assert caught.value is original
    assert synthetic.launches == [] and "reap_attempt" not in synthetic.events
    assert lifecycle._consume_native_process_completion(observation) == "NEVER_LAUNCHED"
    _assert_unavailable(observation)


@pytest.mark.parametrize("kind", ("os", "ordinary", "rejected", "interrupt", "exit"))
def test_synthetic_popen_entry_without_handle_is_ambiguous(synthetic, kind):
    original = {
        "os": OSError(2, _PRIVATE), "ordinary": RuntimeError(_PRIVATE),
        "rejected": native.NativeIORejected("NATIVE_IO_CHILD_FAILED"),
        "interrupt": KeyboardInterrupt(), "exit": SystemExit(7),
    }[kind]
    synthetic.spawn_error = original
    expected = native.NativeIORejected if kind in {"os", "ordinary"} else type(original)
    with lifecycle._observe_native_process() as observation:
        with pytest.raises(expected) as caught:
            synthetic.run(credential=_PRIVATE)
    if kind not in {"os", "ordinary"}:
        assert caught.value is original
    assert len(synthetic.launches) == 1 and "reap_attempt" not in synthetic.events
    assert synthetic.closes == {"read_fd": 1, "write_fd": 1}
    _assert_unavailable(observation)


@pytest.mark.parametrize("outcome", ("return", "exception", "typed_error", "child_json", "bool"))
def test_synthetic_adapter_without_actual_run_has_no_completion(outcome):
    result = {
        "return": {"accepted": True}, "child_json": {"ok": True, "process_state": "REAPED",
                                                        "process_reaped": True},
        "bool": True,
    }.get(outcome)
    error = (RuntimeError(_PRIVATE) if outcome == "exception"
             else native.NativeIORejected("NATIVE_IO_CHILD_FAILED"))

    class BypassRunner(native.NativeIntakeProcessRunner):
        # A compatible injected adapter/subclass does not execute actual run.
        def run(self, *args, **kwargs):
            if outcome in {"exception", "typed_error"}:
                raise error
            return result

    with lifecycle._observe_native_process() as observation:
        if outcome in {"exception", "typed_error"}:
            with pytest.raises(type(error)) as caught:
                BypassRunner().run({"operation": "http_hop"})
            assert caught.value is error
        else:
            assert BypassRunner().run({"operation": "http_hop"}) is result
    _assert_unavailable(observation)


@pytest.mark.parametrize("outcome", ("success", "guard", "ordinary", "protocol", "provider", "interrupt", "exit", "adapter"))
def test_synthetic_reap_completion_survives_known_body_or_later_adapter_error(synthetic, outcome):
    original = {
        "guard": native.NativeIORejected("NATIVE_IO_LEASE_LOST", status=403),
        "ordinary": RuntimeError(_PRIVATE), "interrupt": KeyboardInterrupt(),
        "exit": SystemExit(7), "adapter": RuntimeError(_PRIVATE),
    }.get(outcome)

    def guard():
        if synthetic.launches and outcome in {"guard", "ordinary", "interrupt", "exit"}:
            raise original

    if outcome == "protocol":
        synthetic.output = [b"not-json", b""]
    elif outcome == "provider":
        synthetic.output = [b'{"ok":false,"code":"NATIVE_GCS_HTTP_REJECTED","status":429}', b""]
    if outcome == "success":
        with lifecycle._observe_native_process() as observation:
            assert synthetic.run(guard=guard) == {"accepted": True}
    else:
        expected = type(original) if outcome in {"guard", "interrupt", "exit", "adapter"} else native.NativeIORejected
        with pytest.raises(expected) as caught, lifecycle._observe_native_process() as observation:
            synthetic.run(guard=guard)
            if outcome == "adapter":
                raise original
        if outcome in {"guard", "interrupt", "exit", "adapter"}:
            assert caught.value is original
        if outcome == "provider":
            assert caught.value.code == "NATIVE_GCS_HTTP_REJECTED" and caught.value.status == 429
    assert synthetic.events.count("reap_attempt") == 1
    assert lifecycle._consume_native_process_completion(observation) == "REAPED"
    _assert_unavailable(observation)


@pytest.mark.parametrize("target", ("read_fd", "write_fd", "stdin", "stdout", "stderr"))
def test_synthetic_reap_recorded_before_close_errors_without_retrying_numeric_fds(synthetic, target):
    synthetic.faults[target, 1] = OSError(_PRIVATE)
    with lifecycle._observe_native_process() as observation:
        with pytest.raises(native.NativeIORejected, match="^NATIVE_IO_CHILD_FAILED$"):
            synthetic.run(credential=_PRIVATE)
    assert synthetic.events.count("reap_attempt") == 1
    assert synthetic.closes["read_fd"] == synthetic.closes["write_fd"] == 1
    assert all(synthetic.closes[name] >= 1 for name in ("stdin", "stdout", "stderr"))
    assert lifecycle._consume_native_process_completion(observation) == "REAPED"


@pytest.mark.parametrize("stream", ("stdin", "stdout", "stderr"))
def test_synthetic_final_close_interruption_preserves_reap_fact_and_exception(synthetic, stream):
    original = KeyboardInterrupt()
    synthetic.faults[stream, 2 if stream == "stdin" else 1] = original
    with lifecycle._observe_native_process() as observation:
        with pytest.raises(KeyboardInterrupt) as caught:
            synthetic.run()
    assert caught.value is original
    assert synthetic.closes == {"stdin": 2, "stdout": 1, "stderr": 1}
    assert lifecycle._consume_native_process_completion(observation) == "REAPED"


def test_synthetic_child_json_completion_claim_cannot_override_failed_reap(synthetic):
    synthetic.output = [b'{"ok":true,"result":{"process_reaped":true,"process_state":"REAPED"}}', b""]
    synthetic.reap_error = native.NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED")
    with lifecycle._observe_native_process() as observation:
        with pytest.raises(native.NativeIOUnreaped):
            synthetic.run()
    _assert_unavailable(observation)


def test_synthetic_child_error_code_is_not_a_lifecycle_exception(synthetic):
    synthetic.output = [b'{"ok":false,"code":"NATIVE_IO_CHILD_UNREAPED","status":null}', b""]
    with lifecycle._observe_native_process() as observation:
        with pytest.raises(native.NativeIORejected) as caught:
            synthetic.run()
    assert type(caught.value) is native.NativeIORejected
    assert lifecycle._consume_native_process_completion(observation) == "REAPED"


@pytest.mark.parametrize("phase", ("preflight", "body", "cleanup", "unexpected_cleanup"))
def test_synthetic_unreaped_is_sticky_even_if_a_later_reap_returns(synthetic, phase):
    original = native.NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED")

    def guard():
        if phase == "preflight" or phase == "body" and synthetic.launches:
            raise original

    if phase == "cleanup":
        synthetic.reap_error = original
    elif phase == "unexpected_cleanup":
        synthetic.reap_error = RuntimeError(_PRIVATE)
    synthetic.faults["stdout", 1] = OSError(_PRIVATE)
    with lifecycle._observe_native_process() as observation:
        with pytest.raises(native.NativeIOUnreaped) as caught:
            synthetic.run(guard=guard)
    if phase != "unexpected_cleanup":
        assert caught.value is original
    assert synthetic.events.count("reap_attempt") == (0 if phase == "preflight" else 1)
    _assert_unavailable(observation)


def test_synthetic_adapter_unreaped_outcome_vetoes_completed_run(synthetic):
    original = native.NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED")
    with pytest.raises(native.NativeIOUnreaped) as caught:
        with lifecycle._observe_native_process() as observation:
            assert synthetic.run() == {"accepted": True}
            raise original
    assert caught.value is original and synthetic.events.count("reap_attempt") == 1
    _assert_unavailable(observation)


@pytest.mark.parametrize("phase", ("wait_error", "group_error", "group_present", "absent"))
def test_synthetic_actual_reap_helper_requires_wait_and_group_absence(monkeypatch, phase):
    real_helper = native._kill_and_reap
    synthetic = _SyntheticIO(monkeypatch)
    events = []
    synthetic.process.poll = lambda: 0

    def wait(*, timeout):
        events.append(("wait", timeout))
        if timeout == native.CLEANUP_SECONDS and phase == "wait_error":
            raise subprocess.TimeoutExpired("synthetic-owned", timeout)
        return 0

    def group(pid, sig):
        assert pid == synthetic.process.pid and sig == 0
        events.append("group")
        if phase == "group_error":
            raise PermissionError(_PRIVATE)
        if phase == "absent":
            raise ProcessLookupError()

    synthetic.process.wait = wait
    monkeypatch.setattr(native, "_kill_and_reap", real_helper)
    monkeypatch.setattr(native.os, "killpg", group)
    with lifecycle._observe_native_process() as observation:
        if phase == "absent":
            assert synthetic.run() == {"accepted": True}
        else:
            with pytest.raises(native.NativeIOUnreaped):
                synthetic.run()
    assert ("wait", native.CLEANUP_SECONDS) in events
    if phase == "absent":
        assert events[-2:] == [("wait", native.CLEANUP_SECONDS), "group"]
        assert lifecycle._consume_native_process_completion(observation) == "REAPED"
    else:
        _assert_unavailable(observation)


@pytest.mark.parametrize("phase", ("before", "during", "reap", "after_run"))
def test_synthetic_early_take_is_denied_and_burns_capture(synthetic, monkeypatch, phase):
    attempted = []
    with lifecycle._observe_native_process() as observation:
        def take_once():
            if not attempted:
                _assert_unavailable(observation)
                attempted.append(True)

        def guard():
            if phase == "during" and synthetic.launches:
                take_once()

        if phase == "before":
            take_once()
        if phase == "reap":
            def reap(process):
                synthetic.reap(process)
                take_once()
            monkeypatch.setattr(native, "_kill_and_reap", reap)
        assert synthetic.run(guard=guard) == {"accepted": True}
        if phase == "after_run":
            take_once()
    assert attempted == [True]
    _assert_unavailable(observation)


def test_synthetic_close_while_actual_run_active_cannot_seal_completion(synthetic):
    manager = lifecycle._observe_native_process()
    observation = manager.__enter__()
    closed = []

    def guard():
        if synthetic.launches and not closed:
            manager.__exit__(None, None, None)
            closed.append(True)

    try:
        assert synthetic.run(guard=guard) == {"accepted": True}
    finally:
        if not closed:
            manager.__exit__(None, None, None)
    _assert_unavailable(observation)
    assert lifecycle._current.get() is None


@pytest.mark.parametrize("value", (None, True, False, 1, "REAPED", {"process_reaped": True}))
def test_exact_observation_type_required(value):
    _assert_unavailable(value)


def test_lookalikes_do_not_get_hash_equality_or_repr_called():
    class Lookalike:
        def __hash__(self):
            pytest.fail("Untrusted hash invoked")

        def __eq__(self, other):
            pytest.fail("Untrusted equality invoked")

        def __repr__(self):
            pytest.fail("Untrusted repr invoked")

    _assert_unavailable(Lookalike())


def test_observation_cannot_be_reconstructed_copied_serialized_or_subclassed():
    with lifecycle._observe_native_process() as observation:
        _preflight_rejection()
    for operation in (lambda: type(observation)(), lambda: copy.copy(observation),
                      lambda: copy.deepcopy(observation), lambda: pickle.dumps(observation),
                      lambda: type("ForgedObservation", (type(observation),), {})):
        with pytest.raises(lifecycle.NativeProcessCompletionUnavailable, match=f"^{_CODE}$"):
            operation()
    with pytest.raises(AttributeError):
        observation.process_reaped = True
    _assert_unavailable(object.__new__(type(observation)))
    assert lifecycle._consume_native_process_completion(observation) == "NEVER_LAUNCHED"
    _assert_unavailable(observation)


def test_independent_observations_cannot_take_each_others_record():
    with lifecycle._observe_native_process() as empty:
        pass
    with lifecycle._observe_native_process() as completed:
        _preflight_rejection()
    _assert_unavailable(empty)
    assert lifecycle._consume_native_process_completion(completed) == "NEVER_LAUNCHED"
    _assert_unavailable(completed)


@pytest.mark.parametrize("mode", ("nested", "empty_context", "copied_context"))
def test_overlapping_captures_poison_both_including_hidden_context_overlap(mode):
    separate_context = Context() if mode == "empty_context" else copy_context()

    def inner():
        with lifecycle._observe_native_process() as observation:
            _preflight_rejection()
        return observation

    with lifecycle._observe_native_process() as outer:
        _preflight_rejection()
        nested = inner() if mode == "nested" else separate_context.run(inner)
    _assert_unavailable(outer)
    _assert_unavailable(nested)
    assert lifecycle._current.get() is None
    with lifecycle._observe_native_process() as fresh:
        _preflight_rejection()
    assert lifecycle._consume_native_process_completion(fresh) == "NEVER_LAUNCHED"


@pytest.mark.parametrize("count", (2, 3, 20))
def test_multiple_runs_are_rejected_with_bounded_record(count):
    with lifecycle._observe_native_process() as observation:
        for _ in range(count):
            _preflight_rejection()
        assert lifecycle._captures[observation].runs == 2
    _assert_unavailable(observation)


def test_synthetic_reentrant_actual_run_is_rejected(synthetic):
    invoked = []

    def guard():
        if not invoked:
            invoked.append(True)
            _preflight_rejection()

    with lifecycle._observe_native_process() as observation:
        assert synthetic.run(guard=guard) == {"accepted": True}
    _assert_unavailable(observation)


def _in_thread(call):
    values, failures = [], []

    def work():
        try:
            values.append(call())
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    if failures:
        raise failures[0]
    return values[0]


def test_wrong_thread_take_is_denied_even_after_capture_closes():
    with lifecycle._observe_native_process() as observation:
        _preflight_rejection()
    _in_thread(lambda: _assert_unavailable(observation))
    _assert_unavailable(observation)


@pytest.mark.parametrize("copy_capture", (False, True))
def test_thread_run_cannot_supply_owner_thread_completion(copy_capture):
    with lifecycle._observe_native_process() as observation:
        context = copy_context()
        _in_thread(lambda: context.run(_preflight_rejection) if copy_capture else _preflight_rejection())
    _assert_unavailable(observation)


def test_independent_thread_capture_does_not_contaminate_owner():
    def independent():
        with lifecycle._observe_native_process() as own:
            _preflight_rejection()
        return lifecycle._consume_native_process_completion(own)

    with lifecycle._observe_native_process() as observation:
        _preflight_rejection()
        assert _in_thread(independent) == "NEVER_LAUNCHED"
    assert lifecycle._consume_native_process_completion(observation) == "NEVER_LAUNCHED"


def test_copied_context_after_close_cannot_extend_capture():
    with lifecycle._observe_native_process() as observation:
        context = copy_context()
        _preflight_rejection()
    context.run(_preflight_rejection)
    _assert_unavailable(observation)


@pytest.mark.parametrize("outcome", ("empty", "preflight", "exception"))
def test_capture_resets_context_on_all_exits_and_is_collectible(outcome):
    before = len(lifecycle._captures)

    def capture():
        try:
            with lifecycle._observe_native_process() as observation:
                copied = copy_context()
                if outcome == "preflight":
                    _preflight_rejection()
                elif outcome == "exception":
                    raise RuntimeError(_PRIVATE)
        except RuntimeError:
            pass
        assert lifecycle._current.get() is None
        return weakref.ref(observation), copied

    reference, retained_context = capture()
    gc.collect()
    assert reference() is None and len(lifecycle._captures) == before
    # Even a copied context retaining the registry value cannot retain its key.
    assert retained_context.get(lifecycle._current) is not None
    assert lifecycle._owners.get(threading.current_thread()) is None


def test_completed_capture_retains_no_request_guard_or_error():
    class Request(dict):
        pass

    class Guard:
        def __call__(self):
            raise RuntimeError(_PRIVATE)

    def invoke():
        request, guard = Request(operation="http_hop", private=_PRIVATE), Guard()
        with lifecycle._observe_native_process() as observation:
            try:
                native.NativeIntakeProcessRunner().run(request, deadline=time.monotonic() + 2,
                    guard=guard)
            except RuntimeError:
                pass
        return observation, weakref.ref(request), weakref.ref(guard)

    observation, request_ref, guard_ref = invoke()
    gc.collect()
    assert request_ref() is guard_ref() is None
    record = lifecycle._captures[observation]
    assert record.active_run is None
    assert _PRIVATE not in repr(observation) + repr(record)
    assert lifecycle._consume_native_process_completion(observation) == "NEVER_LAUNCHED"


@pytest.mark.parametrize("hook", ("_begin_run", "_transition", "_finish_run", "_close_capture"))
@pytest.mark.parametrize("timing", ("before", "after"))
@pytest.mark.parametrize("outcome", ("success", "body_error", "unreaped"))
def test_synthetic_observation_faults_preserve_original_outcome_and_deny_receipt(
        monkeypatch, synthetic, hook, timing, outcome):
    original_hook = getattr(lifecycle, hook)

    def fail(*args):
        if timing == "after":
            original_hook(*args)
        raise RuntimeError(_PRIVATE)

    monkeypatch.setattr(lifecycle, hook, fail)
    original = (native.NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED") if outcome == "unreaped"
                else native.NativeIORejected("NATIVE_IO_LEASE_LOST", status=403))
    if outcome == "unreaped":
        synthetic.reap_error = original

    def guard():
        if outcome == "body_error" and synthetic.launches:
            raise original

    with lifecycle._observe_native_process() as observation:
        if outcome == "success":
            assert synthetic.run(guard=guard) == {"accepted": True}
        else:
            with pytest.raises(type(original)) as caught:
                synthetic.run(guard=guard)
            assert caught.value is original
    assert synthetic.events.count("reap_attempt") == 1
    assert lifecycle._current.get() is None
    _assert_unavailable(observation)


@pytest.mark.parametrize("hook", ("_begin_run", "_transition", "_finish_run", "_close_capture"))
def test_synthetic_silent_observation_failure_cannot_claim_never_launched(monkeypatch, synthetic, hook):
    monkeypatch.setattr(lifecycle, hook, lambda *args: None)
    with lifecycle._observe_native_process() as observation:
        assert synthetic.run() == {"accepted": True}
    assert len(synthetic.launches) == 1
    _assert_unavailable(observation)


def test_synthetic_observation_interrupt_does_not_replace_runner_interrupt(monkeypatch, synthetic):
    original = KeyboardInterrupt()

    def fail(*args):
        raise SystemExit(_PRIVATE)

    def guard():
        if synthetic.launches:
            raise original

    monkeypatch.setattr(lifecycle, "_transition", fail)
    with lifecycle._observe_native_process() as observation:
        with pytest.raises(KeyboardInterrupt) as caught:
            synthetic.run(guard=guard)
    assert caught.value is original and synthetic.events.count("reap_attempt") == 1
    _assert_unavailable(observation)


def test_synthetic_registry_failure_preserves_body_and_resets_owner(monkeypatch, synthetic):
    class BrokenRegistry:
        def __setitem__(self, key, value):
            raise RuntimeError(_PRIVATE)

    with monkeypatch.context() as patch:
        patch.setattr(lifecycle, "_captures", BrokenRegistry())
        with lifecycle._observe_native_process() as observation:
            assert synthetic.run() == {"accepted": True}
    assert lifecycle._current.get() is None
    assert lifecycle._owners.get(threading.current_thread()) is None
    _assert_unavailable(observation)


def test_synthetic_capture_initialization_failure_still_runs_body(monkeypatch, synthetic):
    def fail():
        raise RuntimeError(_PRIVATE)

    monkeypatch.setattr(lifecycle, "_Capture", fail)
    with lifecycle._observe_native_process() as observation:
        assert synthetic.run() == {"accepted": True}
    assert synthetic.events.count("reap_attempt") == 1
    assert lifecycle._current.get() is None
    _assert_unavailable(observation)


def test_synthetic_private_run_key_requires_registered_identity(synthetic):
    def guard():
        capture = lifecycle._current.get()
        forged = object.__new__(lifecycle._Run)
        forged.capture = weakref.ref(capture)
        lifecycle._note_native_process(forged, "REAPED")

    with lifecycle._observe_native_process() as observation:
        assert synthetic.run(guard=guard) == {"accepted": True}
    _assert_unavailable(observation)


class _CloseAfterActualClose:
    def __init__(self, stream):
        self.stream = stream

    def fileno(self):
        return self.stream.fileno()

    def close(self):
        self.stream.close()
        raise OSError(_PRIVATE)


class _OwnedChildren:
    """Capture only this fixture's Popen children; never search for processes."""

    def __init__(self, patch):
        self.children, self.streams, self.launches = [], [], []
        self.close_fault = False
        actual_popen = subprocess.Popen

        def launch(*args, **kwargs):
            assert kwargs["start_new_session"] is True
            child = actual_popen(*args, **kwargs)
            self.children.append(child)
            self.launches.append((args, kwargs))
            self.streams.extend((child.stdin, child.stdout, child.stderr))
            if self.close_fault:
                child.stdout = _CloseAfterActualClose(child.stdout)
            return child

        patch.setattr(native.subprocess, "Popen", launch)

    def assert_reaped_before_finalizer(self, expected_returncode):
        assert len(self.children) == 1
        child = self.children[0]
        # Do not call poll/wait here: the product must have performed the wait.
        assert child.returncode == expected_returncode
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
        with pytest.raises(ProcessLookupError):
            os.kill(child.pid, 0)
        with pytest.raises(ProcessLookupError):
            os.killpg(child.pid, 0)

    def finalize(self):
        try:
            for child in self.children:
                # Emergency cleanup only of the captured non-forking child.
                # No process discovery and no cleanup result used as evidence.
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=native.CLEANUP_SECONDS)
        finally:
            for stream in self.streams:
                stream.close()


@pytest.fixture
def owned_children(monkeypatch):
    case = None
    try:
        with monkeypatch.context() as patch:
            case = _OwnedChildren(patch)
            yield case
    finally:
        if case is not None:
            case.finalize()


class _OwnedReplyRunner(native.NativeIntakeProcessRunner):
    def _command(self, credential_fd):
        assert credential_fd is None
        return [sys.executable, "-I", "-S", "-c",
                'import sys; sys.stdin.buffer.read(); print(\'{"ok":true,"result":{"accepted":true}}\')']


class _OwnedSleepingRunner(native.NativeIntakeProcessRunner):
    def _command(self, credential_fd):
        assert credential_fd is None
        return [sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"]


@pytest.mark.parametrize("close_fault", (False, True))
def test_real_owned_success_or_final_close_error_has_completion_after_actual_reap(owned_children, close_fault):
    owned_children.close_fault = close_fault
    with lifecycle._observe_native_process() as observation:
        if close_fault:
            with pytest.raises(native.NativeIORejected, match="^NATIVE_IO_CHILD_FAILED$"):
                _OwnedReplyRunner().run({"operation": "http_hop"}, deadline=time.monotonic() + 3,
                    guard=lambda: None)
        else:
            assert _OwnedReplyRunner().run({"operation": "http_hop"}, deadline=time.monotonic() + 3,
                guard=lambda: None) == {"accepted": True}
    owned_children.assert_reaped_before_finalizer(0)
    assert lifecycle._consume_native_process_completion(observation) == "REAPED"
    _assert_unavailable(observation)


@pytest.mark.parametrize("close_fault", (False, True))
def test_real_owned_guard_error_keeps_identity_and_reaps_before_receipt(owned_children, close_fault):
    owned_children.close_fault = close_fault
    original = native.NativeIORejected("NATIVE_IO_LEASE_LOST", status=403)

    def guard():
        if owned_children.children:
            raise original

    started = time.monotonic()
    with lifecycle._observe_native_process() as observation:
        with pytest.raises(native.NativeIORejected) as caught:
            _OwnedSleepingRunner().run({"operation": "http_hop"}, deadline=started + 3, guard=guard)
    assert caught.value is original
    assert time.monotonic() - started < native.CLEANUP_SECONDS + 1
    owned_children.assert_reaped_before_finalizer(-signal.SIGKILL)
    assert lifecycle._consume_native_process_completion(observation) == "REAPED"


def test_real_owned_deadline_unchanged_with_observation_and_close_fault(owned_children):
    owned_children.close_fault = True
    started = time.monotonic()
    deadline = started + 0.3
    with lifecycle._observe_native_process() as observation:
        with pytest.raises(native.NativeIORejected, match="^NATIVE_IO_DEADLINE_EXPIRED$"):
            _OwnedSleepingRunner().run({"operation": "http_hop"}, deadline=deadline, guard=lambda: None)
    assert deadline <= time.monotonic() < deadline + native.CLEANUP_SECONDS + 1
    owned_children.assert_reaped_before_finalizer(-signal.SIGKILL)
    assert lifecycle._consume_native_process_completion(observation) == "REAPED"


def test_real_fixed_child_protocol_rejection_reaped_without_network(owned_children):
    with lifecycle._observe_native_process() as observation:
        with pytest.raises(native.NativeIORejected, match="^NATIVE_IO_PROTOCOL_REJECTED$"):
            native.NativeIntakeProcessRunner().run({"operation": "invalid-local-operation"},
                deadline=time.monotonic() + 3, guard=lambda: None)
    owned_children.assert_reaped_before_finalizer(0)
    args, settings = owned_children.launches[0]
    assert args[0] == [sys.executable, "-I", "-S", str(native._CHILD)]
    assert settings["pass_fds"] == ()
    assert lifecycle._consume_native_process_completion(observation) == "REAPED"
