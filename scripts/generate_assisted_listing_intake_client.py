#!/usr/bin/env python3
"""Generate the assisted-intake artifact and TypeScript schema/path surface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_validate_assisted_listing_intake_openapi import (
    DEFAULT_BASE,
    DEFAULT_OVERLAYS,
    apply_overlay,
    load_yaml,
)
from scripts.openapi.generate_client import render

SCHEMA_DIR = ROOT / "packages/schemas/assisted_listing_intake"
ARTIFACT = SCHEMA_DIR / "openapi-effective.json"
CLIENT = ROOT / "packages/openapi-client/src/generated/assisted_listing_intake.ts"


def build() -> dict:
    document = load_yaml(DEFAULT_BASE)
    for overlay in DEFAULT_OVERLAYS:
        document = apply_overlay(document, overlay)
    return document


def main() -> int:
    document = build()
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    CLIENT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # The shared renderer emits all schemas. Its path filter expects the live
    # app's /api prefix, so normalize only for generation and preserve the
    # canonical /v1 paths in the committed artifact.
    client_document = dict(document)
    client_document["paths"] = {f"/api{path}": item for path, item in document["paths"].items()}
    CLIENT.write_text(render(client_document), encoding="utf-8")
    print(f"Wrote {ARTIFACT.relative_to(ROOT)} and {CLIENT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
