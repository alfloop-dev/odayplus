#!/usr/bin/env python3
"""Validate remote staging configuration and optional live smoke proof.

This checker is intentionally strict: it does not let a release claim remote
staging proof from placeholder workflow output. In a configured environment it
verifies:

- staging URLs are present;
- secret ownership metadata is present;
- `/platform/health` is reachable;
- `/platform/version.release_sha` matches the supplied release PR head SHA.

No secret values are printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / ".odp_data" / "remote-staging-proof" / "remote-staging-proof-report.json"


REQUIRED_ENV_VARS = (
    "ODP_STAGING_DEPLOY_URL",
    "ODP_STAGING_API_URL",
    "ODP_STAGING_SECRET_OWNER",
)


@dataclass
class CheckResult:
    ok: bool
    name: str
    detail: str


def normalize_url(value: str) -> str:
    return value.rstrip("/")


def fetch_identity_token(audience: str) -> str:
    """Obtain an identity token from the current WIF-backed gcloud identity."""
    cmd = ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as exc:
        raise RuntimeError("Failed to invoke gcloud identity-token minting") from exc

    if proc.returncode != 0:
        raise RuntimeError(
            f"gcloud identity-token minting failed with exit status {proc.returncode}"
        )

    token = proc.stdout.strip()
    if not token:
        raise RuntimeError("gcloud identity-token minting returned empty output")
    return token


def get_json(
    url: str, correlation_id: str, timeout: float, *, token: str | None = None
) -> dict[str, Any]:
    headers = {"x-correlation-id": correlation_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-provided URL
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def configured_env() -> dict[str, str]:
    return {name: os.environ.get(name, "").strip() for name in REQUIRED_ENV_VARS}


def run_checks(args: argparse.Namespace) -> tuple[list[CheckResult], dict[str, Any]]:
    env = configured_env()
    correlation_id = args.correlation_id or f"corr-remote-staging-{int(time.time())}"
    checks: list[CheckResult] = []

    for name, value in env.items():
        checks.append(
            CheckResult(
                ok=bool(value),
                name=f"env:{name}",
                detail="configured" if value else "missing",
            )
        )

    if args.expected_sha:
        checks.append(CheckResult(ok=True, name="expected_sha", detail=args.expected_sha))
    else:
        checks.append(CheckResult(ok=False, name="expected_sha", detail="missing --expected-sha"))

    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "correlation_id": correlation_id,
        "expected_sha": args.expected_sha,
        "staging": {
            "deploy_url": env["ODP_STAGING_DEPLOY_URL"] or None,
            "api_url": env["ODP_STAGING_API_URL"] or None,
            "secret_owner": env["ODP_STAGING_SECRET_OWNER"] or None,
            "secret_values_redacted": True,
        },
    }

    if not all(check.ok for check in checks):
        return checks, report

    api_url = normalize_url(env["ODP_STAGING_API_URL"])
    audience = api_url

    token = None
    try:
        token = fetch_identity_token(audience)
        checks.append(
            CheckResult(
                ok=True,
                name="auth:identity_token",
                detail=f"audience={audience}",
            )
        )
    except Exception:
        checks.append(
            CheckResult(
                ok=False,
                name="auth:identity_token",
                detail="failed to mint identity token",
            )
        )
        return checks, report

    try:
        health = get_json(f"{api_url}/platform/health", correlation_id, args.timeout, token=token)
        checks.append(
            CheckResult(
                ok=health.get("status") == "ok" and health.get("correlation_id") == correlation_id,
                name="smoke:/platform/health",
                detail=f"status={health.get('status')} correlation_id={health.get('correlation_id')}",
            )
        )
        report["health"] = health
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        checks.append(CheckResult(ok=False, name="smoke:/platform/health", detail=str(exc)))

    try:
        version = get_json(f"{api_url}/platform/version", correlation_id, args.timeout, token=token)
        actual_sha = str(version.get("release_sha") or "")
        checks.append(
            CheckResult(
                ok=actual_sha == args.expected_sha,
                name="smoke:/platform/version",
                detail=f"release_sha={actual_sha}",
            )
        )
        report["version"] = version
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        checks.append(CheckResult(ok=False, name="smoke:/platform/version", detail=str(exc)))

    return checks, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-sha", required=True, help="Release PR headRefOid expected on staging."
    )
    parser.add_argument(
        "--correlation-id", default="", help="Correlation id for staging smoke requests."
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON report path.")
    args = parser.parse_args()

    checks, report = run_checks(args)
    report["checks"] = [asdict(check) for check in checks]
    report["ok"] = all(check.ok for check in checks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if report["ok"]:
        print(f"Remote staging proof checks passed. report={args.output}")
        return 0

    print("Remote staging proof checks failed:")
    for check in checks:
        if not check.ok:
            print(f"- {check.name}: {check.detail}")
    print(f"report={args.output}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
