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

ODP-DEV-PRIVATE-API-TRANSPORT-AUTH-001: the API service is private on every
release. Its smoke and runtime identities get a run.invoker binding scoped to
the API service, and the probes carry a transport token minted for the API's
stable URL. The fake curl only admits a token whose audience is the stable URL
of the service it is calling.
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
if "---" in url:
    service = url.split("---", 1)[1].split("-abc.a.run.app", 1)[0]
else:
    service = url.split("://", 1)[1].split("-abc.a.run.app", 1)[0]
iam = state[service].get("iam", [])
token = next(
    (h.split("Bearer ", 1)[1] for h in headers if h.startswith("X-Serverless-Authorization:")),
    "",
)

admitted = False
if "allUsers" in iam:
    if os.environ.get("FAKE_PUBLIC_IAM_LAGS_FOREVER") == "1":
        admitted = False
    elif os.environ.get("FAKE_PUBLIC_IAM_LAGS_UNTIL_ATTEMPT"):
        threshold = int(os.environ["FAKE_PUBLIC_IAM_LAGS_UNTIL_ATTEMPT"])
        log_lines = [
            json.loads(line)
            for line in Path(os.environ["FAKE_LOG"]).read_text(encoding="utf-8").splitlines()
        ]
        anon_count = sum(
            1
            for entry in log_lines
            if entry[0] == "curl"
            and entry[1] == url
            and not any(h.startswith("X-Serverless-Authorization:") for h in entry[2])
        )
        admitted = anon_count >= threshold
    else:
        admitted = True
elif (
    # FAKE_IAM_LAGS_FOREVER names the service whose invoker binding never converges.
    os.environ.get("FAKE_IAM_LAGS_FOREVER") != service
    and token.startswith("idtoken:")
    and "serviceAccount:" + token.split(":")[1] in iam
    and token.split(":", 2)[2] == state[service]["status"]["url"]
):
    admitted = True

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
    'prepare_private_api_transport "${API_SERVICE}" "${API_SERVICE_AUDIENCE}"',
)
# Web candidate deploy -> smoke -> promotion -> public invoker wait -> live E2E gate.
WEB_SEGMENT_MARKERS = (
    'echo "Deploying immutable Web candidate',
    '--output "${LIVE_E2E_REPORT}"',
)

HARNESS = r"""
set -euo pipefail
source "$1"
CLOUD_RUN_NETWORK_ARGS=(--vpc-egress=all-traffic)
API_CANDIDATE_DESCRIPTION="$(mktemp)"
WEB_CANDIDATE_DESCRIPTION="$(mktemp)"
# The smoke and live E2E gate are stubbed: they record what the Web service's
# invoker policy was and what arguments were passed.
run_locked_python() {
  python3 -c '
import json, os, sys
state = json.load(open(os.environ["FAKE_STATE"]))
web = state.get(os.environ["WEB_SERVICE"], {})
target = "smoke" if any("smoke" in arg for arg in sys.argv[1:]) else "live_e2e"
with open(os.environ["FAKE_LOG"], "a") as handle:
    handle.write(json.dumps([target, sys.argv[1:], web.get("iam", []),
        os.environ.get("ODP_WEB_CANDIDATE_INVOKER_TOKEN", ""),
        os.environ.get("ODP_API_INVOKER_TOKEN", "")]) + "\n")
' "$@"
  if [[ "$*" == *"smoke"* ]]; then
    return "${FAKE_SMOKE_EXIT:-0}"
  fi
  return "${FAKE_LIVE_E2E_EXIT:-0}"
}
upsert_scheduler_trigger() { :; }
eval "$2"
eval "$3"
printf 'API_REVISION=%s\nAPI_URL=%s\nAPI_SERVICE_AUDIENCE=%s\nWEB_REVISION=%s\nWEB_URL=%s\nLIVE_E2E_API_URL=%s\nLIVE_E2E_WEB_URL=%s\n' \
  "${API_REVISION}" "${API_URL}" "${API_SERVICE_AUDIENCE}" "${WEB_REVISION}" "${WEB_URL}" \
  "${LIVE_E2E_API_URL:-}" "${LIVE_E2E_WEB_URL:-}"
"""

SMOKE_SA = "smoke@odayplus-dev.iam.gserviceaccount.com"
RUNTIME_SA = "runtime@odayplus-dev.iam.gserviceaccount.com"
API_STABLE_URL = f"https://{API_SERVICE}-abc.a.run.app"
API_TRANSPORT_TOKEN = f"idtoken:{SMOKE_SA}:{API_STABLE_URL}"
API_INVOKERS = [f"serviceAccount:{SMOKE_SA}", f"serviceAccount:{RUNTIME_SA}"]


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
    environment.pop("ODP_API_INVOKER_TOKEN", None)
    environment.pop("ODP_WEB_BASE_URL", None)
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
            "ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT": RUNTIME_SA,
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
            "LIVE_E2E_REPORT": str(tmp_path / "live_e2e.json"),
            "SCHEDULER_SCHEDULE_NAME": "oday-scheduler",
            "SCHEDULER_CANDIDATE_JOB": "oday-scheduler-candidate",
            "ODP_SCHEDULER_CRON": "* * * * *",
            "WORKER_SCHEDULE_NAME": "oday-worker",
            "WORKER_CANDIDATE_JOB": "oday-worker-candidate",
            "ODP_WORKER_CRON": "* * * * *",
            "ODP_CANDIDATE_INVOKER_WAIT_SECONDS": "0",
            "ODP_PUBLIC_INVOKER_WAIT_SECONDS": "0",
            "ODP_API_INVOKER_WAIT_SECONDS": "0",
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


def _is_live_e2e(call: list[object]) -> bool:
    return call[0] == "live_e2e"


def _is_public_grant(call: list[object]) -> bool:
    return call[:3] == ["run", "services", "add-iam-policy-binding"] and (
        "--member=allUsers" in call
    )


def _is_web_promotion(call: list[object]) -> bool:
    return call[:4] == ["run", "services", "update-traffic", WEB_SERVICE]


def _flag(call: list[object], name: str) -> str:
    return next(
        (str(arg).split("=", 1)[1] for arg in call if str(arg).startswith(name + "=")), ""
    )


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
        "LIVE_E2E_API_URL": f"https://{API_SERVICE}-abc.a.run.app",
        "LIVE_E2E_WEB_URL": f"https://{WEB_SERVICE}-abc.a.run.app",
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
    live_web_url = f"https://{WEB_SERVICE}-abc.a.run.app"
    assert ["--web-url", web_url] == smoke[1][smoke[1].index("--web-url") :][:2]
    # At verification time only the smoke identity may invoke the Web service,
    # and the smoke carries an invoker token scoped to that service's URL.
    assert smoke[2] == [f"serviceAccount:{SMOKE_SA}"]
    assert smoke[3] == f"idtoken:{SMOKE_SA}:https://{WEB_SERVICE}-abc.a.run.app"
    # The candidate anonymous probe was refused and the authenticated one admitted.
    probes = [call for call in calls if call[0] == "curl" and WEB_SERVICE in call[1]]
    assert probes[0] == ["curl", f"{web_url}/operator", []]
    assert probes[1] == [
        "curl",
        f"{web_url}/operator",
        [f"X-Serverless-Authorization: Bearer {smoke[3]}"],
    ]
    # The public wait probed the promoted service anonymously after allUsers was granted.
    assert probes[2] == ["curl", f"{live_web_url}/operator", []]
    # Public invocation is granted once, after the smoke and before Web promotion.
    public_grant = _index(calls, _is_public_grant)
    assert calls[public_grant][3] == WEB_SERVICE
    assert _index(calls, _is_smoke) < public_grant < _index(calls, _is_web_promotion)
    assert _index(calls, _is_web_promotion) < _index(calls, _is_live_e2e)
    assert "allUsers" in state[WEB_SERVICE]["iam"]
    assert "allUsers" not in state[API_SERVICE]["iam"]
    live_e2e = calls[_index(calls, _is_live_e2e)]
    assert ["--web-url", live_web_url] == live_e2e[1][live_e2e[1].index("--web-url") :][:2]


def test_first_release_waits_for_public_iam_propagation_before_live_e2e(tmp_path: Path) -> None:
    result, calls, state = _run(
        tmp_path, {}, FAKE_PUBLIC_IAM_LAGS_UNTIL_ATTEMPT="3", ODP_PUBLIC_INVOKER_WAIT_ATTEMPTS="5"
    )

    assert result.returncode == 0, result.stderr
    live_web_url = f"https://{WEB_SERVICE}-abc.a.run.app"
    public_probes = [
        call for call in calls if call[0] == "curl" and call[1] == f"{live_web_url}/operator"
    ]
    assert len(public_probes) == 3
    assert any(_is_live_e2e(call) for call in calls)
    assert "allUsers" in state[WEB_SERVICE]["iam"]


def test_first_release_fails_when_public_iam_never_converges(tmp_path: Path) -> None:
    result, calls, state = _run(
        tmp_path, {}, FAKE_PUBLIC_IAM_LAGS_FOREVER="1", ODP_PUBLIC_INVOKER_WAIT_ATTEMPTS="3"
    )

    assert result.returncode != 0
    assert (
        "public invocation was not admitted on the promoted Web service (last HTTP 403)"
        in result.stderr
    )
    live_web_url = f"https://{WEB_SERVICE}-abc.a.run.app"
    public_probes = [
        call for call in calls if call[0] == "curl" and call[1] == f"{live_web_url}/operator"
    ]
    assert len(public_probes) == 3
    assert not any(_is_live_e2e(call) for call in calls)


def test_first_release_web_is_never_public_when_verification_fails(tmp_path: Path) -> None:
    result, calls, state = _run(tmp_path, {}, FAKE_SMOKE_EXIT="1")

    assert result.returncode != 0
    assert any(_is_smoke(call) for call in calls)
    assert not any(_is_public_grant(call) for call in calls)
    assert not any(_is_web_promotion(call) for call in calls)
    assert not any(_is_live_e2e(call) for call in calls)
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
    assert not any(_is_live_e2e(call) for call in calls)


def test_first_release_fails_when_the_smoke_identity_is_never_admitted(tmp_path: Path) -> None:
    result, calls, state = _run(
        tmp_path, {}, FAKE_IAM_LAGS_FOREVER=WEB_SERVICE, ODP_CANDIDATE_INVOKER_WAIT_ATTEMPTS="3"
    )

    assert result.returncode != 0
    assert "was not admitted to the private Web candidate (last HTTP 403)" in result.stderr
    # One anonymous probe plus the bounded authenticated attempts.
    assert len([call for call in calls if call[0] == "curl" and WEB_SERVICE in call[1]]) == 4
    assert not any(_is_smoke(call) for call in calls)
    assert not any(_is_live_e2e(call) for call in calls)
    assert "allUsers" not in state[WEB_SERVICE]["iam"]


def test_live_e2e_runs_the_web_journey_at_the_canonical_base_url(tmp_path: Path) -> None:
    """The Web CSRF boundary trusts ODP_WEB_BASE_URL, not every Cloud Run alias."""

    state = {
        API_SERVICE: _existing_service(API_SERVICE),
        WEB_SERVICE: _existing_service(WEB_SERVICE),
    }
    canonical = "https://oday-web-767864276141.asia-east1.run.app"

    result, _, _ = _run(tmp_path, state, ODP_WEB_BASE_URL=canonical)

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["LIVE_E2E_WEB_URL"] == canonical
    assert outputs["LIVE_E2E_API_URL"] == f"https://{API_SERVICE}-abc.a.run.app"


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
    assert outputs["LIVE_E2E_WEB_URL"] == f"https://{WEB_SERVICE}-abc.a.run.app"
    # No first-release Web access steps on the blue/green path: the only
    # bindings are the API's service-scoped invokers, and the only token
    # audience is the API's stable URL.
    grants = [call for call in calls if call[:3] == ["run", "services", "add-iam-policy-binding"]]
    assert {call[3] for call in grants} == {API_SERVICE}
    assert after[API_SERVICE]["iam"] == API_INVOKERS
    assert after[WEB_SERVICE]["iam"] == ["allUsers"]
    audiences = {
        _flag(call, "--audiences")
        for call in calls
        if call[:2] == ["auth", "print-identity-token"]
    }
    assert audiences == {API_STABLE_URL}
    assert not any(call[0] == "curl" and WEB_SERVICE in call[1] for call in calls)
    smoke = calls[_index(calls, _is_smoke)]
    assert smoke[3] == ""
    assert smoke[4] == API_TRANSPORT_TOKEN
    assert calls[_index(calls, _is_live_e2e)][4] == API_TRANSPORT_TOKEN


def _is_api_grant(call: list[object]) -> bool:
    return call[:4] == ["run", "services", "add-iam-policy-binding", API_SERVICE]


@pytest.mark.parametrize("existing", [False, True])
def test_private_api_gets_service_scoped_invokers_and_a_stable_audience_token(
    tmp_path: Path, existing: bool
) -> None:
    state = (
        {API_SERVICE: _existing_service(API_SERVICE), WEB_SERVICE: _existing_service(WEB_SERVICE)}
        if existing
        else {}
    )

    result, calls, after = _run(tmp_path, state)

    assert result.returncode == 0, result.stderr
    # The API stays private: only the smoke and runtime identities, bound on
    # the API service itself, never a public or project-wide principal.
    assert after[API_SERVICE]["iam"] == API_INVOKERS
    api_grants = [call for call in calls if _is_api_grant(call)]
    assert [_flag(call, "--member") for call in api_grants] == [
        f"serviceAccount:{SMOKE_SA}",
        f"serviceAccount:{RUNTIME_SA}",
    ]
    assert all(_flag(call, "--role") == "roles/run.invoker" for call in api_grants)
    members = [_flag(call, "--member") for call in calls if "add-iam-policy-binding" in call]
    assert "allAuthenticatedUsers" not in members
    assert not any(
        member == "allUsers" and call[3] == API_SERVICE
        for call in calls
        if "add-iam-policy-binding" in call
        for member in [_flag(call, "--member")]
    )
    # The Web BFF runtime identity can invoke the API before the Web serves.
    web_deploy = _index(calls, lambda call: call[:3] == ["run", "deploy", WEB_SERVICE])
    assert max(calls.index(call) for call in api_grants) < web_deploy
    # Smoke goes to the revision tag URL, yet the transport audience is the
    # stable service URL, and it is never the Web token.
    smoke = calls[_index(calls, _is_smoke)]
    tagged_api = f"https://{TAG}---{API_SERVICE}-abc.a.run.app"
    assert ["--api-url", tagged_api] == smoke[1][smoke[1].index("--api-url") :][:2]
    assert smoke[4] == API_TRANSPORT_TOKEN
    assert smoke[3] != smoke[4]
    live_e2e = calls[_index(calls, _is_live_e2e)]
    assert live_e2e[4] == API_TRANSPORT_TOKEN
    # The API refused an anonymous caller before the token was admitted.
    api_probes = [call for call in calls if call[0] == "curl" and API_SERVICE in call[1]]
    assert api_probes[0] == ["curl", f"{API_STABLE_URL}/platform/version", []]
    assert api_probes[1] == [
        "curl",
        f"{API_STABLE_URL}/platform/version",
        [f"X-Serverless-Authorization: Bearer {API_TRANSPORT_TOKEN}"],
    ]
    # Every API token is minted for the stable API URL from the smoke identity.
    for call in calls:
        if call[:2] == ["auth", "print-identity-token"] and API_SERVICE in str(call):
            assert _flag(call, "--audiences") == API_STABLE_URL
            assert _flag(call, "--impersonate-service-account") == SMOKE_SA
            assert "--include-email" in call
    assert API_TRANSPORT_TOKEN not in result.stdout + result.stderr


def test_api_that_answers_anonymously_stops_the_release_before_web(tmp_path: Path) -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    api_block_flag = '  "${API_TRAFFIC_ARGS[@]}" \\\n  --no-allow-unauthenticated \\\n'
    assert script.count(api_block_flag) == 1
    script = script.replace(
        api_block_flag, '  "${API_TRAFFIC_ARGS[@]}" \\\n  --allow-unauthenticated \\\n'
    )

    result, calls, _state = _run(tmp_path, {}, script_text=script)

    assert result.returncode != 0
    assert "API service answered an anonymous request with HTTP 307" in result.stderr
    assert WEB_SERVICE not in _deploys(calls)
    assert not any(_is_smoke(call) for call in calls)


def test_api_invoker_that_never_converges_stops_the_release_before_web(tmp_path: Path) -> None:
    result, calls, _state = _run(
        tmp_path, {}, FAKE_IAM_LAGS_FOREVER=API_SERVICE, ODP_API_INVOKER_WAIT_ATTEMPTS="3"
    )

    assert result.returncode != 0
    assert "was not admitted to the private API service (last HTTP 403)" in result.stderr
    api_probes = [call for call in calls if call[0] == "curl" and API_SERVICE in call[1]]
    assert len(api_probes) == 4
    assert WEB_SERVICE not in _deploys(calls)
    assert not any(_is_smoke(call) for call in calls)


COMPAT_HARNESS = r"""
set -euo pipefail
source "$1"
execute_job() { :; }
run_locked_python() {
  python3 -c '
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as handle:
    handle.write(json.dumps(["compat", sys.argv[1:], "",
        os.environ.get("ODP_API_INVOKER_TOKEN", "")]) + "\n")
' "$@"
}
eval "$2"
OLD_API_URL="${FAKE_OLD_API_URL}"
OLD_WEB_URL="${FAKE_OLD_WEB_URL}"
run_migration_compatibility_gate
"""


def _run_compat(tmp_path: Path, state: dict[str, object], **env: str):
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("run_migration_compatibility_gate() {")
    end = text.index("\n}\n", start) + 3
    segment = text[start:end]
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
    environment.pop("ODP_API_INVOKER_TOKEN", None)
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment.get('PATH', '')}",
            "FAKE_STATE": str(state_path),
            "FAKE_LOG": str(log_path),
            "GCP_PROJECT": "odayplus-dev",
            "GCP_REGION": "asia-east1",
            "API_SERVICE": API_SERVICE,
            "ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT": RUNTIME_SA,
            "ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT": SMOKE_SA,
            "ODP_DEPLOY_ENV": "dev",
            "ODAY_RELEASE_SHA": "ee06d1d8" + "a" * 32,
            "MIGRATION_CANDIDATE_JOB": "oday-migrate-candidate",
            "MIGRATION_COMPAT_REPORT": str(tmp_path / "compat.json"),
            "MIGRATION_COMPAT_TIMEOUT": "15",
            "MIGRATION_COMPAT_RETRY_ATTEMPTS": "4",
            "MIGRATION_COMPAT_RETRY_BACKOFF": "2",
            "MIGRATION_COMPAT_RETRY_MAX_BACKOFF": "8",
            "MIGRATION_COMPAT_RETRY_DEADLINE": "120",
            "ODP_API_INVOKER_WAIT_SECONDS": "0",
            **env,
        }
    )
    result = subprocess.run(
        ["bash", "-c", COMPAT_HARNESS, "bash", str(TRAFFIC_HELPER), segment],
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


def test_compatibility_gate_reaches_the_old_private_api_with_a_transport_token(
    tmp_path: Path,
) -> None:
    state = {API_SERVICE: _existing_service(API_SERVICE), WEB_SERVICE: _existing_service(WEB_SERVICE)}

    result, calls, after = _run_compat(
        tmp_path,
        state,
        FAKE_OLD_API_URL=API_STABLE_URL,
        FAKE_OLD_WEB_URL=f"https://{WEB_SERVICE}-abc.a.run.app",
    )

    assert result.returncode == 0, result.stderr
    compat = calls[_index(calls, lambda call: call[0] == "compat")]
    assert "compatibility-smoke" in compat[1]
    assert compat[3] == API_TRANSPORT_TOKEN
    assert after[API_SERVICE]["iam"] == API_INVOKERS
    assert after[WEB_SERVICE]["iam"] == ["allUsers"]
    grant_positions = [position for position, call in enumerate(calls) if _is_api_grant(call)]
    assert len(grant_positions) == 2
    assert max(grant_positions) < calls.index(compat)


def test_bootstrap_compatibility_needs_no_api_transport(tmp_path: Path) -> None:
    result, calls, after = _run_compat(tmp_path, {}, FAKE_OLD_API_URL="", FAKE_OLD_WEB_URL="")

    assert result.returncode == 0, result.stderr
    compat = calls[_index(calls, lambda call: call[0] == "compat")]
    assert "bootstrap-compatibility" in compat[1]
    assert compat[3] == ""
    assert after == {}
    assert not any(call[0] in {"run", "auth", "curl"} for call in calls)


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
