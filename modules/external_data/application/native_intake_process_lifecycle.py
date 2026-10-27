"""Private, one-use evidence from one synchronous fixed-runner invocation.

This captures control flow, not authority, output, or a SQL cleanup grant.
Only the runner records spawn entry and successful owned wait/group absence.
An exception, process handle absence, or child reply cannot supply those facts.
Python code with access to module internals is outside this trust boundary.
"""

from __future__ import annotations

import threading
import weakref
from contextlib import contextmanager
from contextvars import ContextVar

_UNAVAILABLE = "NATIVE_PROCESS_COMPLETION_UNAVAILABLE"


class NativeProcessCompletionUnavailable(RuntimeError):
    """Private refusal with no request, process, or exception details."""

    def __init__(self):
        super().__init__(_UNAVAILABLE)
        self.code = _UNAVAILABLE


class _Observation:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise NativeProcessCompletionUnavailable()

    def __init_subclass__(cls, **kwargs):
        raise NativeProcessCompletionUnavailable()

    def __copy__(self):
        raise NativeProcessCompletionUnavailable()

    def __deepcopy__(self, memo):
        raise NativeProcessCompletionUnavailable()

    def __reduce_ex__(self, protocol):
        raise NativeProcessCompletionUnavailable()


class _Capture:
    # No reference back to the weak registry key, callback, exception, request,
    # credential, or process. Dropping the observation releases its registry row.
    __slots__ = ("owner_id", "owner_thread", "invalid", "closed", "runs",
                 "active_run", "finished", "phase", "__weakref__")

    def __init__(self):
        self.owner_id = threading.get_ident()
        self.owner_thread = weakref.ref(threading.current_thread())
        self.invalid = self.closed = self.finished = False
        self.runs = 0
        self.active_run = None
        self.phase = "EMPTY"


class _Run:
    __slots__ = ("capture",)

    def __new__(cls):
        raise NativeProcessCompletionUnavailable()


_captures: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
# Context copies on one thread must not hide overlapping capture scopes.
# Both sides are weak: neither a thread nor a released capture is kept alive.
_owners: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_current: ContextVar[_Capture | None] = ContextVar("native_process_capture", default=None)
_lock = threading.RLock()


def _on_owner_thread(capture):
    # Thread identities may be recycled; retain only a weak identity reference.
    return (capture.owner_id == threading.get_ident()
            and capture.owner_thread() is threading.current_thread())


def _close_capture(capture):
    with _lock:
        if not _on_owner_thread(capture) or capture.active_run is not None:
            capture.invalid = True
        capture.closed = True


@contextmanager
def _observe_native_process():
    """Capture exactly one actual run; closing never replaces the body outcome."""
    observation = object.__new__(_Observation)
    capture = owner = reset = previous_owner = None
    try:
        with _lock:
            parent = _current.get()
            if parent is not None:
                parent.invalid = True
            capture = _Capture()
            capture.invalid = parent is not None
            owner = threading.current_thread()
            previous_owner = _owners.get(owner)
            overlapping = previous_owner() if previous_owner is not None else None
            if overlapping is not None:
                overlapping.invalid = capture.invalid = True
            _owners[owner] = weakref.ref(capture)
            _captures[observation] = capture
            reset = _current.set(capture)
    except BaseException:
        if capture is not None:
            capture.invalid = True
    try:
        yield observation
    except BaseException as error:
        try:
            # Resolve only on unwind to avoid the runner's module-load cycle.
            # An explicit unreaped outcome also vetoes a later adapter path;
            # no other exception is interpreted as positive lifecycle proof.
            from modules.external_data.application.native_intake_process import NativeIOUnreaped

            if capture is not None and isinstance(error, NativeIOUnreaped):
                capture.invalid = True
        except BaseException:
            if capture is not None:
                capture.invalid = True
        raise
    finally:
        # Reset independently of sealing, including when the body raised.
        try:
            if reset is not None:
                _current.reset(reset)
        except BaseException:
            if capture is not None:
                capture.invalid = True
        try:
            if capture is not None:
                _close_capture(capture)
        except BaseException:
            if capture is not None:
                capture.invalid = True
        finally:
            try:
                with _lock:
                    registered = _owners.get(owner) if owner is not None else None
                    active = registered() if registered is not None else None
                    if active is not None and active is capture:
                        del _owners[owner]
                        previous = previous_owner() if previous_owner is not None else None
                        if previous is not None and not previous.closed:
                            _owners[owner] = previous_owner
                    elif active is not None:
                        active.invalid = True
                        if capture is not None:
                            capture.invalid = True
            except BaseException:
                if capture is not None:
                    capture.invalid = True


def _consume_native_process_completion(observation) -> str:
    """Take once, after closure on the issuing thread; any failed take burns it."""
    capture = None
    try:
        with _lock:
            if type(observation) is _Observation:
                capture = _captures.pop(observation, None)
            if (capture is not None and _on_owner_thread(capture)
                    and capture.closed and not capture.invalid and capture.runs == 1
                    and capture.finished and capture.active_run is None):
                if capture.phase == "ENTERED":
                    return "NEVER_LAUNCHED"
                if capture.phase == "REAPED":
                    return "REAPED"
    except BaseException:
        pass
    if capture is not None:
        capture.invalid = True
    raise NativeProcessCompletionUnavailable() from None


def _begin_run(capture):
    with _lock:
        capture.runs = min(2, capture.runs + 1)
        if (not _on_owner_thread(capture) or capture.closed or capture.invalid
                or capture.runs != 1 or capture.active_run is not None):
            capture.invalid = True
            return None
        invocation = object.__new__(_Run)
        invocation.capture = weakref.ref(capture)
        capture.active_run = invocation
        capture.phase = "ENTERED"
        return invocation


def _finish_run(capture, invocation):
    with _lock:
        if (not _on_owner_thread(capture) or capture.closed or invocation is None
                or capture.active_run is not invocation):
            capture.invalid = True
        capture.active_run = None
        capture.finished = True


@contextmanager
def _native_process_run_scope():
    """Wrap all actual run control flow, including preflight and final cleanup."""
    capture = invocation = None
    try:
        capture = _current.get()
        if capture is not None:
            invocation = _begin_run(capture)
    except BaseException:
        if capture is not None:
            capture.invalid = True
    try:
        yield invocation
    finally:
        if capture is not None:
            try:
                _finish_run(capture, invocation)
            except BaseException:
                capture.invalid = True


def _transition(capture, invocation, event):
    with _lock:
        if (not _on_owner_thread(capture) or capture.closed or capture.invalid
                or capture.active_run is not invocation):
            capture.invalid = True
        elif event == "SPAWN_ENTERED" and capture.phase == "ENTERED":
            capture.phase = "SPAWN_ENTERED"
        elif event == "REAPED" and capture.phase == "SPAWN_ENTERED":
            capture.phase = "REAPED"
        else:
            # UNPROVED is sticky even if a later cleanup branch succeeds.
            capture.invalid = True


def _note_native_process(invocation, event):
    """Instrumentation must never change success or the runner's error priority."""
    if invocation is None:
        return
    capture = None
    try:
        if type(invocation) is not _Run:
            return
        capture = invocation.capture()
        if capture is not None:
            _transition(capture, invocation, event)
            if event not in {"SPAWN_ENTERED", "REAPED"} or capture.phase != event:
                capture.invalid = True
    except BaseException:
        if capture is not None:
            capture.invalid = True
