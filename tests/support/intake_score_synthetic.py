"""Explicit synthetic PostgreSQL inputs for browser queue integration.

These are test records, not real property/source/model admission or persona UAT.
No command receipt, candidate, job state or result is manufactured by this seed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.types.json import Jsonb

from models.shared_ml.output_contracts import SITESCORE_OUTPUT_TRANSFORM
from models.shared_ml.production_runtime import ModelInferenceResult
from models.shared_ml.registry import ModelAlias, ModelStage, ModelVersion
from models.shared_ml.scoring_binding import ModelBinding
from modules.listing.domain.models import ListingDedupKey
from modules.sitescore.domain.scoring import SITESCORE_FEATURE_VERSION
from shared.auth import Role
from shared.domain.models import AddressLocation, Listing
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from tests.intake_registry_fixture import (
    APPROVER,
    MANAGER,
    OWNER,
    TENANT,
    _audit_retention_row,
    _calendar_row,
    _workflow_row,
)

SCOPE = "ISOLATED_SYNTHETIC_PG_BROWSER_NOT_PRODUCTION_ADMISSION"


def _insert_policy(connection, row, tenant, approver, now):
    connection.execute("INSERT INTO workflow.decision_policies(policy_version_id,policy_label,policy_id,policy_version,policy_kind,"
        "tenant_id,effective_from,owner_role,approved_by,approved_at,input_contract,output_contract,change_reason,parameters,declared_inputs) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (row.policy_version_id, row.policy_label, row.policy_id, row.policy_version, row.policy_kind,
         tenant, now - timedelta(days=1), row.owner_role, approver, now - timedelta(days=1),
         "synthetic-input", "synthetic-output", SCOPE, Jsonb(dict(row.parameters)), list(row.declared_inputs)))


def headers(actor=MANAGER, *, key=None):
    return {"x-subject-id": actor, "x-tenant-id": TENANT,
            "x-roles": "site_reviewer,expansion_user", "x-operator-role": "expansion-manager",
            "x-correlation-id": str(uuid4()), "Idempotency-Key": key or str(uuid4())}


def prepare_database(database_url):
    config = Config("infra/db/migrations/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("postgresql://", "postgresql+psycopg://").replace("%", "%%"))
    command.upgrade(config, "head")
    apply_upgrade_to_database(database_url)
    now = datetime.now(UTC)
    with psycopg.connect(database_url) as conn:
        conn.execute("INSERT INTO core.tenants(tenant_id,tenant_name) VALUES(%s,%s)", (TENANT, SCOPE))
        for actor, roles in ((MANAGER, (Role.SITE_REVIEWER, Role.EXPANSION_USER)),
                             (APPROVER, (Role.SITE_REVIEWER, Role.EXPANSION_USER, Role.DATA_OWNER, Role.RECORDS_MANAGER)),
                             (OWNER, (Role.EXPANSION_USER,))):
            conn.execute("INSERT INTO identity.accounts(account_id,tenant_id,username,email,status,created_by) "
                         "VALUES(%s,%s,%s,%s,'active','isolated-browser-fixture')",
                         (actor, TENANT, actor, actor + "@example.invalid"))
            for role in roles:
                conn.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES(%s,%s,'isolated-browser-fixture')", (actor, role.value))
            conn.execute("INSERT INTO identity.account_scopes(account_id) VALUES(%s)", (actor,))
        workflow, calendar, retention = _workflow_row(), _calendar_row(), _audit_retention_row()
        workflow.parameters["assignee_role_bindings"]["site-reviewer"] = Role.SITE_REVIEWER.value
        calendar.parameters.update({"coverage_from": f"{now.year - 1}-01-01", "coverage_through": f"{now.year + 1}-12-31", "date_overrides": {}})
        for row in (workflow, calendar, retention):
            _insert_policy(conn, row, TENANT, APPROVER, now)


def run_native_identity_browser(database, ids, parsed, match_case, private_dir, monkeypatch, *, worm_sink_uri,
        manager_region_id=None, independent_reviewer=None):
    """Bridge existing native SQL fixtures to the existing browser composition.

    The parser/matcher and human accounts are actual owned preconditions. Login,
    JWT/session, HTTP commands, signed worker and readback remain their normal
    implementations. This creates no accepted command or READY receipt.
    """
    import json
    import os
    import re
    import secrets
    import signal
    import socket
    import subprocess
    import threading
    import time
    from dataclasses import replace
    from pathlib import Path
    from urllib.error import URLError
    from urllib.request import urlopen

    from apps.worker.oday_worker.main import ODayWorker
    from shared.infrastructure.persistence.factory import build_persistence

    root = Path(__file__).resolve().parents[2]
    assert re.fullmatch(r"oday_native_parser_p_[0-9a-f]{16}", database.dbname), database.dbname
    private_dir.mkdir(mode=0o700)
    password, signing, session_secret = (secrets.token_urlsafe(40) for _ in range(3))
    append_property = str(uuid4()) if independent_reviewer else None
    credentials = {"username": "synthetic-manager-" + ids["reviewer"], "password": password,
        "actor_id": ids["reviewer"], "tenant_id": ids["tenant"], "intake_id": parsed["intake_id"],
        "match_case_id": match_case["match_case_id"] if match_case else None, "manager_region_id": manager_region_id,
        "outside_intake_id": ids["intake"], "context_property_ids": [ids["target"], ids["split_target"]],
        "append_property_id": append_property,
        "independent_reviewer": {"actor_id": independent_reviewer, "username": "synthetic-manager-" + independent_reviewer,
            "password": password} if independent_reviewer else None}
    hash_script = "const a=require('argon2');a.hash(process.env.OWNED_BROWSER_PASSWORD,{type:a.argon2id,memoryCost:65536,timeCost:3,parallelism:1}).then(h=>process.stdout.write(h))"
    phc = subprocess.check_output(["node", "-e", hash_script], cwd=root,
        env={**os.environ, "OWNED_BROWSER_PASSWORD": password}, text=True)
    now = datetime.now(UTC)
    with database.connect() as connection:
        actual_region = connection.execute("SELECT region_id FROM intake.intakes WHERE tenant_id=%s AND intake_id=%s",
            (ids["tenant"], parsed["intake_id"])).fetchone()[0]
        assert (str(actual_region) if actual_region is not None else None) == manager_region_id
        if manager_region_id:
            assert connection.execute("SELECT region_id FROM intake.intakes WHERE tenant_id=%s AND intake_id=%s",
                (ids["tenant"], ids["intake"])).fetchone()[0] is None
        if match_case is None:
            assert independent_reviewer
            assert connection.execute("SELECT count(*) FROM identity.match_cases WHERE tenant_id=%s AND intake_id=%s",
                (ids["tenant"], parsed["intake_id"])).fetchone()[0] == 0
        if append_property:
            address = "Explicit synthetic additional root " + append_property
            connection.execute("INSERT INTO identity.properties(property_id,tenant_id,normalized_address,address_fingerprint,region_id) "
                "VALUES(%s,%s,%s,%s,%s)", (append_property, ids["tenant"], address, sha256(address.encode()).hexdigest(), manager_region_id))
        for account in [ids["reviewer"], *([independent_reviewer] if independent_reviewer else [])]:
            connection.execute("INSERT INTO identity.password_credentials(account_id,phc_hash) VALUES(%s,%s)", (account, phc))
            connection.execute("UPDATE identity.account_scopes SET modules='[\"listing\",\"operator_console\"]'::jsonb,region_ids=%s WHERE account_id=%s",
                (Jsonb([manager_region_id] if manager_region_id else []), account))
            connection.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES(%s,'expansion_user','isolated-browser-fixture')",
                (account,))
        connection.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES(%s,'records_manager','isolated-browser-fixture')",
            (ids["actor"],))
        rows = [replace(row, policy_version_id=row.policy_label + ":" + ids["tenant"], tenant_id=ids["tenant"],
            approved_by=ids["actor"]) for row in (_workflow_row(), _calendar_row(), _audit_retention_row())]
        rows[0].parameters["calendar_version_id"] = rows[1].policy_version_id
        rows[0].parameters["assignee_role_bindings"]["site-reviewer"] = Role.SITE_REVIEWER.value
        rows[1].parameters.update(coverage_from=f"{now.year-1}-01-01", coverage_through=f"{now.year+1}-12-31", date_overrides={})
        for row in rows:
            _insert_policy(connection, row, ids["tenant"], ids["actor"], now)

    def port():
        with socket.socket() as selected:
            selected.bind(("127.0.0.1", 0))
            return selected.getsockname()[1]

    api_port, web_port = port(), port()
    api_url, web_url = f"http://127.0.0.1:{api_port}", f"http://localhost:{web_port}"
    credentials["api_url"] = api_url
    scenario = private_dir / "native-scenario.json"
    scenario.write_text(json.dumps(credentials))
    scenario.chmod(0o600)
    common = {"ODAY_DATABASE_URL": database.url(), "ODP_IDENTITY_DATABASE_URL": database.url(),
        "ODP_AUTH_MODE": "local", "ODP_AUTH_LOCAL_AUDIENCES": "oday-plus",
        "ODP_IDENTITY_TOKEN_SIGNING_KEY": signing, "ODP_AUDIT_WORM_SINK_URI": worm_sink_uri,
        "OPERATOR_TENANT_ID": ids["tenant"], "MLFLOW_DISABLE_AGENT_HINT": "1"}
    for key, value in common.items():
        monkeypatch.setenv(key, value)
    api_env = {**os.environ, "ODP_SCORE_BROWSER_DATABASE_URL": database.url(),
        "ODP_SCORE_BROWSER_MODE": "isolated-synthetic", "ODP_SCORE_BROWSER_CONTROL_KEY": secrets.token_urlsafe(40),
        "ODP_PRODUCT_MODE": "poc", "ODP_PERSISTENCE": "postgresql"}
    web_env = {**os.environ, "ODP_PRODUCT_MODE": "production", "NEXT_PUBLIC_ODP_PRODUCT_MODE": "production",
        "ODP_WEB_SESSION_SECRET": session_secret, "ODP_WEB_BASE_URL": web_url, "ODP_API_BASE_URL": api_url,
        "ODP_API_SERVICE_AUDIENCE": api_url,
        "ODP_DATA_BINDING_MODE": "live", "NEXT_PUBLIC_ODP_DATA_BINDING_MODE": "live",
        "ODP_REQUIRE_LIVE_DATA": "true", "ODP_E2E_MODE": "false", "NEXT_PUBLIC_ODP_E2E_MODE": "false"}
    processes, streams, worker_errors = [], [], []
    stop = threading.Event()
    worker_bundle = None
    worker_thread = None

    def launch(command, environment, label, cwd=root):
        stream = (private_dir / (label + ".log")).open("w")
        streams.append(stream)
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream,
            stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(process)
        return process

    def ready(url, process):
        until = time.monotonic() + 90
        while time.monotonic() < until:
            assert process.poll() is None, "Owned browser server stopped: " + url
            try:
                with urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except (URLError, TimeoutError, OSError):
                pass
            time.sleep(0.1)
        raise AssertionError("Owned browser server not ready: " + url)

    def work():
        worker = ODayWorker(worker_bundle, worker_id="owned-native-browser-worker")
        while not stop.is_set():
            try:
                if not worker.run_once():
                    stop.wait(0.1)
            except Exception as error:
                worker_errors.append(type(error).__name__ + ": " + str(error))
                return

    try:
        api = launch([str(Path(os.sys.executable)), "-m", "uvicorn", "tests.support.intake_score_browser_server:app",
            "--host", "127.0.0.1", "--port", str(api_port)], api_env, "api")
        ready(api_url + "/_synthetic/health", api)
        web = launch(["npm", "run", "dev", "--workspace=@oday-plus/web", "--", "-p", str(web_port),
            "--hostname", "127.0.0.1"], web_env, "web")
        ready(web_url + "/login", web)
        worker_bundle = build_persistence(mode="postgresql")
        worker_thread = threading.Thread(target=work, daemon=True)
        worker_thread.start()
        browser_env = {**web_env, "OPSBOARD_PORT": str(web_port), "ODP_API_PORT": str(api_port),
            "ODP_PLAYWRIGHT_API_HEALTH_URL": api_url + "/_synthetic/health", "ODP_PLAYWRIGHT_REUSE_EXISTING": "1",
            "ODP_NATIVE_BROWSER_SCENARIO_FILE": str(scenario)}
        browser = launch(["node", "node_modules/@playwright/test/cli.js", "test",
            "tests/e2e/operator-assisted-listing-intake.spec.ts", "--grep", "canonical native:",
            "--workers=1", "--retries=0", "--trace=off", "--output", str(private_dir / "browser-results")], browser_env, "browser")
        browser.wait(timeout=240)
        browser_log = (private_dir / "browser.log").read_text()
        for value in (password, signing, session_secret, database.url()):
            browser_log = browser_log.replace(value, "[REDACTED]")
        assert not worker_errors, worker_errors
        assert browser.returncode == 0, browser_log[-12000:]
        with database.connect() as connection:
            sessions = connection.execute("SELECT count(*) FROM identity.sessions WHERE account_id=%s AND revoked_at IS NULL",
                (ids["reviewer"],)).fetchone()[0]
            result = connection.execute("SELECT processing_state,resolved_listing_id FROM intake.intakes WHERE intake_id=%s",
                (parsed["intake_id"],)).fetchone()
            assert sessions > 0 and result[0] == "READY" and result[1]
            regions = connection.execute("SELECT region_ids FROM identity.account_scopes WHERE account_id=%s",
                (ids["reviewer"],)).fetchone()[0]
            assert regions == ([manager_region_id] if manager_region_id else [])
            property_region = connection.execute("SELECT p.region_id FROM expansion.listings l "
                "JOIN identity.properties p ON p.tenant_id=l.tenant_id AND p.property_id=l.property_id "
                "WHERE l.tenant_id=%s AND l.listing_id=%s", (ids["tenant"], str(result[1]))).fetchone()[0]
            assert (str(property_region) if property_region is not None else None) == manager_region_id
            reviewer_sessions = 0
            if independent_reviewer:
                reviewer_sessions = connection.execute("SELECT count(*) FROM identity.sessions WHERE account_id=%s AND revoked_at IS NULL",
                    (independent_reviewer,)).fetchone()[0]
                assert reviewer_sessions > 0
                graph_decision = connection.execute("SELECT d.proposer_subject_id,d.reviewer_subject_id,d.status "
                    "FROM identity.match_decisions d JOIN identity.match_cases c ON c.match_case_id=d.match_case_id "
                    "WHERE c.tenant_id=%s AND c.intake_id=%s AND d.decision_type='MERGE'",
                    (ids["tenant"], parsed["intake_id"])).fetchone()
                assert graph_decision and tuple(map(str, graph_decision)) == (ids["reviewer"], independent_reviewer, "EXECUTED")
                candidates = connection.execute("SELECT confidence,evidence FROM identity.match_candidates "
                    "WHERE tenant_id=%s AND match_case_id=(SELECT match_case_id FROM identity.match_cases "
                    "WHERE tenant_id=%s AND intake_id=%s) ORDER BY rank",
                    (ids["tenant"], ids["tenant"], parsed["intake_id"])).fetchall()
                assert [str(row[0]) for row in candidates] == ["0.7500", "0.7500", "0.3750"]
                assert candidates[-1][1]["manual_context"] == {"case_version": 3, "added_by": ids["reviewer"]}
                assert connection.execute("SELECT count(*) FROM identity.match_cases WHERE tenant_id=%s AND intake_id=%s",
                    (ids["tenant"], parsed["intake_id"])).fetchone()[0] == 1
                assert connection.execute("SELECT count(*) FROM audit.audit_events WHERE tenant_id=%s "
                    "AND action='match.manual_context_added.v1'", (ids["tenant"],)).fetchone()[0] == 1
        return {"scope": SCOPE, "web_mode": "production", "api_auth": "actual-local-JWT-SQL-identity",
            "browser_returncode": browser.returncode, "active_real_sessions": sessions,
            "resolved_listing_id": str(result[1]), "worker_errors": worker_errors, "manager_region_id": manager_region_id,
            "independent_reviewer_id": independent_reviewer, "real_reviewer_sessions": reviewer_sessions,
            "manual_context_appended": bool(append_property),
            "private_artifacts": str(private_dir)}
    except BaseException:
        for label in ("api", "web", "browser"):
            log = private_dir / (label + ".log")
            if log.exists():
                tail = log.read_text()[-6000:]
                for value in (password, signing, session_secret, database.url()):
                    tail = tail.replace(value, "[REDACTED]")
                print(label + " diagnostics:\n" + tail)
        raise
    finally:
        stop.set()
        if worker_thread:
            worker_thread.join(timeout=10)
        if worker_bundle:
            worker_bundle.engine.close()
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        for stream in streams:
            stream.close()
        assert worker_thread is None or not worker_thread.is_alive(), "Owned browser worker did not stop"


def ready_scenario(database_url, bundle, client):
    scenario_id, listing_id, snapshot_id = str(uuid4()), str(uuid4()), str(uuid4())
    captured = datetime.now(UTC) - timedelta(minutes=10)
    source_id = "synthetic-browser-" + scenario_id
    listing = Listing(listing_id=listing_id, source_listing_id=scenario_id, source_id=source_id,
                      address_id=str(uuid4()), listing_status="watching", rent_amount=54000,
                      area_ping=22, floor="1F", frontage_m=5, confidence=0.9, snapshot_id=snapshot_id)
    for name, value in {"property_id": str(uuid4()), "listing_obs_id": str(uuid4()),
                        "prior_90d_cell_net_revenue": 1_800_000.0, "prior_90d_cell_transaction_count": 720,
                        "prior_90d_cell_store_count": 4, "feature_snapshot_time": captured,
                        "hard_rule_failures": []}.items():
        object.__setattr__(listing, name, value)
    address = AddressLocation(address_id=listing.address_id, raw_address="Synthetic browser measured address",
                              normalized_address="Synthetic browser measured address", latitude=25.033964,
                              longitude=121.564468, geocode_confidence=0.95, h3_res_9="8929a1d4d67ffff")
    bundle.listing_repository_for_tenant(TENANT).save_listing(listing, address, ListingDedupKey(
        source_id=source_id, source_listing_id=scenario_id, normalized_address=address.normalized_address,
        rent_amount=listing.rent_amount, area_ping=listing.area_ping))
    base = "/api/v1/operator/network-listings/intake"
    submitted = client.post(base + "/submit", json={"url": "https://rent.591.com.tw/browser-" + scenario_id}, headers=headers())
    assert submitted.status_code == 200, submitted.text
    iid = submitted.json()["id"]
    corrected = client.post(f"{base}/{iid}/correct", json={"fields": {"floor": "1F"}, "reason": SCOPE,
                            "riskSummary": "Explicit synthetic measured input", "riskAcknowledged": True}, headers=headers())
    assert corrected.status_code == 200, corrected.text
    decided = client.post(f"{base}/{iid}/decide", json={"action": "revise", "targetListingId": listing_id,
                          "reason": SCOPE, "riskSummary": "Synthetic local integration", "riskAcknowledged": True}, headers=headers(APPROVER))
    assert decided.status_code == 200, decided.text
    assert decided.json()["stage"] == "READY"
    store = bundle.assisted_intake_store
    store.refresh(TENANT)
    with psycopg.connect(database_url) as conn:
        conn.execute("INSERT INTO intake.source_registry(source_id,display_name,allowed_hosts,canonicalization_rule_version,"
                     "retrieval_mode,policy_owner_subject_id,legal_approval_ref,license_approval_ref,review_expires_at,kill_switch,production_enabled) "
                     "VALUES(%s,%s,ARRAY['example.invalid'],'synthetic-v1','ASSISTED_ENTRY_ONLY',%s,%s,%s,%s,false,true)",
                     (source_id, SCOPE, APPROVER, "synthetic-legal:" + scenario_id, "synthetic-license:" + scenario_id, datetime.now(UTC) + timedelta(days=1)))
        conn.execute("INSERT INTO intake.intakes(intake_id,tenant_id,submitter_subject_id,intake_method,source_id,source_policy_state,processing_state,correlation_id) "
                     "VALUES(%s,%s,%s,'MANUAL',%s,'ASSISTED_ENTRY_ONLY','READY',%s)",
                     (iid, TENANT, MANAGER, source_id, store.intakes[iid]["correlation_id"]))
        conn.execute("INSERT INTO intake.source_snapshots(source_snapshot_id,tenant_id,intake_id,source_id,raw_object_uri,content_sha256,media_type,byte_length,"
                     "captured_at,observed_at,capture_method,retention_class,encryption_key_ref) "
                     "VALUES(%s,%s,%s,%s,%s,%s,'application/json',2,%s,%s,'MANUAL_ENTRY','BUSINESS_5Y','synthetic-local-key')",
                     (snapshot_id, TENANT, iid, source_id, "gs://synthetic-browser-no-cloud-claim/" + snapshot_id, sha256(b"{}").hexdigest(), captured, captured))
    with store.transaction():
        store.refresh(TENANT)
        intake = store.intakes[iid]
        intake.update({"source_id": source_id, "source_policy_version": 1, "source_snapshot_id": snapshot_id,
                       "source_snapshot_version": 1, "intake_method": "MANUAL", "parser_release_id": None, "parser_run_id": None})
        intake["version"] += 1
        store.flush()
    return {"scope": SCOPE, "scenario_id": scenario_id, "intake_id": iid, "listing_id": listing_id,
            "tenant_id": TENANT, "proposer_id": MANAGER, "reviewer_id": APPROVER}


class BrowserModelRuntime:
    """Explicit inference injection; never selected by the production factory."""

    def __init__(self, *, observe):
        self.observe = observe

    def infer(self, **kwargs):
        self.observe(kwargs)
        model = ModelVersion(model_name="sitescore", version="synthetic-browser-v1",
                             artifact_uri="gs://synthetic-browser-no-cloud-claim/model.zip",
                             dataset_snapshot_id="synthetic-browser-training", feature_schema_version=SITESCORE_FEATURE_VERSION,
                             label_version="synthetic-label", metrics={"mae": 8200.0}, stage=ModelStage.PRODUCTION,
                             aliases=frozenset({ModelAlias.PRODUCTION}), run_id="synthetic-browser-mlflow",
                             git_sha="synthetic", approved_by="synthetic-test-only", approved_at=datetime.now(UTC))
        binding = ModelBinding.from_model_version("sitescore", model, artifact_sha256="sha256:" + "a" * 64,
                                                 engine="lightgbm.LGBMRegressor", mlflow_run_id="synthetic-browser-mlflow")
        n = len(kwargs["rows"])
        return ModelInferenceResult(binding=binding, point=(315000.0,) * n, lower=(270000.0,) * n,
                                    upper=(360000.0,) * n, engine="lightgbm.LGBMRegressor",
                                    artifact_sha256="sha256:" + "a" * 64,
                                    model_metadata={"output_transform": dict(SITESCORE_OUTPUT_TRANSFORM)})
