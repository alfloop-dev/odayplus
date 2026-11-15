"""Dev-only driver: verify the installed model-ready views and train the
governed forecastops candidate with the REAL trainer
(``product_ops.modeling.release``), registering it in the dev MLflow.

It never promotes: release aliases need a separately signed approval document
(approver != training actor), which this driver does not create.

The only adaptation is identity plumbing. The trainer's MLflow auth mints a
Cloud Run ID token through ``google.oauth2.id_token.fetch_id_token``, which
supports a metadata server or a service-account key but not the Workload
Identity Federation credentials a GitHub runner has. The job's auth step mints
an ID token for the same deployer service account with the MLflow origin as
audience; this driver hands that token to the trainer's fetcher. MLflow's
Cloud Run IAM still decides access.

Env: RAW_DB_URL, CLOUD_SQL_INSTANCE, MLFLOW_ID_TOKEN, MLFLOW_TRACKING_URI,
ODP_MLFLOW_CLOUD_RUN_AUDIENCE, ODP_MODEL_ARTIFACT_ROOT, ODP_RELEASE_COMMIT_SHA,
ODP_MODEL_TRAINING_ACTOR, MODEL_VERSION. No secrets are printed.
"""

from __future__ import annotations

import io
import json
import os
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime


def _socket_dsn(raw: str, instance: str) -> str:
    u = urllib.parse.urlparse(raw)
    user = urllib.parse.quote(urllib.parse.unquote(u.username or ""), safe="")
    pw = urllib.parse.quote(urllib.parse.unquote(u.password or ""), safe="")
    db = (u.path or "/").lstrip("/") or "postgres"
    host = urllib.parse.quote(f"/cloudsql/{instance}", safe="/:")
    return f"postgresql://{user}:{pw}@/{db}?host={host}"


def _run(label: str, fn, argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(argv)
    print(f"{label} rc={rc}", flush=True)
    return rc, out.getvalue(), err.getvalue()


def _promote(release) -> int:
    """Promote with owner-signed approval documents, in the listed order.

    PROMOTIONS = "version:approval.json[:rollback_target];..." -- the approval
    files are written only after the named approver approves; the trainer
    enforces approver != ODP_MODEL_TRAINING_ACTOR and the role allow-list.
    """
    for item in [p for p in os.environ["PROMOTIONS"].split(";") if p.strip()]:
        parts = item.strip().split(":")
        version, approval = parts[0], parts[1]
        argv = ["promote", "--model", "forecastops", "--version", version,
                "--approval-file", approval]
        if len(parts) > 2 and parts[2]:
            argv += ["--rollback-target", parts[2]]
        rc, out, err = _run(f"PROMOTE {version}", release.main, argv)
        print(out[:4000], err[-3000:], flush=True)
        if rc != 0:
            print(f"RESULT: FAILED (promotion of {version} refused)", flush=True)
            return 1
    print("RESULT: OK (promoted)", flush=True)
    return 0


def _print_state(release) -> int:
    """Read-only: durable (Postgres) vs remote (MLflow) registry state."""
    from models.shared_ml.registry import ModelAlias

    settings = release.ProductionTrainingSettings.from_environment()
    resources = release.build_production_resources(settings)
    try:
        service = resources.application.service
        repo = service.repository
        registry = service.registry
        name = "forecast_revenue_interval"
        for v in repo.list_model_versions(name):
            mc = dict(v.monitoring_config)
            print("DURABLE", v.version, "stage=", v.stage.value, "approved_by=", bool(v.approved_by),
                  "requested_by=", mc.get("requested_by"), "release_id=", mc.get("release_id"), flush=True)
        for alias in ModelAlias:
            d = repo.get_alias(name, alias)
            try:
                r = registry.get_by_alias(model_name=name, alias=alias)
            except Exception as exc:  # noqa: BLE001
                r = f"ERR {type(exc).__name__}: {exc}"
            print("ALIAS", alias.value, "durable=", getattr(d, "version", d),
                  "remote=", getattr(r, "version", r), flush=True)
        for saga in repo.list_release_sagas(name, include_terminal=True):
            print("SAGA", saga.release_id, saga.operation, saga.state.value,
                  "errors=", list(getattr(saga, "compensation_errors", ()) or ())[:3],
                  "last_error=", str(getattr(saga, "last_error", "") or "")[:300], flush=True)
        client = registry._require_client()
        for mv in client.search_model_versions(filter_string=f"name = '{name}'"):
            print("REMOTE", "v" + str(mv.version), mv.tags.get(registry._tag("domain_version")),
                  "stage=", mv.current_stage, "aliases=", list(mv.aliases or []), flush=True)
    finally:
        resources.close()
    print("RESULT: OK (state)", flush=True)
    return 0


def main() -> int:
    os.environ["ODAY_DATABASE_URL"] = _socket_dsn(
        os.environ["RAW_DB_URL"], os.environ["CLOUD_SQL_INSTANCE"]
    )
    token = os.environ.pop("MLFLOW_ID_TOKEN").strip()
    if not token:
        print("RESULT: FAILED (no MLflow ID token)", flush=True)
        return 1

    from modules.learninghub.infrastructure import mlflow_cloud_run_auth

    audience = os.environ["ODP_MLFLOW_CLOUD_RUN_AUDIENCE"].rstrip("/")

    def _wif_minted(_request, requested_audience: str) -> str:
        if requested_audience.rstrip("/") != audience:
            raise RuntimeError("ID token requested for an unexpected audience")
        return token

    mlflow_cloud_run_auth.fetch_id_token = _wif_minted

    from product_ops.modeling import install_views, release

    if os.environ.get("ACTION", "train") == "state":
        return _print_state(release)
    from shared.governance.model_performance import (
        MODEL_PERFORMANCE_DRIFT_POLICY_KIND,
        MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID,
    )

    # The trainer resolves its platform-wide drift policy in the registry scope
    # MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID ("canonical deployment tenant").
    # Dev never provisioned that tenant, so the policy was never seeded. Create
    # the tenant row only; the migration-installed core.tenants trigger seeds
    # the canonical v1 policies through workflow.seed_*_policy().
    import psycopg

    with psycopg.connect(os.environ["ODAY_DATABASE_URL"], connect_timeout=30) as conn:
        created = conn.execute(
            "INSERT INTO core.tenants (tenant_id, tenant_name) VALUES (%s, %s) "
            "ON CONFLICT (tenant_id) DO NOTHING RETURNING tenant_id",
            (MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID, "platform-model-governance"),
        ).fetchone()
        in_force = conn.execute(
            "SELECT count(*) FROM workflow.decision_policies WHERE policy_kind = %s "
            "AND tenant_id = %s AND effective_to IS NULL",
            (MODEL_PERFORMANCE_DRIFT_POLICY_KIND, MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID),
        ).fetchone()[0]
        conn.commit()
    print(
        f"PLATFORM_POLICY tenant_created={bool(created)} drift_policy_in_force={in_force}",
        flush=True,
    )
    if in_force != 1:
        print("RESULT: FAILED (platform drift policy not seeded by trigger)", flush=True)
        return 1

    # The versioned views are already installed in dev (probe 43215309998);
    # only re-verify them read-only instead of re-applying the SQL.
    rc, out, err = _run("INSTALL_VIEWS inventory", install_views.main, ["inventory"])
    if rc != 0:
        print(out[:3000], err[-2000:], flush=True)
        print("RESULT: FAILED (model-ready views not ready)", flush=True)
        return 1

    rc, out, err = _run("INVENTORY forecastops", release.main, ["inventory", "--model", "forecastops"])
    try:
        inv = json.loads(out)
        for m in inv.get("models", []):
            print("INVENTORY", json.dumps(m, sort_keys=True, default=str)[:3000], flush=True)
    except ValueError:
        print(out[:2000], err[-2000:], flush=True)

    if os.environ.get("ACTION", "train") == "promote":
        return _promote(release)

    # Bound training to the date-ordered chunk-loaded stretch: one-batch
    # backfilled history is rejected by the horizon maturity rule.
    start = os.environ["TRAIN_START"].strip()
    end = os.environ.get("TRAIN_END", "").strip() or datetime.now(UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    versions = [v.strip() for v in os.environ["MODEL_VERSIONS"].split(",") if v.strip()]
    for version in versions:
        rc, out, err = _run(
            f"TRAIN forecastops {version}",
            release.main,
            ["train", "--model", "forecastops", "--version", version,
             "--start", start, "--end", end],
        )
        if rc != 0:
            print("TRAIN stderr:", err[-3000:], flush=True)
            print(f"RESULT: FAILED (training {version} refused or failed)", flush=True)
            return 1
        result = json.loads(out)
        print(f"TRAIN result {version}:",
              json.dumps(result, sort_keys=True, default=str)[:8000], flush=True)
    print(f"RESULT: OK (registered {versions}; NOT promoted)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
