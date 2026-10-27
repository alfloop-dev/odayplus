"""Cleanup fault routing plus separately identified real owned-child cases.

The synthetic cases prove control flow only, never actual child reaping.
Real-child cases assert reaping before their independent safety finalizer.
"""

import os
import signal
import subprocess
import sys
import time
import traceback
from collections import Counter
from types import SimpleNamespace

import pytest

from modules.external_data.application import native_intake_process as native

_PRIVATE = "private-credential-and-close-details"
_STREAMS = ("stdin", "stdout", "stderr")
_RESOURCES = ("read_fd", "write_fd", *_STREAMS)


class _SyntheticStream:
    def __init__(self, case, name, descriptor):
        self.case, self.name, self.descriptor = case, name, descriptor

    def fileno(self):
        return self.descriptor

    def close(self):
        self.case.close(self.name)


class _SyntheticSelector:
    def __init__(self):
        self.entries = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def register(self, descriptor, events, data):
        self.entries[descriptor] = SimpleNamespace(fd=descriptor, data=data)

    def unregister(self, descriptor):
        del self.entries[descriptor]

    def get_map(self):
        return self.entries

    def select(self, timeout):
        return [(key, 0) for key in self.entries.values()]


class _SyntheticIO:
    """All FD/process operations are doubles; no OS ownership proof here."""

    def __init__(self, monkeypatch):
        self.events, self.launches = [], []
        self.closes = Counter()
        self.faults = {}
        self.reap_error = self.spawn_error = None
        self.output = [b'{"ok":true,"result":{"accepted":true}}', b""]
        self.process = SimpleNamespace(pid=987654321, returncode=0, wait=self.wait)
        for name, descriptor in zip(_STREAMS, (501, 502, 503), strict=True):
            setattr(self.process, name, _SyntheticStream(self, name, descriptor))
        monkeypatch.setattr(native.subprocess, "Popen", self.spawn)
        monkeypatch.setattr(native.selectors, "DefaultSelector", _SyntheticSelector)
        monkeypatch.setattr(native.os, "pipe", lambda: (601, 602))
        monkeypatch.setattr(native.os, "close", self.close_fd)
        monkeypatch.setattr(native.os, "set_blocking", lambda *args: None)
        monkeypatch.setattr(native.os, "write", lambda fd, data: len(data))
        monkeypatch.setattr(native.os, "read", self.read)
        monkeypatch.setattr(native.os, "killpg", self.forbid_signal)
        monkeypatch.setattr(native, "_kill_and_reap", self.reap)

    def forbid_signal(self, *args):
        pytest.fail("Synthetic cases must never signal an actual process")

    def spawn(self, *args, **kwargs):
        self.launches.append((args, kwargs))
        if self.spawn_error is not None:
            raise self.spawn_error
        return self.process

    def wait(self, *, timeout):
        assert 0 < timeout <= native.MAX_SECONDS
        self.events.append("body_wait")
        return self.process.returncode

    def read(self, descriptor, size):
        assert descriptor in (502, 503)
        return self.output.pop(0) if descriptor == 502 else b""

    def close(self, name):
        self.events.append("close:" + name)
        self.closes[name] += 1
        error = self.faults.get((name, self.closes[name]))
        if error is not None:
            raise error

    def close_fd(self, descriptor):
        self.close({601: "read_fd", 602: "write_fd"}[descriptor])

    def reap(self, process):
        assert process is self.process
        self.events.append("reap_attempt")
        if self.reap_error is not None:
            raise self.reap_error

    def run(self, *, guard=lambda: None, credential=None):
        operation = "gcs_bucket" if credential is not None else "http_hop"
        return native.NativeIntakeProcessRunner().run(
            {"operation": operation}, deadline=time.monotonic() + 2,
            guard=guard, credential=credential)


@pytest.fixture
def synthetic(monkeypatch):
    return _SyntheticIO(monkeypatch)


def _reject_after_spawn(case, error):
    def guard():
        if case.launches:
            raise error
    return guard


def _assert_bounded(error, code, *, kind=native.NativeIORejected):
    assert type(error) is kind
    assert str(error) == error.code == code
    assert error.status is None
    assert _PRIVATE not in "".join(traceback.format_exception(error))


@pytest.mark.parametrize("name", _STREAMS)
def test_synthetic_success_fails_closed_on_each_final_stream_close(synthetic, name):
    synthetic.faults[name, 2 if name == "stdin" else 1] = OSError(_PRIVATE)
    with pytest.raises(native.NativeIORejected) as caught:
        synthetic.run()
    _assert_bounded(caught.value, "NATIVE_IO_CHILD_FAILED")
    assert synthetic.closes == {"stdin": 2, "stdout": 1, "stderr": 1}
    assert synthetic.events.count("reap_attempt") == 1
    assert synthetic.events[-4:] == ["reap_attempt", *["close:" + n for n in _STREAMS]]


@pytest.mark.parametrize("name", ("read_fd", "write_fd", "stdin"))
def test_synthetic_body_close_failure_still_attempts_reap_and_sibling_closes(synthetic, name):
    synthetic.faults[name, 1] = OSError(_PRIVATE)
    with pytest.raises(native.NativeIORejected) as caught:
        synthetic.run(credential=_PRIVATE)
    _assert_bounded(caught.value, "NATIVE_IO_CHILD_FAILED")
    assert synthetic.events.count("reap_attempt") == 1
    assert all(synthetic.closes[n] >= 1 for n in _RESOURCES)
    # Numeric FDs may already be closed/reused even when close raised.
    assert synthetic.closes["read_fd"] == synthetic.closes["write_fd"] == 1


@pytest.mark.parametrize("reap_kind", ("returns", "unreaped", "unexpected"))
def test_synthetic_cleanup_attempts_every_resource_when_all_closes_fail(synthetic, reap_kind):
    if reap_kind != "returns":
        synthetic.reap_error = (native.NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED")
            if reap_kind == "unreaped" else RuntimeError(_PRIVATE))
    for name in _RESOURCES:
        synthetic.faults[name, 1] = OSError(_PRIVATE)
    with pytest.raises(native.NativeIORejected) as caught:
        native._cleanup_owned_io(synthetic.process, 601, 602, body_error=None)
    if reap_kind == "returns":
        _assert_bounded(caught.value, "NATIVE_IO_CHILD_FAILED")
    else:
        _assert_bounded(caught.value, "NATIVE_IO_CHILD_UNREAPED", kind=native.NativeIOUnreaped)
    assert synthetic.closes == dict.fromkeys(_RESOURCES, 1)
    assert synthetic.events[0] == "reap_attempt"


def test_synthetic_spawn_failure_closes_both_fds_without_claiming_reaping(synthetic):
    synthetic.spawn_error = RuntimeError(_PRIVATE)
    synthetic.faults["read_fd", 1] = OSError(_PRIVATE)
    synthetic.faults["write_fd", 1] = OSError(_PRIVATE)
    with pytest.raises(native.NativeIORejected) as caught:
        synthetic.run(credential=_PRIVATE)
    _assert_bounded(caught.value, "NATIVE_IO_CHILD_FAILED")
    assert synthetic.closes == {"read_fd": 1, "write_fd": 1}
    assert "reap_attempt" not in synthetic.events


@pytest.mark.parametrize("body_kind", ("guard", "ordinary", "unreaped", "interrupt", "exit"))
def test_synthetic_body_error_precedes_close_failures_after_reap_returns(synthetic, body_kind):
    errors = {
        "guard": native.NativeIORejected("NATIVE_IO_LEASE_LOST", status=403),
        "ordinary": RuntimeError(_PRIVATE),
        "unreaped": native.NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED"),
        "interrupt": KeyboardInterrupt(),
        "exit": SystemExit(7),
    }
    original = errors[body_kind]
    for name in ("write_fd", *_STREAMS):
        synthetic.faults[name, 1] = OSError(_PRIVATE)
    expected = native.NativeIORejected if body_kind == "ordinary" else type(original)
    with pytest.raises(expected) as caught:
        synthetic.run(credential=_PRIVATE, guard=_reject_after_spawn(synthetic, original))
    if body_kind == "ordinary":
        _assert_bounded(caught.value, "NATIVE_IO_CHILD_FAILED")
    else:
        assert caught.value is original
    assert synthetic.events.count("reap_attempt") == 1
    assert synthetic.closes == dict.fromkeys(_RESOURCES, 1)


@pytest.mark.parametrize("reply,code,status", [
    (b"not-json", "NATIVE_IO_PROTOCOL_REJECTED", None),
    (b'{"ok":true,"result":[],"extra":1}', "NATIVE_IO_PROTOCOL_REJECTED", None),
    (b'{"ok":false,"code":"NATIVE_GCS_HTTP_REJECTED","status":429}',
     "NATIVE_GCS_HTTP_REJECTED", 429),
])
def test_synthetic_protocol_or_provider_rejection_survives_close_failure(synthetic, reply, code, status):
    synthetic.output = [reply, b""]
    synthetic.faults["stdout", 1] = OSError(_PRIVATE)
    synthetic.faults["stderr", 1] = OSError(_PRIVATE)
    with pytest.raises(native.NativeIORejected) as caught:
        synthetic.run()
    assert type(caught.value) is native.NativeIORejected
    assert caught.value.code == code and caught.value.status == status
    assert synthetic.events.count("reap_attempt") == 1
    assert synthetic.closes == {"stdin": 2, "stdout": 1, "stderr": 1}


@pytest.mark.parametrize("body", ("success", "guard", "protocol", "interrupt"))
@pytest.mark.parametrize("reap_kind", ("unreaped", "unexpected", "interrupt"))
def test_synthetic_unknown_reaping_overrides_body_and_all_later_close_errors(synthetic, body, reap_kind):
    synthetic.reap_error = {
        "unreaped": native.NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED"),
        "unexpected": RuntimeError(_PRIVATE),
        "interrupt": KeyboardInterrupt(),
    }[reap_kind]
    def guard():
        return None

    if body in {"guard", "interrupt"}:
        error = native.NativeIORejected("NATIVE_IO_LEASE_LOST") if body == "guard" else KeyboardInterrupt()
        guard = _reject_after_spawn(synthetic, error)
    elif body == "protocol":
        synthetic.output = [b"not-json", b""]
    for name in _STREAMS:
        call = 2 if name == "stdin" and body in {"success", "protocol"} else 1
        synthetic.faults[name, call] = OSError(_PRIVATE)
    with pytest.raises(native.NativeIOUnreaped) as caught:
        synthetic.run(guard=guard)
    _assert_bounded(caught.value, "NATIVE_IO_CHILD_UNREAPED", kind=native.NativeIOUnreaped)
    if reap_kind == "unreaped":
        assert caught.value is synthetic.reap_error
    assert synthetic.events.count("reap_attempt") == 1
    assert all(synthetic.closes[name] >= 1 for name in _STREAMS)


@pytest.mark.parametrize("phase", ("poll", "signal", "wait", "group_check", "group_present"))
def test_synthetic_actual_reap_helper_failure_never_becomes_ordinary_error(monkeypatch, phase):
    events = []

    def poll():
        events.append("poll")
        if phase == "poll":
            raise RuntimeError(_PRIVATE)
        return None

    def wait(*, timeout):
        events.append("wait")
        assert timeout == native.CLEANUP_SECONDS
        if phase == "wait":
            raise RuntimeError(_PRIVATE)
        return -signal.SIGKILL

    def killpg(pid, sig):
        assert pid == 987654321
        events.append(("killpg", sig))
        if (sig == signal.SIGKILL and phase == "signal"
                or sig == 0 and phase == "group_check"):
            raise RuntimeError(_PRIVATE)
        if sig == 0 and phase != "group_present":
            raise ProcessLookupError()

    def close():
        events.append("close")
        raise OSError(_PRIVATE)

    process = SimpleNamespace(pid=987654321, poll=poll, wait=wait,
        **{name: SimpleNamespace(close=close) for name in _STREAMS})
    monkeypatch.setattr(native.os, "killpg", killpg)
    with pytest.raises(native.NativeIOUnreaped) as caught:
        native._cleanup_owned_io(process, None, None, body_error=None)
    _assert_bounded(caught.value, "NATIVE_IO_CHILD_UNREAPED", kind=native.NativeIOUnreaped)
    assert events[-3:] == ["close"] * 3


def test_synthetic_success_inside_callers_except_still_rejects_close_error(synthetic):
    synthetic.faults["stdout", 1] = OSError(_PRIVATE)
    try:
        raise ValueError("unrelated caller exception")
    except ValueError:
        with pytest.raises(native.NativeIORejected) as caught:
            synthetic.run()
    _assert_bounded(caught.value, "NATIVE_IO_CHILD_FAILED")


def test_synthetic_close_interruption_defers_until_all_resources_attempted(synthetic):
    original = KeyboardInterrupt()
    synthetic.faults["read_fd", 1] = original
    with pytest.raises(KeyboardInterrupt) as caught:
        native._cleanup_owned_io(synthetic.process, 601, 602, body_error=None)
    assert caught.value is original
    assert synthetic.closes == dict.fromkeys(_RESOURCES, 1)
    assert synthetic.events[0] == "reap_attempt"


def test_synthetic_clean_success_preserves_fixed_launch_and_credential_isolation(synthetic):
    assert synthetic.run(credential=_PRIVATE) == {"accepted": True}
    args, settings = synthetic.launches[0]
    assert args[0] == [sys.executable, "-I", "-S", str(native._CHILD), "--credential-fd", "601"]
    assert settings["start_new_session"] is settings["close_fds"] is True
    assert settings["pass_fds"] == (601,)
    assert settings["env"] == {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONUNBUFFERED": "1"}
    assert _PRIVATE not in repr((args, settings))
    assert synthetic.events.count("reap_attempt") == 1


class _CloseFaultStream:
    def __init__(self, stream, case, name):
        self.stream, self.case, self.name = stream, case, name

    def fileno(self):
        return self.stream.fileno()

    def close(self):
        self.case.close(self.name, self.stream.close)


class _OwnedChildCase:
    """Only controlled, non-forking children captured from this test's Popen."""

    def __init__(self, patch, target, timing):
        self.target, self.timing = target, timing
        self.children, self.streams = [], []
        self.pair = None
        self.open_fds = set()
        self.closes = Counter()
        self.real_close, real_pipe, real_popen = os.close, os.pipe, subprocess.Popen

        def pipe():
            pair = real_pipe()
            # The credential pipe precedes Popen's internal pipes.
            if self.pair is None:
                self.pair = pair
                self.open_fds.update(pair)
            return pair

        def close_fd(descriptor):
            if descriptor not in self.open_fds:
                return self.real_close(descriptor)
            name = "read_fd" if descriptor == self.pair[0] else "write_fd"

            def close_owned_fd():
                self.real_close(descriptor)
                self.open_fds.remove(descriptor)
            self.close(name, close_owned_fd)

        def launch(*args, **kwargs):
            assert kwargs["start_new_session"] is True
            process = real_popen(*args, **kwargs)
            self.children.append(process)
            originals = tuple(getattr(process, name) for name in _STREAMS)
            self.streams.extend(originals)
            assert os.getsid(process.pid) == os.getpgid(process.pid) == process.pid
            for name, stream in zip(_STREAMS, originals, strict=True):
                setattr(process, name, _CloseFaultStream(stream, self, name))
            return process

        patch.setattr(native.os, "pipe", pipe)
        patch.setattr(native.os, "close", close_fd)
        patch.setattr(native.subprocess, "Popen", launch)

    def close(self, name, actual_close):
        self.closes[name] += 1
        if name == self.target and self.closes[name] == 1:
            if self.timing == "after":
                actual_close()
            raise OSError(_PRIVATE)
        actual_close()

    def finalize(self):
        try:
            for process in self.children:
                # Popen.kill targets only this owned direct child. These test
                # scripts do not fork; never discover or signal other groups.
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=native.CLEANUP_SECONDS)
        finally:
            errors = []
            for stream in self.streams:
                try:
                    stream.close()
                except BaseException as error:
                    errors.append(error)
            for descriptor in self.open_fds:
                try:
                    self.real_close(descriptor)
                except BaseException as error:
                    errors.append(error)
            if errors:
                raise errors[0]


@pytest.fixture
def owned_child_case(monkeypatch):
    cases = []
    try:
        with monkeypatch.context() as patch:
            def make(target, timing):
                assert not cases
                case = _OwnedChildCase(patch, target, timing)
                cases.append(case)
                return case
            yield make
    finally:
        # Fault injection has been undone before this independent finalizer.
        for case in cases:
            case.finalize()


class _OwnedSleepingRunner(native.NativeIntakeProcessRunner):
    def _command(self, credential_fd):
        assert credential_fd is not None
        return [sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"]


def _assert_actual_reaping_before_finalizer(case):
    assert len(case.children) == 1
    process = case.children[0]
    assert process.returncode == -signal.SIGKILL
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)


@pytest.mark.parametrize("target", _RESOURCES)
@pytest.mark.parametrize("timing", ("before", "after"))
def test_real_owned_child_reaped_despite_each_close_fault(owned_child_case, target, timing):
    case = owned_child_case(target, timing)
    rejection = native.NativeIORejected("NATIVE_IO_LEASE_LOST")

    def guard():
        if case.children:
            raise rejection

    started = time.monotonic()
    with pytest.raises(native.NativeIORejected) as caught:
        _OwnedSleepingRunner().run({"operation": "gcs_bucket"}, credential=_PRIVATE,
            deadline=started + 3, guard=guard)
    if target == "read_fd":
        _assert_bounded(caught.value, "NATIVE_IO_CHILD_FAILED")
    else:
        assert caught.value is rejection
    assert time.monotonic() - started < native.CLEANUP_SECONDS + 1
    assert case.closes == dict.fromkeys(_RESOURCES, 1)
    _assert_actual_reaping_before_finalizer(case)


def test_real_owned_child_deadline_is_not_extended_by_close_failure(owned_child_case):
    case = owned_child_case("stdout", "before")
    started = time.monotonic()
    deadline = started + 0.3
    with pytest.raises(native.NativeIORejected) as caught:
        _OwnedSleepingRunner().run({"operation": "gcs_bucket"}, credential=_PRIVATE,
            deadline=deadline, guard=lambda: None)
    _assert_bounded(caught.value, "NATIVE_IO_DEADLINE_EXPIRED")
    finished = time.monotonic()
    assert deadline <= finished < deadline + native.CLEANUP_SECONDS + 1
    assert case.closes["stdout"] == case.closes["stderr"] == 1
    _assert_actual_reaping_before_finalizer(case)
