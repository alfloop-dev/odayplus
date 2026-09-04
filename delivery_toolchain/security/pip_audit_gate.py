#!/usr/bin/env python3
"""Python dependency audit gate with bounded timeouts and fail-closed diagnostics.

Background (ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001)
--------------------------------------------------
`pip-audit` queries PyPI/OSV vulnerability databases over HTTP. When the
database or network fails (socket timeout, 503 Service Unavailable, DNS/connection
errors), pip-audit exits non-zero with error diagnostics.

This gate wraps `pip-audit` to ensure:
* Explicit bounded network/socket timeout and overall process execution budget;
* Structural parsing of audit findings via `--format json`;
* Accurate failure source classification (distinguishing vulnerability findings
  from network/service outages and timeouts);
* Bounded retries on transient transport failures, exiting 2 (AUDIT UNAVAILABLE)
  if the service remains unreachable;
* Every reported vulnerability causes gate failure with exit 1 (VULNERABLE);
  pip-audit's JSON format has no normalized severity, so there is no safe
  high-only equivalent for this gate;
* Clean audit reports pass with exit 0 (OK);
* Transport/database failures never pass: without advisory data the gate stays closed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.release.release_receipts import redact

EXIT_OK = 0
EXIT_VULNERABLE = 1
EXIT_AUDIT_UNAVAILABLE = 2

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 5.0
DEFAULT_SOCKET_TIMEOUT = 15.0
DEFAULT_PROCESS_TIMEOUT = 300.0
DEFAULT_SERVICE = "pypi"
DEFAULT_INSTALLATION_PATH = ".venv/lib/python3.12/site-packages"

REPORT = "report"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AuditOutcome:
    """Result of one `pip-audit` invocation.

    `kind` is `REPORT` when the vulnerability database returned advisory data
    (with `findings` holding any vulnerability descriptions), or `UNAVAILABLE`
    when the audit never yielded a valid report.
    """

    kind: str
    findings: list[str] | None
    detail: str
    total_dependencies: int = 0

    @property
    def has_report(self) -> bool:
        return self.kind == REPORT


def _extract_json(text: str) -> object | None:
    """Extract and parse a JSON object from text, even if preceded by informational headers."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def classify_pip_audit_output(stdout: str, stderr: str) -> AuditOutcome:
    """Classify `pip-audit --format json` output structurally into report vs unavailable."""
    payload = _extract_json(stdout)

    if isinstance(payload, dict) and "dependencies" in payload:
        raw_deps = payload.get("dependencies")
        if isinstance(raw_deps, list):
            if not raw_deps:
                return AuditOutcome(
                    UNAVAILABLE,
                    None,
                    "pip-audit returned an empty dependency report; no packages were audited",
                )
            findings: list[str] = []
            for dep in raw_deps:
                if not isinstance(dep, dict):
                    continue
                dep_name = dep.get("name", "unknown")
                dep_ver = dep.get("version", "")
                vulns = dep.get("vulns")
                if isinstance(vulns, list) and vulns:
                    for v in vulns:
                        vid = v.get("id", "VULN") if isinstance(v, dict) else "VULN"
                        findings.append(f"{dep_name} {dep_ver} ({vid})".strip())
            return AuditOutcome(
                REPORT,
                findings,
                f"pip-audit returned advisory data for {len(raw_deps)} dependencies",
                total_dependencies=len(raw_deps),
            )
        return AuditOutcome(UNAVAILABLE, None, "audit report 'dependencies' is not a list")

    combined = f"{stdout}\n{stderr}".strip()
    lowered = combined.lower()

    if "503" in combined or "service unavailable" in lowered or "502" in combined or "504" in combined:
        detail = f"registry error (503/502/504 Service Unavailable): {combined[:400]}"
    elif "timed out" in lowered or "timeout" in lowered:
        detail = f"PyPI/OSV vulnerability database timeout: {combined[:400]}"
    elif "connection" in lowered or "econnrefused" in lowered or "resolution failed" in lowered:
        detail = f"connection error contacting PyPI/OSV: {combined[:400]}"
    else:
        detail = f"pip-audit produced no parsable report: {combined[:400]}"

    return AuditOutcome(UNAVAILABLE, None, detail)


def run_pip_audit(
    cwd: Path = ROOT,
    socket_timeout: float = DEFAULT_SOCKET_TIMEOUT,
    process_timeout: float = DEFAULT_PROCESS_TIMEOUT,
    service: str = DEFAULT_SERVICE,
    runner=subprocess.run,
) -> AuditOutcome:
    """Run pip-audit once with bounded socket and process timeouts.

    ``uv run --with`` executes the tool in an ephemeral overlay. ``--local``
    would inspect that overlay rather than the project environment and can
    report zero dependencies even after ``uv sync`` installed the project.
    Pointing pip-audit at the synced site-packages directory makes the audit
    scope explicit and keeps the dependency count meaningful.
    """
    cmd = [
        "uv",
        "run",
        "--with",
        "pip-audit",
        "pip-audit",
        "--path",
        DEFAULT_INSTALLATION_PATH,
        "--format",
        "json",
        "--timeout",
        str(int(socket_timeout)),
    ]
    if service and service != "pypi":
        cmd.extend(["--vulnerability-service", service])

    try:
        res = runner(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=process_timeout,
        )
    except subprocess.TimeoutExpired:
        return AuditOutcome(
            UNAVAILABLE,
            None,
            f"pip-audit process timed out after {process_timeout:.0f}s contacting vulnerability database",
        )
    except FileNotFoundError:
        return AuditOutcome(UNAVAILABLE, None, "uv executable not found")

    outcome = classify_pip_audit_output(res.stdout, res.stderr)
    # pip-audit uses exit 1 for a report containing vulnerabilities. Any other
    # non-zero result is an execution or service failure, even if a partial
    # JSON payload happened to be written before it exited.
    if res.returncode not in (EXIT_OK, EXIT_VULNERABLE):
        return AuditOutcome(
            UNAVAILABLE,
            None,
            f"pip-audit exited with status {res.returncode}: {outcome.detail}",
        )
    return outcome


def audit_with_retry(
    cwd: Path = ROOT,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF_SECONDS,
    socket_timeout: float = DEFAULT_SOCKET_TIMEOUT,
    process_timeout: float = DEFAULT_PROCESS_TIMEOUT,
    service: str = DEFAULT_SERVICE,
    sleep=time.sleep,
    runner=subprocess.run,
) -> AuditOutcome:
    """Run pip-audit until it yields a report or attempts are exhausted."""
    outcome = AuditOutcome(UNAVAILABLE, None, "no audit attempt was made")
    for attempt in range(1, max(1, attempts) + 1):
        outcome = run_pip_audit(
            cwd=cwd,
            socket_timeout=socket_timeout,
            process_timeout=process_timeout,
            service=service,
            runner=runner,
        )
        if outcome.has_report:
            return outcome
        print(
            f"pip-audit attempt {attempt}/{attempts} did not return advisory data: "
            f"{outcome.detail}",
            file=sys.stderr,
        )
        if attempt < attempts:
            sleep(backoff * attempt)
    return outcome


def evaluate(outcome: AuditOutcome) -> tuple[int, str]:
    """Map an audit outcome onto an exit code and a human-readable verdict."""
    if not outcome.has_report:
        return (
            EXIT_AUDIT_UNAVAILABLE,
            "AUDIT UNAVAILABLE: the Python vulnerability database (PyPI/OSV) never returned "
            f"advisory data, so this run proves nothing about Python dependencies. Last error: {redact(outcome.detail)}",
        )

    # pip-audit does not provide a normalized severity in its JSON schema.
    # Every advisory is therefore intentionally blocking; treating an unknown
    # severity as below threshold would turn a real finding into a pass.
    if outcome.findings:
        summary = ", ".join(outcome.findings)
        return (
            EXIT_VULNERABLE,
            "VULNERABILITIES FOUND in Python dependencies (all reported findings are blocking; "
            f"pip-audit has no normalized severity): {summary}. "
            "Run 'uv run --with pip-audit pip-audit --path "
            f"{DEFAULT_INSTALLATION_PATH}' for details.",
        )

    return (
        EXIT_OK,
        f"PASS: no Python package vulnerabilities found ({outcome.total_dependencies} dependencies audited).",
    )


def _env_float(name: str, default: float, fallback_name: str | None = None) -> float:
    for var in (name, fallback_name) if fallback_name else (name,):
        if var and var in os.environ:
            try:
                return float(os.environ[var])
            except ValueError:
                pass
    return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def main() -> int:
    parser = argparse.ArgumentParser(description="Python dependency audit gate")
    parser.add_argument(
        "--socket-timeout",
        type=float,
        default=None,
        help="HTTP socket timeout in seconds for querying vulnerability databases",
    )
    parser.add_argument(
        "--process-timeout",
        type=float,
        default=None,
        help="Overall process execution timeout in seconds",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=None,
        help="Number of retry attempts on transient registry errors",
    )
    parser.add_argument(
        "--service",
        type=str,
        default=None,
        help="Vulnerability service (pypi, osv)",
    )
    args = parser.parse_args()

    socket_timeout = (
        args.socket_timeout
        if args.socket_timeout is not None
        else _env_float(
            "ODP_PIP_AUDIT_SOCKET_TIMEOUT_SECONDS",
            DEFAULT_SOCKET_TIMEOUT,
            "ODP_AUDIT_TIMEOUT",
        )
    )
    process_timeout = (
        args.process_timeout
        if args.process_timeout is not None
        else _env_float(
            "ODP_PIP_AUDIT_PROCESS_TIMEOUT_SECONDS",
            DEFAULT_PROCESS_TIMEOUT,
            "ODP_PIP_AUDIT_TIMEOUT_SECONDS",
        )
    )
    attempts = (
        args.attempts
        if args.attempts is not None
        else _env_int("ODP_PIP_AUDIT_ATTEMPTS", DEFAULT_ATTEMPTS)
    )
    service = (
        args.service
        if args.service is not None
        else os.environ.get("ODP_PIP_AUDIT_SERVICE", DEFAULT_SERVICE)
    )

    if socket_timeout <= 0 or process_timeout <= 0 or attempts <= 0:
        print("[FAIL CLOSED] Invalid timeout or attempt configuration", file=sys.stderr)
        return EXIT_AUDIT_UNAVAILABLE

    outcome = audit_with_retry(
        attempts=attempts,
        backoff=_env_float("ODP_PIP_AUDIT_BACKOFF_SECONDS", DEFAULT_BACKOFF_SECONDS),
        socket_timeout=socket_timeout,
        process_timeout=process_timeout,
        service=service,
    )
    code, verdict = evaluate(outcome)
    print(verdict, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
