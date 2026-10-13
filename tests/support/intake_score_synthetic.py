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
            conn.execute("INSERT INTO workflow.decision_policies(policy_version_id,policy_label,policy_id,policy_version,policy_kind,"
                         "tenant_id,effective_from,owner_role,approved_by,approved_at,input_contract,output_contract,change_reason,parameters,declared_inputs) "
                         "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                         (row.policy_version_id, row.policy_label, row.policy_id, row.policy_version, row.policy_kind,
                          TENANT, now - timedelta(days=1), row.owner_role, APPROVER, now - timedelta(days=1),
                          "synthetic-input", "synthetic-output", SCOPE, Jsonb(dict(row.parameters)), list(row.declared_inputs)))


def ready_scenario(database_url, bundle, client):
    scenario_id, listing_id, snapshot_id = str(uuid4()), str(uuid4()), str(uuid4())
    captured = datetime.now(UTC) - timedelta(minutes=10)
    source_id = "synthetic-browser-" + scenario_id
    listing = Listing(listing_id=listing_id, source_listing_id=scenario_id, source_id=source_id,
                      address_id=str(uuid4()), listing_status="watching", rent_amount=54000,
                      area_ping=22, floor="1F", frontage_m=5, confidence=0.9, snapshot_id=snapshot_id)
    for name, value in {"property_id": str(uuid4()), "listing_obs_id": str(uuid4()),
                        "prior_90d_cell_net_revenue": 1_800_000.0, "prior_90d_cell_transaction_count": 720,
                        "prior_90d_cell_store_count": 4, "feature_snapshot_time": captured}.items():
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
