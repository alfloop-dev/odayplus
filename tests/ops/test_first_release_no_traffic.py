"""First-release candidate deploys must not ask Cloud Run for --no-traffic.

ODP-DEPLOY-FIRST-RELEASE-NO-TRAFFIC-FIX-001: Deploy Dev run 36657889962 died
creating ``oday-api`` because gcloud rejects ``--no-traffic`` when the deploy
creates the service. These tests execute the API and Web candidate deploy
segments taken verbatim from ``deploy_cloud_run_waji.sh`` against a fake
gcloud that enforces the same rule, so the new-service branch fails on the
unfixed script and the existing-service argv is pinned exactly.

A new service serves its first revision at once, so a first-release Web
candidate is created without public invocation, the smoke reaches it through
Cloud Run IAM, and allUsers is granted only at promotion. The Web segment here
runs from the candidate deploy through the Web promotion with the smoke stubbed,
so the tests can see what was public at verification time and on failure.
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
            "iam": [],
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
    iam = state[service].setdefault("iam", [])
    if "--allow-unauthenticated" in args and "allUsers" not in iam:
        iam.append("allUsers")
    if "--no-allow-unauthenticated" in args and "allUsers" in iam:
        iam.remove("allUsers")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    raise SystemExit(0)

if args[:3] == ["run", "services", "add-iam-policy-binding"]:
    assert flag("--role") == "roles/run.invoker", args
    iam = state[args[3]].setdefault("iam", [])
    if flag("--member") not in iam:
        iam.append(flag("--member"))
    state_path.write_text(json.dumps(state), encoding="utf-8")
    raise SystemExit(0)

if args[:3] == ["run", "services", "update-traffic"]:
    raise SystemExit(0)

if args[:2] == ["auth", "print-identity-token"]:
    account = flag("--impersonate-service-account")
    print(f"idtoken:{account}:{flag('--audiences')}")
    raise SystemExit(0)

print("unexpected gcloud command", args, file=sys.stderr)
raise SystemExit(2)
"""


FAKE_CURL = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
url = args[-1]
headers = []
for index, argument in enumerate(args):
    if argument == "-H":
        value = args[index + 1]
        if value.startswith("@"):
            headers.extend(Path(value[1:]).read_text(encoding="utf-8").splitlines())
        else:
            headers.append(value)
with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(["curl", url, headers]) + "\n")
state = json.loads(Path(os.environ["FAKE_STATE"]).read_text(encoding="utf-8"))
service = url.split("---", 1)[1].split("-abc.a.run.app", 1)[0]
iam = state[service].get("iam", [])
token = next(
    (h.split("Bearer ", 1)[1] for h in headers if h.startswith("X-Serverless-Authorization:")),
    "",
)
admitted = "allUsers" in iam or (
    os.environ.get("FAKE_IAM_LAGS_FOREVER") != "1"
    and token.startswith("idtoken:")
    and "serviceAccount:" + token.split(":")[1] in iam
    and token.split(":", 2)[2] == state[service]["status"]["url"]
)
sys.stdout.write("307" if admitted else "403")
"""


def _segment(start_marker: str, end_marker: str, text: str | None = None) -> str:
    """Return the deploy script from ``start_marker`` through the line holding ``end_marker``."""
    if text is None:
        text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    end = text.index("\n", end) + 1
    return text[start:end]


API_SEGMENT_MARKERS = (
    'echo "Deploying immutable API candidate',
    'API_SERVICE_AUDIENCE="$(service_snapshot_url',
)
# Web candidate deploy -> smoke -> promotion, including the first-release
# invoker grants on both sides of the smoke.
WEB_SEGMENT_MARKERS = (
    'echo "Deploying immutable Web candidate',
    'promote_service_traffic "${WEB_SERVICE}" "${WEB_REVISION}"',
)

HARNESS = r"""
set -euo pipefail
source "$1"
CLOUD_RUN_NETWORK_ARGS=(--vpc-egress=all-traffic)
API_CANDIDATE_DESCRIPTION="$(mktemp)"
WEB_CANDIDATE_DESCRIPTION="$(mktemp)"
# The smoke is stubbed: it records what the Web service's invoker policy was
# and which candidate invoker token it was handed at verification time.
run_locked_python() {
  python3 -c '
import json, os, sys
state = json.load(open(os.environ["FAKE_STATE"]))
web = state.get(os.environ["WEB_SERVICE"], {})
with open(os.environ["FAKE_LOG"], "a") as handle:
    handle.write(json.dumps(["smoke", sys.argv[1:], web.get("iam", []),
        os.environ.get("ODP_WEB_CANDIDATE_INVOKER_TOKEN", "")]) + "\n")
' "$@"
  return "${FAKE_SMOKE_EXIT:-0}"
}
upsert_scheduler_trigger() { :; }
eval "$2"
eval "$3"
printf 'API_REVISION=%s\nAPI_URL=%s\nAPI_SERVICE_AUDIENCE=%s\nWEB_REVISION=%s\nWEB_URL=%s\n' \
  "${API_REVISION}" "${API_URL}" "${API_SERVICE_AUDIENCE}" "${WEB_REVISION}" "${WEB_URL}"
"""

SMOKE_SA = "smoke@odayplus-dev.iam.gserviceaccount.com"


def _existing_service(service: str) -> dict[str, object]:
    return {
        "metadata": {"name": service},
        "iam": ["allUsers"] if service == WEB_SERVICE else [],
        "status": {
            "url": f"https://{service}-abc.a.run.app",
            "traffic": [
                {"percent": 100, "revisionName": f"{service}-release-previous00"},
            ],
        },
    }


def _unfixed_script() -> str:
    """The deploy script with the traffic/access flags put back to the pre-fix shape."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    for old, new in (
        ('  "${API_TRAFFIC_ARGS[@]}" \\\n', "  --no-traffic \\\n"),
        ('  "${WEB_TRAFFIC_ARGS[@]}" \\\n', "  --no-traffic \\\n"),
        ('  "${WEB_ACCESS_ARGS[@]}" \\\n', "  --allow-unauthenticated \\\n"),
    ):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text


def _run(
    tmp_path: Path,
    state: dict[str, object],
    *,
    script_text: str | None = None,
    **env: str,
) -> tuple[subprocess.CompletedProcess[str], list[list[object]], dict[str, object]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("gcloud", FAKE_GCLOUD), ("curl", FAKE_CURL)):
        tool = bin_dir / name
        tool.write_text(body, encoding="utf-8")
        tool.chmod(0o755)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    log_path = tmp_path / "gcloud.log"
    environment = dict(os.environ)
    environment.pop("GITHUB_ACTIONS", None)
    environment.pop("ODP_WEB_CANDIDATE_INVOKER_TOKEN", None)
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
            "ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT": SMOKE_SA,
            "ODP_OPERATOR_SMOKE_BEARER_TOKEN": "operator-app-token",
            "ODP_DEPLOY_ENV": "dev",
            "SMOKE_REPORT": str(tmp_path / "smoke.json"),
            "SCHEDULER_SCHEDULE_NAME": "oday-scheduler",
            "SCHEDULER_CANDIDATE_JOB": "oday-scheduler-candidate",
            "ODP_SCHEDULER_CRON": "* * * * *",
            "WORKER_SCHEDULE_NAME": "oday-worker",
            "WORKER_CANDIDATE_JOB": "oday-worker-candidate",
            "ODP_WORKER_CRON": "* * * * *",
            "ODP_CANDIDATE_INVOKER_WAIT_SECONDS": "0",
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
            _segment(*API_SEGMENT_MARKERS, text=script_text),
            _segment(*WEB_SEGMENT_MARKERS, text=script_text),
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


def _deploys(calls: list[list[object]]) -> dict[str, list[object]]:
    return {call[2]: call for call in calls if call[:2] == ["run", "deploy"]}


def _index(calls: list[list[object]], predicate) -> int:
    matches = [position for position, call in enumerate(calls) if predicate(call)]
    assert len(matches) == 1, matches
    return matches[0]


def _is_smoke(call: list[object]) -> bool:
    return call[0] == "smoke"


def _is_public_grant(call: list[object]) -> bool:
    return call[:3] == ["run", "services", "add-iam-policy-binding"] and (
        "--member=allUsers" in call
    )


def _is_web_promotion(call: list[object]) -> bool:
    return call[:4] == ["run", "services", "update-traffic", WEB_SERVICE]


def _outputs(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


def _expected_deploy(service: str, *, existing: bool) -> list[str]:
    """The exact argv the pre-fix script sent, adjusted only for a new service."""
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
    public = not api and existing
    argv.append("--allow-unauthenticated" if public else "--no-allow-unauthenticated")
    argv.append("--quiet")
    return argv


def test_first_release_creates_both_services_without_no_traffic(tmp_path: Path) -> None:
    result, calls, state = _run(tmp_path, {})

    assert result.returncode == 0, result.stderr
    deploys = _deploys(calls)
    assert deploys[API_SERVICE] == _expected_deploy(API_SERVICE, existing=False)
    assert deploys[WEB_SERVICE] == _expected_deploy(WEB_SERVICE, existing=False)
    # Both candidates are created private and keep the tag.
    assert "--no-allow-unauthenticated" in deploys[API_SERVICE]
    assert "--no-allow-unauthenticated" in deploys[WEB_SERVICE]
    assert "--allow-unauthenticated" not in deploys[WEB_SERVICE]
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


def test_unfixed_script_fails_the_first_release(tmp_path: Path) -> None:
    result, calls, state = _run(tmp_path, {}, script_text=_unfixed_script())

    assert result.returncode != 0
    assert "--no-traffic not supported when creating a new service" in result.stderr
    assert state == {}
    assert WEB_SERVICE not in _deploys(calls)


def test_first_release_web_is_private_through_verification_and_public_at_promotion(
    tmp_path: Path,
) -> None:
    result, calls, state = _run(tmp_path, {})

    assert result.returncode == 0, result.stderr
    smoke = calls[_index(calls, _is_smoke)]
    web_url = f"https://{TAG}---{WEB_SERVICE}-abc.a.run.app"
    assert ["--web-url", web_url] == smoke[1][smoke[1].index("--web-url") :][:2]
    # At verification time only the smoke identity may invoke the Web service,
    # and the smoke carries an invoker token scoped to that service's URL.
    assert smoke[2] == [f"serviceAccount:{SMOKE_SA}"]
    assert smoke[3] == f"idtoken:{SMOKE_SA}:https://{WEB_SERVICE}-abc.a.run.app"
    # The anonymous probe was refused and the authenticated one admitted.
    probes = [call for call in calls if call[0] == "curl"]
    assert probes[0] == ["curl", f"{web_url}/operator", []]
    assert probes[-1][2] == [f"X-Serverless-Authorization: Bearer {smoke[3]}"]
    # Public invocation is granted once, after the smoke and before Web promotion.
    public_grant = _index(calls, _is_public_grant)
    assert calls[public_grant][3] == WEB_SERVICE
    assert _index(calls, _is_smoke) < public_grant < _index(calls, _is_web_promotion)
    assert "allUsers" in state[WEB_SERVICE]["iam"]
    assert "allUsers" not in state[API_SERVICE]["iam"]


def test_first_release_web_is_never_public_when_verification_fails(tmp_path: Path) -> None:
    result, calls, state = _run(tmp_path, {}, FAKE_SMOKE_EXIT="1")

    assert result.returncode != 0
    assert any(_is_smoke(call) for call in calls)
    assert not any(_is_public_grant(call) for call in calls)
    assert not any(_is_web_promotion(call) for call in calls)
    assert "allUsers" not in state[WEB_SERVICE]["iam"]


def test_first_release_refuses_to_verify_a_web_candidate_that_answers_anonymously(
    tmp_path: Path,
) -> None:
    # A private-looking deploy that nevertheless serves anonymous callers (for
    # example a stray allUsers binding) must stop before the smoke.
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8").replace(
        "WEB_ACCESS_ARGS=(--no-allow-unauthenticated)",
        "WEB_ACCESS_ARGS=(--allow-unauthenticated)",
    )

    result, calls, _state = _run(tmp_path, {}, script_text=script)

    assert result.returncode != 0
    assert "must be private until promotion" in result.stderr
    assert not any(_is_smoke(call) for call in calls)
    assert not any(_is_web_promotion(call) for call in calls)


def test_first_release_fails_when_the_smoke_identity_is_never_admitted(tmp_path: Path) -> None:
    result, calls, state = _run(
        tmp_path, {}, FAKE_IAM_LAGS_FOREVER="1", ODP_CANDIDATE_INVOKER_WAIT_ATTEMPTS="3"
    )

    assert result.returncode != 0
    assert "was not admitted to the private Web candidate (last HTTP 403)" in result.stderr
    # One anonymous probe plus the bounded authenticated attempts.
    assert len([call for call in calls if call[0] == "curl"]) == 4
    assert not any(_is_smoke(call) for call in calls)
    assert "allUsers" not in state[WEB_SERVICE]["iam"]


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
    # No first-release access steps on the blue/green path.
    assert not any(call[:3] == ["run", "services", "add-iam-policy-binding"] for call in calls)
    assert not any(call[:2] == ["auth", "print-identity-token"] for call in calls)
    assert not any(call[0] == "curl" for call in calls)
    assert calls[_index(calls, _is_smoke)][3] == ""


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


def _load_validator():
    import importlib.util
    import sys

    path = ROOT / "product_ops/deployment/validate_cloud_run_live_deployment.py"
    spec = importlib.util.spec_from_file_location("first_release_smoke_validator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("invoker_token", ["", "idtoken-for-web"])
def test_smoke_sends_the_candidate_invoker_token_only_to_web(
    monkeypatch: pytest.MonkeyPatch, invoker_token: str
) -> None:
    validator = _load_validator()
    seen: dict[str, dict[str, str]] = {}

    def fake_json_request(url, *, headers, timeout):
        seen.setdefault("api", {}).update(headers)
        raise OSError("network disabled in this test")

    def fake_request_without_redirect(url, *, headers, timeout):
        seen["web"] = dict(headers)
        return 307, "https://web.example/login?returnTo=%2Foperator"

    monkeypatch.setattr(validator, "_json_request", fake_json_request)
    monkeypatch.setattr(validator, "_request_without_redirect", fake_request_without_redirect)

    _checks, report = validator.smoke_checks(
        api_url="https://api.invalid",
        web_url="https://web.example",
        expected_sha=None,
        bearer_token="operator-app-token",
        operator_role="operator",
        operator_subject="subject",
        operator_tenant="tenant",
        correlation_id="corr-1",
        timeout=0.01,
        web_invoker_token=invoker_token,
    )

    assert "x-serverless-authorization" not in seen["api"]
    # The Web app still sees an anonymous request: no Authorization header.
    assert "authorization" not in seen["web"]
    if invoker_token:
        assert seen["web"]["x-serverless-authorization"] == f"Bearer {invoker_token}"
    else:
        assert "x-serverless-authorization" not in seen["web"]
    assert report["web_invoker_authenticated"] is bool(invoker_token)
    assert invoker_token == "" or invoker_token not in json.dumps(report)
