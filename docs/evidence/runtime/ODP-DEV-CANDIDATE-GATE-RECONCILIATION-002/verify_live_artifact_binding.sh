#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
export PYTHONPATH="${ROOT}:${PYTHONPATH:-}"

python3 - <<'PYEOF'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(".").resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.e2e.check_release_gate_registry import validate_registry  # noqa: E402
from delivery_toolchain.release.release_manifest import (  # noqa: E402
    component_binding_errors,
    compute_manifest_digest,
    initial_release_recovery_errors,
    sources_off_attestation_errors,
    validate_manifest,
    validate_release_admission,
)

EVIDENCE_DIR = ROOT / "docs/evidence/runtime/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002"
MANIFEST_PATH = ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json"
REGISTRY_PATH = ROOT / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"
IMAGES_PATH = EVIDENCE_DIR / "runtime-release-images.json"
ABSENCE_PATH = EVIDENCE_DIR / "initial-release-absence-readback.json"

CANDIDATE_SHA = "04e1572f802a54c2646ba678fe2975226dfbd7c4"
BUILD_RUN_ID = 33942097235
COMPONENT_NAMES = ("api", "web", "worker", "scheduler")

EXPECTED_RAW_SHA256 = {
    "RELEASE_MANIFEST.json": "efe7bed05df8f176b053f448acc0c303d8b81786212a98fc5e56f27031e1f124",
    "runtime-release-images.json": "e177983c92b64b8bd1e9da524010d47712192237adf58c19fa56cbf5550ad23e",
    "initial-release-absence-readback.json": "5e6aba3b690ecbbac394ea2706036bc3319a650a0dfdbad25a61785dca01897f",
}

DOWNLOAD_DIR = Path("/tmp/odp-release-binding-check.YKEX6i")

failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def repo_of(ref: str) -> str:
    return ref.split("@", 1)[0]


def raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_git_file_set_digest(commit: str, file_paths: list[str]) -> str:
    h = hashlib.sha256()
    for rel_path in sorted(file_paths):
        content = subprocess.run(
            ["git", "show", f"{commit}:{rel_path}"],
            cwd=str(ROOT),
            capture_output=True,
            check=True,
        ).stdout
        h.update(rel_path.encode("utf-8"))
        h.update(b"\x00")
        h.update(content)
        h.update(b"\x00")
    return "sha256:" + h.hexdigest()


def compute_git_migration_digest(commit: str = CANDIDATE_SHA) -> str:
    proc = subprocess.run(
        ["git", "ls-tree", f"{commit}:infra/db/migrations"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    sql_files = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[3].endswith(".sql"):
            sql_files.append(f"infra/db/migrations/{parts[3]}")
    return compute_git_file_set_digest(commit, sql_files)


def compute_git_data_contract_digest(commit: str = CANDIDATE_SHA) -> str:
    proc = subprocess.run(
        ["git", "ls-tree", f"{commit}:docs/data"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    data_files = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[1] == "blob":
            data_files.append(f"docs/data/{parts[3]}")
    return compute_git_file_set_digest(commit, data_files)


def compute_git_source_policy_digest(commit: str = CANDIDATE_SHA) -> str:
    policies = [
        "docs/security/license_policy.json",
        "docs/security/license_exemptions.json",
        "docs/security/release_bindings.json",
    ]
    return compute_git_file_set_digest(commit, policies)


def main() -> int:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    images = json.loads(IMAGES_PATH.read_text(encoding="utf-8"))
    release = registry["release"]

    print(f"candidate: {CANDIDATE_SHA}  build run: {BUILD_RUN_ID}\n")

    # 1. Raw byte-exact artifact checks
    check(
        "manifest raw SHA-256 matches build artifact (ID 9962288831)",
        raw_sha256(MANIFEST_PATH) == EXPECTED_RAW_SHA256["RELEASE_MANIFEST.json"],
        raw_sha256(MANIFEST_PATH),
    )
    check(
        "runtime-release-images raw SHA-256 matches build artifact (ID 9962288660)",
        raw_sha256(IMAGES_PATH) == EXPECTED_RAW_SHA256["runtime-release-images.json"],
        raw_sha256(IMAGES_PATH),
    )
    check(
        "initial-release-absence-readback raw SHA-256 matches build artifact (ID 9962288978)",
        raw_sha256(ABSENCE_PATH) == EXPECTED_RAW_SHA256["initial-release-absence-readback.json"],
        raw_sha256(ABSENCE_PATH),
    )

    downloaded_paths = {
        "RELEASE_MANIFEST.json": DOWNLOAD_DIR / f"runtime-release-manifest-{CANDIDATE_SHA}/RELEASE_MANIFEST.json",
        "runtime-release-images.json": DOWNLOAD_DIR / f"runtime-release-images-{CANDIDATE_SHA}/runtime-release-images.json",
        "initial-release-absence-readback.json": DOWNLOAD_DIR / f"initial-release-absence-readback-{CANDIDATE_SHA}/initial-release-absence-readback.json",
    }
    if not DOWNLOAD_DIR.is_dir():
        check("downloaded artifact directory is present for raw-byte comparison", False, str(DOWNLOAD_DIR))
    else:
        for name, downloaded_path in downloaded_paths.items():
            check(f"downloaded artifact file is present: {name}", downloaded_path.is_file(), str(downloaded_path))
        if all(path.is_file() for path in downloaded_paths.values()):
            local_paths = {
                "RELEASE_MANIFEST.json": MANIFEST_PATH,
                "runtime-release-images.json": IMAGES_PATH,
                "initial-release-absence-readback.json": ABSENCE_PATH,
            }
            for name, downloaded_path in downloaded_paths.items():
                check(
                    f"cmp byte-exact: {name} against downloaded artifact",
                    subprocess.run(["cmp", str(local_paths[name]), str(downloaded_path)], capture_output=True).returncode == 0,
                )

    check("manifest schema is valid", not validate_manifest(manifest), str(validate_manifest(manifest)))
    check(
        "manifest_digest equals the SHA-256 of its own canonical payload",
        manifest["manifest_digest"] == compute_manifest_digest(manifest),
        manifest["manifest_digest"],
    )
    check(
        "manifest.candidate_sha is the exact build candidate",
        manifest["candidate_sha"] == CANDIDATE_SHA,
        manifest["candidate_sha"],
    )
    check(
        "manifest was created by the deploy-dev workflow at the candidate SHA",
        manifest["created_by_workflow"].endswith(f"deploy-dev.yml@{CANDIDATE_SHA}"),
        manifest["created_by_workflow"],
    )

    # 2. Candidate tree digests
    for label, recompute in (
        ("migration_digest", compute_git_migration_digest),
        ("data_contract_digest", compute_git_data_contract_digest),
        ("source_policy_digest", compute_git_source_policy_digest),
    ):
        actual = recompute(CANDIDATE_SHA)
        check(f"{label} recomputes from candidate tree {CANDIDATE_SHA[:12]}", manifest[label] == actual, actual)

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

    # sources-off attestation checks (build contract & dev-build environment posture)
    sources_off_errs = sources_off_attestation_errors(
        manifest.get("sources_off_attestation"),
        candidate_sha=manifest["candidate_sha"],
        components=manifest["components"],
        source_policy_digest=manifest["source_policy_digest"],
    )
    check("sources_off_attestation is valid and bound", not sources_off_errs, str(sources_off_errs))

    # initial release recovery checks
    initial_recovery_errs = initial_release_recovery_errors(
        manifest.get("initial_release_recovery"),
        candidate_sha=manifest["candidate_sha"],
        components=manifest["components"],
        environment="dev",
    )
    check("initial_release_recovery is valid and bound", not initial_recovery_errs, str(initial_recovery_errs))

    # 3. Registry checks
    registry_errors = validate_registry(registry, root=ROOT)
    check("registry validates with zero errors under staged admission rules", not registry_errors, str(registry_errors))
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

    # 4. Admission target checks per EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN §6.1
    gate_stages = {g["id"]: (g["stage"], g["environment"], g["admission_target"]) for g in gates}
    expected_gate_stages = {
        "gate-0": ("candidate-built", "dev", "dev"),
        "gate-1": ("candidate-built", "dev", "dev"),
        "gate-2": ("dev-verified", "dev", "staging"),
        "gate-3": ("staging-verified", "staging", "production"),
        "gate-4": ("candidate-built", "dev", "dev"),
        "gate-5": ("staging-verified", "staging", "production"),
        "gate-6": ("staging-verified", "staging", "production"),
    }
    check(
        "gate stages, environments, and admission targets follow rollout plan §6.1",
        gate_stages == expected_gate_stages,
        str(gate_stages),
    )

    # 5. Manifest admission validation
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
PYEOF
