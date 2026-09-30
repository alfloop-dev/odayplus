"""First-release candidate deploys must not ask Cloud Run for --no-traffic.

ODP-DEPLOY-FIRST-RELEASE-NO-TRAFFIC-FIX-001: Deploy Dev run 36657889962 died
creating ``oday-api`` because gcloud rejects ``--no-traffic`` when the deploy
creates the service. These tests execute the API and Web candidate deploy
segments taken verbatim from ``deploy_cloud_run_waji.sh`` against a fake
gcloud that enforces the same rule, so the new-service branch fails on the
unfixed script and the existing-service argv is pinned exactly.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh"
TRAFFIC_HELPER = ROOT / "product_ops/deployment/cloud_run_release_traffic.sh"

API_SERVICE = "oday-api"
WEB_SERVICE = "oday-web"
TAG = "candidate-ee06d1d8aaaaaaaa"
SUFFIX = "release-ee06d1d8aaaa"

FAKE_GCLOUD = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_STATE"])
log_path = Path(os.environ["FAKE_LOG"])
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")


def flag(name):
    for argument in args:
        if argument.startswith(name + "="):
            return argument.split("=", 1)[1]
    return ""


if args[:3] == ["run", "services", "describe"]:
    service = args[3]
    if os.environ.get("FAKE_DESCRIBE_ERROR") == "1":
        print("ERROR: (gcloud.run.services.describe) PERMISSION_DENIED: "
              "Permission 'run.services.get' denied", file=sys.stderr)
        raise SystemExit(1)
    if service not in state:
        print(f"ERROR: (gcloud.run.services.describe) Cannot find service [{service}]",
              file=sys.stderr)
        raise SystemExit(1)
    if flag("--format") == "value(metadata.name)":
        print(service)
    else:
        print(json.dumps(state[service]))
    raise SystemExit(0)

if args[:2] == ["run", "deploy"]:
    service = args[2]
    revision = f"{service}-{flag('--revision-suffix')}"
    tag = flag("--tag")
    tagged = {
        "revisionName": revision,
        "tag": tag,
        "url": f"https://{tag}---{service}-abc.a.run.app",
    }
    if service not in state:
        if "--no-traffic" in args:
            print("ERROR: (gcloud.run.deploy) --no-traffic not supported when "
                  "creating a new service.", file=sys.stderr)
            raise SystemExit(1)
        state[service] = {
            "metadata": {"name": service},
            "status": {
                "url": f"https://{service}-abc.a.run.app",
                "traffic": [
                    {"latestRevision": True, "percent": 100, "revisionName": revision},
                    tagged,
                ],
            },
        }
    else:
        traffic = state[service]["status"]["traffic"]
        if "--no-traffic" not in args:
            traffic = [{"latestRevision": True, "percent": 100, "revisionName": revision}]
        state[service]["status"]["traffic"] = traffic + [tagged]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    raise SystemExit(0)

print("unexpected gcloud command", args, file=sys.stderr)
raise SystemExit(2)
"""


def _segment(start_marker: str, end_marker: str) -> str:
    """Return the deploy script from ``start_marker`` through the line holding ``end_marker``."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    end = text.index("\n", end) + 1
    return text[start:end]


API_SEGMENT_MARKERS = (
    'echo "Deploying immutable API candidate',
    'API_SERVICE_AUDIENCE="$(service_snapshot_url',
)
WEB_SEGMENT_MARKERS = (
    'echo "Deploying immutable Web candidate',
    'WEB_URL="$(tagged_revision_url',
)

HARNESS = r"""
set -euo pipefail
source "$1"
CLOUD_RUN_NETWORK_ARGS=(--vpc-egress=all-traffic)
API_CANDIDATE_DESCRIPTION="$(mktemp)"
WEB_CANDIDATE_DESCRIPTION="$(mktemp)"
eval "$2"
eval "$3"
printf 'API_REVISION=%s\nAPI_URL=%s\nAPI_SERVICE_AUDIENCE=%s\nWEB_REVISION=%s\nWEB_URL=%s\n' \
  "${API_REVISION}" "${API_URL}" "${API_SERVICE_AUDIENCE}" "${WEB_REVISION}" "${WEB_URL}"
"""


def _existing_service(service: str) -> dict[str, object]:
    return {
        "metadata": {"name": service},
        "status": {
            "url": f"https://{service}-abc.a.run.app",
            "traffic": [
                {"percent": 100, "revisionName": f"{service}-release-previous00"},
            ],
        },
    }


def _run(
    tmp_path: Path, state: dict[str, object], **env: str
) -> tuple[subprocess.CompletedProcess[str], list[list[str]], dict[str, object]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(FAKE_GCLOUD, encoding="utf-8")
    gcloud.chmod(0o755)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    log_path = tmp_path / "gcloud.log"
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment.get('PATH', '')}",
            "FAKE_STATE": str(state_path),
            "FAKE_LOG": str(log_path),
            "GCP_PROJECT": "odayplus-dev",
            "GCP_REGION": "asia-east1",
            "API_SERVICE": API_SERVICE,
            "WEB_SERVICE": WEB_SERVICE,
            "API_IMAGE": "registry/api@sha256:" + "a" * 64,
            "WEB_IMAGE": "registry/web@sha256:" + "b" * 64,
            "ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT": "runtime@odayplus-dev.iam.gserviceaccount.com",
            "GCP_CLOUD_SQL_INSTANCE": "odayplus-dev:asia-east1:oday",
            "API_ENV_FILE": "/dev/null",
            "WEB_ENV_FILE": "/dev/null",
            "API_SECRET_BINDINGS": "ODAY_DATABASE_URL=db-url:latest",
            "WEB_SECRET_BINDINGS": "ODAY_DATABASE_URL=db-url:latest",
            "ODAY_RELEASE_SHA": "ee06d1d8" + "a" * 32,
            "REVISION_SUFFIX": SUFFIX,
            "API_REVISION_TAG": TAG,
            "WEB_REVISION_TAG": TAG,
            **env,
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            HARNESS,
            "bash",
            str(TRAFFIC_HELPER),
            _segment(*API_SEGMENT_MARKERS),
            _segment(*WEB_SEGMENT_MARKERS),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = []
    if log_path.exists():
        calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    return result, calls, json.loads(state_path.read_text(encoding="utf-8"))


def _deploys(calls: list[list[str]]) -> dict[str, list[str]]:
    return {call[2]: call for call in calls if call[:2] == ["run", "deploy"]}


def _outputs(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


def _expected_deploy(service: str, *, existing: bool) -> list[str]:
    """The exact argv the pre-fix script sent, minus --no-traffic for a new service."""
    api = service == API_SERVICE
    argv = [
        "run",
        "deploy",
        service,
        f"--image=registry/{'api' if api else 'web'}@sha256:" + ("a" if api else "b") * 64,
        "--region=asia-east1",
        "--project=odayplus-dev",
        "--platform=managed",
        f"--port={8000 if api else 3000}",
        "--service-account=runtime@odayplus-dev.iam.gserviceaccount.com",
        "--add-cloudsql-instances=odayplus-dev:asia-east1:oday",
        "--env-vars-file=/dev/null",
        "--set-secrets=ODAY_DATABASE_URL=db-url:latest",
        "--labels=oday-release-sha=ee06d1d8" + "a" * 32 + ",oday-data-binding=live",
        f"--revision-suffix={SUFFIX}",
        "--vpc-egress=all-traffic",
        f"--tag={TAG}",
    ]
    if existing:
        argv.append("--no-traffic")
    argv.append("--no-allow-unauthenticated" if api else "--allow-unauthenticated")
    argv.append("--quiet")
    return argv


def test_first_release_creates_both_services_without_no_traffic(tmp_path: Path) -> None:
    result, calls, state = _run(tmp_path, {})

    assert result.returncode == 0, result.stderr
    deploys = _deploys(calls)
    assert deploys[API_SERVICE] == _expected_deploy(API_SERVICE, existing=False)
    assert deploys[WEB_SERVICE] == _expected_deploy(WEB_SERVICE, existing=False)
    # The API candidate stays private before verification; both keep the tag.
    assert "--no-allow-unauthenticated" in deploys[API_SERVICE]
    assert f"--tag={TAG}" in deploys[WEB_SERVICE]
    assert set(state) == {API_SERVICE, WEB_SERVICE}

    outputs = _outputs(result.stdout)
    assert outputs == {
        "API_REVISION": f"{API_SERVICE}-{SUFFIX}",
        "API_URL": f"https://{TAG}---{API_SERVICE}-abc.a.run.app",
        "API_SERVICE_AUDIENCE": f"https://{API_SERVICE}-abc.a.run.app",
        "WEB_REVISION": f"{WEB_SERVICE}-{SUFFIX}",
        "WEB_URL": f"https://{TAG}---{WEB_SERVICE}-abc.a.run.app",
    }


def test_existing_services_keep_the_exact_no_traffic_blue_green_argv(tmp_path: Path) -> None:
    state = {
        API_SERVICE: _existing_service(API_SERVICE),
        WEB_SERVICE: _existing_service(WEB_SERVICE),
    }

    result, calls, after = _run(tmp_path, state)

    assert result.returncode == 0, result.stderr
    deploys = _deploys(calls)
    assert deploys[API_SERVICE] == _expected_deploy(API_SERVICE, existing=True)
    assert deploys[WEB_SERVICE] == _expected_deploy(WEB_SERVICE, existing=True)
    for service in (API_SERVICE, WEB_SERVICE):
        serving = [item for item in after[service]["status"]["traffic"] if item.get("percent")]
        assert serving == [{"percent": 100, "revisionName": f"{service}-release-previous00"}]
    outputs = _outputs(result.stdout)
    assert outputs["API_REVISION"] == f"{API_SERVICE}-{SUFFIX}"
    assert outputs["WEB_URL"] == f"https://{TAG}---{WEB_SERVICE}-abc.a.run.app"


def test_describe_failure_other_than_not_found_fails_closed_before_deploy(
    tmp_path: Path,
) -> None:
    result, calls, state = _run(tmp_path, {}, FAKE_DESCRIBE_ERROR="1")

    assert result.returncode != 0
    assert _deploys(calls) == {}
    assert state == {}
    assert "PERMISSION_DENIED" in result.stderr
    assert f"cannot determine whether Cloud Run service '{API_SERVICE}' exists" in result.stderr


def test_presence_is_read_from_describe_not_from_recovery_inputs(tmp_path: Path) -> None:
    # An operator-supplied recovery mode must not decide the traffic flag: the
    # API exists here, so it keeps --no-traffic even though the input claims a
    # first release; the Web service really is new.
    state = {API_SERVICE: _existing_service(API_SERVICE)}

    result, calls, _after = _run(tmp_path, state, INITIAL_RELEASE_RECOVERY="true")

    assert result.returncode == 0, result.stderr
    deploys = _deploys(calls)
    assert deploys[API_SERVICE] == _expected_deploy(API_SERVICE, existing=True)
    assert deploys[WEB_SERVICE] == _expected_deploy(WEB_SERVICE, existing=False)
    describes = [call for call in calls if call[:3] == ["run", "services", "describe"]]
    assert ["run", "services", "describe", API_SERVICE] == describes[0][:4]
    assert "--format=value(metadata.name)" in describes[0]


@pytest.mark.parametrize(
    ("stdout_name", "expected"),
    [(API_SERVICE, "present"), ("oday-api-other", None)],
)
def test_presence_helper_rejects_a_mismatched_describe_name(
    tmp_path: Path, stdout_name: str, expected: str | None
) -> None:
    gcloud = tmp_path / "gcloud"
    gcloud.write_text(f"#!/usr/bin/env bash\necho {stdout_name}\n", encoding="utf-8")
    gcloud.chmod(0o755)
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{tmp_path}:{environment.get('PATH', '')}",
            "GCP_PROJECT": "odayplus-dev",
            "GCP_REGION": "asia-east1",
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; cloud_run_service_presence "$2"',
            "bash",
            str(TRAFFIC_HELPER),
            API_SERVICE,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if expected is None:
        assert result.returncode != 0
        assert result.stdout == ""
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected
