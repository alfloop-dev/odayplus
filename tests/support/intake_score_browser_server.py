"""Isolated browser fixture with real PostgreSQL routers and worker delivery.

Run only via the synthetic test composition. No production app imports this
module, and the existing business authorization dependencies remain intact.
"""
from __future__ import annotations

import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from pydantic import BaseModel

from apps.api.app.routes import listings
from apps.api.app.routes.operator import create_operator_router
from apps.api.oday_api.security.dependencies import bind_audit_log, bind_persistence
from apps.worker.oday_worker.handlers import build_default_registry
from apps.worker.oday_worker.main import ODayWorker
from modules.listing.application.intake_score_delivery import INTAKE_SITESCORE_JOB_TYPE
from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from modules.sitescore.application.intake_score_worker import handle_intake_score
from shared.infrastructure.persistence.factory import build_persistence
from tests.support.intake_score_synthetic import (
    SCOPE,
    TENANT,
    BrowserModelRuntime,
    prepare_database,
    ready_scenario,
)


class Drain(BaseModel):
    scenario_id: str


class SourcePolicyControl(BaseModel):
    production_enabled: bool


class _ReadOnlyBrowserModelRuntime:
    """Allow durable projections; synthetic inference belongs to the worker."""

    def __init__(self) -> None:
        self.infer_calls = 0

    def infer(self, **_kwargs: object) -> object:
        self.infer_calls += 1
        raise AssertionError("Browser read composition must not perform inference")


def create_app():
    database_url = os.environ.get("ODP_SCORE_BROWSER_DATABASE_URL", "")
    if not database_url and os.environ.get("ODP_SCORE_BROWSER_DATABASE_URL_FILE"):
        database_url = Path(os.environ["ODP_SCORE_BROWSER_DATABASE_URL_FILE"]).read_text().strip()
    control_key = os.environ.get("ODP_SCORE_BROWSER_CONTROL_KEY", "")
    database_info = conninfo_to_dict(database_url)
    name = database_info.get("dbname", "")
    native_owned = (re.fullmatch(r"oday_native_parser_p_[0-9a-f]{16}", name) is not None
        and database_info.get("host") in {"127.0.0.1", "localhost"})
    if (os.environ.get("ODP_SCORE_BROWSER_MODE") != "isolated-synthetic"
            or not (name.startswith("odp_score_browser_") or native_owned)
            or len(control_key) < 32 or os.environ.get("ODP_PRODUCT_MODE") != "poc"):
        raise RuntimeError("Explicit isolated synthetic browser configuration required")
    os.environ["ODAY_DATABASE_URL"] = database_url
    os.environ["ODP_REQUIRE_LIVE_DATA"] = "true"
    # The dedicated database must be fresh, rather than silently sharing or
    # resetting another run's state. A second start uses the same migrated DB.
    with psycopg.connect(database_url) as conn:
        initialized = conn.execute("SELECT to_regclass('workflow.decision_policies')").fetchone()[0]
    if initialized is None:
        prepare_database(database_url)
    bundle = build_persistence(mode="postgresql")
    # Local JWTs resolve the actual SQL identity/session stores, just as in
    # the normal API composition. Configured auth never trusts test personas.
    bind_persistence(bundle)
    bind_audit_log(bundle.audit_log)
    scenarios = {}
    observations = {}
    controls = Lock()
    read_runtime = _ReadOnlyBrowserModelRuntime()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        bundle.engine.close()

    app = FastAPI(lifespan=lifespan)
    app.state.persistence_bundle = bundle
    app.state.audit_log = bundle.audit_log
    app.state.operator_intake_repository = bundle.operator_intake_repository
    app.state.job_queue = bundle.job_queue
    app.include_router(create_operator_router(
        audit_log=bundle.audit_log, document_store=bundle.assisted_intake_store._store,
        listing_repository_for_tenant=bundle.listing_repository_for_tenant,
        sitescore_repository_for_tenant=lambda tenant: bundle._scoped_repository("sitescore_repository", tenant),
        sitescore_decision_repository_for_tenant=bundle.sitescore_decision_store_for_tenant,
        avm_repository_for_tenant=lambda tenant: bundle._scoped_repository("avm_repository", tenant),
        netplan_repository_for_tenant=lambda tenant: bundle._scoped_repository("netplan_repository", tenant),
        priceops_repository_for_tenant=lambda tenant: bundle._scoped_repository("priceops_repository", tenant),
        intake_workflow_store=bundle.assisted_intake_store,
        intake_workflow_policy=bundle.assisted_intake_workflow_policy,
        model_runtime=read_runtime,
        score_execution_service=bundle.score_execution_service(model_runtime=read_runtime),
        live_repository=OperatorLiveRepository(bundle), require_live_data=True,
        persistence_mode="postgresql", provider_mode="live", allow_test_reset=False,
    ), prefix="/api/v1")
    app.include_router(listings.create_assisted_intake_router(
        bundle.assisted_intake_store, workflow_policy=bundle.assisted_intake_workflow_policy,
    ), prefix="/api/v1")

    def control(x_synthetic_control: str = Header(default="")):
        if not hmac.compare_digest(x_synthetic_control, control_key):
            raise HTTPException(403, "Synthetic fixture control key required")

    def state(scenario_id):
        if scenario_id not in scenarios:
            raise HTTPException(404, "Unknown synthetic scenario")
        case = scenarios[scenario_id]
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        promotion = next((p for p in store.promotions.values() if p["intake_id"] == case["intake_id"]), None)
        job_id = promotion.get("site_score_job_id") if promotion else None
        candidate_id = promotion.get("candidate_site_id") if promotion else None
        job = bundle.engine.query_one("SELECT job_id,status,attempt,version,result,payload FROM workflow.jobs WHERE job_id=? AND tenant_id=?", (job_id, TENANT)) if job_id else None
        if job:
            for key in ("payload", "result"):
                if isinstance(job[key], str):
                    job[key] = json.loads(job[key])
        delivery = bundle.job_queue.get(job_id) if job_id else None
        repository = bundle._scoped_repository("sitescore_repository", TENANT)
        reports = repository.history(candidate_id) if candidate_id else []
        result = job.get("result") if job else None
        prediction_id = result.get("prediction_run_id") if result else None
        score_run_id = result.get("sitescore_run_id") if result else None
        candidates = [c for c in bundle.listing_repository_for_tenant(TENANT).list_candidates()
                      if c.candidate_site.listing_id == case["listing_id"]]
        events = [e.event_type for e in bundle.outbox_repository.get_unpublished_events()
                  if e.payload.get("candidate_site_id") == candidate_id and candidate_id]
        audit_rows = bundle.engine.query(
            "SELECT audit_event_id,actor_subject_id,resource_id,action,result,before_value,after_value,correlation_id,occurred_at "
            "FROM audit.audit_events WHERE tenant_id=? AND resource_id=? ORDER BY occurred_at",
            (TENANT, promotion["promotion_decision_id"]),
        ) if promotion else []
        for row in audit_rows:
            for key in ("before_value", "after_value"):
                if isinstance(row[key], str):
                    row[key] = json.loads(row[key])
        return {**case, "promotion": ({key: promotion.get(key) for key in (
                    "promotion_decision_id", "status", "candidate_site_id", "site_score_job_id", "version")} if promotion else None),
                "job": ({key: job[key] for key in ("job_id", "status", "attempt", "version")} | {
                    "generation": job["payload"]["generation"],
                    "result": ({key: job["result"].get(key) for key in ("report_id", "prediction_run_id", "sitescore_run_id", "model_version")} if job["result"] else None)} if job else None),
                "delivery": ({"job_id": delivery.job_id, "status": delivery.status.value, "attempts": delivery.attempts} if delivery else None),
                "candidates": [{"id": c.candidate_site.candidate_site_id, "score": c.score, "model_version": c.model_version,
                                "site_status": c.candidate_site.site_status} for c in candidates],
                "reports": [{"report_id": r.report_id, "candidate_site_id": r.candidate_site_id,
                             "model_version": r.model_version, "m12_p50": r.m12.p50,
                             "sitescore_run_id": r.sitescore_run_id} for r in reports],
                "lineage": {"prediction_run_exists": bool(prediction_id and repository.get_prediction_run(prediction_id)),
                            "sitescore_run_exists": bool(score_run_id and repository.get_sitescore_run(score_run_id)),
                            "prediction_count": len(repository.get_predictions(prediction_id)) if prediction_id else 0},
                "audits": audit_rows,
                "event_types": events, "inferences": observations.get(scenario_id, []),
                "read_model_runtime": {"scope": "SYNTHETIC_READ_ONLY_NO_PRODUCTION_MODEL",
                                       "infer_calls": read_runtime.infer_calls}}

    @app.get("/_synthetic/health")
    def health():
        return {"scope": SCOPE, "persistence": bundle.mode,
                "read_model_runtime": {"scope": "SYNTHETIC_READ_ONLY_NO_PRODUCTION_MODEL",
                                       "infer_calls": read_runtime.infer_calls}}

    @app.post("/_synthetic/scenarios", dependencies=[Depends(control)])
    def seed():
        with controls:
            # A debugging/restart interval can exceed the real audit backlog
            # gate. Deliver pending evidence through the real audit worker;
            # never clear rows or bypass the five-minute admission guard.
            audit_bundle = build_persistence(mode="postgresql")
            try:
                worker = ODayWorker(audit_bundle, audit_only=True, worker_id="synthetic-browser-audit-worker")
                for _ in range(500):
                    if not worker.run_once():
                        break
            finally:
                audit_bundle.engine.close()
            case = ready_scenario(database_url, bundle, TestClient(app))
            scenarios[case["scenario_id"]] = case
            return state(case["scenario_id"])

    @app.get("/_synthetic/evidence/{scenario_id}", dependencies=[Depends(control)])
    def evidence(scenario_id: str):
        with controls:
            return state(scenario_id)

    @app.post("/_synthetic/scenarios/{scenario_id}/source-policy", dependencies=[Depends(control)])
    def source_policy(scenario_id: str, body: SourcePolicyControl):
        with controls:
            case = state(scenario_id)
            # Explicitly revoke/restore this synthetic registry fixture. The
            # real worker admission gate, not this route, settles its outcome.
            with psycopg.connect(database_url) as conn:
                conn.execute("UPDATE intake.source_registry SET production_enabled=%s WHERE source_id=%s",
                             (body.production_enabled, "synthetic-browser-" + case["scenario_id"]))
            return {"scope": SCOPE, "production_enabled": body.production_enabled}

    @app.post("/_synthetic/worker/drain", dependencies=[Depends(control)])
    def drain(body: Drain):
        with controls:
            before = state(body.scenario_id)
            if not before["job"]:
                raise HTTPException(409, "Browser must request and approve a real promotion first")
            selected_id = before["job"]["job_id"]
            worker_bundle = build_persistence(mode="postgresql")
            registry = build_default_registry()

            def score(job, persistence):
                if job.job_id != selected_id:
                    raise RuntimeError("Unexpected score scenario in isolated worker")

                def observe(_kwargs):
                    accepted = state(body.scenario_id)
                    observations.setdefault(body.scenario_id, []).append({
                        "job_id": job.job_id, "job_status": accepted["job"]["status"],
                        "attempt": accepted["job"]["attempt"], "promotion_status": accepted["promotion"]["status"],
                        "report_count_before_inference": len(accepted["reports"]),
                    })

                handle_intake_score(job, persistence, model_runtime=BrowserModelRuntime(observe=observe))

            registry.register(INTAKE_SITESCORE_JOB_TYPE, score, replace=True)
            worker = ODayWorker(worker_bundle, registry=registry, worker_id="isolated-browser-score-worker")
            try:
                for _ in range(100):
                    if not worker.run_once():
                        break
                    current = state(body.scenario_id)
                    if current["job"]["status"] in {"SUCCEEDED", "FAILED", "DEAD_LETTER", "CANCELLED"}:
                        break
                return state(body.scenario_id)
            finally:
                worker_bundle.engine.close()

    return app


app = create_app()
