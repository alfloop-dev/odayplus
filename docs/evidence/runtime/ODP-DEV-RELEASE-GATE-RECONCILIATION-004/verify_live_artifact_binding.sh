#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "用法：$0 <GitHub artifact 解壓目錄> <固定 candidate 的乾淨 worktree>" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
export PYTHONPATH="${ROOT}:${PYTHONPATH:-}"

python3 - "$ROOT" "$1" "$2" <<'PYEOF'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.e2e.check_release_gate_registry import validate_registry  # noqa: E402
from delivery_toolchain.release.release_manifest import (  # noqa: E402
    component_binding_errors,
    compute_manifest_digest,
    compute_migration_digest,
    compute_data_contract_digest,
    compute_source_policy_digest,
    compute_sources_off_egress_contract_digest,
    initial_release_recovery_errors,
    sources_off_attestation_errors,
    validate_manifest,
    validate_release_admission,
)

EVIDENCE_DIR = ROOT / "docs/evidence/runtime/ODP-DEV-RELEASE-GATE-RECONCILIATION-004"
MANIFEST_PATH = ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json"
REGISTRY_PATH = ROOT / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"
IMAGES_PATH = EVIDENCE_DIR / "runtime-release-images.json"
ABSENCE_PATH = EVIDENCE_DIR / "initial-release-absence-readback.json"
ENV_RECEIPT_PATH = EVIDENCE_DIR / "release-environment-receipt.json"
NPM_RECEIPT_PATH = EVIDENCE_DIR / "npm-audit-receipt.json"
PHASE_RECEIPT_PATH = EVIDENCE_DIR / "release-phase-receipt.json"

CANDIDATE_SHA = "39ae43f6fe679f03dd7df459a51835cbd2d54f77"
BUILD_RUN_ID = 35493018607
PRODUCER_RUN_ID = 35492613570
COMPONENT_NAMES = ("api", "web", "worker", "scheduler")

EXPECTED_RAW_SHA256 = {
    "RELEASE_MANIFEST.json": "10acdfc1460de9a9bc72c816dea6f81d0ec88778c2f2e23c727e05f9bd06d5de",
    "runtime-release-images.json": "43310488a7530c38623fbc1f24a4457ccd05d8e2162806939166f7fa18a490bf",
    "initial-release-absence-readback.json": "f117e1af58cb513d849cde9afd98d85fbb8209e15fa61060e1da4567568980a5",
    "release-environment-receipt.json": "8cbb0f5618719d27ef139908dfcd4ae3de2c138fcc2cc607f33a13219718a200",
    "npm-audit-receipt.json": "e6e6c5978360278980842d462d0d60814d51bf6745fb60b1a8b39885ad45c02b",
    "release-phase-receipt.json": "94e7587615a27f2d3cf5b1c92b2731ddb8e5500f2b62c8be0fd0748aeb6e3fb0",
}

DOWNLOAD_DIR = Path(sys.argv[2]).resolve()
CANDIDATE_ROOT = Path(sys.argv[3]).resolve()

def candidate_git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(CANDIDATE_ROOT), *args], text=True
    ).strip()

if not CANDIDATE_ROOT.is_dir():
    raise SystemExit("FAIL: candidate worktree 不存在")
if Path(candidate_git("rev-parse", "--show-toplevel")).resolve() != CANDIDATE_ROOT:
    raise SystemExit("FAIL: candidate 參數不是 git worktree 根目錄")
if candidate_git("rev-parse", "HEAD") != CANDIDATE_SHA:
    raise SystemExit(f"FAIL: candidate worktree HEAD 不符 (expected {CANDIDATE_SHA}, got {candidate_git('rev-parse', 'HEAD')})")

failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def repo_of(ref: str) -> str:
    return ref.split("@", 1)[0]


def raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    images = json.loads(IMAGES_PATH.read_text(encoding="utf-8"))
    release = registry["release"]

    print(f"candidate: {CANDIDATE_SHA}  build run: {BUILD_RUN_ID}\n")

    # 1. Raw byte-exact artifact checks
    check(
        "manifest raw SHA-256 matches build artifact (ID 10600115848)",
        raw_sha256(MANIFEST_PATH) == EXPECTED_RAW_SHA256["RELEASE_MANIFEST.json"],
        raw_sha256(MANIFEST_PATH),
    )
    check(
        "runtime-release-images raw SHA-256 matches build artifact (ID 10600485125)",
        raw_sha256(IMAGES_PATH) == EXPECTED_RAW_SHA256["runtime-release-images.json"],
        raw_sha256(IMAGES_PATH),
    )
    check(
        "initial-release-absence-readback raw SHA-256 matches build artifact (ID 10600440351)",
        raw_sha256(ABSENCE_PATH) == EXPECTED_RAW_SHA256["initial-release-absence-readback.json"],
        raw_sha256(ABSENCE_PATH),
    )
    check(
        "release-environment-receipt raw SHA-256 matches build artifact (ID 10599681970)",
        raw_sha256(ENV_RECEIPT_PATH) == EXPECTED_RAW_SHA256["release-environment-receipt.json"],
        raw_sha256(ENV_RECEIPT_PATH),
    )
    check(
        "release-npm-audit-receipt raw SHA-256 matches build artifact (ID 10600055709)",
        raw_sha256(NPM_RECEIPT_PATH) == EXPECTED_RAW_SHA256["npm-audit-receipt.json"],
        raw_sha256(NPM_RECEIPT_PATH),
    )
    check(
        "release-phase-receipt raw SHA-256 matches build artifact (ID 10600290348)",
        raw_sha256(PHASE_RECEIPT_PATH) == EXPECTED_RAW_SHA256["release-phase-receipt.json"],
        raw_sha256(PHASE_RECEIPT_PATH),
    )

    downloaded_paths = {
        "RELEASE_MANIFEST.json": DOWNLOAD_DIR / f"runtime-release-manifest-{CANDIDATE_SHA}/RELEASE_MANIFEST.json",
        "runtime-release-images.json": DOWNLOAD_DIR / f"runtime-release-images-{CANDIDATE_SHA}/runtime-release-images.json",
        "initial-release-absence-readback.json": DOWNLOAD_DIR / f"initial-release-absence-readback-{CANDIDATE_SHA}/initial-release-absence-readback.json",
        "release-environment-receipt.json": DOWNLOAD_DIR / "release-environment-receipt-dev-build/release-environment-receipt.json",
        "npm-audit-receipt.json": DOWNLOAD_DIR / "release-npm-audit-receipt-dev/npm-audit-receipt.json",
        "release-phase-receipt.json": DOWNLOAD_DIR / "release-phase-receipt-dev-build/release-phase-receipt.json",
    }
    if not DOWNLOAD_DIR.is_dir():
        check("downloaded artifact directory is present for raw-byte comparison", False, str(DOWNLOAD_DIR))
    else:
        for name, downloaded_path in downloaded_paths.items():
            check(f"downloaded artifact file is present: {name}", downloaded_path.is_file(), str(downloaded_path))
        if all(path.is_file() for path in downloaded_paths.values()):
            check("downloaded RELEASE_MANIFEST.json matches repo bytes exactly", raw_sha256(MANIFEST_PATH) == raw_sha256(downloaded_paths["RELEASE_MANIFEST.json"]))
            check("downloaded runtime-release-images.json matches repo bytes exactly", raw_sha256(IMAGES_PATH) == raw_sha256(downloaded_paths["runtime-release-images.json"]))
            check("downloaded initial-release-absence-readback.json matches repo bytes exactly", raw_sha256(ABSENCE_PATH) == raw_sha256(downloaded_paths["initial-release-absence-readback.json"]))
            check("downloaded release-environment-receipt.json matches repo bytes exactly", raw_sha256(ENV_RECEIPT_PATH) == raw_sha256(downloaded_paths["release-environment-receipt.json"]))
            check("downloaded npm-audit-receipt.json matches repo bytes exactly", raw_sha256(NPM_RECEIPT_PATH) == raw_sha256(downloaded_paths["npm-audit-receipt.json"]))
            check("downloaded release-phase-receipt.json matches repo bytes exactly", raw_sha256(PHASE_RECEIPT_PATH) == raw_sha256(downloaded_paths["release-phase-receipt.json"]))

    # 2. Manifest self-verification
    manifest_errors = validate_manifest(
        manifest, expected_candidate_sha=CANDIDATE_SHA, expected_digest=manifest["manifest_digest"]
    )
    check("validate_manifest reports zero errors", len(manifest_errors) == 0, str(manifest_errors))
    check("manifest digest is self-verifying", manifest["manifest_digest"] == compute_manifest_digest(manifest))

    # 3. Candidate-tree content digest recalculation
    recomputed_migration = compute_migration_digest(CANDIDATE_ROOT)
    check("migration_digest recomputed from candidate matches manifest", manifest["migration_digest"] == recomputed_migration, f"{manifest['migration_digest']} == {recomputed_migration}")

    recomputed_contract = compute_data_contract_digest(CANDIDATE_ROOT)
    check("data_contract_digest recomputed from candidate matches manifest", manifest["data_contract_digest"] == recomputed_contract, f"{manifest['data_contract_digest']} == {recomputed_contract}")

    recomputed_source = compute_source_policy_digest(CANDIDATE_ROOT)
    check("source_policy_digest recomputed from candidate matches manifest", manifest["source_policy_digest"] == recomputed_source, f"{manifest['source_policy_digest']} == {recomputed_source}")

    recomputed_egress = compute_sources_off_egress_contract_digest(CANDIDATE_ROOT)
    manifest_egress = manifest["sources_off_attestation"]["egress_evidence"]["contract_digest"]
    check("egress_contract_digest recomputed from candidate matches manifest", manifest_egress == recomputed_egress, f"{manifest_egress} == {recomputed_egress}")

    # 4. Images and component binding
    image_errors = component_binding_errors(manifest, images)
    check("component_binding_errors against runtime-release-images.json is empty", len(image_errors) == 0, str(image_errors))

    # 5. Admission checks
    admission_errors = validate_release_admission(manifest)
    check("validate_release_admission reports zero errors", len(admission_errors) == 0, str(admission_errors))

    # 6. Registry integrity
    registry_errors = validate_registry(registry, ROOT)
    check("validate_registry reports zero errors", len(registry_errors) == 0, str(registry_errors))
    check("registry release.candidate_sha matches candidate", release["candidate_sha"] == CANDIDATE_SHA)
    check("registry release.manifest_digest matches manifest", release["manifest_digest"] == manifest["manifest_digest"])
    check("registry decision is no-go", release["decision"] == "no-go")

    print(f"\nSummary: {len(failures)} failures.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF
