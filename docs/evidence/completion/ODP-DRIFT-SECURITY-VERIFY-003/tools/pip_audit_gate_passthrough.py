#!/usr/bin/env python3
"""Capture the real subprocess output of the merged pip_audit_gate, unmodified.

Reviewer finding (reopen #3, item 1): the authoritative 215-package audit was
evidenced only by the gate's one-line PASS summary. The gate consumes
`pip-audit --format json` internally and prints a verdict, so the per-package
advisory payload it actually judged was never written down. The only raw JSON
in the receipts came from a *different*, wrong-scope invocation
(`pip-audit --local`, `dependencies: []`), which proves nothing about the 215
installed packages.

This tool re-runs the same merged gate -- `delivery_toolchain/security/
pip_audit_gate.py` from PR #1188, imported, not copied or edited -- and records
what its child process actually returned.

How it stays a capture rather than a second gate:

* The gate module is imported and its own ``main()`` is what runs, so argument
  parsing, environment resolution, scope selection, retry policy, structural
  classification, ``evaluate()`` and the exit code are all the gate's.
* The only thing injected is the ``runner`` seam ``run_pip_audit`` already
  exposes for its subprocess call. The injected runner calls
  ``subprocess.run`` with the arguments it was given and returns the result
  object untouched, so the gate sees exactly the bytes it would have seen.
* No verdict is computed here. The process exits with the gate's exit code.
* Nothing about the gate on disk is modified; the patch is to the imported
  module object, inside this process, for the duration of this run.

Receipts are written to ``ODP_PASSTHROUGH_CAPTURE_DIR``. Every remaining
argument is forwarded untouched to the gate's own parser.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.security import pip_audit_gate  # noqa: E402

CAPTURE_DIR = Path(
    os.environ.get(
        "ODP_PASSTHROUGH_CAPTURE_DIR",
        str(ROOT / "docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/receipts"),
    )
)
PREFIX = os.environ.get("ODP_PASSTHROUGH_PREFIX", "pip_audit_gate_child")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Tee(io.TextIOBase):
    """Forward writes to the real stream while keeping a copy."""

    def __init__(self, stream):
        self._stream = stream
        self.buffer_text = io.StringIO()

    def write(self, data):  # type: ignore[override]
        self.buffer_text.write(data)
        return self._stream.write(data)

    def flush(self):  # type: ignore[override]
        self._stream.flush()


def _probe_pip_audit_version() -> dict[str, object]:
    """Ask pip-audit its version in a separate, clearly-labelled invocation.

    This is auxiliary provenance, not part of the gate run: the gate never
    calls it and its result cannot influence the gate's verdict.
    """
    cmd = ["uv", "run", "--with", "pip-audit", "pip-audit", "--version"]
    try:
        res = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=300, check=False
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return {"command": cmd, "error": repr(exc)}
    return {
        "command": cmd,
        "command_line": shlex.join(cmd),
        "returncode": res.returncode,
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
        "observed_at_utc": _utc_now(),
    }


def main() -> int:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, object]] = []
    real_run = subprocess.run

    def recording_runner(cmd, **kwargs):
        """Pass straight through to subprocess.run, recording both sides."""
        index = len(attempts) + 1
        started_at = _utc_now()
        started = time.monotonic()
        try:
            res = real_run(cmd, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised; the gate classifies it
            attempts.append(
                {
                    "attempt": index,
                    "argv": list(cmd),
                    "command_line": shlex.join(str(part) for part in cmd),
                    "cwd": kwargs.get("cwd"),
                    "timeout_seconds": kwargs.get("timeout"),
                    "started_at_utc": started_at,
                    "finished_at_utc": _utc_now(),
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "raised": repr(exc),
                }
            )
            raise
        duration = round(time.monotonic() - started, 3)
        finished_at = _utc_now()

        stdout_path = CAPTURE_DIR / f"{PREFIX}_attempt{index}_stdout.json"
        stderr_path = CAPTURE_DIR / f"{PREFIX}_attempt{index}_stderr.txt"
        stdout_path.write_text(res.stdout, encoding="utf-8")
        stderr_path.write_text(res.stderr, encoding="utf-8")

        attempts.append(
            {
                "attempt": index,
                "argv": list(cmd),
                "command_line": shlex.join(str(part) for part in cmd),
                "cwd": kwargs.get("cwd"),
                "timeout_seconds": kwargs.get("timeout"),
                "capture_output": kwargs.get("capture_output"),
                "text_mode": kwargs.get("text"),
                "started_at_utc": started_at,
                "finished_at_utc": finished_at,
                "duration_seconds": duration,
                "returncode": res.returncode,
                "stdout_bytes": len(res.stdout.encode("utf-8")),
                "stderr_bytes": len(res.stderr.encode("utf-8")),
                "stdout_sha256": _sha256(res.stdout),
                "stderr_sha256": _sha256(res.stderr),
                "stdout_receipt": str(stdout_path.relative_to(ROOT)),
                "stderr_receipt": str(stderr_path.relative_to(ROOT)),
                "stderr_text": res.stderr,
            }
        )
        return res

    original_run_pip_audit = pip_audit_gate.run_pip_audit

    def run_pip_audit_with_capture(*args, **kwargs):
        kwargs["runner"] = recording_runner
        return original_run_pip_audit(*args, **kwargs)

    pip_audit_gate.run_pip_audit = run_pip_audit_with_capture

    version_probe = _probe_pip_audit_version()

    gate_stdout = _Tee(sys.stdout)
    gate_stderr = _Tee(sys.stderr)
    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = gate_stdout, gate_stderr
    started_at = _utc_now()
    started = time.monotonic()
    try:
        exit_code = pip_audit_gate.main()
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
        pip_audit_gate.run_pip_audit = original_run_pip_audit
    duration = round(time.monotonic() - started, 3)

    capture = {
        "purpose": (
            "Raw subprocess evidence for the authoritative pip_audit_gate run "
            "(reopen #3 item 1). The gate is imported and unmodified; only its "
            "documented runner seam is injected."
        ),
        "gate_module_file": str(Path(pip_audit_gate.__file__).relative_to(ROOT)),
        "gate_module_sha256": hashlib.sha256(
            Path(pip_audit_gate.__file__).read_bytes()
        ).hexdigest(),
        "gate_forwarded_argv": sys.argv[1:],
        "gate_resolved_defaults": {
            "installation_path": pip_audit_gate.DEFAULT_INSTALLATION_PATH,
            "socket_timeout": pip_audit_gate.DEFAULT_SOCKET_TIMEOUT,
            "process_timeout": pip_audit_gate.DEFAULT_PROCESS_TIMEOUT,
            "attempts": pip_audit_gate.DEFAULT_ATTEMPTS,
            "service": pip_audit_gate.DEFAULT_SERVICE,
        },
        "environment_overrides": {
            key: value
            for key, value in sorted(os.environ.items())
            if key.startswith("ODP_PIP_AUDIT") or key in {"VIRTUAL_ENV", "ODP_AUDIT_TIMEOUT"}
        },
        "started_at_utc": started_at,
        "finished_at_utc": _utc_now(),
        "duration_seconds": duration,
        "gate_exit_code": exit_code,
        "gate_exit_code_source": "return value of pip_audit_gate.main()",
        "gate_verdict_stdout": gate_stdout.buffer_text.getvalue(),
        "gate_verdict_stderr": gate_stderr.buffer_text.getvalue(),
        "child_invocations": attempts,
        "child_invocation_count": len(attempts),
        "pip_audit_version_probe": version_probe,
        "python_version": sys.version,
        "python_executable": sys.executable,
    }
    capture_path = CAPTURE_DIR / f"{PREFIX}_capture.json"
    capture_path.write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
