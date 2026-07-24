#!/usr/bin/env python3
"""Export the live FastAPI schema to the checked-in OpenAPI artifact.

The artifact is the contract of record: the TypeScript client is generated from
it, and CI regenerates it to block undeclared or breaking changes. It is
produced from the real ``create_app()`` -- never hand-written -- so it cannot
drift into describing an API the server does not serve.

Usage::

    python3 scripts/openapi/export_openapi.py            # write the artifact
    python3 scripts/openapi/export_openapi.py --check     # fail if stale
    python3 scripts/openapi/export_openapi.py --stdout    # print, write nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ARTIFACT_PATH = REPO_ROOT / "packages" / "openapi-client" / "openapi.json"


def build_schema() -> dict[str, Any]:
    """Return the OpenAPI schema of the composed app.

    ``ODAY_RELEASE_SHA`` and friends are cleared first: they feed the
    ``/platform/version`` response and would otherwise let a CI run's commit SHA
    leak into the artifact, making the drift check fail on every machine whose
    environment differs.
    """
    for env_key in ("ODAY_RELEASE_SHA", "GITHUB_SHA", "COMMIT_SHA"):
        os.environ.pop(env_key, None)
    # In-memory persistence: exporting a schema must not create a database file
    # or require one to exist.
    # The factory reads ODP_PERSISTENCE (not ..._MODE); the wrong name silently
    # did nothing and a durable env would have written a real SQLite file.
    os.environ["ODP_PERSISTENCE"] = "memory"

    from apps.api.oday_api.main import create_app

    app = create_app(external_provider_validation=lambda: None)
    if app is None:  # pragma: no cover - only when FastAPI is absent
        raise SystemExit("FastAPI is not installed; cannot export the OpenAPI schema.")
    return app.openapi()


def serialize(schema: dict[str, Any]) -> str:
    """Deterministic JSON: byte-identical for the same app on any machine.

    ``sort_keys`` is what makes the drift check meaningful -- without it,
    dict ordering differences would show up as spurious diffs.
    """
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the checked-in artifact differs from the live schema.",
    )
    parser.add_argument("--stdout", action="store_true", help="Print the schema; write nothing.")
    args = parser.parse_args(argv)

    payload = serialize(build_schema())

    if args.stdout:
        sys.stdout.write(payload)
        return 0

    if args.check:
        if not ARTIFACT_PATH.exists():
            print(f"ERROR: {ARTIFACT_PATH.relative_to(REPO_ROOT)} is missing.", file=sys.stderr)
            print("Run: python3 scripts/openapi/export_openapi.py", file=sys.stderr)
            return 1
        current = ARTIFACT_PATH.read_text(encoding="utf-8")
        if current != payload:
            print(
                f"ERROR: {ARTIFACT_PATH.relative_to(REPO_ROOT)} is stale — the API changed "
                "but the artifact was not regenerated.",
                file=sys.stderr,
            )
            print("Run: python3 scripts/openapi/export_openapi.py", file=sys.stderr)
            return 1
        print(f"OK: {ARTIFACT_PATH.relative_to(REPO_ROOT)} matches the live schema.")
        return 0

    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(payload, encoding="utf-8")
    path_count = len(json.loads(payload).get("paths", {}))
    print(f"Wrote {ARTIFACT_PATH.relative_to(REPO_ROOT)} ({path_count} paths).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
