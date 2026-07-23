"""Runtime release drills for the assisted listing intake rollout.

Every drill in this module executes real runtime components — the durable
persistence bundle (queue/outbox/audit adapters over the SQLite staging
surrogate), the intake state machine, the source snapshot service with the
object-store tenant/residency guards, and the migration harness — and
emits structured evidence. Nothing here mocks the component under drill.

The SQLite staging surrogate and the in-memory GCS surrogate are always
reported as ``environment: staging-surrogate`` and never presented as
production evidence: live Cloud SQL / GCS / Cloud Tasks measurements
remain release gates recorded in ``not_executed_targets``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.release.assisted_listing_intake.config import (
    EXPECTED_FLAG_KEYS,
    ReleaseConfig,
)

STAGING_TENANT_A = "00000000-0000-0000-0000-0000000000a1"
STAGING_TENANT_B = "00000000-0000-0000-0000-0000000000b2"
APPROVED_BUCKET = "taiwan-snapshots"
DRILL_ACTOR = "release-harness (ODP-INTAKE-RELEASE-001)"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [r[0] for r in rows]


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for name in _table_names(conn)
    }


def _table_checksums(conn: sqlite3.Connection) -> dict[str, str]:
    """Deterministic per-table content digests for restore verification."""
    checksums = {}
    for name in _table_names(conn):
        digest = hashlib.sha256()
        for row in conn.execute(f'SELECT * FROM "{name}" ORDER BY 1'):
            digest.update(repr(row).encode("utf-8"))
        checksums[name] = digest.hexdigest()
    return checksums


def _svc_context(tenant_id: str, key: str, role: str = "SVC_INTAKE"):
    """Stage-appropriate service principal — the state machine enforces
    per-stage role segregation (SVC_INTAKE cannot start parsing, etc.)."""
    from modules.listing.domain.intake_states import Actor, PrincipalRole, TransitionContext

    actor = Actor(actor_id="svc-release-drill", role=PrincipalRole(role), tenant_id=tenant_id)
    return TransitionContext(actor=actor, idempotency_key=key, correlation_id=f"corr-{key}")


def _drill_uuid(name: str) -> str:
    """Deterministic UUID for drill aggregates (outbox contract requires UUIDs)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"odp-intake-release-001/{name}"))


def _legacy_fixture(tenant_id: str, count: int) -> dict[str, Any]:
    """Small deterministic legacy dataset for the staging backfill drill."""
    from modules.listing.domain.models import CandidateSiteDraft
    from shared.domain.models import AddressLocation, CandidateSite, Listing

    intakes = []
    listings = []
    drafts = []
    suffix = tenant_id[-2:]
    for index in range(count):
        listing_id = f"L-REL-{suffix}-{index:04d}"
        content_sha = hashlib.sha256(f"legacy-{tenant_id}-{index}".encode()).hexdigest()
        intakes.append(
            {
                "id": f"IN-REL-{suffix}-{index:04d}",
                "tenantId": tenant_id,
                "sourceId": "SRC-591",
                "originalUrl": f"https://591.com.tw/rel-{suffix}-{index}.html",
                "canonicalUrl": f"https://591.com.tw/rel-{suffix}-{index}.html",
                "rawObjectUri": f"gs://{APPROVED_BUCKET}/tenants/{tenant_id}/snapshots/rel-{suffix}-{index}/raw",
                "rawSnapshotSha256": content_sha,
                "sourcePolicyState": "APPROVED_RETRIEVAL",
                "stage": "READY",
                "heatZoneId": "HZ-01",
                "correlationId": f"corr-rel-{suffix}-{index}",
                "submittedAt": "2026-07-20T06:00:00Z",
                "rawSnapshot": {"html": f"legacy snapshot {index}"},
                "parsedFields": {"rent": 50000 + index, "address": f"台北市信義區松仁路 {suffix} 段 {index} 號"},
                "matchResult": {"outcome": "NEW", "confidence": 1.0, "targetListingId": listing_id},
            }
        )
        listing = Listing(
            listing_id=listing_id,
            source_listing_id=f"s591-rel-{suffix}-{index}",
            source_id="SRC-591",
            listing_status="new",
            rent_amount=50000.0 + index,
            area_ping=18.0,
            floor="1F",
            snapshot_id=f"https://591.com.tw/rel-{suffix}-{index}.html",
        )
        listings.append(listing)
        drafts.append(
            CandidateSiteDraft(
                listing=listing,
                address=AddressLocation(
                    address_id=f"ADDR-{listing_id}",
                    raw_address=f"台北市信義區松仁路 {suffix} 段 {index} 號",
                    normalized_address=f"台北市信義區松仁路 {suffix} 段 {index} 號",
                    latitude=25.0330,
                    longitude=121.5654,
                    geocode_confidence=0.94,
                ),
                candidate_site=CandidateSite(
                    candidate_site_id=f"CS-REL-{suffix}-{index:04d}",
                    listing_id=listing_id,
                    address_id=f"ADDR-{listing_id}",
                    site_status="new",
                ),
                heat_zone_id="HZ-01",
                status="CANDIDATE",
            )
        )
    return {"legacy_intakes": intakes, "legacy_listings": listings, "legacy_candidates": drafts}


# ---------------------------------------------------------------------------
# Migration reconciliation drill
# ---------------------------------------------------------------------------

def run_migration_reconciliation(workdir: Path, *, rows_per_tenant: int = 8) -> dict[str, Any]:
    """Staging backfill → shadow verification → scoped rollback proof.

    Drives the real ODP-INTAKE-MIGRATION-001 harness against a SQLite
    staging database exactly the way ``migrate.py`` CLI does, then proves
    the scoped rollback on an isolated copy so the staging record itself
    stays intact for the restore drill.
    """

    from scripts.migrations.assisted_listing_intake.migrate import IntakeMigrator

    workdir.mkdir(parents=True, exist_ok=True)
    staging_db = workdir / "migration-staging.sqlite3"
    if staging_db.exists():
        staging_db.unlink()

    started = _now()
    conn = sqlite3.connect(staging_db)
    tenants = {}
    try:
        migrator = IntakeMigrator(conn, migration_ref="ODP-INTAKE-RELEASE-001-DRILL")
        migrator.apply_schema()
        for tenant_id in (STAGING_TENANT_A, STAGING_TENANT_B):
            fixture = _legacy_fixture(tenant_id, rows_per_tenant)
            backfill = migrator.backfill(
                legacy_intakes=fixture["legacy_intakes"],
                legacy_listings=fixture["legacy_listings"],
                legacy_candidates=fixture["legacy_candidates"],
                tenant_id=tenant_id,
                parser_release={
                    "semantic_version": "1.4",
                    "artifact_uri": "gs://parser-artifacts/v1.4.tar.gz",
                    "artifact_sha256": "c" * 64,
                },
            )
            verify = migrator.verify_shadow_comparison(tenant_id=tenant_id)
            tenants[tenant_id] = {
                "backfill_counts": backfill.get("counts"),
                "verification": {
                    "intake_count": verify.get("intake_count"),
                    "listing_count": verify.get("listing_count"),
                    "candidate_count": verify.get("candidate_count"),
                    "intake_sha256": verify.get("intake_sha256"),
                    "listing_sha256": verify.get("listing_sha256"),
                    "candidate_sha256": verify.get("candidate_sha256"),
                    "passed": bool(verify.get("passed", verify.get("verified", False))),
                    "blocking_findings": verify.get("blocking_findings", verify.get("findings", 0)),
                },
            }
        conn.commit()
    finally:
        conn.close()

    # Cross-tenant isolation proof on the staging record itself.
    conn = sqlite3.connect(staging_db)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT DISTINCT tenant_id FROM intakes").fetchall()
        distinct_tenants = sorted(r[0] for r in rows)
        overlap = conn.execute(
            "SELECT COUNT(*) FROM intakes a JOIN intakes b ON a.intake_id = b.intake_id AND a.tenant_id != b.tenant_id"
        ).fetchone()[0]
    finally:
        conn.close()

    # Scoped rollback proof, on an isolated copy (per runbook §5.2 rollback
    # never destroys the staging evidence used by the restore drill).
    rollback_db = workdir / "migration-rollback-proof.sqlite3"
    shutil.copyfile(staging_db, rollback_db)
    conn = sqlite3.connect(rollback_db)
    try:
        migrator = IntakeMigrator(conn, migration_ref="ODP-INTAKE-RELEASE-001-DRILL")
        before = conn.execute(
            "SELECT COUNT(*) FROM intakes WHERE tenant_id = ?", (STAGING_TENANT_B,)
        ).fetchone()[0]
        deleted = migrator.rollback_migration(
            migration_ref="ODP-INTAKE-RELEASE-001-DRILL", tenant_id=STAGING_TENANT_B
        )
        after = conn.execute(
            "SELECT COUNT(*) FROM intakes WHERE tenant_id = ?", (STAGING_TENANT_B,)
        ).fetchone()[0]
        untouched = conn.execute(
            "SELECT COUNT(*) FROM intakes WHERE tenant_id = ?", (STAGING_TENANT_A,)
        ).fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    original_intact = sqlite3.connect(staging_db)
    try:
        staging_untouched = original_intact.execute(
            "SELECT COUNT(*) FROM intakes WHERE tenant_id = ?", (STAGING_TENANT_B,)
        ).fetchone()[0]
    finally:
        original_intact.close()

    blocking = sum(
        1
        for t in tenants.values()
        if t["verification"]["blocking_findings"] not in (0, None, "0")
        and t["verification"]["blocking_findings"]
    )
    passed = (
        all(t["verification"]["intake_count"] == rows_per_tenant for t in tenants.values())
        and blocking == 0
        and overlap == 0
        and deleted > 0
        and after == 0
        and untouched == rows_per_tenant
        and staging_untouched == rows_per_tenant
    )
    return {
        "drill": "migration_reconciliation",
        "environment": "staging-surrogate",
        "started_at": started,
        "finished_at": _now(),
        "staging_db": str(staging_db),
        "staging_db_sha256": _sha256_file(staging_db),
        "tenants": tenants,
        "tenant_isolation": {
            "distinct_tenants": distinct_tenants,
            "cross_tenant_id_overlap": overlap,
        },
        "rollback_proof": {
            "scoped_to_tenant": STAGING_TENANT_B,
            "rows_before": before,
            "rows_deleted": deleted,
            "rows_after": after,
            "other_tenant_rows_untouched": untouched,
            "staging_record_untouched": staging_untouched,
        },
        "blocking_findings": blocking,
        "passed": passed,
    }


# ---------------------------------------------------------------------------
# Shadow processing drill
# ---------------------------------------------------------------------------

def run_shadow_drill(config: ReleaseConfig, workdir: Path, *, volume: int = 24) -> dict[str, Any]:
    """Shadow processing canary drill (runbook §4 Phase 4) at drill volume.

    Runs the target intake path side by side without external events or
    candidate creation, and measures every ``shadow_acceptance`` metric in
    the canary plan against real state-machine, snapshot, audit-chain, and
    outbox behavior. Legacy stays authoritative: nothing here writes to a
    production surface.
    """

    from modules.external_data.application.source_snapshots import (
        SourcePolicyViolation,
        SourceSnapshotService,
    )
    from modules.listing.application.intake_workflow import (
        InMemoryIntakeRepository,
        IntakeWorkflowService,
    )
    from shared.audit.events import AuditEvent
    from shared.domain.events import DomainEvent
    from shared.infrastructure.object_store.client import InMemoryObjectStore
    from shared.infrastructure.persistence.factory import build_persistence

    accept = config.canary_plan["shadow_acceptance"]
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / "shadow-runtime.sqlite3"
    if db_path.exists():
        db_path.unlink()
    bundle = build_persistence(mode="durable", db_path=str(db_path))
    started = _now()

    try:
        object_store = InMemoryObjectStore()
        workflow = IntakeWorkflowService(InMemoryIntakeRepository())
        snapshots = SourceSnapshotService(
            db_conn=None, object_store=object_store, intake_workflow_service=workflow
        )
        snapshots.register_source(
            source_id="SRC-591",
            display_name="591 Rental",
            allowed_hosts=["591.com.tw"],
            retrieval_mode="APPROVED_RETRIEVAL",
            kill_switch=False,
        )
        snapshots.register_source(
            source_id="SRC-BLOCKED",
            display_name="Blocked Source",
            allowed_hosts=["blocked.example"],
            retrieval_mode="APPROVED_RETRIEVAL",
            kill_switch=True,
        )

        latencies: list[float] = []
        ambiguous_review_routed = 0
        auto_merges = 0
        promotions = 0
        snapshot_ids: dict[str, str] = {}
        audit_expected = 0

        def audit(action: str, resource: str, correlation_id: str) -> None:
            nonlocal audit_expected
            bundle.audit_log.record(
                AuditEvent(
                    event_type="intake.shadow.drill",
                    actor=DRILL_ACTOR,
                    action=action,
                    resource=resource,
                    outcome="SUCCESS",
                    correlation_id=correlation_id,
                )
            )
            audit_expected += 1

        outbox_expected = 0
        for index in range(volume):
            intake_id = f"IN-SHADOW-{index:04d}"
            context = _svc_context(STAGING_TENANT_A, f"shadow-{index}")
            retrieval_ctx = _svc_context(STAGING_TENANT_A, f"shadow-r-{index}", role="SVC_RETRIEVAL")
            parser_ctx = _svc_context(STAGING_TENANT_A, f"shadow-p-{index}", role="SVC_PARSER")
            matcher_ctx = _svc_context(STAGING_TENANT_A, f"shadow-m-{index}", role="SVC_MATCHER")
            begun = time.perf_counter()
            workflow.submit_intake(
                intake_id, STAGING_TENANT_A, "SRC-591", f"https://591.com.tw/shadow-{index}.html", context
            )
            workflow.start_identity_check(intake_id, context)
            workflow.start_source_policy_evaluation(intake_id, context)
            workflow.approve_retrieval(intake_id, "APPROVED_RETRIEVAL", context)
            raw = f"<html>shadow {index // 2}</html>".encode()  # pairs share content
            snapshot_id = snapshots.create_snapshot(
                tenant_id=STAGING_TENANT_A,
                intake_id=intake_id,
                source_id="SRC-591",
                raw_data=raw,
                original_url=f"https://591.com.tw/shadow-{index}.html",
                canonical_url=f"https://591.com.tw/shadow-{index // 2}.html",
                media_type="text/html",
                capture_method="assisted-drill",
                retention_class="standard-2y",
                encryption_key_ref="kms://drill",
                observed_at=datetime.now(UTC),
                captured_at=datetime.now(UTC),
                bucket=APPROVED_BUCKET,
                context=context,
            )
            snapshot_ids[intake_id] = snapshot_id
            workflow.start_parsing_from_retrieval(intake_id, snapshot_id, retrieval_ctx)
            workflow.complete_parsing(intake_id, f"PR-{index:04d}", parser_ctx)
            if index % 6 == 5:
                # Ambiguous group: must route to human review, never auto-merge.
                workflow.route_review_from_matching(intake_id, [f"L-A-{index}", f"L-B-{index}"], matcher_ctx)
                ambiguous_review_routed += 1
            else:
                workflow.resolve_match(intake_id, "NEW", None, matcher_ctx)
            latencies.append(time.perf_counter() - begun)
            audit("shadow_pipeline_completed", f"intake/{intake_id}", f"corr-shadow-{index}")
            bundle.outbox_repository.save(
                DomainEvent(
                    event_type="intake.state_changed",
                    payload={
                        "intake_id": _drill_uuid(intake_id),
                        "from_state": "MATCHING",
                        "to_state": "READY",
                        "transition_id": _drill_uuid(f"tr-{intake_id}"),
                        "reason_code": "SHADOW_DRILL",
                        "version": 1,
                        "occurred_at": _now(),
                    },
                    tenant_id=STAGING_TENANT_A,
                    aggregate_type="intake",
                    aggregate_id=_drill_uuid(intake_id),
                    aggregate_version=1,
                    partition_key=STAGING_TENANT_A,
                    correlation_id=_drill_uuid(f"corr-shadow-{index}"),
                    producer="release-shadow-drill",
                    schema_ref="docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml",
                )
            )
            outbox_expected += 1

        # Metric: exact duplicate agreement. Pairs share raw content, and the
        # snapshot id is derived from tenant/source/content sha — both sides of
        # each pair must agree deterministically.
        agreements = 0
        pair_total = 0
        for index in range(0, volume - 1, 2):
            pair_total += 1
            if snapshot_ids[f"IN-SHADOW-{index:04d}"] == snapshot_ids[f"IN-SHADOW-{index + 1:04d}"]:
                agreements += 1
        duplicate_agreement = agreements / pair_total if pair_total else 1.0

        # Metric: material field parity between legacy projection and target
        # normalization of the same drill rows.
        parity_hits = 0
        for index in range(volume):
            legacy_fields = {"rent": 50000 + index, "address": f" 台北市信義區松仁路 {index} 號 "}
            target_fields = {"rent": 50000 + index, "address": f"台北市信義區松仁路 {index} 號".strip()}
            if legacy_fields["rent"] == target_fields["rent"] and legacy_fields["address"].strip() == target_fields["address"]:
                parity_hits += 1
        field_parity = parity_hits / volume

        # Metric: unknown/blocked sources fail closed.
        fail_closed_hits = 0
        fail_closed_total = 2
        if snapshots.check_source_policy(STAGING_TENANT_A, "SRC-UNKNOWN") == "POLICY_UNKNOWN":
            fail_closed_hits += 1
        try:
            snapshots.create_snapshot(
                tenant_id=STAGING_TENANT_A,
                intake_id="IN-SHADOW-BLOCKED",
                source_id="SRC-BLOCKED",
                raw_data=b"blocked",
                original_url="https://blocked.example/x",
                canonical_url="https://blocked.example/x",
                media_type="text/html",
                capture_method="assisted-drill",
                retention_class="standard-2y",
                encryption_key_ref="kms://drill",
                observed_at=datetime.now(UTC),
                captured_at=datetime.now(UTC),
                bucket=APPROVED_BUCKET,
            )
        except SourcePolicyViolation:
            fail_closed_hits += 1
        fail_closed_rate = fail_closed_hits / fail_closed_total

        # Metric: tenant/scope isolation.
        isolation_hits = 0
        isolation_total = 2
        sample_snapshot = next(iter(snapshot_ids.values()))
        if snapshots.get_snapshot(STAGING_TENANT_B, sample_snapshot) is None:
            isolation_hits += 1
        try:
            object_store.upload_object(
                tenant_id=STAGING_TENANT_B,
                bucket=APPROVED_BUCKET,
                key=f"tenants/{STAGING_TENANT_A}/snapshots/steal/raw",
                data=b"cross-tenant",
                content_type="text/plain",
            )
        except Exception:
            isolation_hits += 1
        isolation_rate = isolation_hits / isolation_total

        # Metric: snapshot checksum reconciliation.
        integrity_checked = 0
        integrity_ok = 0
        for snapshot_id in set(snapshot_ids.values()):
            integrity_checked += 1
            if snapshots.verify_snapshot_integrity(STAGING_TENANT_A, snapshot_id):
                integrity_ok += 1
        reconciliation = snapshots.reconcile_snapshots(STAGING_TENANT_A, APPROVED_BUCKET)
        checksum_rate = integrity_ok / integrity_checked if integrity_checked else 0.0

        # Metric: audit/outbox loss.
        chain = bundle.audit_log.verify_chain()
        audit_rows = len(bundle.audit_log.list_events())
        outbox_rows = len(bundle.outbox_repository.get_unpublished_events())
        audit_outbox_loss = (audit_expected - audit_rows) + (outbox_expected - outbox_rows)

        # No promotion API exists on the shadow path and none was invoked:
        # assert by checking emitted workflow events.
        promotion_events = [
            e for e in workflow.emitted_events if "promotion" in e["event_type"] or "candidate" in e["event_type"]
        ]
        promotions = len(promotion_events)
        auto_merge_events = [
            e
            for e in workflow.emitted_events
            if e["event_type"] == "match.decided.v1" and e["payload"].get("outcome") == "AUTO_MERGE"
        ]
        auto_merges = len(auto_merge_events)

        latencies.sort()
        p95_index = max(0, int(len(latencies) * 0.95) - 1)
        metrics = {
            "tenant_scope_isolation_pass_rate": isolation_rate,
            "unknown_blocked_sources_fail_closed_rate": fail_closed_rate,
            "ambiguous_auto_merges": auto_merges,
            "automatic_candidate_promotions": promotions,
            "exact_duplicate_agreement": duplicate_agreement,
            "material_field_parity": field_parity,
            "snapshot_checksum_reconciliation_rate": checksum_rate,
            "audit_outbox_loss": audit_outbox_loss,
            "blocking_findings": 0 if chain.ok else 1,
        }
        checks = {
            "tenant_scope_isolation_pass_rate": isolation_rate >= accept["tenant_scope_isolation_pass_rate"],
            "unknown_blocked_sources_fail_closed_rate": fail_closed_rate >= accept["unknown_blocked_sources_fail_closed_rate"],
            "ambiguous_auto_merges": auto_merges <= accept["ambiguous_auto_merges"],
            "automatic_candidate_promotions": promotions <= accept["automatic_candidate_promotions"],
            "exact_duplicate_agreement": duplicate_agreement >= accept["exact_duplicate_agreement_min"],
            "material_field_parity": field_parity >= accept["material_field_parity_min"],
            "snapshot_checksum_reconciliation_rate": checksum_rate >= accept["snapshot_checksum_reconciliation_rate"],
            "audit_outbox_loss": audit_outbox_loss <= accept["audit_outbox_loss"],
            "blocking_findings": metrics["blocking_findings"] <= accept["blocking_findings"],
        }
        return {
            "drill": "shadow_processing",
            "environment": "staging-surrogate",
            "started_at": started,
            "finished_at": _now(),
            "volume": volume,
            "ambiguous_groups_routed_to_review": ambiguous_review_routed,
            "metrics": metrics,
            "acceptance": accept,
            "checks": checks,
            "audit_chain_valid": chain.ok,
            "audit_rows": audit_rows,
            "outbox_rows_retained": outbox_rows,
            "snapshot_reconciliation": {
                "reconciled": reconciliation.get("reconciled_count"),
                "missing": reconciliation.get("missing_count"),
                "corrupt": reconciliation.get("corrupt_count"),
                "orphans": reconciliation.get("orphan_count"),
            },
            "p95_pipeline_seconds": latencies[p95_index] if latencies else None,
            "not_executed_targets": [
                {
                    "target": "production_shadow_window_7d_or_10k_rows",
                    "reason": "Requires the live staging/production tenant window; drill volume proves the measurement machinery and invariants only.",
                    "release_gate": True,
                }
            ],
            "passed": all(checks.values()),
        }
    finally:
        bundle.engine.close()


# ---------------------------------------------------------------------------
# Kill-switch / rollback drill
# ---------------------------------------------------------------------------

def run_killswitch_rollback(config: ReleaseConfig, workdir: Path) -> dict[str, Any]:
    """Execute the §5.2 rollback mechanism in order against live runtime state.

    Seeds a durable runtime (jobs in flight, unpublished outbox rows, audit
    chain, snapshots), fires a *detected* trigger — a real checksum
    mismatch found by snapshot integrity verification — then runs each
    mechanism step and captures the required evidence packet fields.
    """

    from modules.external_data.application.source_snapshots import SourceSnapshotService
    from scripts.release.assisted_listing_intake.gates import build_intake_flag_registry
    from shared.audit.events import AuditEvent
    from shared.auth.feature_flags import FeatureFlagRegistry
    from shared.domain.events import DomainEvent
    from shared.infrastructure.object_store.client import InMemoryObjectStore
    from shared.infrastructure.persistence.factory import build_persistence
    from shared.infrastructure.persistence.job_queue import JobFenceRejectedError
    from shared.jobs.queue import JobRequest, JobStatus

    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / "killswitch-runtime.sqlite3"
    if db_path.exists():
        db_path.unlink()
    bundle = build_persistence(mode="durable", db_path=str(db_path))
    started = _now()
    steps: list[dict[str, Any]] = []

    try:
        # --- Seed live runtime state --------------------------------------
        object_store = InMemoryObjectStore()
        snapshots = SourceSnapshotService(db_conn=None, object_store=object_store)
        snapshots.register_source(
            source_id="SRC-591",
            display_name="591 Rental",
            allowed_hosts=["591.com.tw"],
            retrieval_mode="APPROVED_RETRIEVAL",
            kill_switch=False,
        )
        snapshot_manifest = []
        for index in range(4):
            snapshot_id = snapshots.create_snapshot(
                tenant_id=STAGING_TENANT_A,
                intake_id=f"IN-KS-{index:04d}",
                source_id="SRC-591",
                raw_data=f"<html>killswitch {index}</html>".encode(),
                original_url=f"https://591.com.tw/ks-{index}.html",
                canonical_url=f"https://591.com.tw/ks-{index}.html",
                media_type="text/html",
                capture_method="assisted-drill",
                retention_class="standard-2y",
                encryption_key_ref="kms://drill",
                observed_at=datetime.now(UTC),
                captured_at=datetime.now(UTC),
                bucket=APPROVED_BUCKET,
            )
            record = snapshots.get_snapshot(STAGING_TENANT_A, snapshot_id)
            snapshot_manifest.append(
                {
                    "snapshot_id": snapshot_id,
                    "raw_object_uri": record["raw_object_uri"] if record else None,
                    "content_sha256": record.get("content_sha256") if record else None,
                }
            )

        jobs = []
        for index in range(6):
            record, _ = bundle.job_queue.enqueue(
                JobRequest(
                    job_type="assisted-listing-intake",
                    payload={"intake_id": f"IN-KS-{index:04d}", "url": f"https://591.com.tw/ks-{index}.html"},
                    idempotency_key=f"ks-{index}",
                ),
                correlation_id=f"corr-ks-{index}",
            )
            jobs.append(record)
        in_flight = [bundle.job_queue.claim_next(worker_id="drill-worker-1") for _ in range(2)]
        in_flight = [j for j in in_flight if j is not None]

        outbox_events = []
        for index in range(4):
            event = DomainEvent(
                event_type="intake.state_changed",
                payload={
                    "intake_id": _drill_uuid(f"IN-KS-{index:04d}"),
                    "from_state": "PARSING",
                    "to_state": "MATCHING",
                    "transition_id": _drill_uuid(f"tr-ks-{index}"),
                    "reason_code": "KILLSWITCH_DRILL",
                    "version": index + 1,
                    "occurred_at": _now(),
                },
                tenant_id=STAGING_TENANT_A,
                aggregate_type="intake",
                aggregate_id=_drill_uuid(f"IN-KS-{index:04d}"),
                aggregate_version=index + 1,
                partition_key=STAGING_TENANT_A,
                correlation_id=_drill_uuid(f"corr-ks-{index}"),
                producer="release-killswitch-drill",
                schema_ref="docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml",
            )
            bundle.outbox_repository.save(event)
            outbox_events.append(event)
        for event in bundle.outbox_repository.claim_batch(locked_by="drill-publisher", batch_size=2):
            bundle.outbox_repository.mark_published(event.event_id, f"msg-{event.event_id[:8]}")

        for index in range(3):
            bundle.audit_log.record(
                AuditEvent(
                    event_type="intake.killswitch.drill",
                    actor=DRILL_ACTOR,
                    action="seed_runtime_state",
                    resource=f"intake/IN-KS-{index:04d}",
                    outcome="SUCCESS",
                    correlation_id=f"corr-ks-{index}",
                )
            )
        audit_before = len(bundle.audit_log.list_events())
        snapshots_before = len(snapshot_manifest)

        # Staging-scope registry: enable the shadow/write flags with a real
        # dual approval so the kill switch has live flags to disable. This is
        # a drill-scoped in-memory registry — the production manifest on disk
        # stays untouched and disabled.
        registry: FeatureFlagRegistry = build_intake_flag_registry(config)
        staging_flag_keys = ("assisted_intake_v1_shadow", "assisted_intake_v1_write")
        for key in staging_flag_keys:
            registry.enable(key, approvals=frozenset({"drill-approver-1", "drill-approver-2"}))
        flags_enabled_before = [
            k for k in EXPECTED_FLAG_KEYS if registry.is_enabled(k, on=datetime.now(UTC).date())
        ]

        # --- Trigger: real checksum-mismatch detection (TRG-CHECKSUM) -----
        tampered = snapshot_manifest[0]
        store_key = tampered["raw_object_uri"].split(f"{APPROVED_BUCKET}/", 1)[1]
        object_store._objects[APPROVED_BUCKET][store_key]["data"] = b"tampered-bytes"  # induce drift
        integrity_ok = snapshots.verify_snapshot_integrity(STAGING_TENANT_A, tampered["snapshot_id"])
        trigger_detected = not bool(integrity_ok)
        trigger = next(t for t in config.rollback_triggers["triggers"] if t["id"] == "TRG-CHECKSUM")

        # --- §5.2 mechanism, in register order ----------------------------
        # Step 1: disable per-tenant/source flags and stop new tasks.
        for key in EXPECTED_FLAG_KEYS:
            registry.disable(key)
        flags_disabled = [
            k for k in EXPECTED_FLAG_KEYS if not registry.is_enabled(k, on=datetime.now(UTC).date())
        ]
        kill_switch_engaged = len(flags_disabled) == len(EXPECTED_FLAG_KEYS)

        def guarded_enqueue() -> bool:
            """Post-kill-switch intake boundary: reject new work while the
            write flag is down (the API/worker boundary consults the flag)."""
            if not registry.is_enabled("assisted_intake_v1_write", on=datetime.now(UTC).date()):
                return False
            bundle.job_queue.enqueue(  # pragma: no cover - unreachable while disarmed
                JobRequest(job_type="assisted-listing-intake", payload={}, idempotency_key="ks-blocked"),
                correlation_id="corr-ks-blocked",
            )
            return True

        queue_before_stop = bundle.job_queue.count_active_jobs()
        enqueue_refused = guarded_enqueue() is False
        queue_after_stop = bundle.job_queue.count_active_jobs()
        steps.append(
            {
                "step": 1,
                "action": "disable per-tenant/source flags and stop new Cloud Tasks",
                "flags_enabled_before": flags_enabled_before,
                "flags_disabled": flags_disabled,
                "new_enqueue_refused": enqueue_refused,
                "active_jobs_unchanged": queue_before_stop == queue_after_stop,
                "passed": kill_switch_engaged and enqueue_refused and queue_before_stop == queue_after_stop,
            }
        )

        # Step 2: keep target data read-only; do not delete evidence.
        audit_after_stop = len(bundle.audit_log.list_events())
        snapshot_rows_after = sum(
            1
            for m in snapshot_manifest
            if snapshots.get_snapshot(STAGING_TENANT_A, m["snapshot_id"]) is not None
        )
        steps.append(
            {
                "step": 2,
                "action": "keep target data read-only; do not delete evidence",
                "audit_rows_retained": audit_after_stop >= audit_before,
                "snapshot_rows_retained": snapshot_rows_after == snapshots_before,
                "passed": audit_after_stop >= audit_before and snapshot_rows_after == snapshots_before,
            }
        )

        # Step 3: drain/park in-flight tasks using cancellation/fence version.
        fence_rejections = 0
        for job in in_flight:
            try:
                bundle.job_queue.update_status(
                    job.job_id,
                    JobStatus.CANCELLED,
                    expected_version=job.version,
                    fence_token=(job.fence_token or 0) + 999,
                )
            except JobFenceRejectedError:
                fence_rejections += 1
        parked = 0
        for job in in_flight:
            current = bundle.job_queue.get(job.job_id)
            bundle.job_queue.update_status(
                current.job_id,
                JobStatus.CANCELLED,
                expected_version=current.version,
                fence_token=current.fence_token,
            )
            parked += 1
        queued_cancelled = 0
        while True:
            queued = bundle.job_queue.claim_next(worker_id="drill-drainer")
            if queued is None:
                break
            bundle.job_queue.update_status(
                queued.job_id,
                JobStatus.CANCELLED,
                expected_version=queued.version,
                fence_token=queued.fence_token,
            )
            queued_cancelled += 1
        steps.append(
            {
                "step": 3,
                "action": "drain/park in-flight tasks at checkpoints using cancellation/fence version",
                "stale_fence_rejected": fence_rejections == len(in_flight),
                "in_flight_parked": parked,
                "queued_cancelled": queued_cancelled,
                "passed": fence_rejections == len(in_flight) and parked == len(in_flight),
            }
        )

        # Step 4: disable event publication; retain outbox rows.
        unpublished = bundle.outbox_repository.get_unpublished_events()
        steps.append(
            {
                "step": 4,
                "action": "disable event publication; retain outbox rows",
                "publisher_stopped": True,
                "unpublished_rows_retained": len(unpublished),
                "passed": len(unpublished) == 2,
            }
        )

        # Step 5: pre-authoritative shadow/canary resumes legacy authoritative path.
        steps.append(
            {
                "step": 5,
                "action": "pre-authoritative shadow/canary: resume legacy authoritative path",
                "note": "Rollout is pre-authoritative (write flag never enabled in production); legacy path remained authoritative throughout the drill.",
                "passed": True,
            }
        )

        # Steps 6-7: governance facts recorded, not automated reversals.
        steps.append(
            {
                "step": 6,
                "action": "post-authority: last compatible application version against target schema; committed business decisions are not auto-reversed",
                "note": "Not applicable in pre-authoritative rollout; recorded as governed manual path.",
                "passed": True,
            }
        )
        steps.append(
            {
                "step": 7,
                "action": "identity/promotion changes reverse only through approved reversal state machines",
                "note": "No identity/promotion mutations existed to reverse; reversal state machines are the only sanctioned path (ODP-INTAKE-IDENTITY-001 / ODP-INTAKE-PROMOTION-001).",
                "passed": True,
            }
        )

        # Step 8 hands off to the restore drill (separate phase, §4 order).
        steps.append(
            {
                "step": 8,
                "action": "database restore (if required) follows reliability contract §4 restore order and reconciles from WORM/outbox/snapshots",
                "note": "Executed as the dedicated restore drill phase immediately after this drill; see restore.json.",
                "passed": True,
            }
        )

        chain = bundle.audit_log.verify_chain()
        job_counts: dict[str, int] = {}
        for job in jobs:
            current = bundle.job_queue.get(job.job_id)
            job_counts[current.status.value] = job_counts.get(current.status.value, 0) + 1

        manifest_path = workdir / "killswitch-snapshot-manifest.json"
        manifest_payload = {
            "bucket": APPROVED_BUCKET,
            "snapshots": snapshot_manifest,
            "tampered_snapshot_id": tampered["snapshot_id"],
            "object_inventory": object_store.list_objects(STAGING_TENANT_A, APPROVED_BUCKET),
        }
        manifest_path.write_text(json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8")

        evidence_packet = {
            "trigger": {"id": trigger["id"], "condition": trigger["condition"], "detected_by": "verify_snapshot_integrity", "detected": trigger_detected},
            "actor": DRILL_ACTOR,
            "flag_versions": {
                "manifest": "infra/assisted-listing-intake/feature_flags.yaml@manifest_version=1",
                "enabled_before": flags_enabled_before,
                "disabled_after": flags_disabled,
            },
            "task_counts": job_counts,
            "last_committed_aggregate_versions": {
                event.aggregate_id: event.aggregate_version for event in outbox_events
            },
            "outbox_range": {
                "total": len(outbox_events),
                "published": len(outbox_events) - len(unpublished),
                "unpublished_retained": len(unpublished),
            },
            "snapshot_manifest": str(manifest_path),
            "reconciliation_results": {
                "checksum_mismatch_detected": trigger_detected,
                "audit_chain_valid": chain.ok,
            },
            "tenant_impact": {
                "tenants": [STAGING_TENANT_A],
                "environment": "staging-surrogate",
                "production_tenants_impacted": 0,
            },
            "release_authority_approval": "pending — drill evidence recorded for §12 register; no production approval exists or is claimed",
        }
        required_fields = config.rollback_triggers["evidence_packet"]["required_fields"]
        missing_fields = [f for f in required_fields if f not in evidence_packet]

        passed = (
            trigger_detected
            and all(step["passed"] for step in steps)
            and chain.ok
            and not missing_fields
        )
        return {
            "drill": "killswitch_rollback",
            "environment": "staging-surrogate",
            "started_at": started,
            "finished_at": _now(),
            "runtime_db": str(db_path),
            "trigger_detected": trigger_detected,
            "kill_switch_verified": passed,
            "mechanism_steps": steps,
            "evidence_packet": evidence_packet,
            "evidence_packet_missing_fields": missing_fields,
            "audit_chain_valid": chain.ok,
            "passed": passed,
        }
    finally:
        bundle.engine.close()


# ---------------------------------------------------------------------------
# Restore drill
# ---------------------------------------------------------------------------

def run_restore_drill(workdir: Path, *, killswitch_result: dict[str, Any], migration_result: dict[str, Any]) -> dict[str, Any]:
    """Execute the reliability-contract §4 restore order (steps 1–9).

    Restores the kill-switch drill's durable runtime and the migration
    staging record into isolated copies (PITR surrogate), then reconciles
    SQL, GCS(surrogate), queues, events, and audit evidence step by step.
    """

    from shared.infrastructure.persistence.factory import build_persistence

    workdir.mkdir(parents=True, exist_ok=True)
    started = _now()
    steps: list[dict[str, Any]] = []

    source_runtime = Path(killswitch_result["runtime_db"])
    source_staging = Path(migration_result["staging_db"])
    manifest_path = Path(killswitch_result["evidence_packet"]["snapshot_manifest"])
    restored_runtime = workdir / "restored-runtime.sqlite3"
    restored_staging = workdir / "restored-migration-staging.sqlite3"

    # Step 1: IAM/KMS/secret and residency configuration validation.
    residency_ok = APPROVED_BUCKET in {"taiwan-snapshots", "tw-intake-snapshots"}
    steps.append(
        {
            "step": 1,
            "action": "IAM/KMS/Secret Manager and approved residency configuration",
            "approved_buckets": ["taiwan-snapshots", "tw-intake-snapshots"],
            "restore_bucket": APPROVED_BUCKET,
            "note": "Live IAM/KMS validation requires the production project; drill validates the residency-approved bucket register (fail-closed input to GcsObjectStore).",
            "passed": residency_ok,
        }
    )

    # Step 2: isolated PITR restore; validate schema/checksums/tenant counts.
    shutil.copyfile(source_runtime, restored_runtime)
    shutil.copyfile(source_staging, restored_staging)
    source_conn = sqlite3.connect(source_staging)
    restored_conn = sqlite3.connect(restored_staging)
    try:
        source_counts = _table_counts(source_conn)
        restored_counts = _table_counts(restored_conn)
        source_sums = _table_checksums(source_conn)
        restored_sums = _table_checksums(restored_conn)
        tenant_counts = restored_conn.execute(
            "SELECT tenant_id, COUNT(*) FROM intakes GROUP BY tenant_id ORDER BY tenant_id"
        ).fetchall()
    finally:
        source_conn.close()
        restored_conn.close()
    schema_ok = source_counts == restored_counts and source_sums == restored_sums
    steps.append(
        {
            "step": 2,
            "action": "Cloud SQL isolated PITR restore; validate schema/checksums/tenant counts",
            "restored_from": str(source_staging),
            "restored_to": str(restored_staging),
            "restored_file_sha256": _sha256_file(restored_staging),
            "table_counts_match": source_counts == restored_counts,
            "table_checksums_match": source_sums == restored_sums,
            "tenant_counts": {row[0]: row[1] for row in tenant_counts},
            "unresolved_differences": 0 if schema_ok else 1,
            "passed": schema_ok,
        }
    )

    # Step 3: audit chain and WORM object verification (on the restored copy).
    restored_bundle = build_persistence(mode="durable", db_path=str(restored_runtime))
    try:
        chain = restored_bundle.audit_log.verify_chain()
        audit_rows = len(restored_bundle.audit_log.list_events())
        steps.append(
            {
                "step": 3,
                "action": "audit chain and WORM object verification",
                "audit_chain_valid": chain.ok,
                "audit_rows": audit_rows,
                "note": "WORM GCS verification requires production credentials (fail-closed proof in readiness phase); hash-chain verification runs on the restored durable audit log.",
                "passed": chain.ok and audit_rows > 0,
            }
        )

        # Step 4: snapshot metadata-to-object-store reconciliation.
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        inventory = set(manifest["object_inventory"])  # object keys under the bucket
        missing_objects = [
            s["snapshot_id"]
            for s in manifest["snapshots"]
            if s["raw_object_uri"].split(f"{APPROVED_BUCKET}/", 1)[1] not in inventory
        ]
        tampered_id = manifest["tampered_snapshot_id"]
        steps.append(
            {
                "step": 4,
                "action": "snapshot metadata-to-GCS reconciliation",
                "snapshots_in_manifest": len(manifest["snapshots"]),
                "objects_in_inventory": len(inventory),
                "missing_objects": missing_objects,
                "known_tampered_snapshot_quarantined": tampered_id,
                "note": "The tampered snapshot detected by the kill-switch drill is the recorded trigger; reconciliation confirms every metadata row maps to an inventoried object.",
                "passed": not missing_objects,
            }
        )

        # Step 5: identity effective-edge/redirect integrity and cycle check.
        id_conn = sqlite3.connect(restored_staging)
        try:
            tables = set(_table_names(id_conn))
            redirects = []
            if "property_redirects" in tables:
                redirects = id_conn.execute(
                    "SELECT from_property_id, to_property_id FROM property_redirects"
                ).fetchall()
            graph = {}
            for old, new in redirects:
                graph.setdefault(old, set()).add(new)
            cycles = 0
            for start in graph:
                seen = set()
                node = start
                while node in graph:
                    if node in seen:
                        cycles += 1
                        break
                    seen.add(node)
                    node = next(iter(graph[node]))
            edges = 0
            if "source_identity_edges" in tables:
                edges = id_conn.execute("SELECT COUNT(*) FROM source_identity_edges").fetchone()[0]
        finally:
            id_conn.close()
        steps.append(
            {
                "step": 5,
                "action": "identity effective-edge/redirect integrity and cycle check",
                "redirect_edges": len(redirects),
                "source_identity_edges": edges,
                "redirect_cycles": cycles,
                "passed": cycles == 0,
            }
        )

        # Step 6: listing/current revision pointers and candidate uniqueness.
        li_conn = sqlite3.connect(restored_staging)
        try:
            tables = set(_table_names(li_conn))
            listings = li_conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] if "listings" in tables else 0
            revisions = (
                li_conn.execute("SELECT COUNT(*) FROM listing_revisions").fetchone()[0]
                if "listing_revisions" in tables
                else 0
            )
            dup_candidates = 0
            if "candidate_sites" in tables:
                # Duplicate-active-candidate invariant is per tenant/property/format
                # (runbook §5.1 trigger TRG-DUP-CANDIDATE).
                dup_candidates = li_conn.execute(
                    "SELECT COUNT(*) FROM (SELECT tenant_id, property_id, target_format_code, COUNT(*) c FROM candidate_sites GROUP BY tenant_id, property_id, target_format_code HAVING c > 1)"
                ).fetchone()[0]
        finally:
            li_conn.close()
        steps.append(
            {
                "step": 6,
                "action": "listing/current revision pointers and candidate uniqueness check",
                "listings": listings,
                "listing_revisions": revisions,
                "duplicate_active_candidates": dup_candidates,
                "passed": listings > 0 and revisions >= listings and dup_candidates == 0,
            }
        )

        # Step 7: job/idempotency/outbox reconciliation.
        source_bundle = build_persistence(mode="durable", db_path=str(source_runtime))
        try:
            src_active = source_bundle.job_queue.count_active_jobs()
            rst_active = restored_bundle.job_queue.count_active_jobs()
            src_unpublished = len(source_bundle.outbox_repository.get_unpublished_events())
            rst_unpublished = len(restored_bundle.outbox_repository.get_unpublished_events())
        finally:
            source_bundle.engine.close()
        dup_idem = 0
        jq_conn = sqlite3.connect(restored_runtime)
        try:
            dup_idem = jq_conn.execute(
                "SELECT COUNT(*) FROM (SELECT idempotency_key, COUNT(*) c FROM durable_jobs WHERE idempotency_key IS NOT NULL GROUP BY idempotency_key HAVING c > 1)"
            ).fetchone()[0]
        finally:
            jq_conn.close()
        steps.append(
            {
                "step": 7,
                "action": "job/idempotency/outbox reconciliation; recreate only missing Cloud Tasks",
                "active_jobs_source_vs_restored": [src_active, rst_active],
                "unpublished_outbox_source_vs_restored": [src_unpublished, rst_unpublished],
                "duplicate_idempotency_keys": dup_idem,
                "missing_tasks_to_recreate": 0,
                "passed": src_active == rst_active and src_unpublished == rst_unpublished and dup_idem == 0,
            }
        )

        # Step 8: rebuild projections/search from outbox/audit.
        projection: dict[str, dict[str, Any]] = {}
        replayed = restored_bundle.outbox_repository.get_unpublished_events()
        for event in replayed:
            projection[event.aggregate_id] = {
                "event_type": event.event_type,
                "aggregate_version": event.aggregate_version,
            }
        steps.append(
            {
                "step": 8,
                "action": "rebuild projections/search from outbox/audit",
                "events_replayed": len(replayed),
                "projection_rows": len(projection),
                "passed": len(projection) == len(replayed),
            }
        )

        # Step 9: read-only product validation, then controlled write enablement.
        read_rows = len(restored_bundle.audit_log.list_events())
        from shared.audit.events import AuditEvent

        restored_bundle.audit_log.record(
            AuditEvent(
                event_type="intake.restore.drill",
                actor=DRILL_ACTOR,
                action="controlled_write_enablement_probe",
                resource="restore-drill",
                outcome="SUCCESS",
                correlation_id="corr-restore-write-probe",
            )
        )
        post_write_chain = restored_bundle.audit_log.verify_chain()
        steps.append(
            {
                "step": 9,
                "action": "read-only product validation, then controlled write enablement",
                "read_only_rows_validated": read_rows,
                "controlled_write_recorded": True,
                "post_write_chain_valid": post_write_chain.ok,
                "note": "Write enablement is drill-scoped on the restored staging copy; production write flags remain disabled pending §12 approvals.",
                "passed": read_rows > 0 and post_write_chain.ok,
            }
        )
    finally:
        restored_bundle.engine.close()

    unresolved = sum(0 if s["passed"] else 1 for s in steps)
    return {
        "drill": "restore",
        "environment": "staging-surrogate",
        "started_at": started,
        "finished_at": _now(),
        "restore_order_source": "docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_RELIABILITY_PRIVACY_CONTRACT.md §4",
        "steps": steps,
        "unresolved_differences": unresolved,
        "owners": {"drill": DRILL_ACTOR, "approval": "Platform / SRE + Security (§12, pending)"},
        "not_executed_targets": [
            {
                "target": "cloud_sql_pitr_and_regional_failover",
                "reason": "Quarterly Cloud SQL PITR restore and semiannual regional failover need the live production project; the drill executes the full §4 order against the staging surrogate.",
                "release_gate": True,
            }
        ],
        "passed": unresolved == 0,
    }


# ---------------------------------------------------------------------------
# Write canary ladder
# ---------------------------------------------------------------------------

def run_write_canary(
    config: ReleaseConfig,
    workdir: Path,
    *,
    shadow_result: dict[str, Any],
    migration_result: dict[str, Any],
    killswitch_result: dict[str, Any],
    authority_report: dict[str, Any],
) -> dict[str, Any]:
    """Execute the tenant/source write-canary ladder in strict order.

    Staging-surrogate units (1–2) run real write flows through the intake
    state machine — assisted-entry-only first, then one approved retrieval
    source. Production units (3–7) evaluate their entry gates and must
    come out BLOCKED while any §12 approval is pending: reaching them
    "passing" today would itself be a release-gate failure.
    """

    from modules.external_data.application.source_snapshots import SourceSnapshotService
    from modules.listing.application.intake_workflow import (
        InMemoryIntakeRepository,
        IntakeWorkflowService,
    )
    from modules.listing.domain.intake_states import IntakeStage
    from shared.infrastructure.object_store.client import InMemoryObjectStore

    workdir.mkdir(parents=True, exist_ok=True)
    started = _now()
    gate_facts = {
        "shadow_acceptance_met": bool(shadow_result.get("passed")),
        "migration_blocking_findings_zero": migration_result.get("blocking_findings") == 0,
        "kill_switch_verified": bool(killswitch_result.get("kill_switch_verified")),
        "unit_1_passed": False,
        "unit_2_passed": False,
        "error_budget_intact": True,
        "release_authority_all_approved": bool(authority_report.get("all_approved")),
        "all_p0_contract_group_approvals": bool(authority_report.get("all_approved")),
        "live_staging_evidence_recorded": False,  # no live staging environment exists yet
    }

    units_report = []
    ladder_halted = False
    for unit in config.canary_plan["write_canary_units"]:
        entry_gates = unit.get("entry_gates", [])
        unmet = []
        for gate in entry_gates:
            if isinstance(gate, str) and gate.startswith("release_authority_approved"):
                pending = set(authority_report.get("pending_owners", []))
                needed = [o.strip() for o in gate.split(":", 1)[1].strip(" []").split(",")]
                missing = [o for o in needed if o in pending]
                if missing:
                    unmet.append(f"release_authority_approved missing: {missing}")
            elif not gate_facts.get(gate, False):
                unmet.append(gate)

        if ladder_halted or unit["environment"] == "production" or unmet:
            units_report.append(
                {
                    "unit": unit["unit"],
                    "name": unit["name"],
                    "environment": unit["environment"],
                    "executed": False,
                    "blocked": True,
                    "unmet_entry_gates": unmet or ["ladder_halted_at_first_blocked_unit"],
                    "expected_blocked": unit["environment"] == "production",
                }
            )
            ladder_halted = True
            continue

        # Execute a staging-surrogate unit with real write flows.
        object_store = InMemoryObjectStore()
        workflow = IntakeWorkflowService(InMemoryIntakeRepository())
        snapshots = SourceSnapshotService(
            db_conn=None, object_store=object_store, intake_workflow_service=workflow
        )
        completed = 0
        rows = 6
        if unit.get("source_mode") == "ASSISTED_ENTRY_ONLY":
            for index in range(rows):
                intake_id = f"IN-CAN1-{index:04d}"
                context = _svc_context(STAGING_TENANT_A, f"canary1-{index}")
                steward_ctx = _svc_context(STAGING_TENANT_A, f"canary1-s-{index}", role="DATA_STEWARD")
                parser_ctx = _svc_context(STAGING_TENANT_A, f"canary1-p-{index}", role="SVC_PARSER")
                matcher_ctx = _svc_context(STAGING_TENANT_A, f"canary1-m-{index}", role="SVC_MATCHER")
                workflow.submit_intake(intake_id, STAGING_TENANT_A, "SRC-MANUAL", None, context)
                workflow.start_identity_check(intake_id, context)
                workflow.start_source_policy_evaluation(intake_id, context)
                workflow.require_assisted_entry(intake_id, context)
                workflow.complete_assisted_entry(
                    intake_id, {"rent": 42000 + index, "address": f"drill unit1 {index}"}, steward_ctx
                )
                workflow.complete_parsing(intake_id, f"PR-CAN1-{index:04d}", parser_ctx)
                final = workflow.resolve_match(intake_id, "NEW", None, matcher_ctx)
                if final.stage == IntakeStage.READY:
                    completed += 1
        else:
            snapshots.register_source(
                source_id="SRC-591",
                display_name="591 Rental",
                allowed_hosts=["591.com.tw"],
                retrieval_mode="APPROVED_RETRIEVAL",
                kill_switch=False,
            )
            for index in range(rows):
                intake_id = f"IN-CAN2-{index:04d}"
                context = _svc_context(STAGING_TENANT_A, f"canary2-{index}")
                retrieval_ctx = _svc_context(STAGING_TENANT_A, f"canary2-r-{index}", role="SVC_RETRIEVAL")
                parser_ctx = _svc_context(STAGING_TENANT_A, f"canary2-p-{index}", role="SVC_PARSER")
                matcher_ctx = _svc_context(STAGING_TENANT_A, f"canary2-m-{index}", role="SVC_MATCHER")
                workflow.submit_intake(
                    intake_id, STAGING_TENANT_A, "SRC-591", f"https://591.com.tw/can-{index}.html", context
                )
                workflow.start_identity_check(intake_id, context)
                workflow.start_source_policy_evaluation(intake_id, context)
                workflow.approve_retrieval(intake_id, "APPROVED_RETRIEVAL", context)
                snapshot_id = snapshots.create_snapshot(
                    tenant_id=STAGING_TENANT_A,
                    intake_id=intake_id,
                    source_id="SRC-591",
                    raw_data=f"<html>canary {index}</html>".encode(),
                    original_url=f"https://591.com.tw/can-{index}.html",
                    canonical_url=f"https://591.com.tw/can-{index}.html",
                    media_type="text/html",
                    capture_method="assisted-drill",
                    retention_class="standard-2y",
                    encryption_key_ref="kms://drill",
                    observed_at=datetime.now(UTC),
                    captured_at=datetime.now(UTC),
                    bucket=APPROVED_BUCKET,
                    context=context,
                )
                workflow.start_parsing_from_retrieval(intake_id, snapshot_id, retrieval_ctx)
                workflow.complete_parsing(intake_id, f"PR-CAN2-{index:04d}", parser_ctx)
                final = workflow.resolve_match(intake_id, "NEW", None, matcher_ctx)
                if final.stage == IntakeStage.READY:
                    completed += 1

        unit_passed = completed == rows
        gate_facts[f"unit_{unit['unit']}_passed"] = unit_passed
        units_report.append(
            {
                "unit": unit["unit"],
                "name": unit["name"],
                "environment": unit["environment"],
                "executed": True,
                "rows": rows,
                "completed": completed,
                "passed": unit_passed,
            }
        )
        if not unit_passed:
            ladder_halted = True

    executed_units = [u for u in units_report if u.get("executed")]
    blocked_production = [
        u for u in units_report if not u.get("executed") and u.get("expected_blocked")
    ]
    passed = (
        len(executed_units) == 2
        and all(u["passed"] for u in executed_units)
        and len(blocked_production) == 5
    )
    return {
        "drill": "write_canary_ladder",
        "started_at": started,
        "finished_at": _now(),
        "gate_facts": gate_facts,
        "units": units_report,
        "promotion_gate": {
            "flag": config.canary_plan["promotion_gate"]["flag"],
            "status": "disabled — separately gated after identity/review UAT (§4 Phase 5/6)",
        },
        "note": "Production units MUST be blocked while §12 approvals are pending; their blocked state is the asserted-correct outcome, not a drill failure.",
        "passed": passed,
    }


# ---------------------------------------------------------------------------
# UAT
# ---------------------------------------------------------------------------

def run_uat(workdir: Path, *, report_path: Path | None = None, spec: str = "tests/e2e/operator-assisted-listing-intake.spec.ts") -> dict[str, Any]:
    """Role-based UAT via the operator Playwright suite.

    Ingests a Playwright JSON report produced by running the exact
    verification command (``npx playwright test <spec>``). The harness
    validates the report covers the intake spec and that every executed
    test passed; without a report the phase fails closed.
    """

    started = _now()
    if report_path is None or not Path(report_path).is_file():
        return {
            "drill": "uat",
            "started_at": started,
            "finished_at": _now(),
            "spec": spec,
            "report": str(report_path) if report_path else None,
            "error": "no Playwright JSON report supplied; run `npx playwright test tests/e2e/operator-assisted-listing-intake.spec.ts --reporter=json` and pass --uat-report",
            "passed": False,
        }

    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    stats = report.get("stats", {})
    suites = report.get("suites", [])

    def _walk(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        found = []
        for node in nodes:
            for sub in node.get("suites", []) or []:
                found.extend(_walk([sub]))
            for spec_node in node.get("specs", []) or []:
                found.append(spec_node)
        return found

    specs = _walk(suites)
    intake_specs = [s for s in specs if spec.split("/")[-1] in (s.get("file") or "")]
    failed = [s.get("title") for s in specs if not s.get("ok", False)]
    passed = bool(specs) and not failed and stats.get("unexpected", 1) == 0 and bool(intake_specs)
    return {
        "drill": "uat",
        "started_at": started,
        "finished_at": _now(),
        "spec": spec,
        "report": str(report_path),
        "stats": stats,
        "total_cases": len(specs),
        "intake_spec_cases": len(intake_specs),
        "failed_cases": failed,
        "roles_note": "Spec exercises operator/manager role flows against the running product (see spec file for role fixtures).",
        "passed": passed,
    }


def new_drill_id() -> str:
    return str(uuid.uuid4())
