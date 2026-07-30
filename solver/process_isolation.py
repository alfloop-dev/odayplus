"""Process isolation contract for solver operations with C++ native ABI conflicts."""

from __future__ import annotations

import base64
import os
import pickle
import subprocess
import sys
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class ProcessIsolationError(RuntimeError):
    """Raised when an isolated solver process execution fails."""


def run_in_process_isolation(
    func: Callable[..., T],
    *args: Any,
    timeout: float = 60.0,
    **kwargs: Any,
) -> T:
    """Execute a solver function in an isolated Python process.

    This guarantees that C++ native ABI symbol collisions (such as OR-Tools
    and CVXPY / HiGHS symbol overlap) are strictly isolated between solver runs.
    """
    payload = base64.b64encode(pickle.dumps((func, args, kwargs))).decode("ascii")
    code = f"""
import sys, pickle, base64
raw = base64.b64decode("{payload}")
func, args, kwargs = pickle.loads(raw)
try:
    res = func(*args, **kwargs)
    out = pickle.dumps((True, res))
    sys.stdout.buffer.write(base64.b64encode(out))
except Exception as e:
    out = pickle.dumps((False, e))
    sys.stdout.buffer.write(base64.b64encode(out))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(sys.path)

    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as err:
        raise ProcessIsolationError(
            f"Solver process isolation timed out after {timeout} seconds"
        ) from err

    if proc.returncode != 0:
        err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
        raise ProcessIsolationError(
            f"Solver isolated process crashed with exit code {proc.returncode}: {err_msg}"
        )

    try:
        success, value_or_exc = pickle.loads(base64.b64decode(proc.stdout))
    except Exception as err:
        raise ProcessIsolationError("Failed to deserialize isolated solver result") from err

    if not success:
        if isinstance(value_or_exc, Exception):
            raise value_or_exc
        raise ProcessIsolationError(str(value_or_exc))

    return value_or_exc
