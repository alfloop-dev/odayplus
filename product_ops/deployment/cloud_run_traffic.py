#!/usr/bin/env python3
"""Parse fail-closed Cloud Run service traffic snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_description(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Cloud Run service description must be a JSON object")
    return payload


def is_present(description: dict[str, Any]) -> bool:
    """Return whether a captured service existed before the release.

    Historical snapshots are raw ``gcloud run services describe`` payloads,
    so the absence marker is deliberately opt-in.  This keeps every existing
    rollback receipt valid while giving a first deployment an explicit state.
    """

    return description.get("exists") is not False


def absent_snapshot(service: str) -> dict[str, Any]:
    service = service.strip()
    if not service:
        raise ValueError("absent Cloud Run snapshot requires a service name")
    return {
        "schema_version": 1,
        "kind": "cloud-run-service-traffic-snapshot",
        "exists": False,
        "service": service,
    }


def service_url(description: dict[str, Any]) -> str:
    if not is_present(description):
        return ""
    status = description.get("status")
    if not isinstance(status, dict):
        raise ValueError("Cloud Run service description is missing status")
    url = str(status.get("url") or "").strip()
    if not url.startswith("https://"):
        raise ValueError("Cloud Run service description has no HTTPS service URL")
    return url


def tagged_target(description: dict[str, Any], tag: str) -> tuple[str, str]:
    status = description.get("status")
    traffic = status.get("traffic") if isinstance(status, dict) else None
    if not isinstance(traffic, list):
        raise ValueError("Cloud Run service description is missing status.traffic")

    matches = [
        item for item in traffic if isinstance(item, dict) and str(item.get("tag") or "") == tag
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one Cloud Run traffic target for tag {tag!r}")

    target = matches[0]
    revision = str(target.get("revisionName") or "").strip()
    url = str(target.get("url") or "").strip()
    if not revision:
        raise ValueError(f"tag {tag!r} has no immutable revisionName")
    if not url.startswith("https://"):
        raise ValueError(f"tag {tag!r} has no HTTPS URL")
    return revision, url


def restore_traffic_argument(description: dict[str, Any]) -> str:
    status = description.get("status")
    traffic = status.get("traffic") if isinstance(status, dict) else None
    if not isinstance(traffic, list):
        raise ValueError("Cloud Run service description is missing status.traffic")

    targets: list[tuple[str, int]] = []
    for item in traffic:
        if not isinstance(item, dict):
            continue
        percent_value = item.get("percent", 0)
        if isinstance(percent_value, bool) or not isinstance(percent_value, int):
            raise ValueError("Cloud Run traffic percent must be an integer")
        if percent_value <= 0:
            continue
        revision = str(item.get("revisionName") or "").strip()
        if not revision:
            raise ValueError("active Cloud Run traffic target has no revisionName")
        targets.append((revision, percent_value))

    if not targets:
        raise ValueError("Cloud Run service has no active revision traffic to restore")
    if sum(percent for _, percent in targets) != 100:
        raise ValueError("active Cloud Run revision traffic must total 100 percent")
    if len({revision for revision, _ in targets}) != len(targets):
        raise ValueError("Cloud Run traffic snapshot contains duplicate active revisions")

    return ",".join(f"{revision}={percent}" for revision, percent in targets)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "exists",
            "service-url",
            "tagged-revision",
            "tagged-url",
            "restore-arg",
            "write-absent",
        ),
    )
    parser.add_argument("--description", required=True, type=Path)
    parser.add_argument("--service")
    parser.add_argument("--tag")
    args = parser.parse_args()

    if args.command == "write-absent":
        if not args.service:
            parser.error("--service is required for write-absent")
        args.description.write_text(
            json.dumps(absent_snapshot(args.service), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0

    description = _read_description(args.description)
    if args.command == "exists":
        print("true" if is_present(description) else "false")
        return 0
    if args.command == "service-url":
        print(service_url(description))
        return 0
    if args.command == "restore-arg":
        print(restore_traffic_argument(description))
        return 0
    if not args.tag:
        parser.error(f"--tag is required for {args.command}")

    revision, url = tagged_target(description, args.tag)
    print(revision if args.command == "tagged-revision" else url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
