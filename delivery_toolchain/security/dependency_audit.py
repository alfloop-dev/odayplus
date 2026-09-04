#!/usr/bin/env python3
"""Dependency security audit runner for npm and Python dependencies.

Enforces fail-closed timeout boundaries and traceable diagnostics for CI and
local developer workflows. Registry timeouts, 503s, and connection failures
are explicitly failed closed without swallow or bypass.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_NPM_TIMEOUT_SECONDS = 60.0
DEFAULT_PIP_TIMEOUT_SECONDS = 60.0


def resolve_timeouts(
    cli_npm_timeout: float | None = None,
    cli_pip_timeout: float | None = None,
) -> tuple[float, float]:
    """Resolve npm and pip audit timeouts from CLI or environment variables."""
    global_timeout_env = os.environ.get("ODP_AUDIT_TIMEOUT")
    default_timeout = float(global_timeout_env) if global_timeout_env else None

    npm_env = os.environ.get("ODP_NPM_AUDIT_TIMEOUT")
    if cli_npm_timeout is not None:
        npm_timeout = cli_npm_timeout
    elif npm_env is not None:
        npm_timeout = float(npm_env)
    elif default_timeout is not None:
        npm_timeout = default_timeout
    else:
        npm_timeout = DEFAULT_NPM_TIMEOUT_SECONDS

    pip_env = os.environ.get("ODP_PIP_AUDIT_TIMEOUT")
    if cli_pip_timeout is not None:
        pip_timeout = cli_pip_timeout
    elif pip_env is not None:
        pip_timeout = float(pip_env)
    elif default_timeout is not None:
        pip_timeout = default_timeout
    else:
        pip_timeout = DEFAULT_PIP_TIMEOUT_SECONDS

    if npm_timeout <= 0:
        raise ValueError(f"npm audit timeout must be positive, got {npm_timeout}")
    if pip_timeout <= 0:
        raise ValueError(f"pip-audit timeout must be positive, got {pip_timeout}")

    return npm_timeout, pip_timeout


def run_npm_audit(
    root: Path = ROOT,
    timeout: float = DEFAULT_NPM_TIMEOUT_SECONDS,
    audit_level: str = "high",
    omit_dev: bool = True,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    """Run npm audit with bounded timeout and fail-closed error handling."""
    lockfile = root / "package-lock.json"
    if not lockfile.exists():
        print("Skipping npm audit: package-lock.json is not present.")
        return 0

    cmd = ["npm", "audit"]
    if omit_dev:
        cmd.append("--omit=dev")
    if audit_level:
        cmd.append(f"--audit-level={audit_level}")

    print(f"Running npm audit (timeout: {timeout:.1f}s, level: {audit_level})...")
    try:
        res = runner(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[FAIL CLOSED] npm audit timed out after {timeout:.1f}s contacting npm registry.",
            file=sys.stderr,
        )
        print(
            "[FAIL CLOSED] Failure source: npm registry response timeout (fail-closed boundary enforced).",
            file=sys.stderr,
        )
        return 1
    except FileNotFoundError:
        print("[FAIL CLOSED] npm executable not found in PATH.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[FAIL CLOSED] npm audit process failed to execute: {exc}", file=sys.stderr)
        return 1

    if res.stdout:
        print(res.stdout)
    if res.stderr:
        print(res.stderr, file=sys.stderr)

    if res.returncode != 0:
        combined = f"{res.stdout}\n{res.stderr}"
        print(
            f"[FAIL CLOSED] npm audit failed with exit code {res.returncode}.",
            file=sys.stderr,
        )
        if any(
            err in combined
            for err in [
                "503",
                "502",
                "504",
                "ECONNREFUSED",
                "ETIMEDOUT",
                "ENOTFOUND",
                "Service Unavailable",
            ]
        ):
            print(
                "[FAIL CLOSED] Failure source: npm registry network/service error (e.g. 503 Service Unavailable). Refusing to ignore registry error.",
                file=sys.stderr,
            )
        else:
            print(
                "[FAIL CLOSED] Failure source: npm vulnerabilities found at or above configured severity threshold.",
                file=sys.stderr,
            )
        return res.returncode if res.returncode != 0 else 1

    print("npm audit passed: no vulnerabilities found at or above high threshold.")
    return 0


def run_pip_audit(
    root: Path = ROOT,
    timeout: float = DEFAULT_PIP_TIMEOUT_SECONDS,
    local: bool = True,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    """Run pip-audit with bounded timeout and fail-closed error handling."""
    cmd = ["uv", "run", "--with", "pip-audit", "pip-audit"]
    if local:
        cmd.append("--local")

    print(f"Running pip-audit (timeout: {timeout:.1f}s, local: {local})...")
    try:
        res = runner(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[FAIL CLOSED] pip-audit timed out after {timeout:.1f}s contacting vulnerability database.",
            file=sys.stderr,
        )
        print(
            "[FAIL CLOSED] Failure source: PyPI/OSV vulnerability database timeout (fail-closed boundary enforced).",
            file=sys.stderr,
        )
        return 1
    except FileNotFoundError:
        print("[FAIL CLOSED] uv executable not found in PATH.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[FAIL CLOSED] pip-audit process failed to execute: {exc}", file=sys.stderr)
        return 1

    if res.stdout:
        print(res.stdout)
    if res.stderr:
        print(res.stderr, file=sys.stderr)

    if res.returncode != 0:
        print(
            f"[FAIL CLOSED] pip-audit failed with exit code {res.returncode}.",
            file=sys.stderr,
        )
        print(
            "[FAIL CLOSED] Failure source: Python package vulnerabilities found or pip-audit execution failure.",
            file=sys.stderr,
        )
        return res.returncode if res.returncode != 0 else 1

    print("pip-audit passed: 0 known vulnerabilities found.")
    return 0


def run_dependency_audit(
    root: Path = ROOT,
    npm_timeout: float = DEFAULT_NPM_TIMEOUT_SECONDS,
    pip_timeout: float = DEFAULT_PIP_TIMEOUT_SECONDS,
    skip_npm: bool = False,
    skip_pip: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    """Execute all configured dependency audits and fail closed on any error."""
    failures: list[str] = []

    if not skip_npm:
        npm_code = run_npm_audit(root=root, timeout=npm_timeout, runner=runner)
        if npm_code != 0:
            failures.append(f"npm audit (exit {npm_code})")

    if not skip_pip:
        pip_code = run_pip_audit(root=root, timeout=pip_timeout, runner=runner)
        if pip_code != 0:
            failures.append(f"pip-audit (exit {pip_code})")

    if failures:
        print(
            f"\n[FAIL CLOSED] Dependency audit gate failed for: {', '.join(failures)}",
            file=sys.stderr,
        )
        return 1

    print("\n[PASS] All dependency audit gates passed successfully.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run dependency security audits with bounded timeouts and fail-closed semantics."
    )
    parser.add_argument(
        "--npm-timeout",
        type=float,
        default=None,
        help=f"Timeout in seconds for npm audit (default: {DEFAULT_NPM_TIMEOUT_SECONDS}s or ODP_NPM_AUDIT_TIMEOUT)",
    )
    parser.add_argument(
        "--pip-timeout",
        type=float,
        default=None,
        help=f"Timeout in seconds for pip-audit (default: {DEFAULT_PIP_TIMEOUT_SECONDS}s or ODP_PIP_AUDIT_TIMEOUT)",
    )
    parser.add_argument(
        "--npm-only",
        action="store_true",
        help="Run only npm audit",
    )
    parser.add_argument(
        "--pip-only",
        action="store_true",
        help="Run only pip-audit",
    )

    args = parser.parse_args(argv)

    try:
        npm_timeout, pip_timeout = resolve_timeouts(
            cli_npm_timeout=args.npm_timeout,
            cli_pip_timeout=args.pip_timeout,
        )
    except ValueError as exc:
        print(f"[FAIL CLOSED] Invalid timeout configuration: {exc}", file=sys.stderr)
        return 1

    return run_dependency_audit(
        root=ROOT,
        npm_timeout=npm_timeout,
        pip_timeout=pip_timeout,
        skip_npm=args.pip_only,
        skip_pip=args.npm_only,
    )


if __name__ == "__main__":
    sys.exit(main())
