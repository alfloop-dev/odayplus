"""Bounded manual-command scans with proof before skipping a withdrawn request."""

from __future__ import annotations

import base64
import json
import time
from uuid import UUID, uuid5

from fastapi import HTTPException

from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_pending_commands import (
    NativeIntakeCommandBudget,
    NativeIntakePendingCommands,
)
from modules.listing.application.native_intake_source_policy import _object, _time

JOB_TYPE = "native-intake-assisted-entry"
SCAN_BATCH = 64
MAX_SCAN_EVENTS = 1000
MAX_SCAN_SECONDS = 10


def _position(cursor, tenant):
    if cursor is None:
        return None
    try:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
            raise ValueError
        data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (not isinstance(data, dict) or set(data) != {"version", "tenant_id", "occurred_at", "event_id"}
                or type(data["version"]) is not int or data["version"] != 1 or data["tenant_id"] != tenant):
            raise ValueError
        return _time(data["occurred_at"]).isoformat(), native_intake_uuid(data["event_id"])
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("NATIVE_ASSISTED_PARTITION_CURSOR_REJECTED") from None


def _cursor(position, tenant):
    if position is None:
        return None
    return base64.urlsafe_b64encode(json.dumps({"version": 1, "tenant_id": tenant,
        "occurred_at": position[0], "event_id": position[1]}, sort_keys=True, separators=(",", ":")).encode()).decode()


def _candidate_conflict(error):
    if isinstance(error, (ValueError, PermissionError)) or (
            isinstance(error, HTTPException) and error.status_code in {401, 403, 409}):
        return True
    current, seen = error, set()
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        if getattr(current, "sqlstate", None) in {"40001", "40P01", "55P03", "57014"}:
            return True
        current = current.__cause__ or current.__context__
    return False


class NativeAssistedEntryPartition:
    def __init__(self, owner):
        self.owner, self.graph, self.engine = owner, owner.graph, owner.engine
        self.pending = NativeIntakePendingCommands(self.graph)
        self.clock_source = owner.source.source
        self.progress = {"scanned": 0, "admitted": 0, "withdrawn": 0, "conflicts": 0,
            "has_more": False, "next_cursor": None, "conflict_examples": []}

    def _rows(self, tenant, position, limit):
        occurred, event_id = position if position else (None, None)
        with self.graph.transaction(tenant):
            before = NativeIntakeCommandBudget(self.clock_source)
            before()
            rows = self.engine.query("SELECT e.event_id,e.occurred_at FROM workflow.outbox_events e "
                "JOIN intake.intakes i ON i.tenant_id=e.tenant_id AND i.intake_id::text=e.payload->'payload'->>'intake_id' "
                "WHERE e.tenant_id=? AND e.event_type='intake.assisted_entry_requested' "
                "AND e.aggregate_type='intake_assisted_entry' AND i.deleted_at IS NULL "
                "AND i.processing_state='AWAITING_ASSISTED_ENTRY' AND i.intake_method='URL' "
                "AND i.source_policy_state IN ('AUTH_REQUIRED','ASSISTED_ENTRY_ONLY') "
                "AND i.version::text=e.payload->'payload'->>'expected_intake_version' "
                "AND NOT EXISTS (SELECT 1 FROM workflow.jobs j WHERE j.tenant_id=e.tenant_id "
                "AND j.job_type='native-intake-assisted-entry' AND j.aggregate_type='intake_assisted_entry' "
                "AND j.aggregate_id=e.aggregate_id) "
                "AND (?::timestamptz IS NULL OR (e.occurred_at,e.event_id)>(?::timestamptz,?::uuid)) "
                "ORDER BY e.occurred_at,e.event_id LIMIT ?", (tenant, occurred, occurred, event_id, limit))
            before()
            return rows

    def _withdrawn(self, tenant, event_id):
        with self.graph.transaction(tenant):
            before = NativeIntakeCommandBudget(self.clock_source)
            before()
            row = self.graph._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?", (tenant, event_id))
            data = _object(_object(row["payload"]).get("payload"))
            intake_id = native_intake_uuid(data.get("intake_id"))
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE", (tenant, intake_id))
            original = self.pending.closed_candidate_envelope(row, intake, "ASSISTED_ENTRY")
            withdrawal = self.pending.withdrawal(original, "ASSISTED_ENTRY", before)
            if withdrawal is None:
                return False
            before()
            job = self.engine.query_one("SELECT job_id FROM workflow.jobs WHERE tenant_id=? "
                "AND aggregate_type='intake_assisted_entry' AND aggregate_id=? LIMIT 1", (tenant, event_id))
            before()
            physical = self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=?",
                (str(uuid5(UUID(event_id), JOB_TYPE)),))
            if withdrawal[0]["payload"]["job_id"] is not None or job is not None or physical is not None:
                raise NativeAssistedEntryConflict("NATIVE_COMMAND_WITHDRAWAL_IS_NOT_JOBLESS")
            before()
            return True

    def admit(self, tenant, *, limit, cursor=None):
        if getattr(getattr(self.engine, "_local", None), "connection", None) is not None:
            raise NativeAssistedEntryConflict("NATIVE_ASSISTED_PARTITION_OUTER_ROOT_REJECTED")
        position = _position(cursor, tenant)
        expires = time.monotonic() + MAX_SCAN_SECONDS
        try:
            while (self.progress["admitted"] < limit and self.progress["scanned"] < MAX_SCAN_EVENTS
                    and time.monotonic() < expires):
                rows = self._rows(tenant, position, min(SCAN_BATCH, MAX_SCAN_EVENTS - self.progress["scanned"]))
                if not rows:
                    break
                for row in rows:
                    if (self.progress["admitted"] >= limit or self.progress["scanned"] >= MAX_SCAN_EVENTS
                            or time.monotonic() >= expires):
                        break
                    receipt = None
                    try:
                        if self._withdrawn(tenant, row["event_id"]):
                            self.progress["withdrawn"] += 1
                        else:
                            receipt = self.owner.admit_event(tenant, row["event_id"])
                            self.progress["admitted"] += 1
                    except Exception as error:
                        if not _candidate_conflict(error):
                            raise
                        self.progress["conflicts"] += 1
                        if len(self.progress["conflict_examples"]) < 10:
                            self.progress["conflict_examples"].append({"event_id": row["event_id"],
                                "error_class": type(error).__name__})
                    position = row["occurred_at"], row["event_id"]
                    self.progress["scanned"] += 1
                    self.progress["next_cursor"] = _cursor(position, tenant)
                    if receipt is not None:
                        yield receipt
            self.progress["has_more"] = bool(self._rows(tenant, position, 1))
        finally:
            self.owner.last_partition_progress = dict(self.progress)
