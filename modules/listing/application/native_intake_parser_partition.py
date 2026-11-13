"""Bounded candidate scans for one explicitly selected parser source/release.

Rows and continuation cursors select candidates only. Every admission uses
the existing owner in a separate fresh bounded transaction, including full
manual history and current human/source/artifact checks. No catalog ranking,
request fabrication, withdrawal interpretation or terminal reprocessing.
"""
from __future__ import annotations

import base64
import json
import time

from fastapi import HTTPException

from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_policy import (
    NativeParserConflict,
    native_intake_parser_policy,
)
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from shared.jobs.queue import JobFenceRejectedError

SCAN_BATCH = 64
MAX_SCAN_EVENTS = 1000
MAX_SCAN_SECONDS = 10
_CURSOR_KEYS = frozenset({"version", "kind", "tenant_id", "source_id", "parser_release_id",
    "occurred_at", "transition_id"})


def _source(value):
    if (type(value) is not str or not value or value != value.strip() or len(value) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("NATIVE_PARSER_PARTITION_SOURCE_REJECTED")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("NATIVE_PARSER_PARTITION_SOURCE_REJECTED") from None
    return value


def _closed_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _position(cursor, tenant, source, release):
    if cursor is None:
        return None
    try:
        if type(cursor) is not str or not 1 <= len(cursor) <= 2048:
            raise ValueError
        value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True),
            object_pairs_hook=_closed_object)
        if (type(value) is not dict or set(value) != _CURSOR_KEYS
                or type(value["version"]) is not int or value["version"] != 1
                or value["kind"] != "native_parser_partition"
                or value["tenant_id"] != tenant or value["source_id"] != source
                or value["parser_release_id"] != release):
            raise ValueError
        return _time(value["occurred_at"]).isoformat(), native_intake_uuid(value["transition_id"])
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ValueError("NATIVE_PARSER_PARTITION_CURSOR_REJECTED") from None


def _cursor(position, tenant, source, release):
    if position is None:
        return None
    return base64.urlsafe_b64encode(json.dumps({"version": 1, "kind": "native_parser_partition",
        "tenant_id": tenant, "source_id": source, "parser_release_id": release,
        "occurred_at": _time(position[0]).isoformat(), "transition_id": position[1]},
        sort_keys=True, separators=(",", ":")).encode()).decode()


def _candidate_conflict(error):
    if isinstance(error, (NativeParserConflict, NativeAssistedEntryConflict, JobFenceRejectedError)):
        return True
    if isinstance(error, HTTPException) and error.status_code in {401, 403, 409}:
        return True
    current, seen = error, set()
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        if getattr(current, "sqlstate", None) in {"40001", "40P01", "55P03", "57014"}:
            return True
        current = current.__cause__ or current.__context__
    return False


class NativeParserPartition:
    def __init__(self, owner):
        self.owner, self.graph, self.engine = owner, owner.graph, owner.engine
        self.progress = {"scanned": 0, "admitted": 0, "conflicts": 0,
            "has_more": False, "next_cursor": None, "conflict_examples": []}

    def _rows(self, tenant, source, position, limit):
        occurred, transition = position if position else (None, None)
        with self.graph.transaction(tenant):
            before = NativeParserCommandBudget(self.owner.clock)
            before()
            rows = self.engine.query("SELECT t.transition_id,t.occurred_at,i.intake_id,i.version,"
                "t.source_snapshot_id FROM intake.intake_stage_transitions t "
                "JOIN intake.intakes i ON i.tenant_id=t.tenant_id AND i.intake_id=t.intake_id "
                "WHERE t.tenant_id=? AND i.source_id=? AND i.processing_state='PARSING' "
                "AND i.intake_method='URL' AND i.deleted_at IS NULL AND i.completed_at IS NULL "
                "AND i.resolved_listing_id IS NULL AND t.resulting_version=i.version "
                "AND t.from_state='AWAITING_ASSISTED_ENTRY' AND t.to_state='PARSING' "
                "AND t.service_principal IS NULL AND t.actor_subject_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM intake.parser_runs r WHERE r.tenant_id=i.tenant_id "
                "AND r.intake_id=i.intake_id) "
                "AND (?::timestamptz IS NULL OR (t.occurred_at,t.transition_id)>(?::timestamptz,?::uuid)) "
                "ORDER BY t.occurred_at,t.transition_id LIMIT ?",
                (tenant, source, occurred, occurred, transition, limit))
            before()
        return rows

    def admit(self, tenant, *, source_id, release_id, limit, cursor=None):
        tenant, release = native_intake_uuid(tenant), native_intake_uuid(release_id)
        source = _source(source_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("NATIVE_PARSER_PARTITION_LIMIT_REJECTED")
        if getattr(getattr(self.engine, "_local", None), "connection", None) is not None:
            raise NativeParserConflict("NATIVE_PARSER_PARTITION_OUTER_ROOT_REJECTED")
        position = _position(cursor, tenant, source, release)
        # Fail absent/bad startup and current policy before scanning. These
        # checks grant no admission and are repeated by the actual owner.
        self.owner._prerequisites()
        native_intake_parser_policy(tenant)
        expires = time.monotonic() + MAX_SCAN_SECONDS
        try:
            while (self.progress["admitted"] < limit and self.progress["scanned"] < MAX_SCAN_EVENTS
                    and time.monotonic() < expires):
                rows = self._rows(tenant, source, position,
                    min(SCAN_BATCH, MAX_SCAN_EVENTS - self.progress["scanned"]))
                if not rows:
                    break
                # Conservative if a consumer closes this iterator early. A
                # complete scan below resolves the actual continuation.
                self.progress["has_more"] = True
                for row in rows:
                    if (self.progress["admitted"] >= limit or self.progress["scanned"] >= MAX_SCAN_EVENTS
                            or time.monotonic() >= expires):
                        break
                    receipt = None
                    try:
                        receipt = self.owner.admit(tenant, row["intake_id"], row["source_snapshot_id"],
                            release, expected_version=row["version"])
                    except Exception as error:
                        if not _candidate_conflict(error):
                            raise
                        self.progress["conflicts"] += 1
                        if len(self.progress["conflict_examples"]) < 10:
                            self.progress["conflict_examples"].append({"intake_id": row["intake_id"],
                                "error_class": type(error).__name__})
                    else:
                        self.progress["admitted"] += 1
                    position = row["occurred_at"], row["transition_id"]
                    self.progress["scanned"] += 1
                    self.progress["next_cursor"] = _cursor(position, tenant, source, release)
                    if receipt is not None:
                        yield receipt
            self.progress["has_more"] = bool(self._rows(tenant, source, position, 1))
        finally:
            self.owner.last_partition_progress = dict(self.progress)
