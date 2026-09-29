#!/usr/bin/env python3
"""Capture the GitHub-side rollout facts that gate the five NFR runtime checks.

Read-only. Every call is `gh api` / `gh run` against alfloop-dev/odayplus.
Each command is stored with its wall-clock start/end, exit code and raw
stdout, so the disposition document can cite a receipt instead of prose.

Environment variable values are not stored. Only per-key sha256
fingerprints and cross-environment equality are recorded, which is enough to
show whether two environments share a project / identity / secret reference
without copying the value.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = "alfloop-dev/odayplus"
ENVIRONMENTS = ("dev", "staging", "production")
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
OUT = Path(__file__).with_name("rollout-readback.json")


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


def fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


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
        if call["exit_code"] != 0 or len(values) >= body.get("total_count", 0):
            break
        page += 1
    return values, calls


def main() -> int:
    receipt: dict = {"repo": REPO, "captured_from": now(), "calls": {}}
    calls = receipt["calls"]

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
    latest = json.loads(calls["runtime_release_runs"]["stdout"] or "[]")
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

    for env in ENVIRONMENTS + ("dev-build", "staging-build", "production-build"):
        calls[f"deployments_{env}"] = run(
            [
                "gh",
                "api",
                f"repos/{REPO}/deployments?environment={env}&per_page=100",
                "--jq",
                "[.[]|{id,sha,ref,created_at}]",
            ]
        )

    for env in ENVIRONMENTS:
        deployments = json.loads(calls[f"deployments_{env}"]["stdout"] or "[]")
        states = {}
        for dep in deployments[:10]:
            call = run(
                [
                    "gh",
                    "api",
                    f"repos/{REPO}/deployments/{dep['id']}/statuses",
                    "--jq",
                    "[.[]|{state,created_at,log_url}]",
                ]
            )
            states[str(dep["id"])] = call
        calls[f"deployment_statuses_{env}"] = states
        # A deployment whose latest status is `success` is not a rollout by
        # itself; record which jobs of the backing run actually ran.
        success_runs = {}
        for call in states.values():
            statuses = json.loads(call["stdout"] or "[]")
            if statuses and statuses[0]["state"] == "success":
                run_id = statuses[0]["log_url"].split("/runs/")[1].split("/")[0]
                success_runs[run_id] = run(
                    [
                        "gh",
                        "run",
                        "view",
                        run_id,
                        "-R",
                        REPO,
                        "--json",
                        "databaseId,headSha,conclusion,jobs",
                        "--jq",
                        "{databaseId,headSha,conclusion,jobs:[.jobs[]|{name,conclusion}]}",
                    ]
                )
        calls[f"success_deployment_runs_{env}"] = success_runs

    identity: dict[str, dict] = {}
    for env in ENVIRONMENTS:
        values, var_calls = env_variables(env)
        calls[f"variables_{env}"] = var_calls
        identity[env] = {k: values.get(k) for k in IDENTITY_KEYS}

    comparison = {}
    for key in IDENTITY_KEYS:
        per_env = {env: identity[env][key] for env in ENVIRONMENTS}
        present = {e: v for e, v in per_env.items() if v}
        comparison[key] = {
            "fingerprints": {e: (fingerprint(v) if v else None) for e, v in per_env.items()},
            "distinct_across_present_envs": len(set(present.values())) == len(present),
            "shared_with_production": sorted(
                e
                for e in ENVIRONMENTS
                if e != "production" and per_env[e] and per_env[e] == per_env["production"]
            ),
        }
    receipt["identity_reference_comparison"] = comparison
    receipt["captured_until"] = now()

    OUT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    failed = [
        name
        for name, call in calls.items()
        if isinstance(call, dict) and call.get("exit_code", 0) != 0
    ]
    print(f"wrote {OUT} failed_calls={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
