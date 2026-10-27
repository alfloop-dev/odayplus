"""Anonymous global controls; caller first locks physical/business/Intake/source.

Counts deliberately read these global ledgers, never tenant-filtered jobs.
There is no SQL root across IO. Every redirect is a new charged HTTP intent;
storage operations use their own inflight slot without charging source rate.
"""

import hashlib
from dataclasses import dataclass
from uuid import uuid4

from modules.listing.application.native_intake_retrieval_policy import (
    MAX_GLOBAL_CONCURRENCY,
    MAX_IO_SECONDS,
    NativeIntakeRetrievalConflict,
)

GLOBAL_CONTROL_TABLES = (
    "intake.native_retrieval_rate_reservations",
    "intake.native_retrieval_inflight_slots",
)
# Distinct fixed advisory namespaces, unrelated to workflow identities. Source
# collisions only serialize more operations; they cannot grant more capacity.
_GLOBAL_LOCK = 0x4F445052
_SOURCE_LOCK = 0x4F445053
SLOT_SECONDS = MAX_IO_SECONDS + 2  # hard IO wall plus bounded cleanup and margin


class NativeRetrievalQuotaExceeded(NativeIntakeRetrievalConflict):
    pass


@dataclass(frozen=True)
class NativeRetrievalReservation:
    reservation_id: str
    rate_reservation_id: str | None
    source_id: str
    expires_at: str


class NativeIntakeRetrievalQuota:
    def __init__(self, engine):
        self.engine = engine

    def _root(self):
        if (getattr(self.engine, "dialect", None) != "postgresql"
                or getattr(getattr(self.engine, "_local", None), "connection", None) is None
                or self.engine.query_one("SHOW transaction_isolation")["transaction_isolation"] != "serializable"):
            raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_QUOTA_SERIALIZABLE_ROOT_REQUIRED")

    def _locks(self, source_id, before):
        self._root()
        if not isinstance(source_id, str) or not 1 <= len(source_id) <= 256:
            raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_QUOTA_SOURCE_REJECTED")
        before()
        self.engine.query_one("SELECT pg_advisory_xact_lock(?,?)", (_GLOBAL_LOCK, 0))
        before()
        source_lock = int.from_bytes(hashlib.sha256(source_id.encode()).digest()[:4], "big", signed=True)
        self.engine.query_one("SELECT pg_advisory_xact_lock(?,?)", (_SOURCE_LOCK, source_lock))
        before()

    def schema(self, *, before):
        self._root()
        before()
        rows = self.engine.query("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,a.attname,"
            "format_type(a.atttypid,a.atttypmod) AS field_type FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid "
            "WHERE n.nspname='intake' AND c.relname IN "
            "('native_retrieval_rate_reservations','native_retrieval_inflight_slots') "
            "AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum")
        expected = {"reservation_id": "uuid", "source_id": "text", "admitted_at": "timestamp with time zone",
                    "expires_at": "timestamp with time zone"}
        for name in ("native_retrieval_rate_reservations", "native_retrieval_inflight_slots"):
            columns = [r for r in rows if r["relname"] == name]
            if ({r["attname"]: r["field_type"] for r in columns} != expected
                    or any(r["relrowsecurity"] or r["relforcerowsecurity"] for r in columns)):
                raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_GLOBAL_QUOTA_SCHEMA_UNAVAILABLE")
        before()
        if self.engine.query_one("SELECT 1 AS forbidden FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='intake' AND c.contype='f' "
            "AND t.relname IN ('native_retrieval_rate_reservations','native_retrieval_inflight_slots') LIMIT 1"):
            raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_GLOBAL_QUOTA_REVERSE_LINK_FORBIDDEN")
        before()

    def reserve(self, source: dict, *, global_limit: int, charge_rate: bool, before) -> NativeRetrievalReservation:
        if (type(global_limit) is not int or not 1 <= global_limit <= MAX_GLOBAL_CONCURRENCY
                or type(charge_rate) is not bool
                or any(type(source.get(k)) is not int or source[k] < 1
                       for k in ("requests_per_minute", "concurrent_requests"))):
            raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_QUOTA_LIMITS_REJECTED")
        self.schema(before=before)
        self._locks(source["source_id"], before)
        self.engine.execute("DELETE FROM intake.native_retrieval_rate_reservations WHERE expires_at<=clock_timestamp()")
        before()
        self.engine.execute("DELETE FROM intake.native_retrieval_inflight_slots WHERE expires_at<=clock_timestamp()")
        before()
        counts = self.engine.query_one("SELECT count(*) AS total,count(*) FILTER (WHERE source_id=?) AS source_total "
            "FROM intake.native_retrieval_inflight_slots WHERE expires_at>clock_timestamp()", (source["source_id"],))
        if counts["total"] >= global_limit or counts["source_total"] >= source["concurrent_requests"]:
            raise NativeRetrievalQuotaExceeded("NATIVE_RETRIEVAL_CONCURRENCY_FULL")
        before()
        rate_id = None
        if charge_rate:
            rate = self.engine.query_one("SELECT count(*) AS total FROM intake.native_retrieval_rate_reservations "
                "WHERE source_id=? AND expires_at>clock_timestamp()", (source["source_id"],))["total"]
            if rate >= source["requests_per_minute"]:
                raise NativeRetrievalQuotaExceeded("NATIVE_RETRIEVAL_SOURCE_RATE_FULL")
            before()
            rate_id = str(uuid4())
            self.engine.execute("INSERT INTO intake.native_retrieval_rate_reservations "
                "(reservation_id,source_id,admitted_at,expires_at) "
                "VALUES (?,?,clock_timestamp(),clock_timestamp()+interval '60 seconds')", (rate_id, source["source_id"]))
        before()
        slot_id = str(uuid4())
        row = self.engine.query_one("INSERT INTO intake.native_retrieval_inflight_slots "
            "(reservation_id,source_id,admitted_at,expires_at) "
            "VALUES (?,?,clock_timestamp(),clock_timestamp()+interval '12 seconds') RETURNING expires_at",
            (slot_id, source["source_id"]))
        before()
        return NativeRetrievalReservation(slot_id, rate_id, source["source_id"], str(row["expires_at"]))

    def reserve_storage(self, source_id: str, *, global_limit: int, source_limit: int,
                        before) -> NativeRetrievalReservation:
        """Reserve only storage capacity from the same anonymous global pool.

        Manual-only sources may have zero HTTP rate/concurrency budgets. This
        explicit interface uses separately closed owner configuration instead;
        it neither reads nor charges the HTTP rate ledger. Retrieval reserve()
        retains its original HTTP policy and counts these same inflight slots.
        """
        if (type(global_limit) is not int or not 1 <= global_limit <= MAX_GLOBAL_CONCURRENCY
                or type(source_limit) is not int or not 1 <= source_limit <= global_limit):
            raise NativeIntakeRetrievalConflict("NATIVE_ASSISTED_STORAGE_LIMITS_REJECTED")
        self.schema(before=before)
        self._locks(source_id, before)
        self.engine.execute("DELETE FROM intake.native_retrieval_inflight_slots WHERE expires_at<=clock_timestamp()")
        before()
        counts = self.engine.query_one("SELECT count(*) AS total,count(*) FILTER (WHERE source_id=?) AS source_total "
            "FROM intake.native_retrieval_inflight_slots WHERE expires_at>clock_timestamp()", (source_id,))
        if counts["total"] >= global_limit or counts["source_total"] >= source_limit:
            raise NativeRetrievalQuotaExceeded("NATIVE_ASSISTED_STORAGE_CONCURRENCY_FULL")
        before()
        slot_id = str(uuid4())
        row = self.engine.query_one("INSERT INTO intake.native_retrieval_inflight_slots "
            "(reservation_id,source_id,admitted_at,expires_at) "
            "VALUES (?,?,clock_timestamp(),clock_timestamp()+interval '12 seconds') RETURNING expires_at",
            (slot_id, source_id))
        before()
        return NativeRetrievalReservation(slot_id, None, source_id, str(row["expires_at"]))

    def release(self, reservation: NativeRetrievalReservation, *, process_reaped: bool, before):
        if process_reaped is not True:
            raise NativeIntakeRetrievalConflict("NATIVE_RETRIEVAL_QUOTA_REAP_PROOF_REQUIRED")
        self.schema(before=before)
        self._locks(reservation.source_id, before)
        self.engine.execute("DELETE FROM intake.native_retrieval_inflight_slots WHERE reservation_id=? AND source_id=?",
                            (reservation.reservation_id, reservation.source_id))
        before()
