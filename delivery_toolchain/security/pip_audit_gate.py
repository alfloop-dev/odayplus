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
import math
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


def get_default_installation_path() -> str:
    """Resolve the active virtual environment site-packages directory dynamically."""
    venv = os.environ.get("VIRTUAL_ENV")
    venv_path = Path(venv) if venv else ROOT / ".venv"
    py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        venv_path / "lib" / py_ver / "site-packages",
        venv_path / "Lib" / "site-packages",
    ]
    for c in candidates:
        if c.exists():
            try:
                return str(c.relative_to(ROOT))
            except ValueError:
                return str(c)
    lib_dir = venv_path / "lib"
    if lib_dir.exists():
        py_dirs = sorted(lib_dir.glob("python3.*/site-packages"))
        if py_dirs and py_dirs[0].exists():
            c = py_dirs[0]
            try:
                return str(c.relative_to(ROOT))
            except ValueError:
                return str(c)
    return f".venv/lib/{py_ver}/site-packages"


DEFAULT_INSTALLATION_PATH = get_default_installation_path()

REPORT = "report"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AuditOutcome:
    """Result of one `pip-audit` invocation.

    `kind` is `REPORT` when the vulnerability database returned advisory data
    (with `findings` holding any vulnerability descriptions), or `UNAVAILABLE`
    when the audit never yielded a valid report.

    `retryable` is True only for a failure a later attempt could plausibly
    resolve: a transport timeout, a connection error or an upstream 5xx. A
    missing executable, unparsable output or an incomplete report is
    deterministic, so retrying it only burns the CI budget before failing
    closed anyway.
    """

    kind: str
    findings: list[str] | None
    detail: str
    total_dependencies: int = 0
    retryable: bool = False

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


def _summarize(items: list[str], limit: int = 10) -> str:
    """Join a bounded sample of `items` so one bad report cannot flood the log."""
    shown = ", ".join(items[:limit])
    if len(items) > limit:
        shown += f", ... (+{len(items) - limit} more)"
    return shown


def classify_dependency_entries(raw_deps: list[object]) -> AuditOutcome:
    """Turn pip-audit's `dependencies` array into a report, or refuse it.

    pip-audit's JSON formatter represents a package it could *not* audit as
    ``{"name": ..., "skip_reason": ...}`` with no ``vulns`` key -- an editable
    install, a package missing from the index, a resolution failure. Such an
    entry proves the package was seen, not that it was scanned. Treating it as
    audited-and-clean (as counting names and defaulting a missing ``vulns`` to
    "none" does) reports "no vulnerabilities" for a tree that was never fully
    checked, which is the fail-open this gate exists to prevent.

    So any skipped or malformed entry makes the whole report incomplete and the
    gate stays closed. Anything already visible in the partial data is carried
    into the detail so the failure is still diagnosable.
    """
    if not raw_deps:
        return AuditOutcome(
            UNAVAILABLE,
            None,
            "pip-audit returned an empty dependency report; no packages were audited",
        )

    findings: list[str] = []
    seen_findings: set[str] = set()
    unique_deps: set[str] = set()
    skipped: list[str] = []
    malformed: list[str] = []

    for index, dep in enumerate(raw_deps):
        if not isinstance(dep, dict):
            malformed.append(f"entry {index} is {type(dep).__name__}, not an object")
            continue

        raw_name = dep.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            malformed.append(f"entry {index} has no usable 'name'")
            continue
        dep_name = raw_name.strip()

        skip_reason = dep.get("skip_reason")
        if skip_reason:
            skipped.append(f"{dep_name} ({str(skip_reason).strip()[:120]})")
            continue

        vulns = dep.get("vulns")
        if not isinstance(vulns, list):
            malformed.append(f"{dep_name} carries no 'vulns' list, so it was never scanned")
            continue

        raw_ver = dep.get("version")
        dep_ver = raw_ver.strip() if isinstance(raw_ver, str) else ""
        unique_deps.add(f"{dep_name}=={dep_ver}")
        for v in vulns:
            vid = v.get("id", "VULN") if isinstance(v, dict) else "VULN"
            finding_str = f"{dep_name} {dep_ver} ({vid})".strip()
            if finding_str not in seen_findings:
                seen_findings.add(finding_str)
                findings.append(finding_str)

    if skipped or malformed:
        parts = [
            f"{len(unique_deps)} of {len(raw_deps)} entries carried advisory data",
        ]
        if skipped:
            parts.append(f"{len(skipped)} not audited: {_summarize(skipped)}")
        if malformed:
            parts.append(f"{len(malformed)} malformed: {_summarize(malformed)}")
        if findings:
            parts.append(
                f"vulnerabilities already visible in the partial data: {_summarize(findings)}"
            )
        return AuditOutcome(
            UNAVAILABLE,
            None,
            "pip-audit returned an incomplete report (" + "; ".join(parts) + ")",
        )

    if not unique_deps:
        return AuditOutcome(
            UNAVAILABLE,
            None,
            "pip-audit returned no audited packages; no dependency carried advisory data",
        )

    return AuditOutcome(
        REPORT,
        findings,
        f"pip-audit returned advisory data for {len(unique_deps)} dependencies",
        total_dependencies=len(unique_deps),
    )


def classify_pip_audit_output(stdout: str, stderr: str) -> AuditOutcome:
    """Classify `pip-audit --format json` output structurally into report vs unavailable."""
    payload = _extract_json(stdout)

    if isinstance(payload, dict) and "dependencies" in payload:
        raw_deps = payload.get("dependencies")
        if isinstance(raw_deps, list):
            return classify_dependency_entries(raw_deps)
        return AuditOutcome(UNAVAILABLE, None, "audit report 'dependencies' is not a list")

    combined = f"{stdout}\n{stderr}".strip()
    lowered = combined.lower()

    # Only these three shapes are transport symptoms that a retry can clear.
    # Anything else here is pip-audit failing deterministically, so it must not
    # buy extra attempts.
    if "503" in combined or "service unavailable" in lowered or "502" in combined or "504" in combined:
        return AuditOutcome(
            UNAVAILABLE,
            None,
            f"registry error (503/502/504 Service Unavailable): {combined[:400]}",
            retryable=True,
        )
    if "timed out" in lowered or "timeout" in lowered:
        return AuditOutcome(
            UNAVAILABLE,
            None,
            f"PyPI/OSV vulnerability database timeout: {combined[:400]}",
            retryable=True,
        )
    if "connection" in lowered or "econnrefused" in lowered or "resolution failed" in lowered:
        return AuditOutcome(
            UNAVAILABLE,
            None,
            f"connection error contacting PyPI/OSV: {combined[:400]}",
            retryable=True,
        )

    return AuditOutcome(
        UNAVAILABLE, None, f"pip-audit produced no parsable report: {combined[:400]}"
    )


def run_pip_audit(
    cwd: Path = ROOT,
    socket_timeout: float = DEFAULT_SOCKET_TIMEOUT,
    process_timeout: float = DEFAULT_PROCESS_TIMEOUT,
    service: str = DEFAULT_SERVICE,
    installation_path: str = DEFAULT_INSTALLATION_PATH,
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
        installation_path,
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
        # A hung request is the canonical transient failure, so this one is
        # worth another attempt within the bounded budget.
        return AuditOutcome(
            UNAVAILABLE,
            None,
            f"pip-audit process timed out after {process_timeout:.0f}s contacting vulnerability database",
            retryable=True,
        )
    except FileNotFoundError:
        # A missing interpreter is an environment defect, not an outage: every
        # further attempt fails identically.
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
            retryable=outcome.retryable,
        )
    return outcome


def audit_with_retry(
    cwd: Path = ROOT,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF_SECONDS,
    socket_timeout: float = DEFAULT_SOCKET_TIMEOUT,
    process_timeout: float = DEFAULT_PROCESS_TIMEOUT,
    service: str = DEFAULT_SERVICE,
    installation_path: str = DEFAULT_INSTALLATION_PATH,
    sleep=time.sleep,
    runner=subprocess.run,
) -> AuditOutcome:
    """Run pip-audit until it yields a report or the retry budget is spent.

    Only a failure classified as transient earns another attempt. A missing
    executable, unparsable output or an incomplete report is deterministic:
    retrying it cannot change the verdict, and spending the backoff budget on
    it only delays a failure that is already certain.
    """
    total = max(1, attempts)
    outcome = AuditOutcome(UNAVAILABLE, None, "no audit attempt was made")
    for attempt in range(1, total + 1):
        outcome = run_pip_audit(
            cwd=cwd,
            socket_timeout=socket_timeout,
            process_timeout=process_timeout,
            service=service,
            installation_path=installation_path,
            runner=runner,
        )
        if outcome.has_report:
            return outcome
        print(
            f"pip-audit attempt {attempt}/{total} did not return advisory data: "
            f"{outcome.detail}",
            file=sys.stderr,
        )
        if not outcome.retryable:
            print(
                "Failure is not a transient transport/service error; failing closed "
                "without further attempts.",
                file=sys.stderr,
            )
            return outcome
        if attempt < total:
            sleep(backoff * attempt)
    return outcome


def evaluate(
    outcome: AuditOutcome,
    installation_path: str = DEFAULT_INSTALLATION_PATH,
) -> tuple[int, str]:
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
            f"{installation_path}' for details.",
        )

    return (
        EXIT_OK,
        f"PASS: no Python package vulnerabilities found ({outcome.total_dependencies} dependencies audited).",
    )


def require_positive_finite(label: str, value: float) -> float:
    """Reject NaN, infinity and non-positive values for a timeout-like parameter.

    ``float("inf")`` and ``float("nan")`` both parse successfully and both slip
    past a plain ``value <= 0`` check, because every comparison against NaN is
    False and infinity is greater than zero. Either one reaching
    ``subprocess.run(timeout=...)`` removes the execution bound this gate exists
    to enforce, so an unbounded audit would look like a healthy one.
    """
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a positive finite number of seconds, got {value!r}")
    return value


def require_non_negative_finite(label: str, value: float) -> float:
    """Reject NaN, infinity and negative values for a backoff-like parameter."""
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a non-negative finite number of seconds, got {value!r}")
    return value


def _parse_env_float(name: str, default: float, fallback_name: str | None = None) -> float:
    for var in (name, fallback_name) if fallback_name else (name,):
        if var and var in os.environ:
            val = os.environ[var]
            try:
                return float(val)
            except ValueError as exc:
                raise ValueError(f"Invalid float for {var}: {val!r}") from exc
    return default


def _parse_env_int(name: str, default: int) -> int:
    if name in os.environ:
        val = os.environ[name]
        try:
            return int(val)
        except ValueError as exc:
            raise ValueError(f"Invalid integer for {name}: {val!r}") from exc
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
    parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Path to the site-packages directory to audit",
    )
    args = parser.parse_args()

    try:
        socket_timeout = (
            args.socket_timeout
            if args.socket_timeout is not None
            else _parse_env_float(
                "ODP_PIP_AUDIT_SOCKET_TIMEOUT_SECONDS",
                DEFAULT_SOCKET_TIMEOUT,
                "ODP_AUDIT_TIMEOUT",
            )
        )
        process_timeout = (
            args.process_timeout
            if args.process_timeout is not None
            else _parse_env_float(
                "ODP_PIP_AUDIT_PROCESS_TIMEOUT_SECONDS",
                DEFAULT_PROCESS_TIMEOUT,
                "ODP_PIP_AUDIT_TIMEOUT_SECONDS",
            )
        )
        attempts = (
            args.attempts
            if args.attempts is not None
            else _parse_env_int("ODP_PIP_AUDIT_ATTEMPTS", DEFAULT_ATTEMPTS)
        )
        backoff = _parse_env_float("ODP_PIP_AUDIT_BACKOFF_SECONDS", DEFAULT_BACKOFF_SECONDS)
        require_positive_finite("socket timeout", socket_timeout)
        require_positive_finite("process timeout", process_timeout)
        require_non_negative_finite("backoff", backoff)
        if attempts <= 0:
            raise ValueError(f"attempts must be a positive integer, got {attempts!r}")
    except ValueError as exc:
        print(f"[FAIL CLOSED] Invalid timeout or attempt configuration: {exc}", file=sys.stderr)
        return EXIT_AUDIT_UNAVAILABLE

    service = (
        args.service
        if args.service is not None
        else os.environ.get("ODP_PIP_AUDIT_SERVICE", DEFAULT_SERVICE)
    )
    installation_path = (
        args.path
        if args.path is not None
        else os.environ.get("ODP_PIP_AUDIT_PATH", DEFAULT_INSTALLATION_PATH)
    )

    outcome = audit_with_retry(
        attempts=attempts,
        backoff=backoff,
        socket_timeout=socket_timeout,
        process_timeout=process_timeout,
        service=service,
        installation_path=installation_path,
    )
    code, verdict = evaluate(outcome, installation_path=installation_path)
    print(verdict, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
