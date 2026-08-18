#!/usr/bin/env python3
"""Shared PTY driver for interactive provider login flows.

Both the Codex and Antigravity sign-in flows only render their device code /
OAuth URL when they believe they are attached to a terminal, so the orchestrator
drives them through a pty and scrapes the rendered output.
"""
from __future__ import annotations

import errno
import os
import pty
import re
import select
import signal
import subprocess
import sys
import termios
import time
from collections.abc import Callable, Mapping

ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[@-_]")

# Keep enough scrollback to hold a full TUI frame (the Antigravity OAuth box is
# redrawn on every spinner tick) without growing without bound.
MAX_BUFFER_CHARS = 200_000


def strip_ansi(text: str) -> str:
    return ANSI_PATTERN.sub("", text)


class PtyLoginSession:
    """Run a login command on a pty while streaming and buffering its output."""

    def __init__(
        self,
        command: list[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | os.PathLike[str] | None = None,
        winsize: tuple[int, int] = (50, 140),
        echo_to_stdout: bool = True,
    ) -> None:
        self.command = list(command)
        self.env = dict(env) if env is not None else None
        self.cwd = str(cwd) if cwd is not None else None
        self.winsize = winsize
        self.echo_to_stdout = echo_to_stdout
        self.buffer = ""
        self._master_fd: int | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._eof = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        master_fd, slave_fd = pty.openpty()
        try:
            termios.tcsetwinsize(slave_fd, self.winsize)
        except OSError:
            pass
        self._process = subprocess.Popen(
            self.command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self.cwd,
            env=self.env,
            text=False,
            close_fds=True,
        )
        os.close(slave_fd)
        self._master_fd = master_fd

    @property
    def process(self) -> subprocess.Popen[bytes]:
        if self._process is None:
            raise RuntimeError("PtyLoginSession.start() has not been called")
        return self._process

    def poll(self) -> int | None:
        return self.process.poll()

    # -- io ----------------------------------------------------------------

    def _pump(self, timeout: float) -> bool:
        """Read one chunk if available. Returns False once the pty is closed."""
        if self._master_fd is None or self._eof:
            return False
        ready, _, _ = select.select([self._master_fd], [], [], timeout)
        if not ready:
            return True
        try:
            chunk = os.read(self._master_fd, 8192)
        except OSError as exc:
            # The pty raises EIO rather than returning EOF once the child exits.
            if exc.errno == errno.EIO:
                self._eof = True
                return False
            raise
        if not chunk:
            self._eof = True
            return False
        text = chunk.decode("utf-8", errors="replace")
        if self.echo_to_stdout:
            sys.stdout.write(text)
            sys.stdout.flush()
        self.buffer = (self.buffer + strip_ansi(text))[-MAX_BUFFER_CHARS:]
        return True

    def send(self, data: str | bytes) -> None:
        if self._master_fd is None:
            raise RuntimeError("PtyLoginSession.start() has not been called")
        payload = data.encode() if isinstance(data, str) else data
        try:
            os.write(self._master_fd, payload)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise

    def wait_for(
        self,
        predicate: Callable[[str], bool],
        *,
        timeout: float,
        poll_interval: float = 0.2,
    ) -> bool:
        """Pump output until ``predicate(buffer)`` holds, the child exits, or timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(self.buffer):
                return True
            alive = self._pump(poll_interval)
            if not alive:
                # Drain whatever is still buffered before giving up.
                return predicate(self.buffer)
            if self.poll() is not None and not self._pump(0):
                return predicate(self.buffer)
        return predicate(self.buffer)

    def wait_for_exit(self, *, timeout: float) -> int | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._pump(0.2) and self.poll() is not None:
                break
            if self.poll() is not None:
                # Drain the tail after exit.
                while self._pump(0):
                    if self._eof:
                        break
                break
        return self.poll()

    def drain(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self._pump(0.2):
                break

    def close(self, *, grace: float = 1.0) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline and process.poll() is None:
                self._pump(0.1)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=grace)
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    def __enter__(self) -> PtyLoginSession:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def unwrap_boxed_text(region: str) -> str:
    """Join a TUI box's wrapped lines back into one unbroken string.

    Both CLIs print long OAuth URLs inside a bordered box, hard-wrapped at the
    terminal width with padding. Stripping each line and concatenating restores
    the original URL.
    """
    parts: list[str] = []
    for raw_line in region.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Drop the box borders (runs of ─ / - / = used as rules).
        if set(line) <= {"─", "-", "=", "_", "━", "·"}:
            continue
        parts.append(line)
    return "".join(parts)
