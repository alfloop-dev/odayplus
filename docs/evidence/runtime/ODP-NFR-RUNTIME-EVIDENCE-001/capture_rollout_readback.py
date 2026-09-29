#!/usr/bin/env python3
"""Capture the GitHub-side rollout facts that gate the five NFR runtime checks.

Read-only. Every call is `gh api` / `gh run` against alfloop-dev/odayplus.
Each command is stored with its wall-clock start/end, exit code and stdout,
so the disposition document can cite a receipt instead of prose.

Deployment history is read in full, not sampled: every GitHub deployment of
every environment (GraphQL, paginated), every status of each deployment, and
the complete job list of every workflow run that ever put a deployment into
`success` (a later deployment turns an earlier `success` into `inactive`, so
both states count). Counts are captured separately so the verifier can prove
nothing was dropped.

Environment variable values are not stored. Only per-key sha256
fingerprints and cross-environment equality are recorded, which is enough to
show whether two environments share a project / identity / secret reference
without copying the value.

Any failed call, truncated page or missing identity key makes the capture
exit 1 without touching the committed receipt.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = "alfloop-dev/odayplus"
OWNER, NAME = REPO.split("/")
ENVIRONMENTS = ("dev", "staging", "production")
BUILD_ENVIRONMENTS = tuple(f"{env}-build" for env in ENVIRONMENTS)
IDENTITY_KEYS = (
    "GCP_PROJECT_ID",
    "GCP_SERVICE_ACCOUNT",
    "GCP_WORKLOAD_IDENTITY_PROVIDER",
    "ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT",
    "ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT",
    "ODAY_DATABASE_URL_SECRET",
    "ODP_AUTH_PRINCIPAL_MAP_SECRET",
    "ODP_WEB_OIDC_CLIENT_SECRET_SECRET",
    "ODP_WEB_SESSION_SECRET_SECRET",
    "ODP_SNAPSHOT_BUCKET",
)
DEPLOYED_STATES = {"SUCCESS", "INACTIVE"}
OUT = Path(__file__).with_name("rollout-readback.json")

COUNT_QUERY = (
    "query($owner:String!,$name:String!,$env:String!){repository(owner:$owner,name:$name)"
    "{deployments(environments:[$env]){totalCount}}}"
)
HISTORY_QUERY = (
    "query($owner:String!,$name:String!,$env:String!,$endCursor:String)"
    "{repository(owner:$owner,name:$name)"
    "{deployments(environments:[$env],first:100,after:$endCursor)"
    "{pageInfo{hasNextPage endCursor} nodes{databaseId commitOid createdAt"
    " statuses(first:100){totalCount nodes{state logUrl}}}}}}"
)
# One compact JSON line per deployment: every status state (newest first) and
# the distinct workflow runs named by its status log URLs.
HISTORY_JQ = (
    ".data.repository.deployments.nodes[]|{id:.databaseId,sha:.commitOid,"
    "created_at:.createdAt,status_total:.statuses.totalCount,"
    "states:[.statuses.nodes[].state],"
    'run_ids:([.statuses.nodes[].logUrl|select(.!=null)|capture("/runs/(?<r>[0-9]+)").r]|unique)}'
)
JOBS_JQ = (
    "{total_count,workflow_name:.jobs[0].workflow_name,head_sha:.jobs[0].head_sha,"
    "jobs:[.jobs[]|{name,conclusion,run_attempt}]}"
)


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(argv: list[str]) -> dict:
    started = now()
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    return {
        "argv": argv,
        "started_at": started,
        "finished_at": now(),
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def graphql(query: str, env: str, *extra: str) -> dict:
    return run(
        [
            "gh",
            "api",
            "graphql",
            *extra,
            "-f",
            f"owner={OWNER}",
            "-f",
            f"name={NAME}",
            "-f",
            f"env={env}",
            "-f",
            f"query={query}",
        ]
    )


def fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def failed_calls(node, path: str = "calls") -> list[str]:
    """Every nested call record whose exit code is not 0."""
    if isinstance(node, dict):
        if "exit_code" in node:
            return [] if node["exit_code"] == 0 else [path]
        return [p for k, v in node.items() for p in failed_calls(v, f"{path}.{k}")]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in failed_calls(v, f"{path}[{i}]")]
    return []


def env_variables(env: str) -> tuple[dict[str, str], list[dict]]:
    values: dict[str, str] = {}
    calls = []
    page = 1
    while True:
        call = run(
            [
                "gh",
                "api",
                f"repos/{REPO}/environments/{env}/variables?per_page=30&page={page}",
            ]
        )
        body = json.loads(call["stdout"]) if call["exit_code"] == 0 else {}
        # Values stay out of the receipt; keep names and the count only.
        call["stdout"] = json.dumps(
            {
                "total_count": body.get("total_count"),
                "names": sorted(v["name"] for v in body.get("variables", [])),
            }
        )
        calls.append(call)
        for var in body.get("variables", []):
            values[var["name"]] = var["value"]
        if call["exit_code"] != 0 or not body.get("variables"):
            break
        if len(values) >= body.get("total_count", 0):
            break
        page += 1
    return values, calls


def main() -> int:
    receipt: dict = {"repo": REPO, "captured_from": now(), "calls": {}}
    calls = receipt["calls"]
    problems: list[str] = []

    calls["runtime_release_runs"] = run(
        [
            "gh",
            "run",
            "list",
            "-R",
            REPO,
            "--workflow",
            "deploy-dev.yml",
            "-L",
            "20",
            "--json",
            "databaseId,event,status,conclusion,createdAt,headSha",
        ]
    )
    latest = (
        json.loads(calls["runtime_release_runs"]["stdout"] or "[]")
        if calls["runtime_release_runs"]["exit_code"] == 0
        else []
    )
    if latest:
        calls["latest_run_jobs"] = run(
            [
                "gh",
                "run",
                "view",
                str(latest[0]["databaseId"]),
                "-R",
                REPO,
                "--json",
                "databaseId,headSha,jobs",
                "--jq",
                "{databaseId,headSha,jobs:[.jobs[]|{databaseId,name,conclusion}]}",
            ]
        )
    else:
        problems.append("no Runtime Release run listed")

    deployed_runs: set[str] = set()
    for env in ENVIRONMENTS + BUILD_ENVIRONMENTS:
        count = graphql(COUNT_QUERY, env, "--jq", ".data.repository.deployments.totalCount")
        history = graphql(HISTORY_QUERY, env, "--paginate", "--jq", HISTORY_JQ)
        calls[f"deployment_count_{env}"] = count
        calls[f"deployment_history_{env}"] = history
        if count["exit_code"] or history["exit_code"]:
            continue
        rows = [json.loads(ln) for ln in history["stdout"].splitlines() if ln.strip()]
        if len(rows) != int(count["stdout"]):
            problems.append(
                f"{env}: history has {len(rows)} rows, totalCount {count['stdout'].strip()}"
            )
        for row in rows:
            if row["status_total"] > len(row["states"]):
                problems.append(f"{env}: deployment {row['id']} statuses truncated")
            if env in ENVIRONMENTS and DEPLOYED_STATES & set(row["states"]):
                if not row["run_ids"]:
                    problems.append(f"{env}: deployment {row['id']} reached success without a run")
                deployed_runs.update(row["run_ids"])

    def jobs_of(run_id: str) -> tuple[str, dict]:
        return run_id, run(
            [
                "gh",
                "api",
                f"repos/{REPO}/actions/runs/{run_id}/jobs?filter=all&per_page=100",
                "--jq",
                JOBS_JQ,
            ]
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        run_jobs = dict(pool.map(jobs_of, sorted(deployed_runs, key=int)))
    calls["deployed_run_jobs"] = run_jobs
    for run_id, call in run_jobs.items():
        if call["exit_code"] == 0:
            body = json.loads(call["stdout"])
            if body["total_count"] != len(body["jobs"]):
                problems.append(
                    f"run {run_id}: {body['total_count']} jobs, captured {len(body['jobs'])}"
                )

    identity: dict[str, dict] = {}
    for env in ENVIRONMENTS:
        values, var_calls = env_variables(env)
        calls[f"variables_{env}"] = var_calls
        identity[env] = {k: values.get(k) for k in IDENTITY_KEYS}
        missing = [k for k, v in identity[env].items() if not v]
        if missing:
            problems.append(f"{env}: identity keys missing {missing}")

    comparison = {}
    for key in IDENTITY_KEYS:
        per_env = {env: identity[env][key] for env in ENVIRONMENTS}
        comparison[key] = {
            "fingerprints": {e: (fingerprint(v) if v else None) for e, v in per_env.items()},
            "shared_with_production": sorted(
                e
                for e in ENVIRONMENTS
                if e != "production" and per_env[e] and per_env[e] == per_env["production"]
            ),
        }
    receipt["identity_reference_comparison"] = comparison
    receipt["captured_until"] = now()

    problems = failed_calls(calls) + problems
    if problems:
        print(f"capture incomplete, {OUT.name} left unchanged:", *problems, sep="\n  ")
        return 1
    OUT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT} ({len(run_jobs)} deployed runs resolved)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
