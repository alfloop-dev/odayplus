"""Isolated PostgreSQL browser composition using the actual password login.

Only explicit loopback/owned synthetic configuration may import this module.
Normal create_app, signed local identity, durable account/session stores and
product authorization remain unchanged; no production admission/UAT claim.
"""
import hmac
import os
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, Header, HTTPException
from psycopg.conninfo import conninfo_to_dict

from modules.heatzone.domain.scoring import HeatZoneScoreResult, HeatZoneState
from modules.heatzone.workers import HeatZoneBatchScoreResult
from modules.listing.domain.models import CandidateSiteDraft, ListingDedupKey
from modules.opsboard.application.network_listings import NetworkListingService
from shared.auth import DataClassification, Role, Scope
from shared.domain.models import AddressLocation, CandidateSite, Listing
from shared.identity.credential_service import CredentialService
from shared.identity.store import Account
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.factory import build_persistence
from shared.infrastructure.persistence.operator_domains import DurableOperatorDomainStateRepository
from tests.intake_registry_fixture import MANAGER, TENANT
from tests.support.intake_score_synthetic import prepare_database

SCOPE = "ISOLATED_SYNTHETIC_AREA_DRAFT_PASSWORD_PG_NO_PRODUCTION_CLAIM"
PASSWORD = "Synthetic!Area-Draft!2026"
_database = os.environ.get("ODP_SCORE_BROWSER_DATABASE_URL", "")
_control = os.environ.get("ODP_SCORE_BROWSER_CONTROL_KEY", "")
parsed = conninfo_to_dict(_database)
if (os.environ.get("ODP_SCORE_BROWSER_MODE") != "isolated-synthetic"
        or not parsed.get("dbname", "").startswith("odp_score_browser_")
        or parsed.get("host") != "127.0.0.1" or len(_control) < 32
        or os.environ.get("ODP_PRODUCT_MODE") != "poc"
        or not os.environ.get("ODP_IDENTITY_TOKEN_SIGNING_KEY")):
    raise RuntimeError("Dedicated owned loopback password/PG browser inputs required")
os.environ["ODAY_DATABASE_URL"] = _database
os.environ["ODP_REQUIRE_LIVE_DATA"] = "true"
with psycopg.connect(_database) as conn:
    initialized = conn.execute("SELECT to_regclass('workflow.decision_policies')").fetchone()[0]
if initialized is None:
    prepare_database(_database)
os.environ["ODP_PERSISTENCE"] = "postgresql"
bundle = build_persistence(mode="postgresql")
_foreign_tenant, _foreign_actor = str(uuid4()), str(uuid4())
_viewer_actor = str(uuid4())
with psycopg.connect(_database) as conn:
    conn.execute("INSERT INTO core.tenants(tenant_id,tenant_name) VALUES(%s,%s)", (_foreign_tenant, SCOPE))
for actor, tenant, roles in ((MANAGER, TENANT, [Role.EXPANSION_USER, Role.SITE_REVIEWER]),
                             (_foreign_actor, _foreign_tenant, [Role.EXPANSION_USER]),
                             (_viewer_actor, TENANT, [Role.AUDITOR])):
    bundle.identity_store.save_account(Account(account_id=UUID(actor),
        tenant_id=UUID(tenant), username=actor, email=actor + "@example.invalid",
        status="active", created_by=SCOPE))
    bundle.identity_store.set_account_roles(actor, roles)
    bundle.identity_store.set_account_scope(actor, Scope(tenant_id=tenant,
        modules=frozenset({"operator_console"}), clearance=DataClassification.CONFIDENTIAL))
    encoded = CredentialService().hash_password(PASSWORD)
    with psycopg.connect(_database) as conn:
        conn.execute("INSERT INTO identity.password_credentials(account_id,phc_hash,must_change) VALUES(%s,%s,false) "
                     "ON CONFLICT(account_id) DO UPDATE SET phc_hash=EXCLUDED.phc_hash,must_change=false", (actor, encoded))

def _normal_app():
    # Import the normal module only after this owned database is migrated;
    # its module-level default application must see a prepared database too.
    from apps.api.oday_api.main import create_app
    return create_app(persistence=bundle)


app = _normal_app()
_lock = Lock()


def control(x_synthetic_control: str = Header(default="")):
    if not hmac.compare_digest(x_synthetic_control, _control):
        raise HTTPException(403, "Synthetic fixture control required")


@app.get("/_synthetic/health")
def health():
    return {"scope": SCOPE, "persistence": "postgresql", "auth": "actual-local-password-and-durable-session"}


@app.post("/_synthetic/area-planning/setup", dependencies=[Depends(control)])
def setup():
    with _lock:
        zone, listing, candidate = [str(uuid4()) for _ in range(3)]
        now = datetime.now(UTC)
        score = HeatZoneScoreResult(
            heat_zone_id=zone, h3_index="8929a1d4d67ffff", h3_resolution=9,
            score=60, priority_rank=1, unmet_demand_score=.6, format_fit_score=.6,
            cannibalization_risk_score=.2, rent_feasibility_score=.6, listing_availability_score=.6,
            confidence=.6, state=HeatZoneState.STILL_EXPANDABLE, feature_snapshot_time=now,
            prediction_origin_time=now, last_scored_at=now, model_version="declared-area-browser-only",
            feature_version="declared-area-browser-only", source_snapshot_ids=("declared-area-browser-source",),
            reasons=(SCOPE,), admin_city="Synthetic", admin_district="Area draft")
        bundle.heatzone_store_for_tenant(TENANT).put(HeatZoneBatchScoreResult(
            job_id=str(uuid4()), tenant_id=TENANT, status="succeeded", scores=(score,), completed_at=now))
        state = DurableOperatorDomainStateRepository(PostgresDocumentStore(bundle.engine), "network-listings")
        # The normal Network reader rebuilds this projection from canonical
        # records. Keep the area owner's snapshot on that same read path.
        repository = bundle.listing_repository_for_tenant(TENANT)
        metadata = bundle.operator_intake_repository_for_tenant(TENANT)
        address = AddressLocation(
            address_id=str(uuid4()), tenant_id=TENANT,
            raw_address="Declared synthetic address " + listing,
            normalized_address="Declared synthetic address " + listing,
        )
        record = Listing(
            listing_id=listing, tenant_id=TENANT, address_id=address.address_id,
            source_id="synthetic-area-browser", source_listing_id=listing,
            listing_status="candidate",
        )
        draft = CandidateSiteDraft(
            listing=record, address=address, heat_zone_id=zone,
            candidate_site=CandidateSite(
                candidate_site_id=candidate, listing_id=listing,
                address_id=address.address_id, site_status="draft", created_by=MANAGER,
            ),
        )
        with bundle.engine.transaction():
            repository.save_listing(record, address, ListingDedupKey(
                source_id=record.source_id, source_listing_id=listing,
                normalized_address=address.normalized_address,
                rent_amount=record.rent_amount, area_ping=record.area_ping,
            ))
            repository.save_candidate(draft)
            metadata.save_listing_metadata(listing, {"heatZoneId": zone})
            metadata.save_candidate_metadata(candidate, {"title": "Synthetic area candidate"})
            aggregate = NetworkListingService(
                listing_repository=repository, intake_repository=metadata,
                seed_fixtures=False, tenant_id=TENANT,
            ).export_state()
            projected = next(row for row in aggregate["candidates"] if row["id"] == candidate)
            projected_listing = next(row for row in aggregate["listings"] if row["id"] == listing)
            assert (projected["listingId"], projected["heatZoneId"]) == (listing, zone)
            assert (projected_listing["tenantId"], projected_listing["heatZoneId"]) == (TENANT, zone)
            assert projected["title"] == "Synthetic area candidate"
            assert all(projected[key] is None for key in (
                "score", "recommendation", "modelVersion", "datasetSnapshotId",
            ))
            assert repository.get_listing(listing).tenant_id == TENANT
            persisted = [row for row in repository.list_candidates()
                         if row.candidate_site.candidate_site_id == candidate]
            assert len(persisted) == 1
            retained = persisted[0]
            assert (retained.listing.listing_id, retained.listing.tenant_id,
                    retained.address.address_id, retained.address.tenant_id,
                    retained.candidate_site.listing_id, retained.candidate_site.address_id,
                    retained.candidate_site.created_by, retained.heat_zone_id) == (
                listing, TENANT, address.address_id, TENANT,
                listing, address.address_id, MANAGER, zone,
            )
            state.save(TENANT, aggregate)
        return {"scope": SCOPE, "zone_id": zone, "candidate_id": candidate,
                "tenant_id": TENANT, "manager_username": MANAGER,
                "foreign_username": _foreign_actor, "viewer_username": _viewer_actor}
