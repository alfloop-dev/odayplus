"""Process isolation contract for solver operations with C++ native ABI conflicts."""

from __future__ import annotations

import os
import pickle
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


class ProcessIsolationError(RuntimeError):
    """Raised when an isolated solver process execution fails."""


_CHILD_SCRIPT = """
import sys
import pickle

result_path = sys.argv[1]
try:
    raw_payload = sys.stdin.buffer.read()
    func, args, kwargs = pickle.loads(raw_payload)
    res = func(*args, **kwargs)
    out = pickle.dumps((True, res))
except Exception as e:
    out = pickle.dumps((False, e))

try:
    with open(result_path, "wb") as f:
        f.write(out)
except Exception:
    pass
"""


def run_in_process_isolation[T](
    func: Callable[..., T],
    *args: Any,
    timeout: float = 60.0,
    **kwargs: Any,
) -> T:
    """Execute a solver function in an isolated Python process.

    This guarantees that C++ native ABI symbol collisions (such as OR-Tools
    and CVXPY / HiGHS symbol overlap) are strictly isolated between solver runs.

    Payload transport is passed via stdin to avoid command line length limits (ARG_MAX),
    and execution results are written to a dedicated temporary file to ensure stdout/stderr
    logging does not corrupt serialized return values.
    """
    try:
        payload = pickle.dumps((func, args, kwargs))
    except Exception as err:
        raise ProcessIsolationError(
            f"Failed to serialize function arguments for isolated solver: {err}"
        ) from err

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(sys.path)

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        result_path = tmp.name

    try:
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c", _CHILD_SCRIPT, result_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            stdout, stderr = proc.communicate(input=payload, timeout=timeout)
        except subprocess.TimeoutExpired as err:
            if "proc" in locals():
                try:
                    proc.kill()
                    proc.wait()
                except OSError:
                    pass
            raise ProcessIsolationError(
                f"Solver process isolation timed out after {timeout} seconds"
            ) from err
        except Exception as err:
            if "proc" in locals():
                try:
                    proc.kill()
                    proc.wait()
                except OSError:
                    pass
            raise ProcessIsolationError(
                f"Failed to spawn or communicate with isolated solver process: {err}"
            ) from err

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            raise ProcessIsolationError(
                f"Solver isolated process crashed with exit code {proc.returncode}: {err_msg}"
            )

        if not os.path.exists(result_path):
            raise ProcessIsolationError("Isolated solver process did not produce a result file")

        try:
            with open(result_path, "rb") as f:
                raw_res = f.read()
            if not raw_res:
                raise ProcessIsolationError(
                    "Isolated solver process produced an empty result file"
                )
            success, value_or_exc = pickle.loads(raw_res)
        except Exception as err:
            if isinstance(err, ProcessIsolationError):
                raise err
            raise ProcessIsolationError("Failed to deserialize isolated solver result") from err

        if not success:
            if isinstance(value_or_exc, Exception):
                raise value_or_exc
            raise ProcessIsolationError(str(value_or_exc))

        return value_or_exc
    finally:
        if os.path.exists(result_path):
            try:
                os.remove(result_path)
            except OSError:
                pass


def _sleeping_worker_record_pid(pid_file: str) -> None:
    import time

    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
        f.flush()
    time.sleep(10)
