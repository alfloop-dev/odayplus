"""Regression tests for ODP-DEV-EGRESS-READBACK-RECOVERY-001.

Runtime Release run 37049356942 failed twice over:

* the sources-off public-egress probe read the candidate worker job back as
  `vpc_egress=""` although gcloud returned the v1 annotation
  `run.googleapis.com/vpc-access-egress: all-traffic`;
* the EXIT-trap recovery then deleted every first-release candidate yet
  reported "previous-release state could not be determined" and "one or more
  Cloud Run recovery actions failed", because bare `return` inside a trap
  reports the status of the failed deployment step.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TRAFFIC_HELPER = ROOT / "product_ops/deployment/cloud_run_release_traffic.sh"
DEPLOY_SCRIPT = ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh"
ANNOTATION = "run.googleapis.com/vpc-access-egress"


def v1_job(egress: object) -> dict[str, object]:
    """The `gcloud run jobs describe --format=json` shape of a candidate job."""
    return {
        "apiVersion": "run.googleapis.com/v1",
        "kind": "Job",
        "metadata": {
            "name": "oday-worker-r-6140d0ef633c",
            "annotations": {"run.googleapis.com/launch-stage": "GA"},
        },
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "run.googleapis.com/network-interfaces": "[]",
                        ANNOTATION: egress,
                    }
                },
                "spec": {"template": {"spec": {"containers": [{"image": "redacted"}]}}},
            }
        },
    }


def v2_job(egress: object) -> dict[str, object]:
    return {
        "name": "projects/p/locations/asia-east1/jobs/oday-worker-r-6140d0ef633c",
        "template": {"template": {"vpcAccess": {"egress": egress}}},
    }


def read_egress(payload: object | str) -> subprocess.CompletedProcess[str]:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        ["bash", "-c", 'source "$1"; cloud_run_job_vpc_egress', "bash", str(TRAFFIC_HELPER)],
        cwd=ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(v1_job("all-traffic"), id="v1-annotation-from-run-37049356942"),
        pytest.param(v1_job("all"), id="v1-annotation-legacy-all"),
        pytest.param(v2_job("ALL_TRAFFIC"), id="v2-template"),
        pytest.param({**v1_job("all-traffic"), **v2_job("ALL_TRAFFIC")}, id="v1-and-v2-agree"),
    ],
)
def test_explicit_all_traffic_readback_is_canonical(payload: object) -> None:
    result = read_egress(payload)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "ALL_TRAFFIC\n"


def test_private_ranges_only_is_read_back_for_refusal() -> None:
    result = read_egress(v1_job("private-ranges-only"))

    assert result.returncode == 0, result.stderr
    assert result.stdout == "PRIVATE_RANGES_ONLY\n"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param(
            {"spec": {"template": {"metadata": {"annotations": {}}}}},
            "carries no VPC egress setting",
            id="missing",
        ),
        pytest.param(v1_job(""), "not a recognised setting", id="empty-annotation"),
        pytest.param(v1_job(None), "not a recognised setting", id="null-annotation"),
        pytest.param(v1_job("ALL"), "not a recognised setting", id="unknown-value"),
        pytest.param(
            v2_job("VPC_EGRESS_UNSPECIFIED"), "not a recognised setting", id="v2-unspecified"
        ),
        pytest.param(v2_job(["ALL_TRAFFIC"]), "not a recognised setting", id="non-string"),
        pytest.param(
            {**v1_job("all-traffic"), **v2_job("PRIVATE_RANGES_ONLY")},
            "contradictory",
            id="contradictory",
        ),
        pytest.param("not json", "not JSON", id="malformed-json"),
        pytest.param([], "not a JSON object", id="non-object"),
    ],
)
def test_missing_malformed_or_contradictory_readback_fails_closed(
    payload: object | str, message: str
) -> None:
    result = read_egress(payload)

    assert result.returncode != 0
    assert result.stdout == ""
    assert message in result.stderr


def test_probe_refuses_anything_but_all_traffic_before_execution() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    probe = script[script.index("run_public_egress_probe() {") :]
    probe = probe[: probe.index("\n}\n")]

    assert '| cloud_run_job_vpc_egress)"; then' in probe
    assert '"job_readback_invalid" "" "not_run"' in probe
    assert '[ "${actual_egress}" != "ALL_TRAFFIC" ]' in probe
    assert probe.index("cloud_run_job_vpc_egress") < probe.index("gcloud run jobs execute")


FAKE_GCLOUD = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

present = set(filter(None, os.environ.get("FAKE_PRESENT", "").split(",")))
failing = set(filter(None, os.environ.get("FAKE_DELETE_FAILURE", "").split(",")))
args = sys.argv[1:]

if args[:2] == ["run", "services"] or args[:2] == ["run", "jobs"]:
    if args[2] == "list":
        for argument in args:
            if argument.startswith("--filter=metadata.name="):
                name = argument.split("=", 2)[2]
                if name in present:
                    print(name)
        raise SystemExit(0)
    if args[2] == "delete":
        with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as handle:
            handle.write(f"delete:{args[3]}\\n")
        if args[3] in failing:
            print("DELETE_FAILED", file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(0)

print("unexpected gcloud command", args, file=sys.stderr)
raise SystemExit(2)
"""

# The recovery half of deploy_cloud_run_waji.sh's handle_deployment_exit, run
# from a real EXIT trap entered after a failed step (`exit 1`).
TRAP_HARNESS = r"""
source "$1"
handle_exit() {
  local status=$?
  local rollback_status=0
  local recovery_mode="unknown"
  trap - EXIT
  set +e
  if recovery_mode="$(release_recovery_mode "${API_SNAPSHOT}" "${WEB_SNAPSHOT}")"; then
    :
  else
    echo "Error: previous-release state could not be determined; no recovery mode is claimed." >&2
  fi
  echo "mode=${recovery_mode}"
  rollback_release_traffic api "${API_SNAPSHOT}" web "${WEB_SNAPSHOT}" || rollback_status=$?
  if [ "${recovery_mode}" = "initial-release-cleanup" ]; then
    cleanup_initial_release_candidates migration-r worker-r scheduler-r || rollback_status=$?
  fi
  echo "rollback_status=${rollback_status}"
  exit "${status}"
}
trap handle_exit EXIT
set -e
false
"""


def run_trap_recovery(tmp_path: Path, **state: str) -> subprocess.CompletedProcess[str]:
    fake = tmp_path / "gcloud"
    fake.write_text(FAKE_GCLOUD, encoding="utf-8")
    fake.chmod(0o755)
    traffic_helper = ROOT / "product_ops/deployment/cloud_run_traffic.py"
    snapshots = {}
    for service in ("api", "web"):
        snapshot = tmp_path / f"{service}.json"
        subprocess.run(
            [
                "python3",
                str(traffic_helper),
                "write-absent",
                f"--description={snapshot}",
                f"--service={service}",
            ],
            check=True,
        )
        snapshots[service] = snapshot
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{tmp_path}:{environment.get('PATH', '')}",
            "GCP_PROJECT": "odayplus",
            "GCP_REGION": "asia-east1",
            "FAKE_LOG": str(tmp_path / "gcloud.log"),
            "API_SNAPSHOT": str(snapshots["api"]),
            "WEB_SNAPSHOT": str(snapshots["web"]),
            **state,
        }
    )
    return subprocess.run(
        ["bash", "-c", TRAP_HARNESS, "bash", str(TRAFFIC_HELPER)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def read_log(tmp_path: Path) -> list[str]:
    log = tmp_path / "gcloud.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_completed_initial_release_cleanup_in_exit_trap_is_reported_as_success(
    tmp_path: Path,
) -> None:
    result = run_trap_recovery(tmp_path, FAKE_PRESENT="api,migration-r,worker-r,scheduler-r")

    # The original deployment failure still decides the exit status.
    assert result.returncode == 1
    assert "mode=initial-release-cleanup" in result.stdout
    assert "rollback_status=0" in result.stdout
    assert "previous-release state could not be determined" not in result.stderr
    assert read_log(tmp_path) == [
        "delete:api",
        "delete:migration-r",
        "delete:worker-r",
        "delete:scheduler-r",
    ]


@pytest.mark.parametrize("failing", ["api", "worker-r"])
def test_genuinely_failed_cleanup_in_exit_trap_is_still_reported(
    tmp_path: Path, failing: str
) -> None:
    result = run_trap_recovery(
        tmp_path,
        FAKE_PRESENT="api,migration-r,worker-r,scheduler-r",
        FAKE_DELETE_FAILURE=failing,
    )

    assert result.returncode == 1
    assert "mode=initial-release-cleanup" in result.stdout
    assert "rollback_status=0" not in result.stdout
    assert "DELETE_FAILED" in result.stderr
    # A failed delete does not stop the remaining candidates from being cleaned.
    assert len(read_log(tmp_path)) == 4
