"""Image signing helper must never turn missing tooling into evidence."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "delivery_toolchain/security/sign_images.sh"
IMAGE = "registry.example.invalid/odayplus/api@sha256:" + "a" * 64


def run_without_cosign(tmp_path: Path, command: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # An empty, private PATH makes this test independent of whether the host
    # running the suite happens to have cosign installed.
    env["PATH"] = str(tmp_path)
    return subprocess.run(
        ["/usr/bin/bash", str(SCRIPT), command, IMAGE],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_sign_without_cosign_fails_before_success_output(tmp_path: Path) -> None:
    result = run_without_cosign(tmp_path, "sign")

    assert result.returncode != 0
    assert "refusing to simulate success" in result.stderr
    assert "successfully" not in result.stdout


def test_verify_without_cosign_fails_before_pass_output(tmp_path: Path) -> None:
    result = run_without_cosign(tmp_path, "verify")

    assert result.returncode != 0
    assert "refusing to simulate success" in result.stderr
    assert "PASSED" not in result.stdout
