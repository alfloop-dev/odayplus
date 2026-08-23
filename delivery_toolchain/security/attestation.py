#!/usr/bin/env python3
"""Attestation contract binding release, image, source, lock, SBOM, NOTICE and evidence hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUTPUT_DIR = ROOT / "docs/evidence/completion/ODP-OSS-LICENSE-GATE-002"
ATTESTATION_PATH = OUTPUT_DIR / "license_gate_attestation.json"

CONTAINER_BASE_IMAGES = [
    "python:3.12-slim",
    "node:22-slim",
]


def sha256_file(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_git_sha(root: Path = ROOT) -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=root,
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


def get_cross_repo_digests(root: Path = ROOT) -> dict[str, str]:
    from delivery_toolchain.security.generate_sbom import get_repo_release_digests

    return get_repo_release_digests(root)


def generate_attestation(root: Path = ROOT) -> dict[str, Any]:
    from delivery_toolchain.security.generate_oss_notice import (
        collect_npm,
        collect_python,
        evaluate_policy,
    )

    cross_repo = get_cross_repo_digests(root)
    release_sha = cross_repo["alfloop-dev/odayplus"]

    npm_comps = collect_npm(root / "node_modules")
    py_comps = collect_python()
    all_comps = npm_comps + py_comps
    eval_results = evaluate_policy(
        policy_path=root / "docs/security/license_policy.json",
        components=all_comps,
        exemptions_path=root / "docs/security/license_exemptions.json",
    )

    sbom_path = root / "docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json"
    sbom_sha256 = sha256_file(sbom_path)

    evidence_hashes = {
        "pyproject_toml_sha256": sha256_file(root / "pyproject.toml"),
        "uv_lock_sha256": sha256_file(root / "uv.lock"),
        "package_json_sha256": sha256_file(root / "package.json"),
        "package_lock_json_sha256": sha256_file(root / "package-lock.json"),
        "license_policy_sha256": sha256_file(root / "docs/security/license_policy.json"),
        "license_exemptions_sha256": sha256_file(root / "docs/security/license_exemptions.json"),
        "license_inventory_sha256": sha256_file(
            root / "docs/evidence/oss-legal-policy/LICENSE_INVENTORY_2026-08-08.md"
        ),
        "release_bindings_sha256": sha256_file(root / "docs/security/release_bindings.json"),
        "notice_sha256": sha256_file(root / "NOTICE-THIRD-PARTY.md"),
        "sbom_sha256": sbom_sha256,
    }

    payload = {
        "schema_version": "1.0.0",
        "task_id": "ODP-OSS-LICENSE-GATE-002",
        "policy_name": "ODP-OSS-License-Gate-Policy-v1",
        "policy_version": "1.0.0",
        "status": "proposed",
        "issued_at": datetime.now(UTC).isoformat(),
        "repository": "alfloop-dev/odayplus",
        "release_sha": release_sha,
        "cross_repo_release_digests": cross_repo,
        "container_base_images": CONTAINER_BASE_IMAGES,
        "evidence_hashes": evidence_hashes,
        "gate_summary": {
            "total_components": len(all_comps),
            "allowed_count": len(eval_results["allowed"]),
            "allowed_with_obligations_count": len(eval_results["allowed_with_obligations"]),
            "review_required_count": len(eval_results["review_required"]),
            "denied_count": len(
                [v for v in eval_results["violations"] if "Denied" in v.get("reason", "")]
            ),
            "unknown_count": len(
                [v for v in eval_results["violations"] if "Unknown" in v.get("reason", "")]
            ),
            "gate_decision": "PASS" if eval_results["status"] == "PASS" else "FAIL",
        },
    }

    canonical_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    content_sha256 = hashlib.sha256(canonical_bytes).hexdigest()

    payload["integrity"] = {
        "algorithm": "SHA-256",
        "content_sha256": content_sha256,
    }

    return payload


def verify_attestation(attestation: dict[str, Any], root: Path = ROOT) -> tuple[bool, list[str]]:
    errors: list[str] = []

    if not isinstance(attestation, dict):
        return False, ["Attestation is not a valid JSON object"]

    integrity = attestation.get("integrity", {})
    recorded_sha = integrity.get("content_sha256")
    if not recorded_sha:
        return False, ["Attestation missing integrity.content_sha256"]

    # Verify content hash matches payload without integrity field
    copy_payload = {k: v for k, v in attestation.items() if k != "integrity"}
    canonical_bytes = json.dumps(copy_payload, sort_keys=True).encode("utf-8")
    actual_sha = hashlib.sha256(canonical_bytes).hexdigest()
    if actual_sha != recorded_sha:
        errors.append(f"Integrity check failed: recorded {recorded_sha} != actual {actual_sha}")

    recorded_git_sha = attestation.get("release_sha")
    live_cross_repo = get_cross_repo_digests(root)
    if recorded_git_sha != live_cross_repo.get("alfloop-dev/odayplus"):
        errors.append(
            "Release SHA drift: expected "
            f"{recorded_git_sha}, got {live_cross_repo.get('alfloop-dev/odayplus')}"
        )

    recorded_cross_repo = attestation.get("cross_repo_release_digests", {})
    if recorded_cross_repo != live_cross_repo:
        errors.append(
            f"Cross-repo digests drift: expected {recorded_cross_repo}, got {live_cross_repo}"
        )

    # Verify live file hashes
    evidence = attestation.get("evidence_hashes", {})
    expected_files = {
        "pyproject_toml_sha256": root / "pyproject.toml",
        "uv_lock_sha256": root / "uv.lock",
        "package_json_sha256": root / "package.json",
        "package_lock_json_sha256": root / "package-lock.json",
        "license_policy_sha256": root / "docs/security/license_policy.json",
        "license_exemptions_sha256": root / "docs/security/license_exemptions.json",
        "license_inventory_sha256": root
        / "docs/evidence/oss-legal-policy/LICENSE_INVENTORY_2026-08-08.md",
        "release_bindings_sha256": root / "docs/security/release_bindings.json",
        "notice_sha256": root / "NOTICE-THIRD-PARTY.md",
    }

    for key, path in expected_files.items():
        expected_hash = evidence.get(key)
        if not expected_hash:
            errors.append(f"Missing evidence hash for {key}")
            continue
        actual_file_hash = sha256_file(path)
        if actual_file_hash != expected_hash:
            errors.append(
                f"Hash drift on {path.relative_to(root)}: expected {expected_hash}, got {actual_file_hash}"
            )

    expected_sbom = evidence.get("sbom_sha256")
    if expected_sbom:
        actual_sbom = sha256_file(root / "docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json")
        if actual_sbom != expected_sbom:
            errors.append(f"Hash drift on sbom_sha256: expected {expected_sbom}, got {actual_sbom}")
    else:
        errors.append("Missing evidence hash for sbom_sha256")

    # Verify gate summary
    gate_summary = attestation.get("gate_summary", {})
    if gate_summary.get("denied_count", 0) > 0:
        errors.append(f"Attestation has denied components: {gate_summary['denied_count']}")
    if gate_summary.get("unknown_count", 0) > 0:
        errors.append(f"Attestation has unknown components: {gate_summary['unknown_count']}")
    if gate_summary.get("review_required_count", 0) > 0:
        errors.append(
            f"Attestation has review_required components: {gate_summary['review_required_count']}"
        )
    if gate_summary.get("gate_decision") != "PASS":
        errors.append(f"Attestation gate decision is not PASS: {gate_summary.get('gate_decision')}")

    return len(errors) == 0, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed attestation contract against live repo state",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="custom path to write attestation JSON to",
    )
    args = parser.parse_args()

    target_path = args.output or ATTESTATION_PATH

    if args.check:
        if not target_path.exists():
            print(f"Attestation file missing at {target_path}", file=sys.stderr)
            return 1
        try:
            data = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Failed to read attestation at {target_path}: {e}", file=sys.stderr)
            return 1
        valid, errors = verify_attestation(data, ROOT)
        if not valid:
            print(f"Attestation verification FAILED at {target_path}:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return 1
        print(f"Attestation contract at {target_path.relative_to(ROOT)} verified successfully.")
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    attestation = generate_attestation(ROOT)
    content = json.dumps(attestation, indent=2) + "\n"
    target_path.write_text(content, encoding="utf-8")
    print(f"Generated attestation contract at {target_path.relative_to(ROOT)}")
    print(f"Content Digest: sha256:{attestation['integrity']['content_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
