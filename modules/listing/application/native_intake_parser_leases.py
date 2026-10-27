"""Own both parser leases and bound IO outside every business SQL root.

Only a committed fresh SQL verification can publish a lease time bound. The
bound is not a cached human/source grant: the operation journal must perform
fresh SQL verification before and after each operation. The in-process guard
also observes current parser key/policy removal while a child is running.
"""
from __future__ import annotations

import math
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from modules.external_data.application.native_intake_process import NativeIORejected
from modules.listing.application.native_intake_assisted_entry_request_proof import _time
from modules.listing.application.native_intake_parser_execution import NativeParserExecutionReads
from modules.listing.application.native_intake_parser_policy import (
    CLEANUP_SECONDS,
    HEARTBEAT_SECONDS,
    LEASE_SECONDS,
    MAX_IO_SECONDS,
    NativeParserConflict,
    NativeParserUnavailable,
    native_intake_parser_policy,
)
from shared.jobs.queue import JobFenceRejectedError


@dataclass(frozen=True)
class NativeParserLeaseTimeBound:
    physical_version: int
    business_version: int
    physical_expires_at: datetime
    business_expires_at: datetime
    timeout_at: datetime
    source_valid_until: datetime
    monotonic_io_bound: float


class NativeParserCompositeLeases:
    def __init__(self, owner, delivery, *, business_fence, policy_sha256):
        self.owner, self.graph, self.engine, self.queue = owner, owner.graph, owner.engine, owner.queue
        self.delivery = delivery
        self.tenant, self.generation = owner.auth.decode(delivery)
        if (type(business_fence) is not int or not 2 <= business_fence <= 2**63 - 1
                or not isinstance(policy_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", policy_sha256)):
            raise JobFenceRejectedError("NATIVE_PARSER_LEASE_BINDING_REQUIRED")
        self.business_fence, self.policy_sha256 = business_fence, policy_sha256
        self._stop, self._lock = threading.Event(), threading.Lock()
        self._stop.set()
        self._thread, self._failure, self._proof = None, None, None

    def _renew(self):
        with self.graph.transaction(self.tenant):
            # This read locks the actual physical/business/run rows in order
            # and checks current input, artifact, original human and source.
            state = NativeParserExecutionReads(self.owner).fresh(self.delivery, self.tenant, self.generation,
                business_fence=self.business_fence)
            physical, job, before, context = state.physical, state.job, state.before, state.context
            if job["status"] != "RUNNING" or context.policy.sha256 != self.policy_sha256:
                raise JobFenceRejectedError("NATIVE_PARSER_RENEWAL_NOT_CURRENT")
            before()
            version = self.queue.heartbeat(self.delivery.job_id, expected_version=physical.version,
                fence_token=physical.fence_token)
            before()
            renewed = self.queue.get(self.delivery.job_id)
            if (renewed is None or renewed.version != version or renewed.lease_expires_at is None
                    or renewed.fence_token != physical.fence_token or renewed.payload != physical.payload
                    or renewed.locked_by != physical.locked_by):
                raise JobFenceRejectedError("NATIVE_PARSER_PHYSICAL_RENEWAL_REJECTED")
            timeout = _time(job["timeout_at"])
            business_until = min(renewed.lease_expires_at, timeout, context.classified.valid_until)
            before()
            if self.engine.execute("UPDATE workflow.jobs SET heartbeat_at=clock_timestamp(),lease_expires_at=?,"
                    "version=version+1,updated_at=clock_timestamp() "
                    "WHERE tenant_id=? AND job_id=? AND version=? AND fence_token=? AND status='RUNNING' "
                    "AND cancellation_requested_at IS NULL AND result IS NULL "
                    "AND lease_expires_at>clock_timestamp() AND timeout_at>clock_timestamp() "
                    "AND ?::timestamptz>clock_timestamp()",
                    (business_until.isoformat(), self.tenant, job["job_id"], job["version"], self.business_fence,
                        context.classified.valid_until.isoformat())).rowcount != 1:
                raise JobFenceRejectedError("NATIVE_PARSER_BUSINESS_RENEWAL_REJECTED")
            self.owner._confirm(context.intake, state.run["parser_release_id"], self.generation,
                context.binding(), context.attempt_token, before)
            sample_started = time.monotonic()
            clock = self.owner.clock._clock()
            bound = min(business_until, renewed.lease_expires_at, timeout, context.classified.valid_until)
            if bound <= clock or (renewed.lease_expires_at - clock).total_seconds() > LEASE_SECONDS:
                raise JobFenceRejectedError("NATIVE_PARSER_RENEWAL_TIME_BOUND_REJECTED")
            proof = NativeParserLeaseTimeBound(renewed.version, job["version"] + 1,
                renewed.lease_expires_at, business_until, timeout, context.classified.valid_until,
                sample_started + (bound - clock).total_seconds())
            before()
        # A lost commit response or rolled-back paired heartbeat never grants
        # IO. Each following SQL root re-reads rows, not these row versions.
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
            raise JobFenceRejectedError("NATIVE_PARSER_DUAL_HEARTBEAT_LOST") from failure
        self._renew()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="native-parser-dual-leases", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self._stop.set()
            with self._lock:
                self._proof = None
            raise

    def pause(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.ident is not None:
            thread.join(timeout=CLEANUP_SECONDS)
            if thread.is_alive():
                raise NativeParserConflict("NATIVE_PARSER_HEARTBEAT_NOT_JOINED")
        self._thread = None
        with self._lock:
            self._proof = None

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
            raise NativeIORejected("NATIVE_IO_PARSER_DUAL_LEASE_LOST")
        try:
            if (getattr(self.owner, "_deployment_configuration_error", None) is not None
                    or getattr(self.owner, "_artifact_startup_error", None) is not None
                    or self.owner.artifact_registry is None
                    or native_intake_parser_policy(self.tenant).sha256 != self.policy_sha256
                    or self.owner.auth.decode(self.delivery) != (self.tenant, self.generation)):
                raise NativeIORejected("NATIVE_IO_PARSER_CURRENT_CONFIG_LOST")
        except (NativeParserUnavailable, NativeParserConflict, PermissionError, JobFenceRejectedError):
            raise NativeIORejected("NATIVE_IO_PARSER_CURRENT_CONFIG_LOST") from None

    def io_deadline(self, *, maximum_seconds=MAX_IO_SECONDS):
        if (type(maximum_seconds) not in {int, float} or not math.isfinite(maximum_seconds)
                or not 0 < maximum_seconds <= MAX_IO_SECONDS):
            raise NativeIORejected("NATIVE_IO_PARSER_DEADLINE_REJECTED")
        self.guard()
        with self._lock:
            proof = self._proof
        if proof is None:
            raise NativeIORejected("NATIVE_IO_PARSER_DUAL_LEASE_LOST")
        bound = min(time.monotonic() + maximum_seconds, proof.monotonic_io_bound - .01)
        if bound <= time.monotonic():
            raise NativeIORejected("NATIVE_IO_PARSER_DEADLINE_EXPIRED")
        return bound
