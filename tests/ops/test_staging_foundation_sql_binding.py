"""Exercise the SQL binding supplied by the actual staging workflow."""
import json
from pathlib import Path

import pytest

from product_ops.deployment import staging_lifecycle as lifecycle

PROJECT = "oday-staging-proj"
INSTANCE = "oday-staging-foundation-sql"
CONNECTION = f"{PROJECT}:asia-east1:{INSTANCE}"


def create_args(tmp_path: Path, instance: str) -> list[str]:
    return [
        "create", "--release-id", "odp-sql-binding-001",
        "--candidate-sha", "a" * 40, "--manifest-digest", "sha256:" + "b" * 64,
        "--project-id", PROJECT, "--region", "asia-east1",
        "--cloud-sql-instance", instance,
        "--api-image", "asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "c" * 64,
        "--web-image", "asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "d" * 64,
        "--worker-image", "asia-east1-docker.pkg.dev/proj/repo/worker@sha256:" + "e" * 64,
        "--scheduler-image", "asia-east1-docker.pkg.dev/proj/repo/scheduler@sha256:" + "f" * 64,
        "--kms-key-id", f"projects/{PROJECT}/locations/asia-east1/keyRings/staging/cryptoKeys/release",
        "--deployer-service-account-email", f"deployer@{PROJECT}.iam.gserviceaccount.com",
        "--owner-task-id", "ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001",
        "--state-dir", str(tmp_path / "state"),
        "--tfvars-out", str(tmp_path / "generated.tfvars.json"),
        "--dry-run",
    ]


@pytest.mark.parametrize("instance", [INSTANCE, CONNECTION])
@pytest.mark.parametrize("explicit", ["", CONNECTION])
def test_cli_generates_consistent_sql_inputs(tmp_path: Path, instance: str, explicit: str) -> None:
    args = create_args(tmp_path, instance)
    if explicit:
        args += ["--cloud-sql-connection-name", explicit]
    assert lifecycle.main(args) == 0
    tfvars = json.loads((tmp_path / "generated.tfvars.json").read_text())
    assert tfvars["cloud_sql_instance_name"] == INSTANCE
    assert tfvars["cloud_sql_connection_name"] == CONNECTION


@pytest.mark.parametrize("instance,explicit", [
    (f"other-project:asia-east1:{INSTANCE}", ""),
    (f"{PROJECT}:us-central1:{INSTANCE}", ""),
    (f"{PROJECT}:asia-east1:{CONNECTION}", ""),
    ("", ""),
    ("projects/p/instances/sql", ""),
    (INSTANCE, f"{PROJECT}:asia-east1:other-instance"),
    (CONNECTION, f"other-project:asia-east1:{INSTANCE}"),
])
def test_cli_rejects_mismatch_before_writing(tmp_path: Path, instance: str, explicit: str, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid binding must not invoke Terraform")
    monkeypatch.setattr(lifecycle, "_run_terraform", forbidden)
    args = create_args(tmp_path, instance)
    if explicit:
        args += ["--cloud-sql-connection-name", explicit]
    assert lifecycle.main(args) == 1
    assert not (tmp_path / "generated.tfvars.json").exists()
    assert not (tmp_path / "state").exists()
