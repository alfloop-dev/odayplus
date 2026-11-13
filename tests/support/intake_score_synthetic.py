"""Explicit synthetic PostgreSQL inputs for browser queue integration.

These are test records, not real property/source/model admission or persona UAT.
No command receipt, candidate, job state or result is manufactured by this seed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal
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
from shared.infrastructure.persistence.operator_intake_workflow import OperatorIntakeWorkflowRepository
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
RetainedBrowserHistory = Literal["assisted-entry-only", "possible-match", "retrieval-timeout", "revision"]


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
        manager_region_id=None, independent_reviewer=None, append_receipt_probe=True, url_manual_capture=None):
    """Bridge existing native SQL fixtures to the existing browser composition.

    The parser/matcher and human accounts are actual owned preconditions. Login,
    JWT/session, HTTP commands, signed worker and readback remain their normal
    implementations. This creates no accepted command or READY receipt.
    Only the metadata transport is emulated locally; no Cloud Run IAM, Google
    token validation or production rollout is qualified by this SQL/browser case.
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
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from urllib.error import HTTPError, URLError
    from urllib.parse import parse_qs, urlencode, urlsplit
    from urllib.request import Request, urlopen

    from apps.worker.oday_worker.main import ODayWorker
    from modules.opsboard.auth.jwt import SigningKey, encode_compact_jwt
    from shared.infrastructure.persistence.factory import build_persistence
    from shared.jobs.queue import JobStatus

    root = Path(__file__).resolve().parents[2]
    assert re.fullmatch(r"oday_native_parser_p_[0-9a-f]{16}", database.dbname), database.dbname
    private_dir.mkdir(mode=0o700)
    password, signing, session_secret, metadata_secret = (secrets.token_urlsafe(40) for _ in range(4))
    append_property = str(uuid4()) if independent_reviewer else None
    credentials = {"username": "synthetic-manager-" + ids["reviewer"], "password": password,
        "actor_id": ids["reviewer"], "tenant_id": ids["tenant"], "intake_id": parsed["intake_id"],
        "match_case_id": match_case["match_case_id"] if match_case else None, "manager_region_id": manager_region_id,
        "outside_intake_id": ids["intake"], "context_property_ids": [ids["target"], ids["split_target"]],
        "append_property_id": append_property, "append_receipt_probe": append_receipt_probe,
        "url_manual": None,
        "independent_reviewer": {"actor_id": independent_reviewer, "username": "synthetic-manager-" + independent_reviewer,
            "password": password} if independent_reviewer else None}
    hash_script = "const a=require('argon2');a.hash(process.env.OWNED_BROWSER_PASSWORD,{type:a.argon2id,memoryCost:65536,timeCost:3,parallelism:1}).then(h=>process.stdout.write(h))"
    phc = subprocess.check_output(["node", "-e", hash_script], cwd=root,
        env={**os.environ, "OWNED_BROWSER_PASSWORD": password}, text=True)
    now = datetime.now(UTC)
    with database.connect() as connection:
        if url_manual_capture is not None:
            original_url, source_id = connection.execute("SELECT original_url,source_id FROM intake.intakes "
                "WHERE tenant_id=%s AND intake_id=%s", (ids["tenant"], parsed["intake_id"])).fetchone()
            assert source_id.startswith("native-parser-") and source_id.endswith(".manual")
            credentials["url_manual"] = {"original_url": original_url.rsplit("/", 1)[0] + "/BrowserFreshListing-" + uuid4().hex,
                "fields": {"address": "Explicit synthetic browser manual address 29", "rent": 43210, "areaPing": 42.5}}
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
    metadata_token = encode_compact_jwt({
        "iss": "urn:odp:isolated-browser:metadata-emulator", "sub": "owned-native-browser-web",
        "aud": api_url, "iat": int(time.time()), "exp": int(time.time()) + 600,
    }, SigningKey(kid="owned-browser-metadata", algorithm="HS256", secret=metadata_secret.encode()))
    metadata_counts = {"accepted": 0, "rejected": 0, "other": 0}
    metadata_lock = threading.Lock()

    class OwnedMetadata(BaseHTTPRequestHandler):
        def do_GET(self):
            target = urlsplit(self.path)
            identity_path = target.path == "/computeMetadata/v1/instance/service-accounts/default/identity"
            accepted = (identity_path
                        and parse_qs(target.query, keep_blank_values=True) == {"audience": [api_url], "format": ["full"]}
                        and self.headers.get("metadata-flavor") == "Google")
            with metadata_lock:
                bucket = ("accepted" if accepted else "rejected") if identity_path else "other"
                metadata_counts[bucket] += 1
            body = metadata_token.encode() if accepted else b"Owned metadata request rejected"
            self.send_response(200 if accepted else 403)
            self.send_header("metadata-flavor", "Google")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

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
    metadata_server = None
    metadata_thread = None
    url_admissions = []

    if url_manual_capture is not None:
        from shared.infrastructure.persistence.factory import PersistenceBundle

        original_manual_factory = PersistenceBundle.canonical_intake_assisted_entry

        def manual_factory(bundle, **kwargs):
            kwargs.setdefault("snapshot_transport_factory", url_manual_capture.factory)
            return original_manual_factory(bundle, **kwargs)

        monkeypatch.setattr(PersistenceBundle, "canonical_intake_assisted_entry", manual_factory)

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
        # Drive only this new browser Intake's real committed events through
        # the existing four admission owners. Do not run the source-wide
        # parser/matcher scheduler against the original CASE_CONTEXT Intake.
        admissions = [worker_bundle.canonical_intake_start, worker_bundle.canonical_intake_identity,
            worker_bundle.canonical_intake_source_policy, worker_bundle.canonical_intake_assisted_entry]

        def admit_browser_event():
            if credentials["url_manual"] is None or len(url_admissions) == len(admissions):
                return
            with database.connect() as connection:
                row = connection.execute("SELECT intake_id FROM intake.intakes WHERE tenant_id=%s AND original_url=%s",
                    (ids["tenant"], credentials["url_manual"]["original_url"])).fetchall()
                if not row:
                    return
                assert len(row) == 1
                intake_id = str(row[0][0])
                if not url_admissions:
                    events = connection.execute("SELECT event_id FROM workflow.outbox_events WHERE tenant_id=%s "
                        "AND aggregate_type='intake' AND aggregate_id=%s AND event_type='intake.submitted'",
                        (ids["tenant"], intake_id)).fetchall()
                else:
                    previous = url_admissions[-1]
                    if worker_bundle.job_queue.get(previous["job_id"]).status != JobStatus.SUCCEEDED:
                        return
                    result = connection.execute("SELECT result FROM workflow.jobs WHERE tenant_id=%s AND job_id=%s",
                        (ids["tenant"], previous["job_id"])).fetchone()[0]
                    assert result["intake_id"] == intake_id
                    if len(url_admissions) < 3:
                        events = [(result["outbox_event_id"],)]
                    else:
                        assert result["processing_state"] == "AWAITING_ASSISTED_ENTRY"
                        events = connection.execute("SELECT event_id FROM workflow.outbox_events WHERE tenant_id=%s "
                            "AND aggregate_type='intake_assisted_entry' AND event_type='intake.assisted_entry_requested' "
                            "AND payload->'payload'->>'intake_id'=%s", (ids["tenant"], intake_id)).fetchall()
                        if not events:
                            return
                assert len(events) == 1
            receipt = admissions[len(url_admissions)]().admit_event(ids["tenant"], str(events[0][0]))
            assert receipt["intake_id"] == intake_id
            url_admissions.append(receipt)

        while not stop.is_set():
            try:
                admit_browser_event()
                if not worker.run_once():
                    stop.wait(0.1)
            except Exception as error:
                worker_errors.append(type(error).__name__ + ": " + str(error))
                return

    try:
        metadata_server = ThreadingHTTPServer(("127.0.0.1", 0), OwnedMetadata)
        metadata_thread = threading.Thread(target=metadata_server.serve_forever, daemon=True)
        metadata_thread.start()
        web_env["GCE_METADATA_HOST"] = f"127.0.0.1:{metadata_server.server_port}"
        metadata_url = (f"http://{web_env['GCE_METADATA_HOST']}/computeMetadata/v1/instance/"
                        "service-accounts/default/identity")
        for audience, headers in ((api_url, {}), ("urn:wrong-owned-audience", {"metadata-flavor": "Google"})):
            try:
                with urlopen(Request(metadata_url + "?" + urlencode({"audience": audience, "format": "full"}),
                                     headers=headers), timeout=2):
                    raise AssertionError("Owned metadata accepted an invalid request")
            except HTTPError as error:
                assert error.code == 403
        api = launch([str(Path(os.sys.executable)), "-m", "uvicorn", "tests.support.intake_score_browser_server:app",
            "--host", "127.0.0.1", "--port", str(api_port)], api_env, "api")
        ready(api_url + "/_synthetic/health", api)
        # The transport fixture's issuer/key cannot authenticate an API user.
        try:
            with urlopen(Request(api_url + "/api/v1/canonical-intakes",
                                 headers={"authorization": "Bearer " + metadata_token}), timeout=2):
                raise AssertionError("Metadata fixture acquired end-user authority")
        except HTTPError as error:
            assert error.code == 401
        web = launch(["npm", "run", "start", "--workspace=@oday-plus/web", "--", "-p", str(web_port),
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
        for value in (password, signing, session_secret, metadata_secret, metadata_token, database.url()):
            browser_log = browser_log.replace(value, "[REDACTED]")
        assert not worker_errors, worker_errors
        assert browser.returncode == 0, browser_log[-12000:]
        assert metadata_counts["accepted"] > 0 and metadata_counts["rejected"] == 2
        reload = json.loads((private_dir / "native-receipt-reload.json").read_text())
        assert (reload["actor_id"], reload["tenant_id"]) == (ids["reviewer"], ids["tenant"])
        assert reload["postCount"] == 1 and reload["whole_page_reload"] and reload["recovered_by_exact_GET"]
        assert reload["post_keys_seen"] == [reload["key"]]
        assert reload["storage_accessor_denial_gated"] and reload["storage_reference_preserved"]
        assert reload["storage_denial_uncaught_errors"] == 0 and reload["storage_recovery_POSTs"] == 0
        original = worker_bundle.assisted_intake_store.read_command_replay(ids["tenant"], ids["reviewer"],
            "proposeCanonicalIdentity", reload["key"], reload["match_case_id"])
        assert original is not None and original[1] == reload["receipt"] and original[2] == 201
        recovered = reload["other_command_recoveries"]
        expected_variants = {"case-create", "case-append", "identity-review"} if independent_reviewer else set()
        assert {entry["variant"] for entry in recovered} == expected_variants
        assert len(recovered) == len(expected_variants)
        submissions = reload["submission_command_recoveries"]
        assert [entry["variant"] for entry in submissions] == (["url-submit", "manual-entry"] if credentials["url_manual"] else [])
        for entry in submissions:
            assert (entry["actor_id"], entry["tenant_id"]) == (ids["reviewer"], ids["tenant"])
            assert entry["post_keys_seen"] == [entry["key"]] and entry["postCount"] == 1
            assert entry["whole_page_reload"] and entry["recovered_by_exact_GET"] and not entry["same_provider_receipt_before_reload"]
        if submissions:
            from apps.api.app.routes.canonical_intakes import (
                CanonicalIntakeAssistedEntryRequestReceipt,
            )
            from modules.listing.application.native_intake_assisted_entry_request import (
                NativeAssistedEntryRequestOwner,
            )
            from modules.listing.application.native_intake_pending_commands import (
                NativeIntakeCommandBudget,
            )
            from modules.listing.application.native_intake_requester_authority import (
                _current_native_requester_profile,
            )

            submitted, manual = submissions
            assert submitted["receipt"]["intake_id"] == submitted["intake_id"] == manual["intake_id"] == manual["receipt"]["intake_id"]
            saved = worker_bundle.assisted_intake_store.read_command_replay(
                ids["tenant"], ids["reviewer"], "submitCanonicalIntake", submitted["key"])
            assert saved is not None and saved[1] == submitted["receipt"] and saved[2] == 201
            owner = NativeAssistedEntryRequestOwner(worker_bundle.canonical_identity_graph())
            with owner.graph.transaction(ids["tenant"]):
                before = NativeIntakeCommandBudget(owner.source.source)
                row = owner.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?",
                    (ids["tenant"], manual["intake_id"]))
                principal = _current_native_requester_profile(owner.engine, ids["reviewer"], row, before=before)
                readback = owner.read_receipt(principal, manual["intake_id"], manual["key"], before=before)
                assert readback is not None and set(readback) == set(manual["receipt"])
                assert CanonicalIntakeAssistedEntryRequestReceipt.model_validate(readback).model_dump(mode="json") == manual["receipt"]
            assert len(url_admissions) == 4 and len({entry["job_id"] for entry in url_admissions}) == 4
            assert all(worker_bundle.job_queue.get(entry["job_id"]).status == JobStatus.SUCCEEDED for entry in url_admissions)
        for entry in recovered:
            is_review = entry["variant"] == "identity-review"
            actor = independent_reviewer if is_review else ids["reviewer"]
            operation = "reviewCanonicalIdentity" if is_review else "createCanonicalMatchCase"
            resource = entry["receipt"]["decision_id"] if is_review else parsed["intake_id"]
            assert (entry["actor_id"], entry["tenant_id"], entry["operation"], entry["resource_id"]) == (
                actor, ids["tenant"], operation, resource)
            assert entry["postCount"] == 1 and entry["whole_page_reload"] and entry["recovered_by_exact_GET"]
            assert entry["same_provider_receipt_before_reload"] is (entry["variant"] == "case-append" and append_receipt_probe)
            expected_keys = ([entry["key"], next(row["key"] for row in recovered if row["variant"] == "case-append")]
                if entry["variant"] == "case-create" else [entry["key"]])
            assert entry["post_keys_seen"] == expected_keys
            saved = worker_bundle.assisted_intake_store.read_command_replay(
                ids["tenant"], actor, operation, entry["key"], resource)
            assert saved is not None and saved[1] == entry["receipt"] and saved[2] == (200 if is_review else 201)
        with database.connect() as connection:
            if submissions:
                new_intake = submitted["intake_id"]
                assert connection.execute("SELECT count(*) FROM intake.intakes WHERE tenant_id=%s AND original_url=%s",
                    (ids["tenant"], credentials["url_manual"]["original_url"])).fetchone()[0] == 1
                assert connection.execute("SELECT processing_state FROM intake.intakes WHERE tenant_id=%s AND intake_id=%s",
                    (ids["tenant"], new_intake)).fetchone()[0] == "PARSING"
                jobs = connection.execute("SELECT job_id,status FROM workflow.jobs WHERE tenant_id=%s "
                    "AND aggregate_id IN (%s,%s) AND job_type IN "
                    "('native-intake-start','native-intake-identity','native-intake-source-policy','native-intake-assisted-entry')",
                    (ids["tenant"], new_intake, manual["receipt"]["request_id"])).fetchall()
                assert {str(row[0]) for row in jobs} == {entry["job_id"] for entry in url_admissions}
                assert all(row[1] == "SUCCEEDED" for row in jobs)
                assert connection.execute("SELECT count(*) FROM workflow.outbox_events WHERE tenant_id=%s "
                    "AND aggregate_type='intake' AND aggregate_id=%s AND event_type='intake.submitted'",
                    (ids["tenant"], new_intake)).fetchone()[0] == 1
                assert connection.execute("SELECT count(*) FROM workflow.outbox_events WHERE tenant_id=%s "
                    "AND aggregate_type='intake_assisted_entry' AND event_type='intake.assisted_entry_requested' "
                    "AND payload->'payload'->>'intake_id'=%s", (ids["tenant"], new_intake)).fetchone()[0] == 1
                for entry, action in [(submitted, "intake.create"), (manual, "intake.assisted_entry_requested.v1")]:
                    audits = connection.execute("SELECT audit_event_id FROM audit.audit_events WHERE tenant_id=%s "
                        "AND resource_type='intake' AND resource_id=%s AND actor_subject_id=%s AND action=%s",
                        (ids["tenant"], new_intake, ids["reviewer"], action)).fetchall()
                    assert [str(row[0]) for row in audits] == [entry["receipt"]["audit_event_id"]]
                snapshots = connection.execute("SELECT source_snapshot_id,capture_method FROM intake.source_snapshots "
                    "WHERE tenant_id=%s AND intake_id=%s", (ids["tenant"], new_intake)).fetchall()
                assert len(snapshots) == 1 and snapshots[0][1] == "MANUAL_ENTRY"
                proposals = connection.execute("SELECT correction_id,field_path,corrected_value,status,source_snapshot_id,proposed_by "
                    "FROM intake.human_corrections WHERE tenant_id=%s AND intake_id=%s", (ids["tenant"], new_intake)).fetchall()
                assert sorted(str(row[0]) for row in proposals) == sorted(manual["receipt"]["correction_ids"])
                assert {row[1]: row[2] for row in proposals} == credentials["url_manual"]["fields"]
                assert all(row[3] == "PROPOSED" and row[4] == snapshots[0][0] and str(row[5]) == ids["reviewer"] for row in proposals)
            for entry in recovered:
                assert connection.execute("SELECT count(*) FROM audit.audit_events "
                    "WHERE tenant_id=%s AND audit_event_id=%s",
                    (ids["tenant"], entry["receipt"]["audit_event_id"])).fetchone()[0] == 1
                if entry["variant"] == "identity-review":
                    assert entry["receipt"]["review_audit_event_id"] is not None
                    assert connection.execute("SELECT count(*) FROM audit.audit_events "
                        "WHERE tenant_id=%s AND audit_event_id=%s",
                        (ids["tenant"], entry["receipt"]["review_audit_event_id"])).fetchone()[0] == 1
                    jobs = connection.execute("SELECT job_id FROM workflow.jobs WHERE tenant_id=%s "
                        "AND job_type='identity-execution' AND aggregate_type='identity_decision' AND aggregate_id=%s",
                        (ids["tenant"], entry["receipt"]["decision_id"])).fetchall()
                    assert [str(row[0]) for row in jobs] == [entry["receipt"]["job_id"]]
                    reviews = connection.execute("SELECT audit_event_id FROM audit.audit_events WHERE tenant_id=%s "
                        "AND resource_type='identity_decision' AND resource_id=%s AND actor_subject_id=%s "
                        "AND service_principal IS NULL AND action='identity.decision_approved.v1' AND result='SUCCEEDED'",
                        (ids["tenant"], entry["receipt"]["decision_id"], independent_reviewer)).fetchall()
                    assert [str(row[0]) for row in reviews] == [entry["receipt"]["review_audit_event_id"]]
                else:
                    for candidate_id in entry["receipt"]["candidate_ids"]:
                        assert connection.execute("SELECT count(*) FROM identity.match_candidates "
                            "WHERE tenant_id=%s AND match_case_id=%s AND match_candidate_id=%s",
                            (ids["tenant"], entry["receipt"]["match_case_id"], candidate_id)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM identity.match_decisions "
                "WHERE tenant_id=%s AND match_decision_id=%s AND proposer_subject_id=%s",
                (ids["tenant"], reload["receipt"]["decision_id"], ids["reviewer"])).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM workflow.jobs WHERE tenant_id=%s AND job_id=%s",
                (ids["tenant"], reload["receipt"]["job_id"])).fetchone()[0] == 1
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
            "service_identity_transport": "isolated-loopback-metadata-emulator", "cloud_run_iam_verified": False,
            "metadata_requests": dict(metadata_counts),
            "browser_returncode": browser.returncode, "active_real_sessions": sessions,
            "committed_response_lost_whole_reload": reload,
            "resolved_listing_id": str(result[1]), "worker_errors": worker_errors, "manager_region_id": manager_region_id,
            "independent_reviewer_id": independent_reviewer, "real_reviewer_sessions": reviewer_sessions,
            "manual_context_appended": bool(append_property),
            "new_url_manual_admissions": url_admissions,
            "private_artifacts": str(private_dir)}
    except BaseException:
        for label in ("api", "web", "browser"):
            log = private_dir / (label + ".log")
            if log.exists():
                tail = log.read_text()[-6000:]
                for value in (password, signing, session_secret, metadata_secret, metadata_token, database.url()):
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
        if metadata_server:
            if metadata_thread and metadata_thread.is_alive():
                metadata_server.shutdown()
            metadata_server.server_close()
        if metadata_thread and metadata_thread.is_alive():
            metadata_thread.join(timeout=5)
        assert worker_thread is None or not worker_thread.is_alive(), "Owned browser worker did not stop"
        assert metadata_thread is None or not metadata_thread.is_alive(), "Owned metadata transport did not stop"


def ready_scenario(database_url, bundle, client, *,
        retained_history: RetainedBrowserHistory | None = None):
    # The no-body control retains its original ready scenario. Explicit history
    # variants stop before the original UI correction/decision/recovery commands.
    # These fixtures attest neither fresh source policy nor successful retrieval.
    if retained_history not in (None, "assisted-entry-only", "possible-match", "retrieval-timeout", "revision"):
        raise ValueError("Unknown retained browser history")
    scenario_id = str(uuid4())
    source_id = "synthetic-browser-" + scenario_id
    # Explicit retained Operator history, not a fresh HTTP command or native
    # submission. The existing bridge initializes its workflow in the same SQL
    # transaction; correction and resolution below remain actual API commands.
    repository = OperatorIntakeWorkflowRepository(
        bundle.assisted_intake_store, bundle.operator_intake_repository_for_tenant(TENANT),
        TENANT, bundle.assisted_intake_workflow_policy,
    )
    iid, correlation_id = str(uuid4()), str(uuid4())
    submitted_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    url = "https://rent.591.com.tw/browser-" + scenario_id
    history = {}
    listing_id = None
    target_seed = None
    if retained_history in {"possible-match", "revision"}:
        from modules.external_data.application.assisted_intake import (
            RETRIEVAL_CORPUS,
            MatchResult,
            MatchSignal,
            parse_snapshot,
        )

        # Reuse the existing explicit synthetic archive's field layout, without
        # calling retrieve(), storing a raw capture or producing a parser run.
        # The comparison target is an actual tenant Listing, not a UI stub.
        listing_id = str(uuid4())
        listing = Listing(listing_id=listing_id, source_listing_id="retained-target-" + scenario_id,
            source_id=source_id, address_id=str(uuid4()), listing_status="watching",
            rent_amount=53000, area_ping=22, floor="1F")
        address = AddressLocation(address_id=listing.address_id,
            raw_address="新北市板橋區府中路 52 號 1F", normalized_address="新北市板橋區府中路 52 號 1F")
        target_seed = (listing, address, ListingDedupKey(
            source_id=source_id, source_listing_id=listing.source_listing_id,
            normalized_address=address.normalized_address, rent_amount=53000, area_ping=22))
        revision = retained_history == "revision"
        history = {
            "stage": "READY" if revision else "NEEDS_REVIEW",
            "parsedFields": parse_snapshot(RETRIEVAL_CORPUS["https://www.synthetic.example/detail-88520242.html"
                if revision else "https://www.synthetic.example/detail-99310418.html"]),
            "matchResult": MatchResult("REVISION" if revision else "POSSIBLE_MATCH", 0.95 if revision else 0.55, listing_id, (
                MatchSignal("normalizedAddress", "正規化地址", True, "Explicit retained synthetic comparison"),
                MatchSignal("rent", "租金", False, "Retained commercial revision compared with actual target") if revision else
                MatchSignal("floor", "樓層", False, "Retained 2F submission compared with actual 1F target"),
            ), "Explicit retained synthetic match history; no retrieval or parser run is attested.").to_dict(),
        }
    elif retained_history == "retrieval-timeout":
        from modules.external_data.application.assisted_intake import RETRIEVAL_CORPUS

        failure = RETRIEVAL_CORPUS["https://www.synthetic.example/detail-50000001.html"].failure
        assert failure is not None
        history = {"stage": "FAILED", "failure": failure.to_dict()}
    with repository.transaction():
        if target_seed:
            bundle.listing_repository_for_tenant(TENANT).save_listing(*target_seed)
        repository.save_intake({
            "id": iid, "tenantId": TENANT, "intakeMethod": "URL",
            "originalUrl": url, "canonicalUrl": url, "heatZoneId": None,
            "submitter": MANAGER, "submittedBy": MANAGER, "owner": MANAGER,
            "submittedAt": submitted_at, "updatedAt": submitted_at, "lastObservedAt": None,
            "stage": "AWAITING_ASSISTED_ENTRY", "version": 1,
            "sourceId": source_id, "policy": "ASSISTED_ENTRY_ONLY",
            "policyLabel": "僅人工補錄", "policyReason": SCOPE,
            "correlationId": correlation_id, "parsedFields": {}, "matchResult": None,
            "rawSnapshot": None, "snapshotId": None, "capturedAt": None,
            "parserVersion": None, "auditEvents": [], **history,
        })
    base = "/api/v1/operator/network-listings/intake"
    retained = client.get(f"{base}/{iid}", headers=headers())
    assert retained.status_code == 200, retained.text
    initial = retained.json()
    assert initial["stage"] == history.get("stage", "AWAITING_ASSISTED_ENTRY")
    assert initial["assignmentId"] and initial["slaInstanceId"]
    case = {"scope": SCOPE, "scenario_id": scenario_id, "intake_id": iid,
            "listing_id": listing_id, "original_url": url,
            "tenant_id": TENANT, "proposer_id": MANAGER, "reviewer_id": APPROVER}
    if retained_history is not None:
        return case

    listing_id, snapshot_id = str(uuid4()), str(uuid4())
    captured = datetime.now(UTC) - timedelta(minutes=10)
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
    corrected = client.post(f"{base}/{iid}/correct", json={"fields": {"floor": "1F"}, "reason": SCOPE,
                            "riskSummary": "Explicit synthetic measured input", "riskAcknowledged": True}, headers=headers())
    assert corrected.status_code == 200, corrected.text
    decided = client.post(f"{base}/{iid}/decide", json={"action": "revise", "targetListingId": listing_id,
                          "reason": SCOPE, "riskSummary": "Synthetic local integration", "riskAcknowledged": True},
                          headers={**headers(APPROVER), "If-Match": f'W/"{corrected.json()["version"]}"'})
    assert decided.status_code == 200, decided.text
    resolved = decided.json()
    assert resolved["stage"] == "READY" and resolved["version"] == 3
    assert resolved["matchResult"]["targetListingId"] == listing_id
    assert resolved["submittedBy"] == MANAGER
    assert resolved["assignmentId"] == initial["assignmentId"]
    assert resolved["slaInstanceId"] == initial["slaInstanceId"]
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
    return {**case, "listing_id": listing_id}


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
