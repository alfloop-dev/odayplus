"""Staging backfill, reconciliation, and rollback execution engine (ODP-INTAKE-MIGRATION-001).

Implements versioned mapping, partition backfill (by tenant, source, and month),
dry-run isolation, resume logic, shadow proof verification, and automated lineage validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from shared.infrastructure.persistence import assisted_listing_intake as intake_schema

logger = logging.getLogger("assisted-listing-intake-migration")

FINDING_TYPE_MAP = {
    "MISSING_EVIDENCE": "MISSING_EVIDENCE",
    "DUPLICATE_CANDIDATE": "DUPLICATE_CANDIDATE",
    "STATE_MAPPING_CONFLICT": "STATE_MAPPING_CONFLICT",
    "COUNT_MISMATCH": "COUNT_MISMATCH",
    "CHECKSUM_MISMATCH": "CHECKSUM_MISMATCH",
    "SHADOW_COMPARISON_PROOF": "SHADOW_COMPARISON_PROOF",
    "MISSING_PARSER_PROVENANCE": "MISSING_EVIDENCE",
    "MISSING_SNAPSHOT_PROVENANCE": "MISSING_EVIDENCE",
    "MISSING_TIMESTAMP_PARTITION": "INVALID_SCOPE",
    "MISSING_ADDRESS": "STATE_MAPPING_CONFLICT",
    "MISSING_REDIRECT_TARGET_PROPERTY": "ORPHAN_REFERENCE",
    "MISSING_INTAKE_LINEAGE": "ORPHAN_REFERENCE",
    "MISSING_SHADOW_PROOF": "COUNT_MISMATCH",
}

SOURCE_KIND_MAP = {
    "legacy_listing": "legacy_listing",
    "legacy_candidate": "legacy_candidate",
    "snapshot": "snapshot",
    "identity": "identity",
    "audit": "audit",
    "job": "job",
    "scope": "scope",
    "schema": "schema",
    "parser_release": "schema",
    "legacy_intake": "snapshot",
    "shadow_proof": "snapshot",
}


def ensure_uuid(val: Any, default: str | None = None) -> str | None:
    """Ensure a string or object is returned as a valid UUID string."""
    if not val:
        return default
    try:
        return str(uuid.UUID(str(val)))
    except ValueError:
        try:
            return str(uuid.uuid5(uuid.NAMESPACE_DNS, str(val)))
        except Exception:
            return default


def get_val(obj: Any, key: str, default: Any = None) -> Any:
    """Safely get a value from a dictionary or an object attribute."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def calculate_checksum(ids: Iterable[str]) -> str:
    """Compute deterministic SHA-256 checksum for a collection of string IDs."""
    sorted_ids = sorted(str(x) for x in ids if str(x).strip())
    content = ",".join(sorted_ids)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class IntakeMigrator:
    """Handles migration execution, partition backfill, reconciliation, and rollback."""

    def __init__(self, db_conn: Any, migration_ref: str = "ODP-INTAKE-MIGRATION-001") -> None:
        self.db_conn = db_conn
        self.migration_ref = migration_ref
        self._expected_counts: dict[str, dict[str, int]] = {}
        self._expected_checksums: dict[str, dict[str, str]] = {}
        if not self._is_sqlite() and hasattr(self.db_conn, "autocommit"):
            try:
                self.db_conn.autocommit = False
            except Exception:
                pass

    def _is_sqlite(self) -> bool:
        conn_str = str(type(self.db_conn)).lower()
        if "psycopg" in conn_str or "postgres" in conn_str:
            return False
        return "sqlite" in conn_str or hasattr(self.db_conn, "row_factory")

    def _execute_count(self, sql: str, params: tuple = (), tenant_id: str | None = None) -> int:
        is_sqlite = self._is_sqlite()
        if is_sqlite:
            for s in ("intake.", "identity.", "expansion.", "workflow.", "audit."):
                sql = sql.replace(s, "")
            sql = sql.replace("%s", "?")
            cur = self.db_conn.cursor()
            cur.execute(sql, params)
            return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        else:
            cur = self.db_conn.cursor()
            if tenant_id:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            cur.execute(sql, params)
            return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0

    def _execute(self, sql: str, params: tuple = (), tenant_id: str | None = None) -> list[dict[str, Any]]:
        is_sqlite = self._is_sqlite()
        if is_sqlite:
            for s in ("intake.", "identity.", "expansion.", "workflow.", "audit."):
                sql = sql.replace(s, "")
            sql = sql.replace("%s", "?")
            if hasattr(self.db_conn, "query"):
                with self.db_conn.lock:
                    res = self.db_conn.query(sql, params)
                    return [dict(row) for row in res]
            else:
                cur = self.db_conn.cursor()
                cur.execute(sql, params)
                if cur.description:
                    columns = [col[0] for col in cur.description]
                    return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]
                return []
        else:
            cur = self.db_conn.cursor()
            if tenant_id:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            cur.execute(sql, params)
            if cur.description:
                columns = [col[0] for col in cur.description]
                return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]
            return []

    def apply_schema(self) -> None:
        """Apply the ordered PostgreSQL upgrade schema if it doesn't exist."""
        is_sqlite = self._is_sqlite()
        exists = False
        if is_sqlite:
            res = self._execute("SELECT name FROM sqlite_master WHERE type='table' AND name='source_registry'")
            if not res:
                res = self._execute("SELECT name FROM sqlite_master WHERE type='table' AND name='intakes'")
            exists = len(res) > 0
        else:
            res = self._execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='intake' AND table_name='intakes'"
            )
            exists = len(res) > 0

        if not exists:
            logger.info("Applying Assisted Listing Intake schema upgrade steps...")
            if is_sqlite:
                for _name, sql in intake_schema.upgrade_statements():
                    self._execute_raw_sql(sql)
            else:
                intake_schema.apply_upgrade(self.db_conn.cursor().execute)
            logger.info("Schema applied successfully.")

        if hasattr(self.db_conn, "commit"):
            self.db_conn.commit()

    def _execute_raw_sql(self, sql: str) -> None:
        lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
        clean_sql = "\n".join(lines)
        statements = clean_sql.split(";")
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            if any(k in stmt for k in ["ROW LEVEL SECURITY", "POLICY", "CREATE SCHEMA", "COMMENT ON", "DO $$", "DO $tenant_rls$", "tenant_tables regclass", "END LOOP", "END\n$tenant_rls$"]):
                continue
            if stmt.startswith("CREATE EXTENSION") or (stmt.startswith("ALTER TABLE") and ("ADD CONSTRAINT" in stmt or "DROP CONSTRAINT" in stmt or "ADD COLUMN IF NOT EXISTS" in stmt)):
                continue
            stmt = stmt.replace("timestamptz", "timestamp")
            stmt = stmt.replace("jsonb", "text")
            stmt = stmt.replace("DEFAULT gen_random_uuid()", "")
            stmt = stmt.replace("DEFAULT now()", "DEFAULT CURRENT_TIMESTAMP")
            stmt = stmt.replace("uuid PRIMARY KEY DEFAULT gen_random_uuid()", "uuid PRIMARY KEY")
            stmt = stmt.replace("uuid[]", "text")
            stmt = stmt.replace("text[]", "text")
            try:
                self._execute(stmt)
            except Exception as e:
                logger.debug("SQLite schema adjustment ignored statement: %s (Error: %s)", stmt, e)

    def rollback_schema(self) -> None:
        """Drop the upgrade schemas (only for clean test sandbox tear-down)."""
        is_sqlite = self._is_sqlite()
        if is_sqlite:
            for table in reversed(intake_schema.TENANT_TABLES + intake_schema.NON_TENANT_TABLES):
                self._execute(f"DROP TABLE IF EXISTS {table}")
        else:
            intake_schema.apply_downgrade(self.db_conn.cursor().execute)

    def rollback_migration(self, migration_ref: str | None = None, tenant_id: str | None = None) -> int:
        """Perform full scoped data rollback for a migration_ref without dropping tables or destroying unrelated tenant data."""
        target_ref = migration_ref or self.migration_ref
        deleted_count = 0
        tenant_uuid = ensure_uuid(tenant_id) if tenant_id else None

        # 1. Scoped deletion of candidate_sites and property_redirects via promotion_decisions
        if tenant_uuid:
            deleted_count += self._execute_count(
                "DELETE FROM expansion.candidate_sites WHERE tenant_id = %s AND promotion_decision_id IN (SELECT promotion_decision_id FROM expansion.promotion_decisions WHERE migration_ref = %s AND tenant_id = %s)",
                (tenant_uuid, target_ref, tenant_uuid),
                tenant_id=tenant_uuid,
            )
            deleted_count += self._execute_count(
                "DELETE FROM identity.property_redirects WHERE tenant_id = %s AND decision_id IN (SELECT promotion_decision_id FROM expansion.promotion_decisions WHERE migration_ref = %s AND tenant_id = %s)",
                (tenant_uuid, target_ref, tenant_uuid),
                tenant_id=tenant_uuid,
            )
            deleted_count += self._execute_count(
                "DELETE FROM expansion.promotion_decisions WHERE migration_ref = %s AND tenant_id = %s",
                (target_ref, tenant_uuid),
                tenant_id=tenant_uuid,
            )
        else:
            deleted_count += self._execute_count(
                "DELETE FROM expansion.candidate_sites WHERE promotion_decision_id IN (SELECT promotion_decision_id FROM expansion.promotion_decisions WHERE migration_ref = %s)",
                (target_ref,),
            )
            deleted_count += self._execute_count(
                "DELETE FROM identity.property_redirects WHERE decision_id IN (SELECT promotion_decision_id FROM expansion.promotion_decisions WHERE migration_ref = %s)",
                (target_ref,),
            )
            deleted_count += self._execute_count(
                "DELETE FROM expansion.promotion_decisions WHERE migration_ref = %s",
                (target_ref,),
            )

        # 2. Scope intake/identity/expansion deletions strictly to migration-created intakes and listings
        if tenant_uuid:
            mig_intakes = self._execute(
                "SELECT DISTINCT intake_id FROM intake.intake_stage_transitions WHERE tenant_id = %s AND service_principal = 'migration_worker' AND permission = 'intake:backfill'",
                (tenant_uuid,),
                tenant_id=tenant_uuid,
            )
        else:
            mig_intakes = self._execute(
                "SELECT DISTINCT intake_id FROM intake.intake_stage_transitions WHERE service_principal = 'migration_worker' AND permission = 'intake:backfill'"
            )
        mig_intake_ids = [r["intake_id"] for r in mig_intakes]

        if mig_intake_ids:
            for i_id in mig_intake_ids:
                # Match decisions / candidates / cases
                deleted_count += self._execute_count(
                    "DELETE FROM identity.match_decisions WHERE match_case_id IN (SELECT match_case_id FROM identity.match_cases WHERE intake_id = %s)",
                    (i_id,),
                    tenant_id=tenant_uuid,
                )
                deleted_count += self._execute_count(
                    "DELETE FROM identity.match_candidates WHERE match_case_id IN (SELECT match_case_id FROM identity.match_cases WHERE intake_id = %s)",
                    (i_id,),
                    tenant_id=tenant_uuid,
                )
                deleted_count += self._execute_count(
                    "DELETE FROM identity.match_cases WHERE intake_id = %s",
                    (i_id,),
                    tenant_id=tenant_uuid,
                )

                # Listings and identity edges associated with migration intakes
                res_listing_rows = self._execute(
                    "SELECT resolved_listing_id FROM intake.intakes WHERE intake_id = %s AND resolved_listing_id IS NOT NULL",
                    (i_id,),
                    tenant_id=tenant_uuid,
                )
                for l_row in res_listing_rows:
                    l_id = l_row["resolved_listing_id"]
                    deleted_count += self._execute_count(
                        "DELETE FROM identity.source_identity_edges WHERE listing_id = %s",
                        (l_id,),
                        tenant_id=tenant_uuid,
                    )
                    deleted_count += self._execute_count(
                        "DELETE FROM expansion.listing_observations WHERE listing_id = %s",
                        (l_id,),
                        tenant_id=tenant_uuid,
                    )
                    deleted_count += self._execute_count(
                        "DELETE FROM expansion.listing_revisions WHERE listing_id = %s",
                        (l_id,),
                        tenant_id=tenant_uuid,
                    )
                    self._execute(
                        "UPDATE intake.intakes SET resolved_listing_id = NULL WHERE intake_id = %s",
                        (i_id,),
                        tenant_id=tenant_uuid,
                    )
                    deleted_count += self._execute_count(
                        "DELETE FROM expansion.listings WHERE listing_id = %s",
                        (l_id,),
                        tenant_id=tenant_uuid,
                    )
                    deleted_count += self._execute_count(
                        "DELETE FROM identity.properties WHERE property_id NOT IN (SELECT property_id FROM expansion.listings)",
                        tenant_id=tenant_uuid,
                    )

                deleted_count += self._execute_count(
                    "DELETE FROM intake.human_corrections WHERE intake_id = %s",
                    (i_id,),
                    tenant_id=tenant_uuid,
                )
                deleted_count += self._execute_count(
                    "DELETE FROM intake.parser_runs WHERE intake_id = %s",
                    (i_id,),
                    tenant_id=tenant_uuid,
                )
                deleted_count += self._execute_count(
                    "DELETE FROM intake.source_snapshots WHERE intake_id = %s",
                    (i_id,),
                    tenant_id=tenant_uuid,
                )
                deleted_count += self._execute_count(
                    "DELETE FROM intake.intake_stage_transitions WHERE intake_id = %s",
                    (i_id,),
                    tenant_id=tenant_uuid,
                )
                deleted_count += self._execute_count(
                    "DELETE FROM intake.intakes WHERE intake_id = %s",
                    (i_id,),
                    tenant_id=tenant_uuid,
                )

        # 3. Outbox and Audit scoped deletion
        if tenant_uuid:
            deleted_count += self._execute_count(
                "DELETE FROM workflow.outbox_events WHERE tenant_id = %s AND event_type = 'CandidateSitePromoted' AND aggregate_type = 'CandidateSite'",
                (tenant_uuid,),
                tenant_id=tenant_uuid,
            )
            deleted_count += self._execute_count(
                "DELETE FROM audit.audit_events WHERE tenant_id = %s AND action = 'BACKFILL' AND event_type = 'CANDIDATE_BACKFILL'",
                (tenant_uuid,),
                tenant_id=tenant_uuid,
            )
            deleted_count += self._execute_count(
                "DELETE FROM workflow.reconciliation_findings WHERE migration_id = %s AND tenant_id = %s",
                (target_ref, tenant_uuid),
                tenant_id=tenant_uuid,
            )
        else:
            deleted_count += self._execute_count(
                "DELETE FROM workflow.outbox_events WHERE event_type = 'CandidateSitePromoted' AND aggregate_type = 'CandidateSite'"
            )
            deleted_count += self._execute_count(
                "DELETE FROM audit.audit_events WHERE action = 'BACKFILL' AND event_type = 'CANDIDATE_BACKFILL'"
            )
            deleted_count += self._execute_count(
                "DELETE FROM workflow.reconciliation_findings WHERE migration_id = %s",
                (target_ref,),
            )

        if hasattr(self.db_conn, "commit"):
            self.db_conn.commit()
        return deleted_count

    def register_sources_and_parsers(
        self,
        sources: list[tuple[str, str, str, list[str]]] | None = None,
        parser_release: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Register sources and parser releases using supplied verified provenance."""
        default_sources = [
            ("SRC-591", "591 licensed broker intake", "APPROVED_RETRIEVAL", ["591.com.tw"]),
            ("SRC-BROKER", "Broker confirmation", "ASSISTED_ENTRY_ONLY", []),
        ]
        target_sources = sources or default_sources

        for src_id, name, mode, hosts in target_sources:
            check_sql = "SELECT source_id FROM intake.source_registry WHERE source_id = %s"
            if not self._execute(check_sql, (src_id,)):
                insert_sql = """
                    INSERT INTO intake.source_registry (
                        source_id, display_name, retrieval_mode, canonicalization_rule_version,
                        allowed_hosts, policy_owner_subject_id, version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                hosts_val = json.dumps(hosts) if self._is_sqlite() else hosts
                self._execute(
                    insert_sql,
                    (
                        src_id,
                        name,
                        mode,
                        "v1.0",
                        hosts_val,
                        ensure_uuid("system"),
                        1,
                    ),
                )

        if not parser_release:
            if tenant_id:
                self._create_finding(
                    tenant_id=ensure_uuid(tenant_id),
                    source_kind="schema",
                    source_id="parser_release",
                    target_ids=[ensure_uuid("parser-missing")],
                    finding_type="MISSING_EVIDENCE",
                    severity="WARNING",
                    expected={"parser_release": "dict with artifact_uri and artifact_sha256"},
                    actual={"parser_release": None},
                )
            return

        sem_ver = get_val(parser_release, "semantic_version") or "1.4"
        art_uri = get_val(parser_release, "artifact_uri")
        art_sha = get_val(parser_release, "artifact_sha256")

        if not art_uri or not art_sha:
            if tenant_id:
                self._create_finding(
                    tenant_id=ensure_uuid(tenant_id),
                    source_kind="schema",
                    source_id=sem_ver,
                    target_ids=[ensure_uuid(f"parser-{sem_ver}")],
                    finding_type="MISSING_EVIDENCE",
                    severity="BLOCKING",
                    expected={"artifact_uri": "non-empty URI", "artifact_sha256": "64-char sha256"},
                    actual={"artifact_uri": art_uri, "artifact_sha256": art_sha},
                )
            return

        check_parser = "SELECT parser_release_id FROM intake.parser_releases WHERE semantic_version = %s"
        if not self._execute(check_parser, (sem_ver,)):
            insert_parser = """
                INSERT INTO intake.parser_releases (
                    parser_release_id, source_id, package_name, semantic_version,
                    input_schema_version, output_schema_version, artifact_uri,
                    artifact_sha256, test_corpus_version, validation_status, version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            parser_id = ensure_uuid(get_val(parser_release, "parser_release_id") or f"parser-{sem_ver}")
            val_status = get_val(parser_release, "validation_status") or "VALIDATED"
            pkg_name = get_val(parser_release, "package_name") or "listing-parser"
            src_id = get_val(parser_release, "source_id") or "SRC-591"

            self._execute(
                insert_parser,
                (
                    parser_id,
                    src_id,
                    pkg_name,
                    sem_ver,
                    "v1.0",
                    "v1.0",
                    art_uri,
                    art_sha,
                    "v1.0",
                    val_status,
                    1,
                ),
            )

    def backfill(
        self,
        legacy_intakes: list[dict[str, Any]],
        legacy_listings: list[Any],
        legacy_candidates: list[Any],
        dry_run: bool = False,
        resume: bool = False,
        tenant_id: str | None = None,
        source_id: str | None = None,
        month: str | None = None,
        sources: list[tuple[str, str, str, list[str]]] | None = None,
        parser_release: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform the staging backfill. Returns count proofs and reconciliation report."""
        self.apply_schema()
        target_tenant = ensure_uuid(tenant_id or "00000000-0000-0000-0000-000000000001")
        self.register_sources_and_parsers(sources=sources, parser_release=parser_release, tenant_id=target_tenant)

        reconciled_count = 0
        skipped_count = 0
        quarantined_count = 0
        findings_count = 0

        # Pre-pass: map addresses, sources, and real legacy timestamps ONLY
        listing_to_address: dict[str, Any] = {}
        listing_to_month: dict[str, str | None] = {}
        listing_to_source: dict[str, str] = {}
        intake_resolved_listings: dict[str, str] = {}

        for draft in legacy_candidates:
            lst = get_val(draft, "listing")
            addr = get_val(draft, "address")
            if lst:
                lst_id = get_val(lst, "listing_id") or get_val(lst, "id")
                if lst_id:
                    lst_uuid = ensure_uuid(lst_id)
                    if addr:
                        listing_to_address[lst_uuid] = addr
                    c_src = get_val(lst, "source_id") or get_val(lst, "sourceId") or "SRC-591"
                    listing_to_source[lst_uuid] = c_src

        for intake in legacy_intakes:
            i_source = intake.get("sourceId") or intake.get("source_id") or "SRC-591"
            i_date_str = intake.get("submittedAt") or intake.get("firstSeenAt") or intake.get("submitted_at")
            i_month = i_date_str[:7] if (i_date_str and isinstance(i_date_str, str)) else None
            match_res = intake.get("matchResult") or {}
            lst_id = match_res.get("targetListingId") or intake.get("resolved_listing_id") or intake.get("resolvedListingId")
            if lst_id:
                lst_uuid = ensure_uuid(lst_id)
                intake_uuid = ensure_uuid(intake.get("id"))
                if intake_uuid:
                    intake_resolved_listings[intake_uuid] = lst_uuid
                if i_month:
                    listing_to_month[lst_uuid] = i_month
                listing_to_source[lst_uuid] = i_source
                parsed = intake.get("parsedFields") or {}
                if lst_uuid not in listing_to_address and parsed.get("address"):
                    listing_to_address[lst_uuid] = {
                        "normalized_address": parsed.get("address"),
                        "raw_address": parsed.get("address"),
                        "latitude": parsed.get("latitude"),
                        "longitude": parsed.get("longitude"),
                    }

        processed_intake_uuids: list[str] = []
        processed_listing_uuids: list[str] = []
        processed_candidate_uuids: list[str] = []

        try:
            # 1. Backfill Intakes
            for legacy_intake in legacy_intakes:
                l_id = legacy_intake.get("id")
                i_tenant = ensure_uuid(legacy_intake.get("tenantId") or legacy_intake.get("tenant_id") or tenant_id or "tenant-a")
                i_source = legacy_intake.get("sourceId") or legacy_intake.get("source_id") or "SRC-591"
                i_date_str = legacy_intake.get("submittedAt") or legacy_intake.get("firstSeenAt") or legacy_intake.get("submitted_at")
                i_month = i_date_str[:7] if (i_date_str and isinstance(i_date_str, str)) else None

                if tenant_id and i_tenant != ensure_uuid(tenant_id):
                    continue
                if source_id and i_source != source_id:
                    continue
                if month:
                    if not i_month or i_month != month:
                        findings_count += 1
                        quarantined_count += 1
                        self._create_finding(
                            tenant_id=i_tenant,
                            source_kind="snapshot",
                            source_id=l_id,
                            target_ids=[ensure_uuid(l_id)],
                            finding_type="INVALID_SCOPE",
                            severity="WARNING",
                            expected={"month_partition": month},
                            actual={"month_partition": i_month},
                        )
                        continue

                intake_uuid = ensure_uuid(l_id)

                if resume:
                    check_exists = "SELECT intake_id FROM intake.intakes WHERE intake_id = %s AND tenant_id = %s"
                    if self._execute(check_exists, (intake_uuid, i_tenant), tenant_id=i_tenant):
                        skipped_count += 1
                        continue

                self._execute("SELECT 1", tenant_id=i_tenant)

                url = legacy_intake.get("originalUrl") or legacy_intake.get("sourceUrl") or ""
                canon_url = legacy_intake.get("canonicalUrl") or url
                canon_url_sha = hashlib.sha256(canon_url.encode()).hexdigest() if canon_url else None
                stage = legacy_intake.get("stage") or "READY"
                source_policy = legacy_intake.get("sourcePolicyState") or legacy_intake.get("source_policy_state") or "POLICY_UNKNOWN"
                sub_time = i_date_str or datetime.now(UTC).isoformat()

                if not url:
                    findings_count += 1
                    self._create_finding(
                        tenant_id=i_tenant,
                        source_kind="legacy_listing",
                        source_id=l_id,
                        target_ids=[intake_uuid],
                        finding_type="MISSING_EVIDENCE",
                        severity="WARNING",
                        expected={"originalUrl": "non-empty string"},
                        actual={"originalUrl": None},
                    )

                insert_intake_sql = """
                    INSERT INTO intake.intakes (
                        intake_id, tenant_id, heat_zone_id, submitter_subject_id,
                        intake_method, original_url, canonical_url, canonical_url_sha256,
                        source_id, source_policy_state, processing_state, resolved_listing_id,
                        correlation_id, version, submitted_at, last_transition_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                self._execute(
                    insert_intake_sql,
                    (
                        intake_uuid,
                        i_tenant,
                        ensure_uuid(legacy_intake.get("heatZoneId") or legacy_intake.get("heat_zone_id")),
                        ensure_uuid("system"),
                        "URL",
                        url,
                        canon_url,
                        canon_url_sha,
                        i_source,
                        source_policy,
                        stage,
                        None,
                        ensure_uuid(legacy_intake.get("correlationId") or f"corr-{l_id}"),
                        1,
                        sub_time,
                        sub_time,
                    ),
                    tenant_id=i_tenant,
                )

                corr_id = ensure_uuid(legacy_intake.get("correlationId") or f"corr-{l_id}")
                insert_trans_sql = """
                    INSERT INTO intake.intake_stage_transitions (
                        transition_id, tenant_id, intake_id, sequence_no, from_state,
                        to_state, actor_subject_id, service_principal, permission, resulting_version, correlation_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                self._execute(
                    insert_trans_sql,
                    (
                        ensure_uuid(f"TRANS-{l_id}"),
                        i_tenant,
                        intake_uuid,
                        1,
                        None,
                        stage,
                        ensure_uuid("system"),
                        "migration_worker",
                        "intake:backfill",
                        1,
                        corr_id,
                    ),
                    tenant_id=i_tenant,
                )

                snap_id = ensure_uuid(legacy_intake.get("snapshotId") or f"SNAP-{l_id}")
                raw_uri = legacy_intake.get("rawObjectUri") or legacy_intake.get("raw_object_uri")
                snap_sha = legacy_intake.get("rawSnapshotSha256")

                if not raw_uri:
                    findings_count += 1
                    self._create_finding(
                        tenant_id=i_tenant,
                        source_kind="snapshot",
                        source_id=l_id,
                        target_ids=[intake_uuid],
                        finding_type="MISSING_EVIDENCE",
                        severity="WARNING",
                        expected={"rawObjectUri": "valid artifact URI"},
                        actual={"rawObjectUri": None},
                    )
                    raw_uri = f"unverified://missing-snapshot/{snap_id}"

                if "rawSnapshot" in legacy_intake:
                    raw_snap = legacy_intake["rawSnapshot"]
                    if isinstance(raw_snap, bytes):
                        raw_snap_bytes = raw_snap
                    elif isinstance(raw_snap, str):
                        raw_snap_bytes = raw_snap.encode("utf-8")
                    else:
                        raw_snap_bytes = json.dumps(raw_snap, sort_keys=True, ensure_ascii=False).encode("utf-8")

                    computed_sha = hashlib.sha256(raw_snap_bytes).hexdigest()
                    final_sha = snap_sha or computed_sha

                    insert_snap_sql = """
                        INSERT INTO intake.source_snapshots (
                            source_snapshot_id, tenant_id, intake_id, source_id,
                            original_url, canonical_url, raw_object_uri, content_sha256,
                            media_type, byte_length, captured_at, observed_at,
                            capture_method, retention_class, encryption_key_ref, version
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    self._execute(
                        insert_snap_sql,
                        (
                            snap_id,
                            i_tenant,
                            intake_uuid,
                            i_source,
                            url,
                            canon_url,
                            raw_uri,
                            final_sha,
                            "application/json",
                            len(raw_snap_bytes),
                            sub_time,
                            sub_time,
                            "SERVER_RETRIEVAL",
                            "STANDARD",
                            "kms://default-key",
                            1,
                        ),
                        tenant_id=i_tenant,
                    )

                    if "parsedFields" in legacy_intake:
                        parser_rel_id = ensure_uuid(get_val(parser_release, "parser_release_id") or f"parser-{get_val(parser_release, 'semantic_version') or '1.4'}")
                        check_pr_sql = "SELECT parser_release_id FROM intake.parser_releases WHERE parser_release_id = %s"
                        if self._execute(check_pr_sql, (parser_rel_id,)):
                            parsed_fields = legacy_intake["parsedFields"]
                            insert_parser_run_sql = """
                                INSERT INTO intake.parser_runs (
                                    parser_run_id, tenant_id, intake_id, source_snapshot_id,
                                    parser_release_id, status, parsed_payload, normalized_payload,
                                    correlation_id, version
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """
                            parser_run_id = ensure_uuid(f"PRUN-{l_id}")
                            self._execute(
                                insert_parser_run_sql,
                                (
                                    parser_run_id,
                                    i_tenant,
                                    intake_uuid,
                                    snap_id,
                                    parser_rel_id,
                                    "SUCCEEDED",
                                    json.dumps(parsed_fields),
                                    json.dumps(parsed_fields),
                                    ensure_uuid("system"),
                                    1,
                                ),
                                tenant_id=i_tenant,
                            )
                        else:
                            findings_count += 1
                            self._create_finding(
                                tenant_id=i_tenant,
                                source_kind="parser_release",
                                source_id=l_id,
                                target_ids=[intake_uuid],
                                finding_type="MISSING_EVIDENCE",
                                severity="WARNING",
                                expected={"parser_release_exists": True},
                                actual={"parser_release_exists": False},
                            )

                if "humanCorrections" in legacy_intake or "corrections" in legacy_intake:
                    corrections = legacy_intake.get("humanCorrections") or legacy_intake.get("corrections") or []
                    for corr in corrections:
                        corr_id = ensure_uuid(get_val(corr, "correction_id") or f"CORR-{uuid.uuid4()}")
                        insert_corr_sql = """
                            INSERT INTO intake.human_corrections (
                                correction_id, tenant_id, intake_id, listing_id, field_path,
                                field_classification, parsed_value, normalized_value, corrected_value,
                                after_effective_value, reason, proposed_by, status, version
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                        """
                        self._execute(
                            insert_corr_sql,
                            (
                                corr_id,
                                i_tenant,
                                intake_uuid,
                                ensure_uuid(get_val(corr, "listing_id")),
                                get_val(corr, "field_path") or "parsedFields.rent",
                                get_val(corr, "field_classification") or "PUBLIC",
                                json.dumps(get_val(corr, "parsed_value")),
                                json.dumps(get_val(corr, "normalized_value")),
                                json.dumps(get_val(corr, "corrected_value")),
                                json.dumps(get_val(corr, "after_effective_value")),
                                get_val(corr, "reason") or "Legacy manual correction",
                                ensure_uuid(get_val(corr, "proposed_by") or "system"),
                                get_val(corr, "status") or "APPLIED",
                            ),
                            tenant_id=i_tenant,
                        )

                match_res = legacy_intake.get("matchResult")
                if match_res:
                    outcome = match_res.get("outcome") or "NEW"
                    match_case_id = ensure_uuid(f"MC-{l_id}")
                    insert_match_case_sql = """
                        INSERT INTO identity.match_cases (
                            match_case_id, tenant_id, intake_id, outcome, status,
                            confidence, proposed_by, version
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    self._execute(
                        insert_match_case_sql,
                        (
                            match_case_id,
                            i_tenant,
                            intake_uuid,
                            outcome,
                            "APPROVED",
                            float(match_res.get("confidence") or 1.0),
                            "system",
                            1,
                        ),
                        tenant_id=i_tenant,
                    )

                    for idx, cand_match in enumerate(match_res.get("candidates") or match_res.get("match_candidates") or [], start=1):
                        cand_match_id = ensure_uuid(f"MCAND-{l_id}-{idx}")
                        cand_prop_id = ensure_uuid(get_val(cand_match, "property_id"))
                        if cand_prop_id:
                            check_p = self._execute(
                                "SELECT property_id FROM identity.properties WHERE property_id = %s AND tenant_id = %s",
                                (cand_prop_id, i_tenant),
                                tenant_id=i_tenant,
                            )
                            if not check_p:
                                cand_prop_id = None

                        cand_lst_id = ensure_uuid(get_val(cand_match, "listing_id"))
                        if cand_lst_id:
                            check_l = self._execute(
                                "SELECT listing_id FROM expansion.listings WHERE listing_id = %s AND tenant_id = %s",
                                (cand_lst_id, i_tenant),
                                tenant_id=i_tenant,
                            )
                            if not check_l:
                                cand_lst_id = None

                        insert_cand_match_sql = """
                            INSERT INTO identity.match_candidates (
                                match_candidate_id, tenant_id, match_case_id, property_id,
                                listing_id, rank, confidence, evidence
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """
                        self._execute(
                            insert_cand_match_sql,
                            (
                                cand_match_id,
                                i_tenant,
                                match_case_id,
                                cand_prop_id,
                                cand_lst_id,
                                idx,
                                float(get_val(cand_match, "confidence") or 0.9),
                                json.dumps(get_val(cand_match, "evidence") or {}),
                            ),
                            tenant_id=i_tenant,
                        )

                    match_dec_id = ensure_uuid(f"MDEC-{l_id}")
                    insert_match_dec_sql = """
                        INSERT INTO identity.match_decisions (
                            match_decision_id, tenant_id, match_case_id, decision_type,
                            status, proposer_subject_id, reason, before_graph, after_graph, version
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """
                    self._execute(
                        insert_match_dec_sql,
                        (
                            match_dec_id,
                            i_tenant,
                            match_case_id,
                            "CREATE" if outcome == "NEW" else "DUPLICATE",
                            "APPROVED",
                            ensure_uuid("system"),
                            "Legacy match backfill",
                            "{}",
                            "{}",
                        ),
                        tenant_id=i_tenant,
                    )

                reconciled_count += 1
                processed_intake_uuids.append(intake_uuid)

            # 2. Backfill Listings
            for legacy_lst in legacy_listings:
                l_id = get_val(legacy_lst, "listing_id") or get_val(legacy_lst, "id")
                listing_uuid = ensure_uuid(l_id)
                i_tenant = ensure_uuid(tenant_id or get_val(legacy_lst, "tenant_id") or get_val(legacy_lst, "tenantId") or "tenant-a")
                i_source = get_val(legacy_lst, "source_id") or get_val(legacy_lst, "sourceId") or listing_to_source.get(listing_uuid) or "SRC-591"

                if tenant_id and i_tenant != ensure_uuid(tenant_id):
                    continue
                if source_id and i_source != source_id:
                    continue

                lst_month = listing_to_month.get(listing_uuid)
                if not lst_month:
                    date_str = (
                        get_val(legacy_lst, "submittedAt")
                        or get_val(legacy_lst, "submitted_at")
                        or get_val(legacy_lst, "observedAt")
                        or get_val(legacy_lst, "observed_at")
                        or get_val(legacy_lst, "firstSeenAt")
                    )
                    if date_str and isinstance(date_str, str):
                        lst_month = date_str[:7]
                        listing_to_month[listing_uuid] = lst_month

                if month:
                    if not lst_month or lst_month != month:
                        findings_count += 1
                        quarantined_count += 1
                        self._create_finding(
                            tenant_id=i_tenant,
                            source_kind="legacy_listing",
                            source_id=l_id,
                            target_ids=[listing_uuid],
                            finding_type="INVALID_SCOPE",
                            severity="WARNING",
                            expected={"month_partition": month},
                            actual={"month_partition": lst_month},
                        )
                        continue

                if resume:
                    check_exists = "SELECT listing_id FROM expansion.listings WHERE listing_id = %s AND tenant_id = %s"
                    if self._execute(check_exists, (listing_uuid, i_tenant), tenant_id=i_tenant):
                        skipped_count += 1
                        continue

                self._execute("SELECT 1", tenant_id=i_tenant)

                addr_obj = listing_to_address.get(listing_uuid)
                if addr_obj:
                    address_str = get_val(addr_obj, "normalized_address") or get_val(addr_obj, "raw_address")
                    lat = get_val(addr_obj, "latitude")
                    lng = get_val(addr_obj, "longitude")
                else:
                    address_str = get_val(legacy_lst, "address") or get_val(legacy_lst, "normalized_address")
                    lat = get_val(legacy_lst, "latitude")
                    lng = get_val(legacy_lst, "longitude")

                if not address_str:
                    findings_count += 1
                    quarantined_count += 1
                    self._create_finding(
                        tenant_id=i_tenant,
                        source_kind="legacy_listing",
                        source_id=l_id,
                        target_ids=[listing_uuid],
                        finding_type="STATE_MAPPING_CONFLICT",
                        severity="BLOCKING",
                        expected={"address": "valid string"},
                        actual={"address": None},
                    )
                    prop_status = "QUARANTINED"
                else:
                    prop_status = "ACTIVE"

                lat_val = float(lat) if lat is not None else None
                lng_val = float(lng) if lng is not None else None

                prop_addr_str = address_str or f"NO_ADDRESS_{listing_uuid}"
                prop_uuid = ensure_uuid(f"PROP-{prop_addr_str}")
                addr_fingerprint = hashlib.sha256(prop_addr_str.encode()).hexdigest()

                check_prop = "SELECT property_id FROM identity.properties WHERE property_id = %s AND tenant_id = %s"
                if not self._execute(check_prop, (prop_uuid, i_tenant), tenant_id=i_tenant):
                    insert_prop_sql = """
                        INSERT INTO identity.properties (
                            property_id, tenant_id, normalized_address, address_fingerprint,
                            latitude, longitude, status, version
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    self._execute(
                        insert_prop_sql,
                        (
                            prop_uuid,
                            i_tenant,
                            prop_addr_str,
                            addr_fingerprint,
                            lat_val,
                            lng_val,
                            prop_status,
                            1,
                        ),
                        tenant_id=i_tenant,
                    )

                status = get_val(legacy_lst, "listing_status") or get_val(legacy_lst, "status") or "ACTIVE"
                source_listing_id = get_val(legacy_lst, "source_listing_id") or get_val(legacy_lst, "sourceListingId") or f"src-{l_id}"
                snap_url = get_val(legacy_lst, "snapshot_id") or get_val(legacy_lst, "sourceUrl") or ""
                url_sha = hashlib.sha256(snap_url.encode()).hexdigest() if snap_url else None

                rev_uuid = ensure_uuid(f"REV-{l_id}")
                obs_uuid = ensure_uuid(f"OBS-{l_id}")

                insert_listing_sql = """
                    INSERT INTO expansion.listings (
                        listing_id, tenant_id, property_id, source_id, source_listing_id,
                        canonical_url_sha256, lifecycle_state, current_revision_id,
                        current_observation_id, version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s)
                """
                self._execute(
                    insert_listing_sql,
                    (
                        listing_uuid,
                        i_tenant,
                        prop_uuid,
                        i_source,
                        source_listing_id,
                        url_sha,
                        "ACTIVE" if status in ("new", "watching", "ACTIVE") else "REMOVED",
                        1,
                    ),
                    tenant_id=i_tenant,
                )

                for in_uuid, resolved_l_uuid in intake_resolved_listings.items():
                    if resolved_l_uuid == listing_uuid:
                        self._execute(
                            "UPDATE intake.intakes SET resolved_listing_id = %s WHERE intake_id = %s AND tenant_id = %s",
                            (listing_uuid, in_uuid, i_tenant),
                            tenant_id=i_tenant,
                        )

                rent = float(get_val(legacy_lst, "rent_amount") or get_val(legacy_lst, "rentPerMonth") or 0)
                area = float(get_val(legacy_lst, "area_ping") or get_val(legacy_lst, "areaPing") or 0)
                floor = get_val(legacy_lst, "floor") or ""

                normalized_vals = {
                    "rent": rent,
                    "areaPing": area,
                    "floor": floor,
                    "providerListingId": source_listing_id,
                }
                mat_fingerprint = hashlib.sha256(f"{rent}:{area}:{floor}".encode()).hexdigest()

                insert_rev_sql = """
                    INSERT INTO expansion.listing_revisions (
                        listing_revision_id, tenant_id, listing_id, revision_no,
                        revision_kind, normalized_values, effective_values,
                        material_fingerprint, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                self._execute(
                    insert_rev_sql,
                    (
                        rev_uuid,
                        i_tenant,
                        listing_uuid,
                        1,
                        "CREATED",
                        json.dumps(normalized_vals),
                        json.dumps(normalized_vals),
                        mat_fingerprint,
                        datetime.now(UTC).isoformat(),
                    ),
                    tenant_id=i_tenant,
                )

                obs_time = get_val(legacy_lst, "observed_at") or get_val(legacy_lst, "observedAt") or get_val(legacy_lst, "submittedAt") or datetime.now(UTC).isoformat()
                obs_kind = get_val(legacy_lst, "observation_kind") or "UNCHANGED"
                if obs_kind not in ('UNCHANGED', 'FRESHNESS_REFRESHED', 'REMOVED', 'UNAVAILABLE', 'BLOCKED', 'EXPIRED', 'STALE'):
                    obs_kind = "UNCHANGED"
                obs_evidence = json.dumps(get_val(legacy_lst, "evidence") or {"source_listing_id": source_listing_id})

                insert_obs_sql = """
                    INSERT INTO expansion.listing_observations (
                        listing_observation_id, tenant_id, listing_id,
                        observation_kind, observed_at, evidence
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                """
                self._execute(
                    insert_obs_sql,
                    (
                        obs_uuid,
                        i_tenant,
                        listing_uuid,
                        obs_kind,
                        obs_time,
                        obs_evidence,
                    ),
                    tenant_id=i_tenant,
                )

                # Update current_revision_id and current_observation_id on expansion.listings
                self._execute(
                    "UPDATE expansion.listings SET current_revision_id = %s, current_observation_id = %s WHERE listing_id = %s AND tenant_id = %s",
                    (rev_uuid, obs_uuid, listing_uuid, i_tenant),
                    tenant_id=i_tenant,
                )

                edge_uuid = ensure_uuid(f"EDGE-{l_id}")
                insert_edge_sql = """
                    INSERT INTO identity.source_identity_edges (
                        edge_id, tenant_id, source_id, source_entity_id,
                        listing_id, property_id, match_strategy, confidence,
                        edge_version, effective_from
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                self._execute(
                    insert_edge_sql,
                    (
                        edge_uuid,
                        i_tenant,
                        i_source,
                        source_listing_id,
                        listing_uuid,
                        prop_uuid,
                        "EXACT_SOURCE_KEY",
                        1.0,
                        1,
                        datetime.now(UTC).isoformat(),
                    ),
                    tenant_id=i_tenant,
                )

                if get_val(legacy_lst, "redirected_to_property_id"):
                    to_prop_uuid = ensure_uuid(get_val(legacy_lst, "redirected_to_property_id"))
                    check_to = self._execute(
                        "SELECT property_id FROM identity.properties WHERE property_id = %s AND tenant_id = %s",
                        (to_prop_uuid, i_tenant),
                        tenant_id=i_tenant,
                    )
                    if not check_to:
                        findings_count += 1
                        quarantined_count += 1
                        self._create_finding(
                            tenant_id=i_tenant,
                            source_kind="legacy_listing",
                            source_id=l_id,
                            target_ids=[listing_uuid, to_prop_uuid],
                            finding_type="ORPHAN_REFERENCE",
                            severity="BLOCKING",
                            expected={"redirect_property_exists": True},
                            actual={"redirect_property_exists": False},
                        )
                    else:
                        dec_id = ensure_uuid(f"DEC-REDIR-{l_id}")
                        redir_id = ensure_uuid(f"REDIR-{l_id}")

                        insert_redir_sql = """
                            INSERT INTO identity.property_redirects (
                                redirect_id, tenant_id, from_property_id, to_property_id, decision_id, version
                            ) VALUES (%s, %s, %s, %s, %s, 1)
                        """
                        self._execute(
                            insert_redir_sql,
                            (
                                redir_id,
                                i_tenant,
                                prop_uuid,
                                to_prop_uuid,
                                dec_id,
                            ),
                            tenant_id=i_tenant,
                        )

                processed_listing_uuids.append(listing_uuid)

            # 3. Backfill Candidates
            for legacy_cand in legacy_candidates:
                candidate_site = get_val(legacy_cand, "candidate_site")
                c_id = get_val(candidate_site, "candidate_site_id") or get_val(legacy_cand, "id")
                listing = get_val(legacy_cand, "listing")
                lst_id = get_val(listing, "listing_id") or get_val(legacy_cand, "listingId")
                status = get_val(legacy_cand, "status") or "CANDIDATE"

                candidate_uuid = ensure_uuid(c_id)
                listing_uuid = ensure_uuid(lst_id)
                i_tenant = ensure_uuid(tenant_id or get_val(candidate_site, "tenant_id") or "tenant-a")

                if tenant_id and i_tenant != ensure_uuid(tenant_id):
                    continue

                c_source = get_val(listing, "source_id") or get_val(listing, "sourceId") or listing_to_source.get(listing_uuid) or "SRC-591"
                if source_id and c_source != source_id:
                    continue

                # Real month partition comes from legacy evidence only
                c_month = listing_to_month.get(listing_uuid)
                if not c_month:
                    date_str = (
                        get_val(legacy_cand, "submittedAt")
                        or get_val(legacy_cand, "submitted_at")
                        or get_val(candidate_site, "submittedAt")
                        or get_val(candidate_site, "submitted_at")
                    )
                    if date_str and isinstance(date_str, str):
                        c_month = date_str[:7]

                if month:
                    if not c_month or c_month != month:
                        findings_count += 1
                        quarantined_count += 1
                        self._create_finding(
                            tenant_id=i_tenant,
                            source_kind="legacy_candidate",
                            source_id=c_id,
                            target_ids=[candidate_uuid],
                            finding_type="INVALID_SCOPE",
                            severity="BLOCKING",
                            expected={"month_partition": month},
                            actual={"month_partition": c_month},
                        )
                        continue

                if resume:
                    check_exists = "SELECT candidate_site_id FROM expansion.candidate_sites WHERE candidate_site_id = %s AND tenant_id = %s"
                    if self._execute(check_exists, (candidate_uuid, i_tenant), tenant_id=i_tenant):
                        skipped_count += 1
                        continue

                self._execute("SELECT 1", tenant_id=i_tenant)

                prop_row = self._execute(
                    "SELECT property_id FROM expansion.listings WHERE listing_id = %s AND tenant_id = %s",
                    (listing_uuid, i_tenant),
                    tenant_id=i_tenant,
                )
                if not prop_row:
                    findings_count += 1
                    quarantined_count += 1
                    self._create_finding(
                        tenant_id=i_tenant,
                        source_kind="legacy_candidate",
                        source_id=c_id,
                        target_ids=[candidate_uuid],
                        finding_type="STATE_MAPPING_CONFLICT",
                        severity="BLOCKING",
                        expected={"listing_exists": True},
                        actual={"listing_exists": False},
                    )
                    continue

                prop_uuid = prop_row[0]["property_id"]

                check_dup = """
                    SELECT candidate_site_id FROM expansion.candidate_sites
                    WHERE tenant_id = %s AND property_id = %s AND status NOT IN ('REJECTED', 'OPENED')
                """
                dup_results = self._execute(check_dup, (i_tenant, prop_uuid), tenant_id=i_tenant)

                decision_status = "COMPLETED"
                decision_type = "LEGACY_RECONCILED"

                if len(dup_results) > 0:
                    findings_count += 1
                    self._create_finding(
                        tenant_id=i_tenant,
                        source_kind="legacy_candidate",
                        source_id=c_id,
                        target_ids=[candidate_uuid] + [ensure_uuid(r["candidate_site_id"]) for r in dup_results],
                        finding_type="DUPLICATE_CANDIDATE",
                        severity="WARNING",
                        expected={"duplicate_candidate_count": 0},
                        actual={"duplicate_candidate_count": len(dup_results)},
                    )
                    decision_status = "FAILED"
                    status = "REJECTED"
                    quarantined_count += 1

                decision_uuid = ensure_uuid(f"PD-{c_id}")

                listing_url_row = self._execute(
                    "SELECT canonical_url_sha256 FROM expansion.listings WHERE listing_id = %s AND tenant_id = %s",
                    (listing_uuid, i_tenant),
                    tenant_id=i_tenant,
                )
                url_sha = listing_url_row[0]["canonical_url_sha256"] if listing_url_row else None

                if url_sha:
                    intake_row = self._execute(
                        "SELECT intake_id FROM intake.intakes WHERE (resolved_listing_id = %s OR canonical_url_sha256 = %s) AND tenant_id = %s",
                        (listing_uuid, url_sha, i_tenant),
                        tenant_id=i_tenant,
                    )
                else:
                    intake_row = self._execute(
                        "SELECT intake_id FROM intake.intakes WHERE resolved_listing_id = %s AND tenant_id = %s",
                        (listing_uuid, i_tenant),
                        tenant_id=i_tenant,
                    )

                if intake_row:
                    intake_uuid = intake_row[0]["intake_id"]
                else:
                    findings_count += 1
                    quarantined_count += 1
                    self._create_finding(
                        tenant_id=i_tenant,
                        source_kind="legacy_candidate",
                        source_id=c_id,
                        target_ids=[candidate_uuid],
                        finding_type="ORPHAN_REFERENCE",
                        severity="BLOCKING",
                        expected={"intake_exists": True},
                        actual={"intake_exists": False},
                    )
                    continue

                insert_dec_sql = """
                    INSERT INTO expansion.promotion_decisions (
                        promotion_decision_id, tenant_id, intake_id, listing_id, property_id,
                        target_format_code, status, proposer_subject_id, reason, gate_snapshot,
                        decision_type, migration_ref, version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                """
                self._execute(
                    insert_dec_sql,
                    (
                        decision_uuid,
                        i_tenant,
                        intake_uuid,
                        listing_uuid,
                        prop_uuid,
                        "ODAY_G2",
                        decision_status,
                        ensure_uuid("system"),
                        "Legacy migration backfill",
                        "{}",
                        decision_type,
                        self.migration_ref,
                    ),
                    tenant_id=i_tenant,
                )

                insert_cand_sql = """
                    INSERT INTO expansion.candidate_sites (
                        candidate_site_id, tenant_id, property_id, source_listing_id,
                        promotion_decision_id, target_format_code, status, version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
                """
                self._execute(
                    insert_cand_sql,
                    (
                        candidate_uuid,
                        i_tenant,
                        prop_uuid,
                        listing_uuid,
                        decision_uuid,
                        "ODAY_G2",
                        "NEW" if status in ("CANDIDATE", "new") else "REJECTED",
                    ),
                    tenant_id=i_tenant,
                )

                event_id = ensure_uuid(f"EVT-{c_id}")
                insert_outbox_sql = """
                    INSERT INTO workflow.outbox_events (
                        outbox_event_id, tenant_id, event_id, event_type, event_version,
                        aggregate_type, aggregate_id, aggregate_version, partition_key,
                        payload, correlation_id, occurred_at, retention_until
                    ) VALUES (%s, %s, %s, %s, 1, %s, %s, 1, %s, %s, %s, %s, %s)
                """
                self._execute(
                    insert_outbox_sql,
                    (
                        ensure_uuid(f"OUTBOX-{c_id}"),
                        i_tenant,
                        event_id,
                        "CandidateSitePromoted",
                        "CandidateSite",
                        candidate_uuid,
                        i_tenant,
                        json.dumps({"candidate_site_id": candidate_uuid, "status": status}),
                        ensure_uuid(f"corr-{c_id}"),
                        datetime.now(UTC).isoformat(),
                        datetime.now(UTC).isoformat(),
                    ),
                    tenant_id=i_tenant,
                )

                audit_evt_id = ensure_uuid(f"AUDIT-{c_id}")
                audit_sha = hashlib.sha256(f"{audit_evt_id}:{candidate_uuid}".encode()).hexdigest()
                seq_row = self._execute("SELECT COALESCE(MAX(sequence_no), 0) as max_seq FROM audit.audit_events WHERE tenant_id = %s", (i_tenant,), tenant_id=i_tenant)
                audit_seq = (seq_row[0]["max_seq"] if seq_row else 0) + 1

                insert_audit_sql = """
                    INSERT INTO audit.audit_events (
                        audit_event_id, tenant_id, sequence_no, event_type, actor_subject_id,
                        action, resource_type, resource_id, decision_id, result, correlation_id,
                        event_sha256, occurred_at, retained_until
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                self._execute(
                    insert_audit_sql,
                    (
                        audit_evt_id,
                        i_tenant,
                        audit_seq,
                        "CANDIDATE_BACKFILL",
                        ensure_uuid("system"),
                        "BACKFILL",
                        "CandidateSite",
                        candidate_uuid,
                        decision_uuid,
                        "SUCCEEDED",
                        ensure_uuid(f"corr-{c_id}"),
                        audit_sha,
                        datetime.now(UTC).isoformat(),
                        datetime.now(UTC).isoformat(),
                    ),
                    tenant_id=i_tenant,
                )

                processed_candidate_uuids.append(candidate_uuid)

            t_key = target_tenant
            p_key = f"{t_key}:{source_id or 'ALL'}:{month or 'ALL'}"
            proof_expected = {
                "tenant_id": t_key,
                "source_id": source_id or "ALL",
                "month": month or "ALL",
                "partition_key": p_key,
                "intakes": len(processed_intake_uuids),
                "listings": len(processed_listing_uuids),
                "candidates": len(processed_candidate_uuids),
                "intakes_sha256": calculate_checksum(processed_intake_uuids),
                "listings_sha256": calculate_checksum(processed_listing_uuids),
                "candidates_sha256": calculate_checksum(processed_candidate_uuids),
            }
            self._expected_counts[t_key] = {
                "intakes": len(processed_intake_uuids),
                "listings": len(processed_listing_uuids),
                "candidates": len(processed_candidate_uuids),
            }
            self._expected_checksums[t_key] = {
                "intakes": proof_expected["intakes_sha256"],
                "listings": proof_expected["listings_sha256"],
                "candidates": proof_expected["candidates_sha256"],
            }

            if not dry_run:
                proof_finding_id = ensure_uuid(f"PROOF-{p_key}")
                check_pf = self._execute(
                    "SELECT finding_id FROM workflow.reconciliation_findings WHERE (finding_id = %s OR (tenant_id = %s AND migration_id = %s AND severity = 'INFO' AND source_id = %s)) AND tenant_id = %s",
                    (proof_finding_id, t_key, self.migration_ref, f"PROOF-{p_key}", t_key),
                    tenant_id=t_key,
                )
                if check_pf:
                    if not (resume and len(processed_intake_uuids) == 0 and len(processed_listing_uuids) == 0 and len(processed_candidate_uuids) == 0):
                        self._execute(
                            "UPDATE workflow.reconciliation_findings SET expected = %s, actual = %s WHERE finding_id = %s AND tenant_id = %s",
                            (json.dumps(proof_expected), json.dumps(proof_expected), check_pf[0]["finding_id"], t_key),
                            tenant_id=t_key,
                        )
                else:
                    self._create_finding(
                        tenant_id=t_key,
                        source_kind="snapshot",
                        source_id=f"PROOF-{p_key}",
                        target_ids=[proof_finding_id],
                        finding_type="CHECKSUM_MISMATCH",
                        severity="INFO",
                        expected=proof_expected,
                        actual=proof_expected,
                        finding_id=proof_finding_id,
                    )

            if dry_run:
                if hasattr(self.db_conn, "rollback"):
                    self.db_conn.rollback()
                logger.info("Dry run complete. Rolled back all changes.")
            else:
                if hasattr(self.db_conn, "commit"):
                    self.db_conn.commit()
                logger.info("Backfill transaction committed.")

        except Exception as exc:
            if hasattr(self.db_conn, "rollback"):
                self.db_conn.rollback()
            logger.exception("Backfill aborted due to error: %s", exc)
            raise

        return {
            "migration_id": self.migration_ref,
            "dry_run": dry_run,
            "status": "success" if not dry_run else "dry_run",
            "counts": {
                "intakes_processed": reconciled_count,
                "listings_processed": len(processed_listing_uuids),
                "candidates_processed": len(processed_candidate_uuids),
                "skipped_due_to_resume": skipped_count,
                "quarantined": quarantined_count,
                "findings": findings_count,
            },
            "checksums": {
                "intakes_sha256": calculate_checksum(processed_intake_uuids),
                "listings_sha256": calculate_checksum(processed_listing_uuids),
                "candidates_sha256": calculate_checksum(processed_candidate_uuids),
            },
        }

    def _create_finding(
        self,
        tenant_id: str,
        source_kind: str,
        source_id: str,
        target_ids: list[str],
        finding_type: str,
        severity: str,
        expected: dict[str, Any],
        actual: dict[str, Any],
        finding_id: str | None = None,
    ) -> None:
        """Create a reconciliation finding inside `workflow.reconciliation_findings` using SQL enum bounds."""
        fid = finding_id or str(uuid.uuid4())
        is_sqlite = self._is_sqlite()
        target_ids_val = json.dumps(target_ids) if is_sqlite else target_ids

        mapped_finding_type = FINDING_TYPE_MAP.get(finding_type, "STATE_MAPPING_CONFLICT")
        mapped_source_kind = SOURCE_KIND_MAP.get(source_kind, "snapshot")
        status_val = "RESOLVED" if severity == "INFO" else "OPEN"

        sql = """
            INSERT INTO workflow.reconciliation_findings (
                finding_id, migration_id, tenant_id, source_kind, source_id,
                target_ids, finding_type, severity, expected, actual,
                owner_role, status, version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        self._execute(
            sql,
            (
                fid,
                self.migration_ref,
                tenant_id,
                mapped_source_kind,
                source_id,
                target_ids_val,
                mapped_finding_type,
                severity,
                json.dumps(expected),
                json.dumps(actual),
                "DATA_STEWARD",
                status_val,
                1,
            ),
            tenant_id=tenant_id,
        )

    def verify_shadow_comparison(
        self,
        tenant_id: str,
        expected_intake_count: int | None = None,
        expected_listing_count: int | None = None,
        expected_candidate_count: int | None = None,
    ) -> dict[str, Any]:
        """Perform shadow comparison against persisted proof. Returns verification results."""
        t_tenant = ensure_uuid(tenant_id)
        self._execute("SELECT 1", tenant_id=t_tenant)

        intake_rows = self._execute("SELECT intake_id FROM intake.intakes WHERE tenant_id = %s", (t_tenant,), tenant_id=t_tenant)
        listing_rows = self._execute("SELECT listing_id FROM expansion.listings WHERE tenant_id = %s", (t_tenant,), tenant_id=t_tenant)
        candidate_rows = self._execute("SELECT candidate_site_id FROM expansion.candidate_sites WHERE tenant_id = %s", (t_tenant,), tenant_id=t_tenant)

        actual_intake_ids = [r["intake_id"] for r in intake_rows]
        actual_listing_ids = [r["listing_id"] for r in listing_rows]
        actual_candidate_ids = [r["candidate_site_id"] for r in candidate_rows]

        actual_intake_count = len(actual_intake_ids)
        actual_listing_count = len(actual_listing_ids)
        actual_candidate_count = len(actual_candidate_ids)

        actual_intake_sha = calculate_checksum(actual_intake_ids)
        actual_listing_sha = calculate_checksum(actual_listing_ids)
        actual_candidate_sha = calculate_checksum(actual_candidate_ids)

        proof_rows = self._execute(
            "SELECT finding_id, expected FROM workflow.reconciliation_findings WHERE severity = 'INFO' AND tenant_id = %s AND migration_id = %s AND (source_id LIKE 'PROOF-%%' OR source_id = %s) ORDER BY version DESC",
            (t_tenant, self.migration_ref, self.migration_ref),
            tenant_id=t_tenant,
        )
        partition_proofs: dict[str, dict[str, Any]] = {}
        for r in proof_rows:
            raw_exp = r["expected"]
            exp_dict = json.loads(raw_exp) if isinstance(raw_exp, str) else raw_exp
            pkey = exp_dict.get("partition_key") or r["finding_id"]
            if pkey not in partition_proofs:
                partition_proofs[pkey] = exp_dict

        in_mem_counts = self._expected_counts.get(t_tenant, {})
        in_mem_shas = self._expected_checksums.get(t_tenant, {})

        has_proof = bool(partition_proofs or in_mem_counts or expected_intake_count is not None)

        if not has_proof:
            self._create_finding(
                tenant_id=t_tenant,
                source_kind="snapshot",
                source_id="verification",
                target_ids=[],
                finding_type="COUNT_MISMATCH",
                severity="BLOCKING",
                expected={"proof_exists": True},
                actual={"proof_exists": False},
            )
            return {
                "tenant_id": t_tenant,
                "intake_count": actual_intake_count,
                "listing_count": actual_listing_count,
                "candidate_count": actual_candidate_count,
                "intake_sha256": actual_intake_sha,
                "listing_sha256": actual_listing_sha,
                "candidate_sha256": actual_candidate_sha,
                "open_findings": 1,
                "blocking_findings": 1,
                "shadow_comparison_success": False,
                "failures": {"missing_shadow_proof": 1},
            }

        if expected_intake_count is not None:
            exp_intake_c = expected_intake_count
            exp_listing_c = expected_listing_count
            exp_candidate_c = expected_candidate_count
            exp_intake_sha = None
            exp_listing_sha = None
            exp_candidate_sha = None
        elif partition_proofs:
            exp_intake_c = sum(p.get("intakes", 0) for p in partition_proofs.values())
            exp_listing_c = sum(p.get("listings", 0) for p in partition_proofs.values())
            exp_candidate_c = sum(p.get("candidates", 0) for p in partition_proofs.values())
            if len(partition_proofs) == 1:
                single_p = next(iter(partition_proofs.values()))
                exp_intake_sha = single_p.get("intakes_sha256")
                exp_listing_sha = single_p.get("listings_sha256")
                exp_candidate_sha = single_p.get("candidates_sha256")
            else:
                exp_intake_sha = None
                exp_listing_sha = None
                exp_candidate_sha = None
        else:
            exp_intake_c = in_mem_counts.get("intakes")
            exp_listing_c = in_mem_counts.get("listings")
            exp_candidate_c = in_mem_counts.get("candidates")
            exp_intake_sha = in_mem_shas.get("intakes")
            exp_listing_sha = in_mem_shas.get("listings")
            exp_candidate_sha = in_mem_shas.get("candidates")

        findings = self._execute(
            "SELECT COUNT(*) as n FROM workflow.reconciliation_findings WHERE tenant_id = %s AND status = 'OPEN'",
            (t_tenant,),
            tenant_id=t_tenant,
        )
        open_findings = findings[0]["n"] if findings else 0

        blocking_findings = self._execute(
            "SELECT COUNT(*) as n FROM workflow.reconciliation_findings WHERE tenant_id = %s AND status = 'OPEN' AND severity = 'BLOCKING'",
            (t_tenant,),
            tenant_id=t_tenant,
        )[0]["n"]

        if exp_intake_c is not None and actual_intake_count != exp_intake_c:
            self._create_finding(
                tenant_id=t_tenant,
                source_kind="snapshot",
                source_id="intakes",
                target_ids=actual_intake_ids,
                finding_type="COUNT_MISMATCH",
                severity="BLOCKING",
                expected={"count": exp_intake_c},
                actual={"count": actual_intake_count},
            )
            blocking_findings += 1

        if exp_intake_sha and actual_intake_sha != exp_intake_sha:
            self._create_finding(
                tenant_id=t_tenant,
                source_kind="snapshot",
                source_id="intakes",
                target_ids=actual_intake_ids,
                finding_type="CHECKSUM_MISMATCH",
                severity="BLOCKING",
                expected={"checksum": exp_intake_sha},
                actual={"checksum": actual_intake_sha},
            )
            blocking_findings += 1

        if exp_listing_c is not None and actual_listing_count != exp_listing_c:
            self._create_finding(
                tenant_id=t_tenant,
                source_kind="legacy_listing",
                source_id="listings",
                target_ids=actual_listing_ids,
                finding_type="COUNT_MISMATCH",
                severity="BLOCKING",
                expected={"count": exp_listing_c},
                actual={"count": actual_listing_count},
            )
            blocking_findings += 1

        if exp_listing_sha and actual_listing_sha != exp_listing_sha:
            self._create_finding(
                tenant_id=t_tenant,
                source_kind="legacy_listing",
                source_id="listings",
                target_ids=actual_listing_ids,
                finding_type="CHECKSUM_MISMATCH",
                severity="BLOCKING",
                expected={"checksum": exp_listing_sha},
                actual={"checksum": actual_listing_sha},
            )
            blocking_findings += 1

        if exp_candidate_c is not None and actual_candidate_count != exp_candidate_c:
            self._create_finding(
                tenant_id=t_tenant,
                source_kind="legacy_candidate",
                source_id="candidate_sites",
                target_ids=actual_candidate_ids,
                finding_type="COUNT_MISMATCH",
                severity="BLOCKING",
                expected={"count": exp_candidate_c},
                actual={"count": actual_candidate_count},
            )
            blocking_findings += 1

        if exp_candidate_sha and actual_candidate_sha != exp_candidate_sha:
            self._create_finding(
                tenant_id=t_tenant,
                source_kind="legacy_candidate",
                source_id="candidate_sites",
                target_ids=actual_candidate_ids,
                finding_type="CHECKSUM_MISMATCH",
                severity="BLOCKING",
                expected={"checksum": exp_candidate_sha},
                actual={"checksum": actual_candidate_sha},
            )
            blocking_findings += 1

        pd_dup = self._execute(
            """
            SELECT listing_id, COUNT(*) as c
            FROM expansion.promotion_decisions
            WHERE tenant_id = %s AND status NOT IN ('REJECTED','COMPLETED','FAILED','SCORE_FAILED')
            GROUP BY listing_id
            HAVING COUNT(*) > 1
            """,
            (t_tenant,),
            tenant_id=t_tenant,
        )

        shadow_comparison_success = (
            (len(pd_dup) == 0)
            and (blocking_findings == 0)
            and (exp_intake_c is None or actual_intake_count == exp_intake_c)
            and (exp_listing_c is None or actual_listing_count == exp_listing_c)
            and (exp_candidate_c is None or actual_candidate_count == exp_candidate_c)
            and (exp_intake_sha is None or actual_intake_sha == exp_intake_sha)
            and (exp_listing_sha is None or actual_listing_sha == exp_listing_sha)
            and (exp_candidate_sha is None or actual_candidate_sha == exp_candidate_sha)
        )

        return {
            "tenant_id": t_tenant,
            "intake_count": actual_intake_count,
            "listing_count": actual_listing_count,
            "candidate_count": actual_candidate_count,
            "intake_sha256": actual_intake_sha,
            "listing_sha256": actual_listing_sha,
            "candidate_sha256": actual_candidate_sha,
            "open_findings": open_findings,
            "blocking_findings": blocking_findings,
            "shadow_comparison_success": shadow_comparison_success,
            "failures": {
                "duplicate_promotions": len(pd_dup),
                "blocking_reconciliation_findings": blocking_findings,
            },
        }


def main() -> None:
    """CLI entrypoint for executable staging backfill, verification, and rollback."""
    parser = argparse.ArgumentParser(description="Assisted Listing Intake Migration CLI (ODP-INTAKE-MIGRATION-001)")
    parser.add_argument("--action", choices=["backfill", "verify", "rollback", "schema-upgrade", "schema-rollback"], default="backfill")
    parser.add_argument("--tenant-id", type=str, default="00000000-0000-0000-0000-000000000001", help="Target tenant UUID")
    parser.add_argument("--source-id", type=str, default=None, help="Source system ID filter")
    parser.add_argument("--month", type=str, default=None, help="YYYY-MM partition filter")
    parser.add_argument("--migration-ref", type=str, default="ODP-INTAKE-MIGRATION-001", help="Migration reference identifier")
    parser.add_argument("--dry-run", action="store_true", help="Execute without committing transaction")
    parser.add_argument("--resume", action="store_true", help="Resume backfill skipping existing records")
    parser.add_argument("--input-file", type=str, default=None, help="JSON file containing legacy inputs")
    parser.add_argument("--db-dsn", "--dsn", "--db-uri", dest="db_dsn", type=str, default=None, help="Database connection DSN or URL")
    parser.add_argument("--sqlite-path", type=str, default=None, help="Path to SQLite database file")

    args = parser.parse_args()

    import os
    import sys

    dsn = args.db_dsn or os.environ.get("ODAY_DATABASE_URL") or os.environ.get("INTAKE_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

    if not dsn and not args.sqlite_path:
        print("Error: No database connection target provided. Specify --db-dsn/--dsn, --sqlite-path, or set ODAY_DATABASE_URL.", file=sys.stderr)
        sys.exit(1)

    if dsn:
        try:
            import psycopg
            conn = psycopg.connect(dsn, autocommit=False)
        except Exception:
            import psycopg2
            conn = psycopg2.connect(dsn)
    else:
        import sqlite3
        conn = sqlite3.connect(args.sqlite_path)

    migrator = IntakeMigrator(conn, migration_ref=args.migration_ref)
    migrator.apply_schema()

    if args.action == "schema-upgrade":
        print("Schema upgrade applied successfully.")
        return
    elif args.action == "schema-rollback":
        migrator.rollback_schema()
        print("Schema downgrade applied successfully.")
        return
    elif args.action == "rollback":
        deleted = migrator.rollback_migration(migration_ref=args.migration_ref, tenant_id=args.tenant_id)
        print(f"Scoped migration rollback executed. Deleted records count: {deleted}")
        return

    legacy_intakes = []
    legacy_listings = []
    legacy_candidates = []
    parser_release = None

    if args.input_file:
        with open(args.input_file, encoding="utf-8") as f:
            data = json.load(f)
            legacy_intakes = data.get("legacy_intakes", [])
            legacy_listings = data.get("legacy_listings", [])
            legacy_candidates = data.get("legacy_candidates", [])
            parser_release = data.get("parser_release")

    if args.action == "backfill":
        res = migrator.backfill(
            legacy_intakes=legacy_intakes,
            legacy_listings=legacy_listings,
            legacy_candidates=legacy_candidates,
            dry_run=args.dry_run,
            resume=args.resume,
            tenant_id=args.tenant_id,
            source_id=args.source_id,
            month=args.month,
            parser_release=parser_release,
        )
        print(json.dumps(res, indent=2))
    elif args.action == "verify":
        res = migrator.verify_shadow_comparison(tenant_id=args.tenant_id)
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
