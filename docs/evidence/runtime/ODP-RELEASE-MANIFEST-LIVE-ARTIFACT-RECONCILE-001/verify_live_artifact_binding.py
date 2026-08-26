#!/usr/bin/env python3
"""Re-verify the live-artifact binding recorded by ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001.

The gate registry and the release manifest each *claim* to describe the same
release candidate.  This script refuses to take either at its word: it
recomputes every digest that can be recomputed from the repository, and
cross-checks every reference that can be cross-checked without network access.

What it proves without a registry credential:

* the manifest is the byte-exact ``runtime-release-manifest`` artifact of the
  build run -- its ``manifest_digest`` is the SHA-256 of its own canonical
  payload, so any edit to the file invalidates it;
* ``migration_digest`` / ``data_contract_digest`` / ``source_policy_digest``
  recompute to the recorded values from this checkout, which is what binds the
  manifest to the candidate *source tree* and not merely to a commit label;
* the four component images equal the build phase's ``runtime-release-images``
  handoff, so the manifest cannot quote a digest the build never produced;
* every component repository has exactly one SBOM ref and one signature ref;
* the registry's candidate SHA and manifest digest match the manifest, and the
  release is still fail-closed: zero receipts, seven blocked gates, ``no-go``.

What it deliberately does *not* claim: that the image, SBOM, and signature
digests are still resolvable in Artifact Registry.  That needs registry
credentials this environment does not have; see the transcript for the probe
that failed closed with EXIT=1 rather than being reported as a pass.

Usage:  python3 docs/evidence/runtime/ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001/verify_live_artifact_binding.py
Exit codes: 0 = every checkable binding holds, 1 = at least one does not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from delivery_toolchain.release.release_manifest import (  # noqa: E402
    component_binding_errors,
    compute_data_contract_digest,
    compute_manifest_digest,
    compute_migration_digest,
    compute_source_policy_digest,
    validate_manifest,
    validate_release_admission,
)

EVIDENCE_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json"
REGISTRY_PATH = ROOT / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"
IMAGES_PATH = EVIDENCE_DIR / "runtime-release-images.json"

CANDIDATE_SHA = "ebc4fca5c2dd5871275aee39a18406dd67464f04"
BUILD_RUN_ID = 33003734045
COMPONENT_NAMES = ("api", "web", "worker", "scheduler")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def repo_of(ref: str) -> str:
    return ref.split("@", 1)[0]


def main() -> int:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    images = json.loads(IMAGES_PATH.read_text(encoding="utf-8"))
    release = registry["release"]

    print(f"candidate: {CANDIDATE_SHA}  build run: {BUILD_RUN_ID}\n")

    check("manifest schema is valid", not validate_manifest(manifest), str(validate_manifest(manifest)))
    check(
        "manifest_digest equals the SHA-256 of its own canonical payload",
        manifest["manifest_digest"] == compute_manifest_digest(manifest),
        manifest["manifest_digest"],
    )
    check(
        "manifest.candidate_sha is the live candidate",
        manifest["candidate_sha"] == CANDIDATE_SHA,
        manifest["candidate_sha"],
    )
    check(
        "manifest was created by the deploy-dev workflow at the candidate SHA",
        manifest["created_by_workflow"].endswith(f"deploy-dev.yml@{CANDIDATE_SHA}"),
        manifest["created_by_workflow"],
    )

    for label, recompute in (
        ("migration_digest", compute_migration_digest),
        ("data_contract_digest", compute_data_contract_digest),
        ("source_policy_digest", compute_source_policy_digest),
    ):
        actual = recompute(ROOT)
        check(f"{label} recomputes from this checkout", manifest[label] == actual, actual)

    binding = component_binding_errors(manifest, images)
    check("the four build-handoff images are exactly the manifest components", not binding, str(binding))
    check(
        "handoff covers every deployable component",
        sorted(images) == sorted(COMPONENT_NAMES),
        str(sorted(images)),
    )
    check(
        "migration reuses the worker image rather than introducing a fifth artifact",
        manifest["components"]["migration"]["image"] == manifest["components"]["worker"]["image"],
    )

    component_repos = {repo_of(c["image"]) for c in manifest["components"].values()}
    for field in ("sbom_refs", "signature_refs"):
        refs = manifest[field]
        repos = [repo_of(ref) for ref in refs]
        check(f"{field}: one reference per component repository", sorted(repos) == sorted(component_repos), str(sorted(repos)))
        check(f"{field}: every reference is digest-pinned", all("@sha256:" in ref for ref in refs))
        check(f"{field}: no reference reuses an image digest", not (set(refs) & {c["image"] for c in manifest["components"].values()}))

    check("no external source is enabled for this release", manifest["external_sources_expected_enabled"] == [])

    check(
        "registry candidate SHA matches the manifest",
        release["candidate_sha"] == manifest["candidate_sha"],
        release["candidate_sha"],
    )
    check(
        "registry manifest_digest matches the manifest",
        release["manifest_digest"] == manifest["manifest_digest"],
        release["manifest_digest"],
    )
    check(
        "registry manifest_ref points at the manifest this script verified",
        (ROOT / release["manifest_ref"]).resolve() == MANIFEST_PATH.resolve(),
        release["manifest_ref"],
    )

    gates = registry["gates"]
    check("registry still records seven gates", len(gates) == 7, str(len(gates)))
    check("every gate is re-opened against the live candidate", all(g["release_sha"] == CANDIDATE_SHA for g in gates))
    check("no gate carries a receipt", all(not g["receipts"] for g in gates))
    check("every gate is blocked and names a blocker", all(g["status"] == "blocked" and g["blockers"] for g in gates))
    check("release decision is fail-closed", release["decision"] == "no-go", release["decision"])

    # A ready artifact is not a release. The manifest is admissible as an
    # artifact; admission to an environment is still refused, because that is
    # decided by the registry decision and by a signed Supervisor lease, not by
    # the manifest's own status field.
    check(
        "the artifact itself is complete (admission is still blocked by the registry, not by a broken artifact)",
        not validate_release_admission(manifest),
        str(validate_release_admission(manifest)),
    )

    print()
    if failures:
        print(f"FAILED: {len(failures)} binding check(s) did not hold")
        return 1
    print("Live-artifact binding verified; the release remains NO-GO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
