#!/usr/bin/env python3
"""Fail-closed admission check for the supervisor-owned runtime release.

This is intentionally small and local.  It is the only check that may admit a
deployment workflow: it validates the supervisor's lease and exact SHA against
the committed Gate 0-6 registry without invoking other checkers, GitHub, or
cloud CLIs.  The broader evidence check remains a CI/reporting tool and is not
part of the deployment control path.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TASK_RE = re.compile(r"^ODP-[A-Z0-9-]+$")
LEASE_RE = re.compile(r"^release-[A-Za-z0-9._:-]+$")
PASSING_STATUSES = frozenset({"passed", "pass", "success"})


def admission_errors(
    registry: dict,
    *,
    release_sha: str,
    environment: str,
    task_id: str,
    lease: str,
) -> list[str]:
    errors: list[str] = []
    if environment not in {"dev", "staging"}:
        errors.append("environment must be dev or staging")
    if not SHA_RE.fullmatch(release_sha):
        errors.append("release_sha must be a 40-character lowercase git SHA")
    if not TASK_RE.fullmatch(task_id):
        errors.append("task_id must match ODP-[A-Z0-9-]+")
    if not LEASE_RE.fullmatch(lease):
        errors.append("release_lease must match release-[A-Za-z0-9._:-]+")

    release = registry.get("release")
    if not isinstance(release, dict):
        return errors + ["registry.release is missing"]
    if release.get("decision") != "go":
        errors.append(f"registry decision is {release.get('decision')!r}, expected 'go'")
    if release.get("candidate_sha") != release_sha:
        errors.append("registry candidate_sha does not match release_sha")

    gates = registry.get("gates")
    if not isinstance(gates, list) or len(gates) != 7:
        errors.append("registry must contain exactly seven gates")
        return errors
    for gate in gates:
        if not isinstance(gate, dict):
            errors.append("registry contains a non-object gate")
            continue
        gate_id = str(gate.get("id") or "unknown")
        if gate.get("status") not in PASSING_STATUSES:
            errors.append(f"{gate_id} status is {gate.get('status')!r}")
        if gate.get("release_sha") != release_sha:
            errors.append(f"{gate_id} release_sha does not match release_sha")
        if not isinstance(gate.get("receipts"), list) or not gate["receipts"]:
            errors.append(f"{gate_id} has no release receipt")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True, dest="release_sha")
    parser.add_argument("--environment", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--lease", required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args(argv)

    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"runtime admission blocked: cannot read registry ({exc})", file=sys.stderr)
        return 1
    errors = admission_errors(
        registry,
        release_sha=args.release_sha,
        environment=args.environment,
        task_id=args.task_id,
        lease=args.lease,
    )
    if errors:
        print("runtime admission blocked:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        f"runtime admission passed: environment={args.environment} "
        f"sha={args.release_sha} task={args.task_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
