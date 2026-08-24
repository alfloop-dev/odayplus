#!/usr/bin/env python3
"""Explicit v1 Gate 0-6 registry -> staged v2 migration.

Migration never fabricates receipts or clears a gate.  A legacy registry has
no manifest identity or deployment stage, so every migrated gate is placed at
the conservative ``candidate-built`` / ``dev`` boundary until new evidence
attests a later stage.  A manifest must be supplied and must bind the same
candidate SHA; otherwise migration stops rather than guessing.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from delivery_toolchain.release.release_manifest import is_exact_sha, validate_manifest

LEGACY_SCHEMA_VERSION = "1.0.0"
TARGET_SCHEMA_VERSION = "2.0.0"


class RegistryMigrationError(ValueError):
    """Raised when legacy state cannot be migrated without losing truth."""


def migrate_registry(
    legacy: dict[str, Any],
    manifest: dict[str, Any],
    *,
    migrated_at: str | None = None,
) -> dict[str, Any]:
    """Return a staged v2 registry or raise a fail-closed migration error."""

    if not isinstance(legacy, dict):
        raise RegistryMigrationError("legacy registry must be a JSON object")
    if legacy.get("schema_version") != LEGACY_SCHEMA_VERSION:
        raise RegistryMigrationError(
            f"migration expects schema_version {LEGACY_SCHEMA_VERSION!r}, "
            f"got {legacy.get('schema_version')!r}"
        )
    if not isinstance(manifest, dict):
        raise RegistryMigrationError("migration requires an immutable manifest object")

    candidate_sha = (legacy.get("release") or {}).get("candidate_sha")
    if not is_exact_sha(candidate_sha):
        raise RegistryMigrationError(
            "legacy release.candidate_sha must be an exact 40-character lowercase git SHA"
        )
    manifest_errors = validate_manifest(
        manifest,
        expected_candidate_sha=candidate_sha,
    )
    if manifest_errors:
        raise RegistryMigrationError("manifest is not valid:\n- " + "\n- ".join(manifest_errors))

    timestamp = migrated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    result = copy.deepcopy(legacy)
    result["schema_version"] = TARGET_SCHEMA_VERSION
    result["migration"] = {
        "from_schema_version": LEGACY_SCHEMA_VERSION,
        "source_registry_id": legacy.get("registry_id"),
        "migrated_at": timestamp,
        "strategy": "legacy-gates-to-staged-registry",
        "re_attestation_required": True,
    }

    release = result.setdefault("release", {})
    release["manifest_ref"] = "docs/evidence/gates/RELEASE_MANIFEST.json"
    release["manifest_digest"] = manifest["manifest_digest"]
    release["stage"] = "candidate-built"
    release["environment"] = "dev"
    release["admission_target"] = "dev"

    gates = result.get("gates")
    if not isinstance(gates, list):
        raise RegistryMigrationError("legacy registry must contain a gates list")
    for gate in gates:
        if not isinstance(gate, dict):
            raise RegistryMigrationError("legacy registry contains a non-object gate")
        gate["stage"] = "candidate-built"
        gate["environment"] = "dev"
        gate["admission_target"] = "dev"

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        legacy = json.loads(args.registry.read_text(encoding="utf-8"))
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        migrated = migrate_registry(legacy, manifest)
        args.output.write_text(
            json.dumps(migrated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError, RegistryMigrationError) as exc:
        print(f"release gate registry migration blocked: {exc}")
        return 1

    print(f"migrated {args.registry} -> {args.output} ({TARGET_SCHEMA_VERSION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
