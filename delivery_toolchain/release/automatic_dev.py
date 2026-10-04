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


def previous_deployment() -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Read actual successful deployment artifacts, never green build-only runs.

    Artifact expiry/unreadability is an error. The first-release absence probe
    independently refuses to proceed if historical artifacts are unavailable
    while live resources exist. No absence is inferred from a failed API call.
    """
    for page in range(1, 11):
        runs = api(f"actions/workflows/deploy-dev.yml/runs?branch=dev&status=success"
                   f"&per_page=100&page={page}").get("workflow_runs", [])
        for run in runs:
            if str(run["id"]) == os.environ.get("GITHUB_RUN_ID"):
                continue
            if run.get("path") != WORKFLOW or run.get("event") != "workflow_dispatch":
                continue
            artifacts = api(f"actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
            matches = [a for a in artifacts if a.get("name") == DEPLOYED_ARTIFACT]
            if not matches:
                continue
            if len(matches) != 1 or matches[0].get("expired"):
                raise Refused("Previous dev deployment artifact is ambiguous or expired.")
            jobs = api(f"actions/runs/{run['id']}/jobs?per_page=100")["jobs"]
            if not any(j.get("name") == DEPLOY_JOB and j.get("conclusion") == "success"
                       for j in jobs):
                raise Refused("Previous artifact has no successful deployment job.")
            archive = api(f"actions/artifacts/{matches[0]['id']}/zip", raw=True)
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                entries = zipped.infolist()
                if (len(entries) != 1 or entries[0].filename != "RELEASE_MANIFEST.json"
                        or entries[0].file_size > 2_000_000):
                    raise Refused("Unexpected previous deployment artifact contents.")
                manifest = json.loads(zipped.read(entries[0]))
            errors = validate_release_admission(manifest, environment="dev")
            if errors:
                raise Refused("Previous dev manifest is not admissible: " + "; ".join(errors))
            return manifest, run
        if len(runs) < 100:
            return None
    raise Refused("Deployment history scan limit reached; no safe predecessor selected.")


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
