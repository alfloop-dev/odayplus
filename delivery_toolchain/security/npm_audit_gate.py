#!/usr/bin/env python3
"""Production npm dependency audit gate.

Background (ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001)
------------------------------------------------------
``npm audit`` resolves advisories through the registry.  ``@npmcli/arborist``
(``lib/audit-report.js``) asks ``POST /-/npm/v1/security/advisories/bulk``
first and only falls back to the deprecated
``POST /-/npm/v1/security/audits/quick`` endpoint when the bulk request
throws.  A ``quick`` response therefore always means the bulk request already
failed, and the body it returns -- including
``Invalid package tree, run npm install to rebuild your package-lock.json`` --
is registry output, not a local verdict on the lockfile.

The previous gate (``npm audit --omit=dev --audit-level=high``) collapsed both
outcomes into "exit non-zero", so a registry hiccup was indistinguishable from
a real vulnerability and a single transient 400/503 reddened every
product-scoped PR.

This gate keeps the same security threshold but separates the two states:

* a parsed audit report decides pass/fail purely on severity counts, so
  vulnerabilities at or above the threshold always fail;
* a registry transport failure is retried a bounded number of times and, if it
  never resolves, fails with a distinct exit code.

A transport failure is never reported as a pass: without a report we have no
vulnerability data, so the gate stays closed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.release.release_receipts import redact

# Ordered least to most severe; the threshold selects this level and above.
SEVERITY_ORDER = ("info", "low", "moderate", "high", "critical")
DEFAULT_THRESHOLD = "high"

EXIT_OK = 0
EXIT_VULNERABLE = 1
EXIT_AUDIT_UNAVAILABLE = 2

# A transient registry error resolves on a retry; an outage does not. Three
# attempts keep the gate responsive while absorbing the observed single-shot
# 400/503 responses.
DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 300.0

REPORT = "report"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AuditOutcome:
    """Result of one ``npm audit`` invocation.

    ``kind`` is ``REPORT`` when the registry returned advisory data (and
    ``counts`` holds the per-severity totals), or ``UNAVAILABLE`` when the
    request never produced a report.
    """

    kind: str
    counts: dict[str, int] | None
    detail: str

    @property
    def has_report(self) -> bool:
        return self.kind == REPORT


def validate_threshold(threshold: str) -> str:
    if threshold not in SEVERITY_ORDER:
        raise ValueError(f"unknown severity threshold: {threshold!r}")
    if SEVERITY_ORDER.index(threshold) > SEVERITY_ORDER.index(DEFAULT_THRESHOLD):
        raise ValueError(
            f"production audit threshold cannot be lowered to {threshold!r}; "
            f"must be '{DEFAULT_THRESHOLD}' or stricter"
        )
    return threshold


def severities_at_or_above(threshold: str) -> tuple[str, ...]:
    threshold = validate_threshold(threshold)
    return SEVERITY_ORDER[SEVERITY_ORDER.index(threshold) :]


def _loads(text: str) -> object | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def classify_audit_output(stdout: str, stderr: str) -> AuditOutcome:
    """Decide whether ``npm audit --json`` produced advisory data.

    The discriminator is structural rather than textual. A real report always
    carries ``auditReportVersion`` plus ``metadata.vulnerabilities``. When the
    registry fails, npm's ``auditError`` helper instead emits an error object
    (``message``/``statusCode``/``body``) and never sets
    ``auditReportVersion``. Matching on that shape keeps registry wording --
    "Invalid package tree", "This endpoint is being retired", a 503 -- out of
    the decision.
    """
    payload = _loads(stdout)

    if isinstance(payload, dict) and "auditReportVersion" in payload:
        metadata = payload.get("metadata")
        raw_counts = metadata.get("vulnerabilities") if isinstance(metadata, dict) else None
        if isinstance(raw_counts, dict):
            counts = {level: int(raw_counts.get(level, 0) or 0) for level in SEVERITY_ORDER}
            return AuditOutcome(REPORT, counts, "npm returned an audit report")
        # A report without severity counts cannot be evaluated; treat it as no
        # data rather than as a pass.
        return AuditOutcome(UNAVAILABLE, None, "audit report is missing metadata.vulnerabilities")

    if isinstance(payload, dict):
        status = payload.get("statusCode")
        message = payload.get("message") or payload.get("body") or ""
        detail = f"registry error (statusCode={status}): {str(message).strip()[:400]}"
        return AuditOutcome(UNAVAILABLE, None, detail)

    combined = f"{stdout}\n{stderr}".strip()
    return AuditOutcome(
        UNAVAILABLE, None, f"npm audit produced no parsable report: {combined[:400]}"
    )


def run_npm_audit(cwd: Path = ROOT, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> AuditOutcome:
    """Run the production audit once and classify its output."""
    try:
        res = subprocess.run(
            ["npm", "audit", "--omit=dev", "--json"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return AuditOutcome(UNAVAILABLE, None, f"npm audit timed out after {timeout:.0f}s")
    except FileNotFoundError:
        return AuditOutcome(UNAVAILABLE, None, "npm executable not found")

    # The return code is deliberately ignored when a report is present: with
    # --json npm exits non-zero merely because findings exist, and the severity
    # counts are the authoritative signal.
    return classify_audit_output(res.stdout, res.stderr)


def audit_with_retry(
    cwd: Path = ROOT,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF_SECONDS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    sleep=time.sleep,
) -> AuditOutcome:
    """Run the audit until it yields a report or the attempts are exhausted."""
    outcome = AuditOutcome(UNAVAILABLE, None, "no audit attempt was made")
    for attempt in range(1, max(1, attempts) + 1):
        outcome = run_npm_audit(cwd=cwd, timeout=timeout)
        if outcome.has_report:
            return outcome
        print(
            f"npm audit attempt {attempt}/{attempts} did not return advisory data: "
            f"{outcome.detail}",
            file=sys.stderr,
        )
        if attempt < attempts:
            sleep(backoff * attempt)
    return outcome


def evaluate(outcome: AuditOutcome, threshold: str = DEFAULT_THRESHOLD) -> tuple[int, str]:
    """Map an outcome onto an exit code and a human-readable verdict."""
    try:
        valid_threshold = validate_threshold(threshold)
    except ValueError as exc:
        return (
            EXIT_AUDIT_UNAVAILABLE,
            f"AUDIT UNAVAILABLE: invalid severity threshold: {exc}",
        )

    if not outcome.has_report:
        return (
            EXIT_AUDIT_UNAVAILABLE,
            "AUDIT UNAVAILABLE: the npm registry never returned advisory data, so this run "
            f"proves nothing about production dependencies. Last error: {redact(outcome.detail)}",
        )

    counts = outcome.counts or {}
    blocking = severities_at_or_above(valid_threshold)
    failing = {level: counts.get(level, 0) for level in blocking if counts.get(level, 0)}
    if failing:
        summary = ", ".join(f"{count} {level}" for level, count in failing.items())
        return (
            EXIT_VULNERABLE,
            f"VULNERABILITIES FOUND at or above '{valid_threshold}' in production dependencies: "
            f"{summary}. Run 'npm audit --omit=dev' for details.",
        )

    total = counts.get("total", sum(counts.get(level, 0) for level in SEVERITY_ORDER))
    return (
        EXIT_OK,
        f"PASS: no production vulnerabilities at or above '{valid_threshold}' "
        f"({total} finding(s) below the threshold).",
    )


def build_audit_receipt(
    outcome: AuditOutcome,
    code: int,
    verdict: str,
    threshold: str = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Construct a redacted, schema-compliant audit receipt dictionary."""
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "receipt_kind": "npm_audit",
        "gate": "npm_audit_gate",
        "secret_values_redacted": True,
        "status": "passed" if code == EXIT_OK else "failed",
        "result": "pass" if code == EXIT_OK else "fail",
        "exit_code": code,
        "threshold": threshold,
        "omit_dev": True,
        "outcome_kind": outcome.kind,
        "counts": outcome.counts,
        "detail": redact(outcome.detail),
        "verdict": redact(verdict),
        "candidate_sha": os.environ.get("ODAY_RELEASE_SHA", ""),
        "recorded_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    return redact(receipt)


def write_audit_receipt(
    path: Path,
    outcome: AuditOutcome,
    code: int,
    verdict: str,
    threshold: str = DEFAULT_THRESHOLD,
) -> None:
    """Write the redacted audit receipt atomically to disk."""
    receipt = build_audit_receipt(outcome, code, verdict, threshold)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(receipt, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(target)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Production npm audit security gate.")
    parser.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help="Path to write the redacted audit receipt JSON.",
    )
    parser.add_argument(
        "--threshold",
        default=os.environ.get("ODP_NPM_AUDIT_LEVEL", DEFAULT_THRESHOLD),
        help=f"Severity threshold (default: {DEFAULT_THRESHOLD}). Cannot be lowered below 'high'.",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=_env_int("ODP_NPM_AUDIT_ATTEMPTS", DEFAULT_ATTEMPTS),
        help=f"Retry attempts (default: {DEFAULT_ATTEMPTS}).",
    )
    parser.add_argument(
        "--backoff",
        type=float,
        default=_env_float("ODP_NPM_AUDIT_BACKOFF_SECONDS", DEFAULT_BACKOFF_SECONDS),
        help=f"Backoff seconds between retries (default: {DEFAULT_BACKOFF_SECONDS}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_env_float("ODP_NPM_AUDIT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        help=f"Timeout seconds per attempt (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    args = parser.parse_args(argv)

    try:
        threshold = validate_threshold(args.threshold)
    except ValueError as exc:
        print(f"Invalid threshold configuration: {exc}", file=sys.stderr)
        if args.receipt:
            outcome = AuditOutcome(UNAVAILABLE, None, f"invalid threshold configuration: {exc}")
            write_audit_receipt(
                args.receipt, outcome, EXIT_AUDIT_UNAVAILABLE, str(exc), args.threshold
            )
        return EXIT_AUDIT_UNAVAILABLE

    outcome = audit_with_retry(
        attempts=args.attempts,
        backoff=args.backoff,
        timeout=args.timeout,
    )
    code, verdict = evaluate(outcome, threshold)
    print(verdict, file=sys.stderr if code else sys.stdout)

    if args.receipt:
        write_audit_receipt(args.receipt, outcome, code, verdict, threshold)

    return code


if __name__ == "__main__":
    raise SystemExit(main())
