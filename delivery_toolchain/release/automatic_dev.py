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
    release_candidate_job_name,
    validate_release_admission,
)

WORKFLOW = ".github/workflows/deploy-dev.yml"
CI_WORKFLOW = ".github/workflows/ci.yml"
AUTHORITY = "protected-dev-ci-standing-policy-v1"
DEPLOY_JOB = "Deploy the admitted artifact by immutable digest"
# The step whose success means dev traffic and live gates are committed; later
# evidence-publication steps can still fail the job without undoing it.
LIVE_STEP = "Deploy Cloud Run by immutable digest"
# Runs only after LIVE_STEP failed. Its success, plus the artifact it publishes,
# is the only positive evidence of what a failed live step left live.
RESTORE_STEP = "Read back dev live state after a failed live step"
RESTORE_ARTIFACT = "dev-failed-live-step-readback"
RESTORE_FILE = "dev-failed-live-step-readback.json"
RESTORE_KIND = "dev-failed-live-step-readback-v2"
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


def live_step_outcome(job: dict[str, Any]) -> tuple[str, str]:
    """What the live step of a deploy job that did not succeed proves.

    Returns ``(kind, when)``:

    * ``committed`` -- the live step succeeded, so every live gate passed and
      live dev is this release even though a later step failed the job.
    * ``not_started`` -- the live step was skipped or never began, which is
      positive evidence that this attempt did not touch live dev.
    * ``readback`` -- the live step failed and the post-failure live read-back
      step succeeded; the read-back artifact still has to name what is live.
    * ``uncertain`` -- anything else. ``deploy_cloud_run_waji.sh`` promotes
      traffic before its last live gate, and its EXIT trap only *logs* a failed
      traffic or scheduler restore before exiting with the original status, so
      a failed, cancelled, timed-out or interrupted live step reads identically
      whether the predecessor came back or the candidate/mixed state stayed.

    Unreadable step outcomes cannot prove anything and refuse.
    """
    steps = job.get("steps")
    if not isinstance(steps, list):
        raise Refused("Dev deploy job step outcomes are unreadable; no safe predecessor selected.")
    live = [step for step in steps if isinstance(step, dict) and step.get("name") == LIVE_STEP]
    if len(live) > 1:
        raise Refused("Dev deploy job reports the live step twice; no safe predecessor selected.")
    if not live:
        return "not_started", ""
    step = live[0]
    when = str(step.get("completed_at") or step.get("started_at")
               or job.get("completed_at") or job.get("started_at") or "")
    conclusion = step.get("conclusion")
    if conclusion == "success":
        return "committed", when
    if conclusion == "skipped" or (
            conclusion is None and not step.get("started_at")
            and step.get("status") in {"queued", "pending", "waiting"}):
        return "not_started", ""
    if conclusion == "failure" and any(
            isinstance(other, dict) and other.get("name") == RESTORE_STEP
            and other.get("conclusion") == "success" for other in steps):
        return "readback", when
    return "uncertain", when


def restoration_readback(run: dict[str, Any], attempt: int) -> str | None | bool:
    """Release (16-hex prefix) the read-back proved live, ``None`` for an empty
    target, or ``False`` when no valid read-back artifact proves either."""
    name = f"{RESTORE_ARTIFACT}-{attempt}"
    artifacts = api(f"actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
    matches = [a for a in artifacts if a.get("name") == name]
    if len(matches) != 1 or matches[0].get("expired"):
        return False
    archive = api(f"actions/artifacts/{matches[0]['id']}/zip", raw=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entries = zipped.infolist()
        if (len(entries) != 1 or entries[0].filename != RESTORE_FILE
                or entries[0].file_size > 100_000):
            return False
        receipt = json.loads(zipped.read(entries[0]))
    title = str(run.get("display_title", "")).split()
    live = receipt.get("live_release") if isinstance(receipt, dict) else False
    if (not isinstance(receipt, dict) or receipt.get("kind") != RESTORE_KIND
            or receipt.get("environment") != "dev"
            or not isinstance(receipt.get("scheduler_baseline_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["scheduler_baseline_sha256"])
            or len(title) != 5 or receipt.get("failed_candidate_sha") != title[4]
            or str(receipt.get("run_id")) != str(run["id"])
            or str(receipt.get("run_attempt")) != str(attempt)
            or not (live is None or (isinstance(live, str) and re.fullmatch(r"[0-9a-f]{16}", live)
                                     and live != title[4][:16]))):
        return False
    return live


def deployed_attempts(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Attempts of ``run`` that changed, or may have changed, live dev; newest first.

    Reruns share one run id, and the run-level status/conclusion only reflect
    the latest attempt, so every attempt is read on its own. ``kind`` is:

    * ``complete`` -- the deploy job succeeded; its manifest is retained.
    * ``committed`` -- the live step succeeded but the job failed later (for
      example while publishing evidence): live, but its manifest is unproven.
    * ``restored`` -- the live step failed and a post-failure live read-back
      proved live dev is wholly ``restored_release`` (``None``: empty target).
    * ``uncertain`` -- the live step started and did not succeed, with no such
      proof. Its live effect is unknown.

    Attempts whose deploy job was skipped, or whose live step provably never
    started, did not touch live dev and are omitted. The current attempt of
    this very run is excluded: it has not deployed anything yet.
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
            restored: str | None = None
            if j.get("conclusion") == "success":
                kind, completed_at = "complete", (
                    j.get("completed_at")
                    or j.get("started_at")
                    or run.get("updated_at")
                    or run.get("created_at")
                    or ""
                )
            elif j.get("conclusion") == "skipped":
                break
            else:
                kind, completed_at = live_step_outcome(j)
                if kind == "not_started":
                    break
                if kind == "readback":
                    proven = restoration_readback(run, attempt)
                    if proven is False:
                        kind = "uncertain"
                    else:
                        kind, restored = "restored", proven
            found.append({
                "attempt": attempt,
                "completed_at": completed_at,
                "job_id": j.get("id"),
                "kind": kind,
                "complete": kind == "complete",
                "restored_release": restored,
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

    The newest attempt that changed, or may have changed, live dev decides:

    * a completed deploy is the predecessor;
    * a live commit whose job failed afterwards refuses (manifest unproven);
    * a failed live step with a successful post-failure read-back proves what
      is live, so the newest completed deploy of exactly that release is the
      predecessor (an empty target read-back means no predecessor);
    * a failed, cancelled or interrupted live step without that proof refuses
      whenever any older release could still be live. Selecting the older
      release would claim a rollback nobody observed, and deduplicating
      against it would skip a deploy that may be needed. Only when no release
      was ever live does it fall through to a first release, whose fresh
      target absence read-back (build and admission) proves the failed
      attempt left nothing behind before anything is mutated.
    """
    repo = repository()
    events: list[tuple[datetime, int, int, dict[str, Any], dict[str, Any], list[str]]] = []
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
            for entry in attempts:
                events.append((parse_iso(entry["completed_at"]), int(run.get("id") or 0),
                               int(entry["attempt"]), entry, run, title))
        if len(runs) < 100:
            reached_end = True
            break

    if not reached_end:
        raise Refused("Deployment history scan limit reached; no safe predecessor selected.")

    if not events:
        return None

    events.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    newest, best_run, title = events[0][3], events[0][4], events[0][5]
    if newest["kind"] == "committed":
        # Live dev is this run's release, but its job failed after the live
        # commit, so the deployed manifest is unproven. Neither an older
        # predecessor nor a first release is true; a signed manual dev deploy
        # naming the live release's rollback manifest restores the evidence.
        raise Refused("Latest dev deploy committed live but its job failed afterwards; "
                      "its deployed manifest is unproven, so no predecessor is selected. "
                      "Run a signed manual dev deploy with an explicit rollback manifest.")
    if newest["kind"] == "uncertain":
        if any(item[3]["kind"] in {"complete", "committed"}
               or (item[3]["kind"] == "restored" and item[3]["restored_release"] is not None)
               for item in events[1:]):
            raise Refused(
                f"Latest dev deploy (run {best_run.get('id')} attempt {newest['attempt']}) "
                "started its live step and did not succeed, and no live read-back proves "
                "the previous release was restored; the live release is unknown, so no "
                "predecessor is selected and nothing is reported as already deployed. "
                "Read back the dev Cloud Run traffic and scheduler targets, then run a "
                "signed manual dev deploy with an explicit rollback manifest for what is live.")
        # Nothing was ever proven live: a first release, gated by a fresh
        # absence read-back that refuses if this attempt left resources.
        return None
    if newest["kind"] == "restored":
        live = newest["restored_release"]
        if live is None:
            return None
        chosen = next((item for item in events[1:] if item[3]["kind"] == "complete"
                       and len(item[5]) == 5 and item[5][4][:16] == live), None)
        if chosen is None:
            raise Refused("Live read-back names a dev release with no retained successful "
                          "deployment; no safe predecessor selected.")
        best_run, title = chosen[4], chosen[5]
        if not dev_environment_deployed(best_run):
            raise Refused("Successful dev deploy job lacks dev environment proof; "
                          "no safe predecessor selected.")

    manifest = deployed_manifest(best_run, title)
    return manifest, best_run


def deployed_manifest(best_run: dict[str, Any], title: list[str]) -> dict[str, Any]:
    """The retained, admissible manifest of one successful dev deployment."""
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
    return manifest


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


def gcloud_json(*args: str) -> Any:
    result = subprocess.run(["gcloud", *args, "--format=json"], capture_output=True,
                            timeout=120, check=False)
    if result.returncode:
        raise Refused("Cloud read-back failed; the failed live step stays unproven.")
    return json.loads(result.stdout or "null")


def service_description(name: str, *, project: str, region: str) -> dict[str, Any] | None:
    """The exact Cloud Run service, ``None`` only when the listing proves absence."""
    listed = gcloud_json("run", "services", "list", f"--project={project}",
                         f"--region={region}", f"--filter=metadata.name={name}")
    if not isinstance(listed, list):
        raise Refused("Unexpected Cloud Run service listing; live state unproven.")
    exact = [s for s in listed if isinstance(s, dict)
             and s.get("metadata", {}).get("name") == name]
    if len(exact) != len(listed) or len(exact) > 1:
        raise Refused(f"Cloud Run service listing for {name} is ambiguous; live state unproven.")
    return exact[0] if exact else None


def serving_release(description: dict[str, Any]) -> str:
    """The one release (16-hex tag prefix) that serves all traffic of a service.

    The deploy script tags every candidate revision ``candidate-<sha16>`` and
    restores traffic by explicit revision name, so a restored service routes
    100% to pinned revisions carrying exactly one such tag, all of one release.
    """
    status = description.get("status")
    traffic = status.get("traffic") if isinstance(status, dict) else None
    if not isinstance(traffic, list) or not traffic:
        raise Refused("Cloud Run service has no readable traffic; live state unproven.")
    tags: dict[str, set[str]] = {}
    serving: dict[str, int] = {}
    for item in traffic:
        if not isinstance(item, dict):
            raise Refused("Unreadable Cloud Run traffic entry; live state unproven.")
        revision = str(item.get("revisionName") or "")
        match = re.fullmatch(r"candidate-([0-9a-f]{16})", str(item.get("tag") or ""))
        if match and revision:
            tags.setdefault(revision, set()).add(match.group(1))
        percent = item.get("percent") or 0
        if not isinstance(percent, int) or percent < 0:
            raise Refused("Unreadable Cloud Run traffic percentage; live state unproven.")
        if percent:
            if not revision or item.get("latestRevision"):
                raise Refused("Cloud Run traffic is not pinned to immutable revisions.")
            serving[revision] = serving.get(revision, 0) + percent
    if sum(serving.values()) != 100:
        raise Refused("Cloud Run traffic does not total 100%; live state unproven.")
    releases: set[str] = set()
    for revision in serving:
        found = tags.get(revision, set())
        if len(found) != 1:
            raise Refused("A serving revision has no unique release tag; live state unproven.")
        releases |= found
    if len(releases) != 1:
        raise Refused("More than one release serves traffic; live state is mixed.")
    return releases.pop()


def failed_live_step_readback(env: dict[str, str], baseline: Path | None = None) -> dict[str, Any]:
    """Prove what live dev is after this run's live step failed, or refuse.

    Either the whole target is absent (the first-release cleanup worked), or
    API and Web route all traffic to one release that is not the failed
    candidate and both scheduler triggers run exactly that release's jobs.
    In either case, both triggers must also match their authoritative,
    attempt-bound pre-deploy configuration, including intentional PAUSED state.
    Anything else -- mixed traffic, candidate still serving, an unreadable or
    inconsistent resource -- refuses, so the attempt remains uncertain and the
    next release fails closed instead of guessing.
    """
    from delivery_toolchain.release import scheduler_restore_proof
    from delivery_toolchain.release.probe_release_target_absence import (
        ProbeError,
        probe_target_absence,
    )

    failed = env.get("ODAY_RELEASE_SHA", "")
    if env.get("ODP_DEPLOY_ENV") != "dev" or not re.fullmatch(r"[0-9a-f]{40}", failed):
        raise Refused("Failed live step read-back is restricted to an exact dev release.")
    names = {key: env.get(key, "") for key in (
        "GCP_PROJECT", "GCP_REGION", "API_SERVICE", "WEB_SERVICE", "MIGRATION_JOB",
        "WORKER_JOB", "SCHEDULER_JOB", "WORKER_SCHEDULE_NAME", "SCHEDULER_SCHEDULE_NAME")}
    if not all(names.values()):
        raise Refused("Dev target bindings are incomplete; live state unproven.")
    project, region = names["GCP_PROJECT"], names["GCP_REGION"]
    try:
        baseline_digest, triggers = scheduler_restore_proof.verify(env, baseline, gcloud_json)
    except (OSError, ValueError, TypeError) as exc:
        raise Refused("Scheduler restoration is unproven; pre-deploy configuration proof required.") from exc
    api_service = service_description(names["API_SERVICE"], project=project, region=region)
    web_service = service_description(names["WEB_SERVICE"], project=project, region=region)
    live: str | None
    if api_service is None and web_service is None:
        try:
            probe_target_absence(target_environment="dev", project=project, region=region,
                                 candidate_sha=failed, targets={
                                     "api": names["API_SERVICE"], "web": names["WEB_SERVICE"],
                                     "migration": names["MIGRATION_JOB"],
                                     "worker": names["WORKER_JOB"],
                                     "scheduler": names["SCHEDULER_JOB"]})
        except ProbeError as exc:
            raise Refused("Dev target is not empty after a failed first release: "
                          + "; ".join(exc.errors)) from exc
        live = None
    elif api_service is None or web_service is None:
        raise Refused("Only one of the dev API/Web services exists; live state is mixed.")
    else:
        live = serving_release(api_service)
        if serving_release(web_service) != live:
            raise Refused("Dev API and Web serve different releases; live state is mixed.")
        if live == failed[:16]:
            raise Refused("The failed candidate still serves dev traffic.")
        for trigger, base in ((names["SCHEDULER_SCHEDULE_NAME"], names["SCHEDULER_JOB"]),
                              (names["WORKER_SCHEDULE_NAME"], names["WORKER_JOB"])):
            # Job names keep only the first RELEASE_JOB_NAME_SHA_LENGTH (<16)
            # characters of the release SHA, so the tag prefix names them.
            job = release_candidate_job_name(base, live.ljust(40, "0"))
            expected = (f"https://run.googleapis.com/v2/projects/{project}/locations/"
                        f"{region}/jobs/{job}:run")
            if triggers[trigger].get("httpTarget", {}).get("uri") != expected:
                raise Refused(f"Scheduler trigger {trigger} does not run the serving release.")
    return {"kind": RESTORE_KIND, "environment": "dev", "failed_candidate_sha": failed,
            "run_id": env.get("GITHUB_RUN_ID", ""), "run_attempt": env.get("GITHUB_RUN_ATTEMPT", ""),
            "live_release": live,
            "scheduler_baseline_sha256": baseline_digest,
            "observed_at": datetime.now(UTC).isoformat(timespec="seconds")}


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
    parser.add_argument("action", choices=["dispatch", "preflight", "recovery", "admit", "deploy-check",
                                           "live-readback", "scheduler-baseline"])
    parser.add_argument("--candidate", default=os.environ.get("ODAY_RELEASE_SHA", ""))
    parser.add_argument("--environment", default=os.environ.get("RELEASE_ENVIRONMENT", ""))
    parser.add_argument("--ci-run-id", default=os.environ.get("AUTO_CI_RUN_ID", ""))
    parser.add_argument("--directory", type=Path, default=Path(".odp_data/release/automatic-dev"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--digest", default=os.environ.get("MANIFEST_DIGEST", ""))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "scheduler-baseline":
            from delivery_toolchain.release import scheduler_restore_proof
            if args.output is None:
                raise Refused("Missing pre-deploy Scheduler baseline destination.")
            baseline = scheduler_restore_proof.capture(dict(os.environ), gcloud_json)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x") as destination:
                json.dump(baseline, destination, indent=2)
                destination.write("\n")
            print("Captured attempt-bound dev Scheduler configuration digests before mutation.")
            return 0
        if args.action == "live-readback":
            # Runs after a failed dev live step of any (manual or automatic)
            # deploy; writes a receipt only when the live state is proven.
            if args.output is None:
                raise Refused("Missing read-back receipt destination.")
            receipt = failed_live_step_readback(dict(os.environ), args.baseline)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(receipt, indent=2) + "\n")
            print("Live dev read-back: "
                  + (f"release {receipt['live_release']} serves all traffic."
                     if receipt["live_release"] else "target is empty."))
            return 0
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
