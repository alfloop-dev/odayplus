"""Standing dev release authority, backed by GitHub facts rather than human leases.

The existing Runtime Release owns build, migration, traffic and live validation.
This module only connects those phases for protected dev commits. It cannot
admit staging/production, invent a human signature, or turn a build into a
successful deployment. GitHub environment approval and signed manual admission
remain the authority for other environments.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from delivery_toolchain.release.release_manifest import (
    component_binding_errors,
    load_manifest,
    manifest_release_profile,
    validate_release_admission,
)

WORKFLOW = ".github/workflows/deploy-dev.yml"
CI_WORKFLOW = ".github/workflows/ci.yml"
AUTHORITY = "protected-dev-ci-standing-policy-v1"
DEPLOY_JOB = "Deploy the admitted artifact by immutable digest"
# The step whose success means dev traffic and live gates are committed; later
# evidence-publication steps can still fail the job without undoing it.
LIVE_STEP = "Deploy Cloud Run by immutable digest"
DEPLOYED_ARTIFACT = "deployed-release-manifest-dev"
COMPONENTS = ("api", "web", "worker", "scheduler")


class Refused(RuntimeError):
    """Safe diagnostic containing no token, environment dump or raw API error."""


def github(path: str, *, data: dict[str, Any] | None = None, raw: bool = False) -> Any:
    command = ["gh", "api", path]
    if data is not None:
        command += ["--method", "POST", "--input", "-"]
    result = subprocess.run(
        command, input=json.dumps(data).encode() if data is not None else None,
        capture_output=True, timeout=60, check=False,
    )
    if result.returncode:
        raise Refused("GitHub read/dispatch failed; inspect Actions permissions and retry.")
    if raw:
        return result.stdout
    return json.loads(result.stdout) if result.stdout else None


def repository() -> str:
    value = os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise Refused("Missing GitHub repository identity.")
    return value


def api(path: str, **kwargs: Any) -> Any:
    return github(f"repos/{repository()}/{path}", **kwargs)


def context_errors(env: dict[str, str], *, candidate: str, target: str) -> list[str]:
    errors = []
    repo = env.get("GITHUB_REPOSITORY", "")
    if target != "dev":
        errors.append("Automatic deployment is restricted to dev.")
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        errors.append("Candidate must be an exact SHA.")
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        errors.append("Automatic release must use the existing workflow_dispatch path.")
    if env.get("GITHUB_REF") != "refs/heads/dev" or env.get("GITHUB_SHA") != candidate:
        errors.append("Release must run the exact dev dispatch commit.")
    if env.get("GITHUB_WORKFLOW_REF") != f"{repo}/{WORKFLOW}@refs/heads/dev":
        errors.append("Unexpected release workflow identity.")
    return errors


def ci_errors(run: dict[str, Any], branch: dict[str, Any], *, candidate: str,
              repo: str) -> list[str]:
    errors = []
    if branch.get("protected") is not True:
        errors.append("dev must be a protected branch.")
    if branch.get("commit", {}).get("sha") != candidate:
        errors.append("Candidate was superseded; the newer dev CI owns the next release.")
    if (run.get("event") != "push" or run.get("head_branch") != "dev"
            or run.get("head_sha") != candidate or run.get("path") != CI_WORKFLOW
            or run.get("repository", {}).get("full_name") != repo
            or run.get("head_repository", {}).get("full_name") != repo):
        errors.append("CI must be a same-repository dev push for the exact candidate.")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        errors.append("The exact candidate CI has not completed successfully.")
    return errors


def verify_ci(candidate: str, target: str, run_id: str, *, wait: bool = False) -> None:
    errors = context_errors(dict(os.environ), candidate=candidate, target=target)
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        errors.append("A real triggering CI run id is required.")
    if errors:
        raise Refused(" ".join(errors))
    # CI dispatches asynchronously in its final job; let that job return first.
    for attempt in range(31 if wait else 1):
        run = api(f"actions/runs/{run_id}")
        if run.get("status") == "completed" or not wait or attempt == 30:
            break
        time.sleep(2)
    errors = ci_errors(run, api("branches/dev"), candidate=candidate, repo=repository())
    if errors:
        raise Refused(" ".join(errors))


def parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return datetime.min.replace(tzinfo=UTC)


def live_step_committed(job: dict[str, Any]) -> str | None:
    """Completion time of the live-commit step in a job that did not succeed.

    ``deploy_cloud_run_waji.sh`` commits only after every live gate passes and
    rolls back any earlier failure, so a failed job whose live step succeeded
    still changed live dev. Unreadable step outcomes cannot prove otherwise.
    """
    steps = job.get("steps")
    if not isinstance(steps, list):
        raise Refused("Dev deploy job step outcomes are unreadable; no safe predecessor selected.")
    for step in steps:
        if step.get("name") == LIVE_STEP and step.get("conclusion") == "success":
            return step.get("completed_at") or job.get("completed_at") or ""
    return None


def deployed_attempts(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Attempts of ``run`` that changed live dev, newest first.

    Reruns share one run id, and the run-level status/conclusion only reflect
    the latest attempt. A successful first attempt therefore stays live even
    while a rerun is in progress or after it fails, so success is read per
    attempt. An attempt whose live step committed but whose job failed later
    (for example while publishing evidence) is live yet ``complete`` is false:
    its deployed manifest is not proven. The current attempt of this very run
    is excluded: it has not deployed anything yet.
    """
    latest = int(run.get("run_attempt") or 1)
    if str(run["id"]) == os.environ.get("GITHUB_RUN_ID"):
        latest = int(os.environ.get("GITHUB_RUN_ATTEMPT") or 1) - 1
    found = []
    for attempt in range(latest, 0, -1):
        jobs = api(f"actions/runs/{run['id']}/attempts/{attempt}/jobs?per_page=100")["jobs"]
        for j in jobs:
            if j.get("name") != DEPLOY_JOB:
                continue
            if j.get("conclusion") == "success":
                complete, completed_at = True, (
                    j.get("completed_at")
                    or j.get("started_at")
                    or run.get("updated_at")
                    or run.get("created_at")
                    or ""
                )
            elif j.get("conclusion") == "skipped":
                break
            else:
                committed = live_step_committed(j)
                if committed is None:
                    break
                complete, completed_at = False, committed
            found.append({
                "attempt": attempt,
                "completed_at": completed_at,
                "job_id": j.get("id"),
                "complete": complete,
            })
            break
    return found


def paged(path: str, *, pages: int = 20) -> list[Any]:
    """Every record of a GitHub list endpoint; an unproven complete read refuses."""
    records: list[Any] = []
    separator = "&" if "?" in path else "?"
    for page in range(1, pages + 1):
        batch = api(f"{path}{separator}per_page=100&page={page}")
        if not isinstance(batch, list):
            raise Refused("Unexpected GitHub list response.")
        records += batch
        if len(batch) < 100:
            return records
    raise Refused("Deployment evidence scan limit reached; no safe predecessor selected.")


def dev_environment_deployed(run: dict[str, Any]) -> bool:
    """GitHub's own dev environment deployment record for this run succeeded.

    Newer dev-bound jobs at the same dispatch SHA (for example manual deploys
    refused before mutation) add environment records, so the proof can sit
    beyond the first page; both lists are read exhaustively.
    """
    for deployment in paged(f"deployments?environment=dev&sha={run['head_sha']}"):
        for status in paged(f"deployments/{deployment['id']}/statuses"):
            url = f"{status.get('log_url') or ''} {status.get('target_url') or ''}"
            if status.get("state") == "success" and f"/actions/runs/{run['id']}" in url:
                return True
    return False


def previous_deployment() -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Read actual successful dev deployment artifacts, never green build-only runs.

    Any dispatch ref may deploy dev manually with a signed lease, so the scan is
    not narrowed to ``branch=dev``; dev is proven by the run title, GitHub's dev
    environment deployment record and the manifest's own admission. Artifact
    expiry/unreadability is an error. The first-release absence probe
    independently refuses to proceed if historical artifacts are unavailable
    while live resources exist. No absence is inferred from a failed API call.
    """
    repo = repository()
    successful: list[tuple[datetime, int, dict[str, Any], list[str]]] = []
    reached_end = False

    for page in range(1, 11):
        runs = api(f"actions/workflows/deploy-dev.yml/runs?event=workflow_dispatch"
                   f"&per_page=100&page={page}").get("workflow_runs", [])
        for run in runs:
            if (run.get("path") != WORKFLOW or run.get("event") != "workflow_dispatch"
                    or run.get("repository", {}).get("full_name") != repo
                    or run.get("head_repository", {}).get("full_name") != repo):
                continue
            title = str(run.get("display_title", "")).split()
            if title[:3] != ["Runtime", "Release", "dev"]:
                continue
            attempts = deployed_attempts(run)
            if not attempts:
                continue
            # A successful dev deploy job may have changed live dev; without
            # its environment proof an older predecessor cannot be trusted.
            if attempts[0]["complete"] and not dev_environment_deployed(run):
                raise Refused("Successful dev deploy job lacks dev environment proof; "
                              "no safe predecessor selected.")
            completed_dt = parse_iso(attempts[0]["completed_at"])
            successful.append((completed_dt, int(run.get("id") or 0), run, title,
                               attempts[0]["complete"]))
        if len(runs) < 100:
            reached_end = True
            break

    if not reached_end:
        raise Refused("Deployment history scan limit reached; no safe predecessor selected.")

    if not successful:
        return None

    _, _, best_run, title, complete = max(successful, key=lambda item: (item[0], item[1]))
    if not complete:
        # Live dev is this run's release, but its job failed after the live
        # commit, so the deployed manifest is unproven. Neither an older
        # predecessor nor a first release is true; a signed manual dev deploy
        # naming the live release's rollback manifest restores the evidence.
        raise Refused("Latest dev deploy committed live but its job failed afterwards; "
                      "its deployed manifest is unproven, so no predecessor is selected. "
                      "Run a signed manual dev deploy with an explicit rollback manifest.")

    artifacts = api(f"actions/runs/{best_run['id']}/artifacts?per_page=100")["artifacts"]
    matches = [a for a in artifacts if a.get("name") == DEPLOYED_ARTIFACT]
    if not matches:
        raise Refused("Previous dev deployment artifact is missing.")
    if any(a.get("expired") for a in matches):
        raise Refused("Previous dev deployment artifact is expired.")

    manifests = []
    for match in matches:
        archive = api(f"actions/artifacts/{match['id']}/zip", raw=True)
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            entries = zipped.infolist()
            if (len(entries) != 1 or entries[0].filename != "RELEASE_MANIFEST.json"
                    or entries[0].file_size > 2_000_000):
                raise Refused("Unexpected previous deployment artifact contents.")
            manifests.append(json.loads(zipped.read(entries[0])))
    manifest = manifests[0]
    if any(other != manifest for other in manifests[1:]):
        raise Refused("Previous dev deployment artifacts are ambiguous.")
    if (len(title) != 5 or title[3] not in {"deploy", "auto"}
            or title[4] != manifest.get("candidate_sha")):
        raise Refused("Previous dev deployment title does not match its manifest.")
    errors = validate_release_admission(manifest, environment="dev")
    if errors:
        raise Refused("Previous dev manifest is not admissible: " + "; ".join(errors))

    return manifest, best_run


def output(**values: Any) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        raise Refused("Missing GitHub output destination.")
    with Path(path).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            text = str(value)
            if "\n" in text or "\r" in text:
                raise Refused("Invalid multiline output.")
            stream.write(f"{key}={text}\n")


def automatic_admission(manifest_path: Path, *, candidate: str, digest: str,
                        images: dict[str, str], ci_run_id: str) -> dict[str, Any]:
    manifest, errors = load_manifest(
        manifest_path, expected_candidate_sha=candidate, expected_digest=digest,
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        errors.append("Exact build manifest digest is required.")
    if set(images) != set(COMPONENTS):
        errors.append("Exactly four built component digests are required.")
    if manifest is not None:
        errors += validate_release_admission(manifest, environment="dev")
        errors += component_binding_errors(manifest, images)
        if (not manifest.get("sources_off_attestation")
                or manifest.get("external_sources_expected_enabled") != []):
            errors.append("Automatic dev releases require the standing sources-off posture.")
    if errors:
        raise Refused(" ".join(errors))
    return {"admitted": True, "authority": AUTHORITY, "environment": "dev",
            "candidate_sha": candidate, "manifest_digest": digest,
            "release_profile": manifest_release_profile(manifest),
            "ci_run_id": ci_run_id, "build_run_id": os.environ["GITHUB_RUN_ID"],
            "live_deployment_verified": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["dispatch", "preflight", "recovery", "admit", "deploy-check"])
    parser.add_argument("--candidate", default=os.environ.get("ODAY_RELEASE_SHA", ""))
    parser.add_argument("--environment", default=os.environ.get("RELEASE_ENVIRONMENT", ""))
    parser.add_argument("--ci-run-id", default=os.environ.get("AUTO_CI_RUN_ID", ""))
    parser.add_argument("--directory", type=Path, default=Path(".odp_data/release/automatic-dev"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--digest", default=os.environ.get("MANIFEST_DIGEST", ""))
    args = parser.parse_args(argv)
    try:
        if args.action == "dispatch":
            if (os.environ.get("GITHUB_EVENT_NAME") != "push"
                    or os.environ.get("GITHUB_REF") != "refs/heads/dev"
                    or args.candidate != os.environ.get("GITHUB_SHA")
                    or not re.fullmatch(r"[0-9a-f]{40}", args.candidate)):
                raise Refused("Only dev push CI may request automatic deployment.")
            branch = api("branches/dev")
            if branch.get("protected") is not True:
                raise Refused("dev is not protected.")
            if branch.get("commit", {}).get("sha") != args.candidate:
                print("Superseded CI; newer dev commit will request its own release.")
                return 0
            api("actions/workflows/deploy-dev.yml/dispatches", data={"ref": "dev", "inputs": {
                "phase": "auto", "environment": "dev", "release_sha": args.candidate,
                "task_id": "ODP-DEV-LIVE-DEPLOY-EXECUTION-001", "release_profile": "dev-admin",
                "auto_ci_run_id": os.environ["GITHUB_RUN_ID"],
            }})
            print("Dev release requested; this is not a deployment success receipt.")
            return 0
        verify_ci(args.candidate, args.environment, args.ci_run_id, wait=args.action == "preflight")
        if args.action == "deploy-check":
            return 0
        args.directory.mkdir(parents=True, exist_ok=True)
        if args.action in {"preflight", "recovery"}:
            previous = previous_deployment()
            already = previous is not None and previous[0]["candidate_sha"] == args.candidate
            if args.action == "preflight":
                output(proceed="false" if already else "true")
                print("Already deployed; no duplicate build/deploy." if already else "Exact dev CI verified.")
            else:
                if already:
                    raise Refused("Candidate already deployed; retry the complete workflow to deduplicate.")
                predecessor = args.directory / "previous-release.json"
                if previous:
                    predecessor.write_text(json.dumps(previous[0], indent=2) + "\n")
                output(initial_release="false" if previous else "true",
                       rollback_manifest=str(predecessor) if previous else "")
            return 0
        images = {name: os.environ.get(name.upper() + "_IMAGE", "") for name in COMPONENTS}
        if args.manifest is None:
            raise Refused("Missing transported build manifest.")
        receipt = automatic_admission(args.manifest, candidate=args.candidate,
                                      digest=args.digest, images=images, ci_run_id=args.ci_run_id)
        (args.directory / "admission.json").write_text(json.dumps(receipt, indent=2) + "\n")
        output(manifest_digest=receipt["manifest_digest"], release_profile=receipt["release_profile"])
        return 0
    except (Refused, OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
        # GitHub errors are sanitized by the adapter; never dump subprocess stderr/env.
        message = str(exc) if isinstance(exc, Refused) else f"Readback failed ({type(exc).__name__})."
        print("Automatic dev release refused: " + message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
