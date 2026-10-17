"""Bound and reap one fixed native I/O child without inheriting authority.

No SQL root may be held while invoking this runner. Its guard observes the
owner's composite-lease controller; it never substitutes for an SQL intent.
"""

from __future__ import annotations

import json
import math
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

MAX_INPUT = 750_000
MAX_OUTPUT = 750_000
MAX_STDERR = 65_536
MAX_SECONDS = 10
CLEANUP_SECONDS = 1
_CHILD = Path(__file__).with_name("native_intake_io_child.py").resolve()


class NativeIORejected(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None):
        super().__init__(code)
        self.code, self.status = code, status


class NativeIOUnreaped(NativeIORejected):
    """The caller must retain its quota until conservative expiry/recovery."""


def _kill_and_reap(process) -> None:
    # The process was created in its own session. Never signal by command
    # name, broad process search, tenant or a caller-supplied process ID.
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            raise NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED") from None
    try:
        process.wait(timeout=CLEANUP_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        raise NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED") from None
    # A fixed production child never forks; this check also refuses an
    # unexpected surviving process group rather than releasing quota early.
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return
    except OSError:
        raise NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED") from None
    raise NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED")


class NativeIntakeProcessRunner:
    def _command(self, credential_fd: int | None) -> list[str]:
        # This fixed entrypoint/isolated interpreter cannot be selected by a
        # URL, request body, job payload, policy JSON or environment setting.
        command = [sys.executable, "-I", "-S", str(_CHILD)]
        if credential_fd is not None:
            command += ["--credential-fd", str(credential_fd)]
        return command

    def run(self, request: dict, *, deadline: float, guard: Callable[[], None],
            credential: str | None = None) -> dict:
        start = time.monotonic()
        if (type(deadline) not in {int, float} or not math.isfinite(deadline)
                or not start < deadline <= start + MAX_SECONDS):
            raise NativeIORejected("NATIVE_IO_DEADLINE_REJECTED")
        if not isinstance(request, dict) or "timeout_seconds" in request:
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
        operation = request.get("operation")
        gcs = isinstance(operation, str) and operation.startswith("gcs_")
        if (credential is None) == gcs:
            raise NativeIORejected("NATIVE_IO_CREDENTIAL_CHANNEL_REJECTED")
        token = None
        if credential is not None:
            if (not isinstance(credential, str) or not 1 <= len(credential) <= 8192
                    or not credential.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in credential)):
                raise NativeIORejected("NATIVE_IO_CREDENTIAL_CHANNEL_REJECTED")
            token = credential.encode("ascii")
        guard()
        payload = (json.dumps({**request, "timeout_seconds": min(MAX_SECONDS, deadline - time.monotonic())},
                             separators=(",", ":"), allow_nan=False) + "\n").encode()
        if len(payload) > MAX_INPUT:
            raise NativeIORejected("NATIVE_IO_LENGTH_REJECTED")
        read_fd = write_fd = None
        process = None
        try:
            if token is not None:
                read_fd, write_fd = os.pipe()
            process = subprocess.Popen(self._command(read_fd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONUNBUFFERED": "1"},
                close_fds=True, pass_fds=() if read_fd is None else (read_fd,), start_new_session=True)
            if read_fd is not None:
                os.close(read_fd)
                read_fd = None
            with selectors.DefaultSelector() as selector:
                streams = {"stdin": process.stdin, "out": process.stdout, "err": process.stderr}
                writes = {process.stdin.fileno(): memoryview(payload)}
                for name, stream in streams.items():
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream.fileno(), selectors.EVENT_WRITE if name == "stdin" else selectors.EVENT_READ, name)
                if write_fd is not None:
                    os.set_blocking(write_fd, False)
                    writes[write_fd] = memoryview(token)
                    selector.register(write_fd, selectors.EVENT_WRITE, "credential")
                output, stderr_bytes = bytearray(), 0
                while selector.get_map():
                    guard()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise NativeIORejected("NATIVE_IO_DEADLINE_EXPIRED")
                    for key, _ in selector.select(min(0.05, remaining)):
                        descriptor, name = key.fd, key.data
                        if name in {"stdin", "credential"}:
                            try:
                                sent = os.write(descriptor, writes[descriptor][:65_536])
                            except BlockingIOError:
                                continue
                            writes[descriptor] = writes[descriptor][sent:]
                            if not writes[descriptor]:
                                selector.unregister(descriptor)
                                if name == "stdin":
                                    process.stdin.close()
                                else:
                                    os.close(write_fd)
                                    write_fd = None
                            continue
                        try:
                            data = os.read(descriptor, 65_536)
                        except BlockingIOError:
                            continue
                        if not data:
                            selector.unregister(descriptor)
                        elif name == "out":
                            output.extend(data)
                            if len(output) > MAX_OUTPUT:
                                raise NativeIORejected("NATIVE_IO_LENGTH_REJECTED")
                        else:
                            stderr_bytes += len(data)
                            if stderr_bytes > MAX_STDERR:
                                raise NativeIORejected("NATIVE_IO_LENGTH_REJECTED")
                guard()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise NativeIORejected("NATIVE_IO_DEADLINE_EXPIRED")
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    raise NativeIORejected("NATIVE_IO_DEADLINE_EXPIRED") from None
                guard()
                if process.returncode != 0 or time.monotonic() >= deadline or stderr_bytes:
                    raise NativeIORejected("NATIVE_IO_CHILD_FAILED")
                try:
                    reply = json.loads(output)
                except (ValueError, UnicodeError):
                    raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED") from None
                if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
                    raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
                if reply["ok"]:
                    if set(reply) != {"ok", "result"} or not isinstance(reply["result"], dict):
                        raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
                    return reply["result"]
                if (set(reply) != {"ok", "code", "status"} or not isinstance(reply["code"], str)
                        or not reply["code"].startswith(("NATIVE_IO_", "NATIVE_HTTP_", "NATIVE_GCS_"))
                        or not reply["code"].replace("_", "").isalnum()
                        or len(reply["code"]) > 96
                        or (reply["status"] is not None and (type(reply["status"]) is not int or not 100 <= reply["status"] <= 599))):
                    raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
                raise NativeIORejected(reply["code"], status=reply["status"])
        except NativeIORejected:
            raise
        except Exception:
            raise NativeIORejected("NATIVE_IO_CHILD_FAILED") from None
        finally:
            if read_fd is not None:
                os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                _kill_and_reap(process)
