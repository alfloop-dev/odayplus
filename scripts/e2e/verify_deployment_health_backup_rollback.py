#!/usr/bin/env python3
"""Verify E2E deployment health, backup/restore, and data rollback evidence."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = "infra/docker/docker-compose.e2e.yml"
DB_PATH = "/data/product-e2e.sqlite3"
BACKUP_PATH = "/storage/backups/product-e2e.sqlite3.backup"
REPORT_NAME = "deployment-health-backup-rollback-report.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run E2E deployment health, backup/restore, and rollback proof."
    )
    parser.add_argument("--project", default=os.environ.get("ODP_E2E_PROJECT", "oday-plus-e2e-pv014"))
    parser.add_argument("--api-port", default=os.environ.get("ODP_E2E_API_PORT", "8099"))
    parser.add_argument("--web-port", default=os.environ.get("ODP_E2E_WEB_PORT", "3100"))
    parser.add_argument(
        "--source-stub-port",
        default=os.environ.get("ODP_E2E_SOURCE_STUB_PORT", "8077"),
    )
    parser.add_argument(
        "--diagnostics-dir",
        default=os.environ.get(
            "ODP_E2E_DIAGNOSTICS_DIR",
            ".odp_data/deployment-health-backup-rollback",
        ),
    )
    parser.add_argument("--keep-stack", action="store_true")
    args = parser.parse_args()

    diagnostics_dir = ROOT / args.diagnostics_dir
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    compose = [
        "docker",
        "compose",
        "-p",
        args.project,
        "-f",
        COMPOSE_FILE,
    ]
    env = {
        **os.environ,
        "ODP_E2E_API_PORT": str(args.api_port),
        "ODP_E2E_WEB_PORT": str(args.web_port),
        "ODP_E2E_SOURCE_STUB_PORT": str(args.source_stub_port),
    }
    api_url = f"http://127.0.0.1:{args.api_port}"
    web_url = f"http://127.0.0.1:{args.web_port}"
    source_url = f"http://127.0.0.1:{args.source_stub_port}"
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "project": args.project,
        "compose_file": COMPOSE_FILE,
        "diagnostics_dir": str(diagnostics_dir),
    }

    try:
        run(compose + ["down", "--remove-orphans", "--volumes"], env=env, check=False)
        run(compose + ["up", "-d", "--build"], env=env)
        wait_for_json(f"{api_url}/platform/health")
        wait_for_url(f"{web_url}/")
        wait_for_json(f"{source_url}/external/listing_raw_snapshot.valid.json")

        seed = run(
            [
                sys.executable,
                "scripts/e2e/seed_product_e2e_data.py",
                "--wait",
                "--api-url",
                api_url,
                "--source-stub-url",
                source_url,
                "--diagnostics-dir",
                str(diagnostics_dir),
            ],
            env=env,
            capture=True,
        )
        report["seed_stdout"] = seed.stdout.strip()
        health = {
            "api": wait_for_json(f"{api_url}/platform/health"),
            "web_status": wait_for_url(f"{web_url}/"),
            "source_fixture": wait_for_json(f"{source_url}/external/listing_raw_snapshot.valid.json"),
            "worker": wait_for_worker_heartbeat(compose, env),
        }
        report["health"] = health

        before_cases = list_cases(api_url)
        backup = create_backup(compose, env)
        report["backup"] = backup

        probe_store_id = f"pv014-rollback-probe-{int(time.time())}"
        probe_case = create_probe_case(api_url, probe_store_id)
        after_probe_cases = list_cases(api_url)
        assert_case_present(after_probe_cases, probe_store_id)

        run(compose + ["stop", "web", "worker", "api"], env=env)
        restore = restore_backup(compose, env)
        run(compose + ["up", "-d", "api", "web", "worker"], env=env)
        wait_for_json(f"{api_url}/platform/health")
        wait_for_url(f"{web_url}/")
        restored_cases = list_cases(api_url)

        if has_store(restored_cases, probe_store_id):
            raise RuntimeError("rollback probe still exists after restoring the backup")
        if not has_store(restored_cases, "e2e-store-taipei-001"):
            raise RuntimeError("seed AVM case is missing after restore")

        report["rollback"] = {
            "probe_store_id": probe_store_id,
            "probe_case_id": probe_case["case_id"],
            "case_count_before_probe": len(before_cases),
            "case_count_after_probe": len(after_probe_cases),
            "case_count_after_restore": len(restored_cases),
            "probe_removed": True,
            "seed_case_preserved": True,
            "restore": restore,
        }
        report["unsupported_or_documented"] = {
            "model_artifact_rollback": "not mutated by this deployment drill; Learning Hub alias rollback is covered by PV-007 product E2E",
            "policy_rollback": "policy files are immutable in the image for this E2E stack; image rollback is represented by redeploying the previous image tag",
            "remote_staging_rollout": "not configured because ODP_STAGING_DEPLOY_URL/host variables are placeholders",
        }
        report["result"] = "passed"
        write_report(diagnostics_dir, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        report["result"] = "failed"
        report["error"] = str(exc)
        write_report(diagnostics_dir, report)
        raise
    finally:
        run(compose + ["ps"], env=env, capture=True, check=False).write_to(
            diagnostics_dir / "compose-ps.txt"
        )
        run(
            compose + ["logs", "--no-color", "--tail=200"],
            env=env,
            capture=True,
            check=False,
        ).write_to(diagnostics_dir / "compose-tail.log")
        if not args.keep_stack and os.environ.get("ODP_E2E_KEEP_STACK") != "1":
            run(compose + ["down", "--remove-orphans", "--volumes"], env=env, check=False)


class CommandResult:
    def __init__(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.returncode = completed.returncode
        self.stdout = completed.stdout or ""
        self.stderr = completed.stderr or ""

    def write_to(self, path: Path) -> None:
        path.write_text(self.stdout + self.stderr, encoding="utf-8")


def run(
    command: list[str],
    *,
    env: dict[str, str],
    capture: bool = False,
    check: bool = True,
) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        check=False,
    )
    if check and completed.returncode != 0:
        output = completed.stdout or ""
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{output}")
    return CommandResult(completed)


def wait_for_json(url: str, *, timeout_seconds: int = 120) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return get_json(url)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def wait_for_url(url: str, *, timeout_seconds: int = 120) -> int:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=10) as response:
                return int(response.status)
        except (
            ConnectionResetError,
            HTTPError,
            URLError,
            TimeoutError,
            http.client.RemoteDisconnected,
        ) as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def get_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "x-subject-id": "verify-backup-rollback",
            "x-roles": "finance_legal,expansion_user,operations_manager,auditor,data_owner,platform_admin",
        }
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any], *, correlation_id: str) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-correlation-id": correlation_id,
            "x-subject-id": "verify-backup-rollback",
            "x-roles": "finance_legal,expansion_user,operations_manager,auditor,data_owner,platform_admin",
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def list_cases(api_url: str) -> list[dict[str, Any]]:
    payload = get_json(f"{api_url}/avm/cases")
    return list(payload["items"])


def has_store(cases: list[dict[str, Any]], store_id: str) -> bool:
    return any(case.get("store_id") == store_id for case in cases)


def assert_case_present(cases: list[dict[str, Any]], store_id: str) -> None:
    if not has_store(cases, store_id):
        raise RuntimeError(f"expected AVM case for {store_id}")


def create_probe_case(api_url: str, store_id: str) -> dict[str, Any]:
    return post_json(
        f"{api_url}/avm/cases",
        {
            "store_id": store_id,
            "gm_ttm": 100_000,
            "forecast_gm_next_12m": 105_000,
            "asset_book_value": 50_000,
            "equipment_fair_value": 20_000,
            "lease_liability": 5_000,
            "working_capital": 7_000,
            "comparable_multiples": [2.5, 2.7],
            "liquidity_discount": 0.1,
            "quality_score": 0.9,
            "source_snapshot_ids": ["pv014-rollback-probe"],
            "prediction_origin_time": "2026-06-29T00:00:00Z",
            "created_by": "pv014-rollback-probe",
            "idempotency_key": store_id,
        },
        correlation_id="corr-pv014-backup-restore-rollback",
    )


def wait_for_worker_heartbeat(compose: list[str], env: dict[str, str]) -> dict[str, Any]:
    deadline = time.time() + 60
    last_output = ""
    while time.time() < deadline:
        result = run(
            compose
            + [
                "exec",
                "-T",
                "worker",
                "python",
                "-c",
                (
                    "from pathlib import Path; import json; "
                    "p=Path('/storage/worker-heartbeat.jsonl'); "
                    "print(p.read_text().strip().splitlines()[-1] if p.exists() and p.read_text().strip() else '')"
                ),
            ],
            env=env,
            capture=True,
            check=False,
        )
        last_output = result.stdout.strip()
        if last_output:
            return json.loads(last_output)
        time.sleep(2)
    raise RuntimeError(f"worker heartbeat not observed: {last_output}")


def create_backup(compose: list[str], env: dict[str, str]) -> dict[str, Any]:
    code = (
        "from pathlib import Path; import hashlib, json, sqlite3; "
        f"src=Path('{DB_PATH}'); dst=Path('{BACKUP_PATH}'); dst.parent.mkdir(parents=True, exist_ok=True); "
        "dst.unlink(missing_ok=True); "
        "source=sqlite3.connect(src); backup=sqlite3.connect(dst); "
        "source.backup(backup); backup.close(); source.close(); "
        "digest=hashlib.sha256(dst.read_bytes()).hexdigest(); "
        "print(json.dumps({'path': str(dst), 'sha256': digest, 'size_bytes': dst.stat().st_size}))"
    )
    result = run(compose + ["exec", "-T", "api", "python", "-c", code], env=env, capture=True)
    return parse_last_json_line(result.stdout)


def restore_backup(compose: list[str], env: dict[str, str]) -> dict[str, Any]:
    code = (
        "from pathlib import Path; import hashlib, json, shutil; "
        f"src=Path('{BACKUP_PATH}'); dst=Path('{DB_PATH}'); "
        "Path(str(dst) + '-wal').unlink(missing_ok=True); Path(str(dst) + '-shm').unlink(missing_ok=True); "
        "shutil.copy2(src, dst); "
        "digest=hashlib.sha256(dst.read_bytes()).hexdigest(); "
        "print(json.dumps({'restored_path': str(dst), 'sha256': digest, 'size_bytes': dst.stat().st_size}))"
    )
    result = run(
        compose + ["run", "--rm", "--no-deps", "api", "python", "-c", code],
        env=env,
        capture=True,
    )
    return parse_last_json_line(result.stdout)


def write_report(diagnostics_dir: Path, report: dict[str, Any]) -> None:
    report["report_sha256"] = sha256_json(report)
    (diagnostics_dir / REPORT_NAME).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_json(payload: dict[str, Any]) -> str:
    clean = {key: value for key, value in payload.items() if key != "report_sha256"}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def parse_last_json_line(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return json.loads(stripped)
    raise RuntimeError(f"no JSON object found in command output: {output}")


if __name__ == "__main__":
    raise SystemExit(main())
