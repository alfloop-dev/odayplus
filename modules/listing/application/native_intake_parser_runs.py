"""Canonical parser-run CAS inside the application's bounded SQL roots.

The application owner separately proves current authority, accepted attempts,
both fences and atomic job/audit/event/Intake settlement. This repository owns
no transaction and confers none of those grants. It never resets a terminal
run, creates a parallel table or invents parser output.
"""
from __future__ import annotations

from datetime import datetime

from modules.external_data.application.native_parser_output import NativeParserOutput
from modules.listing.application.native_intake_assisted_entry_request_proof import _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget

_READS = (
    "SELECT * FROM intake.parser_runs WHERE tenant_id=? AND parser_run_id=?",
    "SELECT * FROM intake.parser_runs WHERE tenant_id=? AND parser_run_id=? FOR UPDATE",
)
_EDGES = {
    "accept": ({"QUEUED"},
        "UPDATE intake.parser_runs SET status='RUNNING',started_at=COALESCE(started_at,?),version=version+1 "
        "WHERE tenant_id=? AND parser_run_id=? AND version=? AND status=? AND intake_id=? "
        "AND source_snapshot_id=? AND parser_release_id=? AND correlation_id=? AND completed_at IS NULL "
        "AND parsed_payload IS NULL AND normalized_payload IS NULL "
        "AND field_confidence='{}'::jsonb AND validation_errors='[]'::jsonb "
        "AND (started_at IS NULL OR started_at<=?::timestamptz) "
        "AND created_at<=?::timestamptz AND ?::timestamptz<=clock_timestamp() RETURNING *"),
    "retry": ({"RUNNING"},
        "UPDATE intake.parser_runs SET status='QUEUED',version=version+1 "
        "WHERE tenant_id=? AND parser_run_id=? AND version=? AND status=? AND intake_id=? "
        "AND source_snapshot_id=? AND parser_release_id=? AND correlation_id=? AND completed_at IS NULL "
        "AND started_at IS NOT NULL AND parsed_payload IS NULL AND normalized_payload IS NULL "
        "AND field_confidence='{}'::jsonb AND validation_errors='[]'::jsonb RETURNING *"),
    "complete_output": ({"RUNNING"},
        "UPDATE intake.parser_runs SET status=?,parsed_payload=?::jsonb,normalized_payload=?::jsonb,"
        "field_confidence=?::jsonb,validation_errors=?::jsonb,completed_at=?,version=version+1 "
        "WHERE tenant_id=? AND parser_run_id=? AND version=? AND status=? AND intake_id=? "
        "AND source_snapshot_id=? AND parser_release_id=? AND correlation_id=? AND completed_at IS NULL "
        "AND started_at IS NOT NULL AND started_at<=?::timestamptz "
        "AND parsed_payload IS NULL AND normalized_payload IS NULL "
        "AND field_confidence='{}'::jsonb AND validation_errors='[]'::jsonb "
        "AND created_at<=?::timestamptz AND ?::timestamptz<=clock_timestamp() RETURNING *"),
    "complete_negative": ({"QUEUED", "RUNNING"},
        "UPDATE intake.parser_runs SET status=?,completed_at=?,version=version+1 "
        "WHERE tenant_id=? AND parser_run_id=? AND version=? AND status=? AND intake_id=? "
        "AND source_snapshot_id=? AND parser_release_id=? AND correlation_id=? AND completed_at IS NULL "
        "AND parsed_payload IS NULL AND normalized_payload IS NULL "
        "AND field_confidence='{}'::jsonb AND validation_errors='[]'::jsonb "
        "AND (status='QUEUED' OR started_at IS NOT NULL) "
        "AND (started_at IS NULL OR started_at<=?::timestamptz) "
        "AND created_at<=?::timestamptz AND ?::timestamptz<=clock_timestamp() RETURNING *"),
}


def _reject():
    raise NativeParserConflict("NATIVE_PARSER_RUN_CAS_REJECTED") from None


def _timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _reject()
    return value.isoformat()


class CanonicalNativeParserRunRepository:
    def __init__(self, engine):
        self.engine = engine

    def _root(self, tenant, before):
        tenant = native_intake_uuid(tenant)
        if (not isinstance(before, NativeParserCommandBudget) or before.source.engine is not self.engine
                or getattr(self.engine, "dialect", None) != "postgresql"
                or getattr(getattr(self.engine, "_local", None), "connection", None) is None):
            _reject()
        before()
        root = self.engine.query_one("SELECT current_setting('transaction_isolation') AS isolation, "
            "current_setting('app.tenant_id',true) AS tenant")
        if root["isolation"] != "serializable" or root["tenant"] != tenant:
            _reject()
        before.check_in_memory()
        return tenant

    def get(self, tenant, run_id, *, before, for_update=False):
        tenant = self._root(tenant, before)
        run_id = native_intake_uuid(run_id)
        if type(for_update) is not bool:
            _reject()
        before()
        row = self.engine.query_one(_READS[int(for_update)], (tenant, run_id))
        before.check_in_memory()
        return row

    def insert_queued(self, *, tenant, run_id, intake_id, snapshot_id, release_id, correlation_id, at, before):
        tenant = self._root(tenant, before)
        run_id, intake_id, snapshot_id, release_id, correlation_id = (native_intake_uuid(value) for value in (
            run_id, intake_id, snapshot_id, release_id, correlation_id))
        stamp = _timestamp(at)
        # Read only existing metadata through a bounded, literal reference
        # lookup. This repository writes parser_runs and never stores evidence.
        before()
        snapshot = self.engine.query_one("SELECT source_id,capture_method,media_type,captured_at "
            "FROM intake.source_snapshots WHERE tenant_id=? AND source_snapshot_id=? AND intake_id=? FOR SHARE",
            (tenant, snapshot_id, intake_id))
        before.check_in_memory()
        if (snapshot is None or snapshot["capture_method"] != "MANUAL_ENTRY"
                or snapshot["media_type"] != "application/json" or _time(snapshot["captured_at"]) > at):
            _reject()
        before()
        row = self.engine.query_one("INSERT INTO intake.parser_runs (parser_run_id,tenant_id,intake_id,"
            "source_snapshot_id,parser_release_id,status,parsed_payload,normalized_payload,field_confidence,"
            "validation_errors,started_at,completed_at,correlation_id,version,created_at) "
            "SELECT ?,?,?,?,?, 'QUEUED',NULL,NULL,'{}'::jsonb,'[]'::jsonb,NULL,NULL,?,1,? "
            "FROM intake.intakes i JOIN intake.parser_releases p ON p.source_id=i.source_id "
            "WHERE i.tenant_id=? AND i.intake_id=? AND p.parser_release_id=? AND i.source_id=? "
            "AND i.processing_state='PARSING' AND i.deleted_at IS NULL "
            "AND i.resolved_listing_id IS NULL AND i.completed_at IS NULL AND i.correlation_id=? "
            "AND p.validation_status='PRODUCTION' AND p.deprecated_at IS NULL "
            "AND p.released_by IS NOT NULL AND p.released_at IS NOT NULL AND p.released_at<=clock_timestamp() "
            "AND p.released_at<=?::timestamptz "
            "AND ?::timestamptz<=clock_timestamp() "
            "RETURNING *", (run_id, tenant, intake_id, snapshot_id, release_id, correlation_id, stamp,
                tenant, intake_id, release_id, snapshot["source_id"], correlation_id, stamp, stamp))
        before.check_in_memory()
        if row is None:
            _reject()
        return row

    def _cas(self, row, *, before, action, values, predicate_values=()):
        tenant = self._root(row["tenant_id"], before)
        run_id = native_intake_uuid(row["parser_run_id"])
        if (action not in _EDGES or type(row["version"]) is not int or row["version"] < 1
                or row["status"] not in _EDGES[action][0]):
            _reject()
        # Lineage is immutable across acceptance, retry and every terminal edge.
        intake, snapshot, release, correlation = (native_intake_uuid(row[name]) for name in (
            "intake_id", "source_snapshot_id", "parser_release_id", "correlation_id"))
        before()
        updated = self.engine.query_one(_EDGES[action][1], (*values, tenant, run_id, row["version"], row["status"],
                intake, snapshot, release, correlation, *predicate_values))
        before.check_in_memory()
        if updated is None:
            _reject()
        return updated

    def accept(self, row, *, at, before):
        stamp = _timestamp(at)
        return self._cas(row, before=before, action="accept", values=(stamp,), predicate_values=(stamp, stamp, stamp))

    def retry_same_run(self, row, *, before):
        return self._cas(row, before=before, action="retry", values=())

    def complete_output(self, row, output, *, at, before):
        if not isinstance(output, NativeParserOutput) or output.status not in {"SUCCEEDED", "PARTIAL"}:
            _reject()
        facts = output.facts
        stamp = _timestamp(at)
        return self._cas(row, before=before, action="complete_output",
            values=(output.status, facts.parsed_payload_json, facts.normalized_payload_json,
                facts.field_confidence_json, facts.validation_errors_json, stamp),
            predicate_values=(stamp, stamp, stamp))

    def complete_negative(self, row, *, status, at, before):
        # These are storage edges only. The owner must still prove accepted
        # attempts/fences and truthful cancellation/failure evidence. There is
        # no prerequisite/source/key read here that could prevent owned cleanup.
        if (status not in {"FAILED", "CANCELLED"} or row["status"] not in {"QUEUED", "RUNNING"}
                or (row["status"] == "QUEUED" and status != "CANCELLED")):
            _reject()
        stamp = _timestamp(at)
        return self._cas(row, before=before, action="complete_negative", values=(status, stamp),
            predicate_values=(stamp, stamp, stamp))
