from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.e2e.test_external_proof_handback_artifact import EXPECTED_SHA, QUEUE, valid_handback

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_external_proof_handback_bundle.py"


def write_bundle(directory: Path) -> list[Path]:
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    paths: list[Path] = []
    for entry in queue["queue"]:
        task_id = entry["task_id"]
        path = directory / f"{task_id}.json"
        path.write_text(json.dumps(valid_handback(task_id), indent=2), encoding="utf-8")
        paths.append(path)
    return paths


def run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_external_proof_handback_bundle_accepts_complete_directory(tmp_path) -> None:
    write_bundle(tmp_path)

    result = run_checker(str(tmp_path), "--expected-sha", EXPECTED_SHA)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "External proof handback bundle checks passed." in result.stdout


def test_external_proof_handback_bundle_rejects_missing_task(tmp_path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "ODP-PV-STAGE-002.json").unlink()

    result = run_checker(str(tmp_path), "--expected-sha", EXPECTED_SHA)

    assert result.returncode == 1
    assert "missing handbacks for tasks" in result.stdout
    assert "ODP-PV-STAGE-002" in result.stdout


def test_external_proof_handback_bundle_rejects_duplicate_task(tmp_path) -> None:
    write_bundle(tmp_path)
    duplicate_dir = tmp_path / "duplicates"
    duplicate_dir.mkdir()
    duplicate = duplicate_dir / "ODP-MAP-STAGE-001-duplicate.json"
    duplicate.write_text(
        json.dumps(valid_handback("ODP-MAP-STAGE-001"), indent=2), encoding="utf-8"
    )

    result = run_checker(str(tmp_path), str(duplicate_dir), "--expected-sha", EXPECTED_SHA)

    assert result.returncode == 1
    assert "duplicate handbacks for ODP-MAP-STAGE-001" in result.stdout


def test_external_proof_handback_bundle_rejects_mixed_release_sha_without_expected_sha(
    tmp_path,
) -> None:
    write_bundle(tmp_path)
    payload = json.loads((tmp_path / "ODP-EXT-PROD-001.json").read_text(encoding="utf-8"))
    payload["release_head_ref_oid"] = "0" * 40
    (tmp_path / "ODP-EXT-PROD-001.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(str(tmp_path))

    assert result.returncode == 1
    assert "multiple release_head_ref_oid values" in result.stdout
