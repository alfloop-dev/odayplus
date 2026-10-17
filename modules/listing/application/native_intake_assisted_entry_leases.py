"""An assisted-entry owner renews both leases outside every business SQL root."""

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from modules.external_data.application.native_intake_process import NativeIORejected
from modules.listing.application.native_intake_assisted_entry_policy import (
    HEARTBEAT_SECONDS,
    JOB_TYPE,
    NativeAssistedEntryAuthenticator,
    NativeAssistedEntryConflict,
    native_intake_assisted_entry_policy,
)
from modules.listing.application.native_intake_retrieval_policy import canonical_sha256
from shared.jobs.queue import JobFenceRejectedError, JobStatus


def _time(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise JobFenceRejectedError("NATIVE_ASSISTED_ENTRY_LEASE_TIME_UNAVAILABLE")
    return result


@dataclass(frozen=True)
class NativeAssistedLeaseProof:
    physical_version: int
    business_version: int
    physical_expires_at: datetime
    business_expires_at: datetime
    timeout_at: datetime
    monotonic_io_bound: float


class NativeAssistedCompositeLeases:
    def __init__(self, graph, delivery, *, business_fence: int, policy_sha256: str):
        self.graph, self.engine, self.queue, self.delivery = graph, graph.engine, graph.queue, delivery
        self.tenant, self.generation = NativeAssistedEntryAuthenticator().decode(delivery)
        if type(business_fence) is not int or business_fence < 1:
            raise JobFenceRejectedError("NATIVE_ASSISTED_ENTRY_BUSINESS_FENCE_REQUIRED")
        self.business_fence, self.policy_sha256 = business_fence, policy_sha256
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._failure = None
        self._proof = None

    def _renew(self):
        # No cached row version is passed into the root. The physical row is
        # locked/read first, then business; either both renew or both roll back.
        with self.graph.transaction(self.tenant):
            self.engine.query_one("SELECT set_config('lock_timeout',?,true)", ("100ms",))
            self.engine.query_one("SELECT set_config('statement_timeout',?,true)", ("500ms",))
            self.graph._one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (self.delivery.job_id,))
            physical = self.queue.get(self.delivery.job_id)
            business = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
                                       (self.tenant, self.delivery.job_id))
            now = _time(self.engine.query_one("SELECT clock_timestamp() AS now")["now"])
            payload = business["payload"]
            if (physical is None or physical.status != JobStatus.RUNNING or physical.job_type != JOB_TYPE
                    or physical.payload != self.delivery.payload or physical.locked_by != self.delivery.locked_by
                    or not physical.locked_by or physical.fence_token != self.delivery.fence_token
                    or physical.correlation_id != self.delivery.correlation_id
                    or physical.lease_expires_at is None or physical.lease_expires_at <= now
                    or business["job_type"] != JOB_TYPE or business["status"] != "RUNNING"
                    or business["result"] is not None or business["fence_token"] != self.business_fence
                    or business["cancellation_requested_at"] is not None
                    or business["lease_expires_at"] is None or _time(business["lease_expires_at"]) <= now
                    or _time(business["timeout_at"]) <= now
                    or payload.get("delivery_generation") != self.generation
                    or payload.get("accepted_delivery_fence") != physical.fence_token
                    or payload.get("accepted_worker") != physical.locked_by
                    or canonical_sha256(payload["policy"]) != self.policy_sha256
                    or native_intake_assisted_entry_policy(self.tenant).sha256 != self.policy_sha256):
                raise JobFenceRejectedError("NATIVE_ASSISTED_ENTRY_DUAL_LEASE_NOT_OWNED")
            if NativeAssistedEntryAuthenticator().decode(physical) != (self.tenant, self.generation):
                raise JobFenceRejectedError("NATIVE_ASSISTED_ENTRY_RENEWAL_KEY_REJECTED")
            version = self.queue.heartbeat(self.delivery.job_id, expected_version=physical.version,
                                           fence_token=physical.fence_token)
            renewed = self.queue.get(self.delivery.job_id)
            if (renewed.version != version or renewed.lease_expires_at is None
                    or renewed.fence_token != physical.fence_token):
                raise JobFenceRejectedError("NATIVE_ASSISTED_ENTRY_PHYSICAL_RENEWAL_REJECTED")
            if self.engine.execute("UPDATE workflow.jobs SET heartbeat_at=clock_timestamp(),lease_expires_at=?,"
                "version=version+1,updated_at=clock_timestamp() "
                "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                "AND cancellation_requested_at IS NULL AND result IS NULL "
                "AND lease_expires_at>clock_timestamp() AND timeout_at>clock_timestamp()",
                (renewed.lease_expires_at.isoformat(), self.tenant, self.delivery.job_id,
                 business["version"], self.business_fence)).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_ASSISTED_ENTRY_BUSINESS_RENEWAL_REJECTED")
            clock_sample_started = time.monotonic()
            clock = _time(self.engine.query_one("SELECT clock_timestamp() AS now")["now"])
            timeout = _time(business["timeout_at"])
            bound = min(timeout, renewed.lease_expires_at)
            if bound <= clock:
                raise JobFenceRejectedError("NATIVE_ASSISTED_ENTRY_RENEWAL_EXPIRED")
            proof = NativeAssistedLeaseProof(renewed.version, business["version"] + 1,
                renewed.lease_expires_at, renewed.lease_expires_at, timeout,
                clock_sample_started + (bound - clock).total_seconds())
        # Publish state only after SQL commits; an unknown commit never grants
        # IO. A subsequent root re-reads both rows without these cached versions.
        with self._lock:
            self._proof = proof

    def _run(self):
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                self._renew()
            except BaseException as error:
                with self._lock:
                    self._failure = error
                self._stop.set()
                return

    def start(self):
        self.pause()
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise JobFenceRejectedError("NATIVE_ASSISTED_ENTRY_DUAL_HEARTBEAT_LOST") from failure
        self._renew()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="native-assisted-entry-dual-leases", daemon=True)
        self._thread.start()

    def pause(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.ident is not None:
            thread.join(timeout=1)
            if thread.is_alive():
                raise NativeAssistedEntryConflict("NATIVE_ASSISTED_ENTRY_HEARTBEAT_NOT_JOINED")
        self._thread = None

    @contextmanager
    def root(self):
        self.pause()
        with self.graph.transaction(self.tenant):
            yield

    def guard(self):
        with self._lock:
            proof, failure = self._proof, self._failure
        if (failure is not None or proof is None or self._stop.is_set()
                or time.monotonic() >= proof.monotonic_io_bound):
            raise NativeIORejected("NATIVE_IO_DUAL_LEASE_LOST")

    def io_deadline(self):
        self.guard()
        with self._lock:
            return min(time.monotonic() + 10, self._proof.monotonic_io_bound - 0.01)
