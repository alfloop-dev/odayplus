"""Focused failure-path tests for first-release candidate cleanup."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TRAFFIC_HELPER = ROOT / "product_ops/deployment/cloud_run_release_traffic.sh"

FAKE_GCLOUD = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

present = set(filter(None, os.environ.get("FAKE_PRESENT", "").split(",")))
log_path = Path(os.environ["FAKE_LOG"])
args = sys.argv[1:]

if args[:3] == ["run", "jobs", "list"]:
    if os.environ.get("FAKE_LIST_FAILURE") == "1":
        print("PERMISSION_DENIED", file=sys.stderr)
        raise SystemExit(1)
    name = ""
    for argument in args:
        if argument.startswith("--filter=metadata.name="):
            name = argument.split("=", 2)[2]
    if name in present:
        print(name)
    if name in set(filter(None, os.environ.get("FAKE_AMBIGUOUS", "").split(","))):
        print(name + "-other")
    raise SystemExit(0)

if args[:3] == ["run", "jobs", "delete"]:
    job = args[3]
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"delete:{job}\\n")
    if job in set(filter(None, os.environ.get("FAKE_DELETE_FAILURE", "").split(","))):
        print("DELETE_FAILED", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)

print("unexpected gcloud command", args, file=sys.stderr)
raise SystemExit(2)
"""


@pytest.fixture
def fake_gcloud(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "fake-gcloud"
    executable.write_text(FAKE_GCLOUD, encoding="utf-8")
    executable.chmod(0o755)
    return executable, tmp_path / "gcloud.log"


def run_cleanup(
    fake_gcloud: tuple[Path, Path],
    jobs: tuple[str, ...],
    **state: str,
) -> subprocess.CompletedProcess[str]:
    executable, log_path = fake_gcloud
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{executable.parent}:{environment.get('PATH', '')}",
            "GCP_PROJECT": "odayplus",
            "GCP_REGION": "asia-east1",
            "FAKE_GCLOUD": str(executable),
            "FAKE_LOG": str(log_path),
            **state,
        }
    )
    # The helper calls the `gcloud` executable by name. Put the fake at that
    # name while keeping the fixture path visible in the test output.
    named = executable.parent / "gcloud"
    named.symlink_to(executable)
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; cleanup_initial_release_candidates "${@:2}"',
            "bash",
            str(TRAFFIC_HELPER),
            *jobs,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def read_log(fake_gcloud: tuple[Path, Path]) -> list[str]:
    _executable, log_path = fake_gcloud
    if not log_path.exists():
        return []
    return log_path.read_text(encoding="utf-8").splitlines()


def test_initial_recovery_deletes_all_sha_suffixed_candidate_jobs(
    fake_gcloud: tuple[Path, Path],
) -> None:
    jobs = (
        "oday-migration-r-aaaaaaaaaaaa",
        "oday-worker-r-aaaaaaaaaaaa",
        "oday-scheduler-r-aaaaaaaaaaaa",
    )

    result = run_cleanup(fake_gcloud, jobs, FAKE_PRESENT=",".join(jobs))

    assert result.returncode == 0, result.stderr
    assert read_log(fake_gcloud) == [f"delete:{job}" for job in jobs]


def test_initial_recovery_does_not_delete_a_candidate_that_is_already_absent(
    fake_gcloud: tuple[Path, Path],
) -> None:
    jobs = (
        "oday-migration-r-bbbbbbbbbbbb",
        "oday-worker-r-bbbbbbbbbbbb",
        "oday-scheduler-r-bbbbbbbbbbbb",
    )

    result = run_cleanup(fake_gcloud, jobs)

    assert result.returncode == 0, result.stderr
    assert read_log(fake_gcloud) == []


def test_initial_recovery_fails_closed_when_candidate_readback_fails(
    fake_gcloud: tuple[Path, Path],
) -> None:
    jobs = (
        "oday-migration-r-cccccccccccc",
        "oday-worker-r-cccccccccccc",
        "oday-scheduler-r-cccccccccccc",
    )

    result = run_cleanup(fake_gcloud, jobs, FAKE_LIST_FAILURE="1")

    assert result.returncode != 0
    assert read_log(fake_gcloud) == []
    assert "cannot read back candidate Cloud Run Job" in result.stderr


def test_initial_recovery_fails_closed_when_candidate_delete_fails(
    fake_gcloud: tuple[Path, Path],
) -> None:
    jobs = (
        "oday-migration-r-dddddddddddd",
        "oday-worker-r-dddddddddddd",
        "oday-scheduler-r-dddddddddddd",
    )

    result = run_cleanup(
        fake_gcloud,
        jobs,
        FAKE_PRESENT=",".join(jobs),
        FAKE_DELETE_FAILURE="oday-worker-r-dddddddddddd",
    )

    assert result.returncode != 0
    assert read_log(fake_gcloud) == [f"delete:{job}" for job in jobs]
    assert "DELETE_FAILED" in result.stderr


