"""Atomic purpose-bound native parser admission, with no external IO.

An explicit snapshot/release selects one canonical run and two coupled jobs.
Missing positive prerequisites roll back before admission. Construction and
historical readers remain available after positive configuration is removed.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import UUID, uuid4, uuid5

from modules.external_data.application.native_parser_artifacts import (
    NativeParserArtifactRejected,
    NativeParserArtifactUnavailable,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _object
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_job_proof import (
    NativeParserJobProof,
    parser_admission_provenance,
)
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    NativeParserAuthenticator,
    NativeParserConflict,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_parser_prerequisites import (
    NativeParserAdmissionPrerequisites,
    NativeParserCommandBudget,
    NativeParserSQLClock,
    native_parser_run_id,
)
from modules.listing.application.native_intake_parser_runs import CanonicalNativeParserRunRepository
from modules.listing.application.native_intake_pending_commands import NativeIntakePendingCommands
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from shared.audit.backlog import CanonicalAuditBacklog
from shared.jobs.queue import JobRequest, JobStatus


class NativeIntakeParserOwner:
    def __init__(self, graph, *, artifact_registry=None, deployment_configuration_error=None,
            artifact_startup_error=None, storage_factory=None, parser_process_factory=None):
        if (deployment_configuration_error is not None
                and (type(deployment_configuration_error) is not str
                    or deployment_configuration_error != "NATIVE_PARSER_DEPLOYMENT_CONFIGURATION_REJECTED")):
            raise ValueError("NATIVE_PARSER_DEPLOYMENT_ERROR_CODE_REJECTED")
        if (artifact_startup_error is not None
                and (type(artifact_startup_error) is not str or artifact_startup_error not in {"REJECTED", "UNAVAILABLE"})):
            raise ValueError("NATIVE_PARSER_ARTIFACT_STARTUP_ERROR_REJECTED")
        if any(value is not None and not callable(value) for value in (storage_factory, parser_process_factory)):
            raise TypeError("NATIVE_PARSER_TRUSTED_FACTORY_REQUIRED")
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        # A missing positive capability must not prevent historical reads or
        # later owned negative settlement. Positive verification is explicit.
        self.artifact_registry = artifact_registry
        # This bounded startup observation is supplied only by trusted
        # composition. Keep it separate from absence without retaining the
        # invalid setting or exception traceback. It grants no action.
        self._deployment_configuration_error = deployment_configuration_error
        self._artifact_startup_error = artifact_startup_error
        self._storage_factory, self._parser_process_factory = storage_factory, parser_process_factory
        self.clock = NativeParserSQLClock(self.engine)
        self.runs = CanonicalNativeParserRunRepository(self.engine)
        self.jobs = NativeParserJobProof(graph)
        self.pending = NativeIntakePendingCommands(graph)
        self.auth = NativeParserAuthenticator()

    def require_deployment(self):
        if self._deployment_configuration_error is not None:
            raise NativeParserUnavailable(self._deployment_configuration_error) from None
        if self._artifact_startup_error == "REJECTED":
            raise NativeParserArtifactRejected("NATIVE_PARSER_ARTIFACT_REJECTED") from None
        if self._artifact_startup_error == "UNAVAILABLE":
            raise NativeParserArtifactUnavailable("NATIVE_PARSER_ARTIFACT_READ_UNAVAILABLE") from None

    def _prerequisites(self):
        self.require_deployment()
        return NativeParserAdmissionPrerequisites(self.graph, self.artifact_registry)

    def _audit(self, intake, action, previous, result, *, at, audit_id, causation):
        self.graph.audit_sink.persist({"audit_event_id": audit_id, "tenant_id": intake["tenant_id"],
            "resource_type": "intake", "resource_id": intake["intake_id"], "action": action, "result": "SUCCEEDED",
            "actor_subject_id": None, "service_principal": JOB_TYPE, "actor_role": "SVC_PARSER",
            "correlation_id": intake["correlation_id"], "causation_id": causation, "occurred_at": at.isoformat(),
            "before_value": previous, "after_value": result,
            "reason": "Reserve the explicitly selected currently authorized native parser execution."},
            {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})

    def admit(self, tenant, intake_id, snapshot_id, release_id, *, expected_version):
        tenant, intake_id, snapshot_id, release_id = (native_intake_uuid(value) for value in (
            tenant, intake_id, snapshot_id, release_id))
        if type(expected_version) is not int or not 2 <= expected_version <= 2**63 - 1:
            raise ValueError("NATIVE_PARSER_EXPLICIT_VERSION_REQUIRED")
        run_id = native_parser_run_id(tenant, snapshot_id, release_id)
        job_id = str(uuid5(UUID(run_id), JOB_TYPE))
        with self.graph.transaction(tenant):
            before = NativeParserCommandBudget(self.clock)
            # All owners use physical, business, then Intake lock order.
            before()
            physical_row = self.engine.query_one("SELECT job_id FROM durable_jobs WHERE job_id=? FOR UPDATE", (job_id,))
            before()
            prior = self.engine.query_one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR UPDATE",
                (tenant, job_id))
            before()
            intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                (tenant, intake_id))
            if intake["version"] != expected_version:
                raise NativeParserConflict("NATIVE_PARSER_INTAKE_VERSION_CONFLICT")
            generation = 1 if prior is None else _object(prior["payload"])["delivery_generation"]
            context = self._prerequisites().verify(
                intake, release_id, generation=generation, before=before)
            if context.parser_run_id != run_id or context.job_id != job_id:
                raise NativeParserConflict("NATIVE_PARSER_SNAPSHOT_BINDING_CONFLICT")
            before.deadline = min(before.deadline, context.classified.valid_until)
            binding = context.binding()
            # Existing human/recheck lanes retain their exact closure readers.
            self.pending.require_clear(intake, before=before)
            before()
            foreign = self.engine.query_one("SELECT parser_run_id FROM intake.parser_runs "
                "WHERE tenant_id=? AND intake_id=? AND parser_run_id<>? LIMIT 1", (tenant, intake_id, run_id))
            if foreign is not None:
                # A separate post-terminal reprocessing contract must preserve
                # earlier proofs; this first owner never resets another run.
                raise NativeParserConflict("NATIVE_PARSER_OTHER_RUN_REQUIRES_EXPLICIT_REPROCESSING")
            before()
            orphan = self.engine.query_one("SELECT job_id FROM workflow.jobs WHERE tenant_id=? "
                "AND job_type=? AND payload->'admission'->'binding'->>'intake_id'=? AND job_id<>? LIMIT 1",
                (tenant, JOB_TYPE, intake_id, job_id))
            if orphan is not None:
                raise NativeParserConflict("NATIVE_PARSER_OTHER_COMMAND_CONFLICT")
            row = self.runs.get(tenant, run_id, before=before, for_update=True)
            if prior is not None:
                if row is None or physical_row is None:
                    raise NativeParserConflict("NATIVE_PARSER_PARTIAL_ADMISSION_REJECTED")
                receipt = self.jobs.admission(prior, row, before=before)
                if (canonical_sha256(_object(prior["payload"])["admission"]["binding"]) != canonical_sha256(binding)
                        or self.auth.decode(self.queue.get(job_id)) != (tenant, generation)):
                    raise NativeParserConflict("NATIVE_PARSER_ADMISSION_REPLAY_CHANGED")
                self._confirm(intake, release_id, generation, binding, context.attempt_token, before)
                before()
                return receipt
            if row is not None or physical_row is not None:
                raise NativeParserConflict("NATIVE_PARSER_PARTIAL_ADMISSION_REJECTED")
            before()
            CanonicalAuditBacklog(self.engine).require_available(tenant)
            at, audit_id = self.clock._clock(), str(uuid4())
            timeout = at + timedelta(seconds=context.policy.deadline_seconds)
            receipt = {"job_id": job_id, "parser_run_id": run_id, "intake_id": intake_id,
                "source_snapshot_id": snapshot_id, "parser_release_id": release_id,
                "admitted_intake_version": expected_version, "job_status_at_admission": "QUEUED",
                "timeout_at": timeout.isoformat(), "audit_event_id": audit_id}
            token_hash = hashlib.sha256(context.attempt_token.encode()).hexdigest()
            original = {"binding": binding, "artifact": context.artifact.snapshot(), "policy": context.policy.snapshot(),
                "admitted_at": at.isoformat(), "timeout_at": timeout.isoformat(),
                "initial_attempt_token_sha256": token_hash, "receipt": receipt}
            payload = {"admission": original, "delivery_generation": 1,
                "attempt_token_sha256": token_hash, "attempt_intents": []}
            self.runs.insert_queued(tenant=tenant, run_id=run_id, intake_id=intake_id, snapshot_id=snapshot_id,
                release_id=release_id, correlation_id=intake["correlation_id"], at=at, before=before)
            before()
            if self.engine.execute("INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
                    "status,checkpoint,attempt,max_attempts,fence_token,timeout_at,payload,correlation_id,idempotency_key) "
                    "SELECT ?,?,?,'parser_run',?,'QUEUED','PARSING',0,?,1,?,?::jsonb,?,? "
                    "WHERE ?::timestamptz>clock_timestamp() AND ?::timestamptz>clock_timestamp()",
                    (job_id, tenant, JOB_TYPE, run_id, context.policy.max_attempts, timeout.isoformat(),
                        canonical_json(payload), intake["correlation_id"], run_id,
                        timeout.isoformat(), context.classified.valid_until.isoformat())).rowcount != 1:
                raise NativeParserConflict("NATIVE_PARSER_ADMISSION_EXPIRED")
            before()
            physical, created = self.queue.enqueue(JobRequest(JOB_TYPE,
                {"job_id": job_id, "tenant_id": tenant, "attempt_token": context.attempt_token}, job_id=job_id,
                idempotency_key=JOB_TYPE + ":" + tenant + ":" + run_id), correlation_id=intake["correlation_id"])
            if not created or physical.job_id != job_id:
                raise NativeParserConflict("NATIVE_PARSER_PHYSICAL_RESERVATION_CONFLICT")
            before()
            if self.engine.execute("UPDATE durable_jobs SET max_retries=? WHERE job_id=? AND status=? "
                    "AND attempts=0 AND fence_token=0",
                    (context.policy.max_attempts, job_id, JobStatus.QUEUED.value)).rowcount != 1:
                raise NativeParserConflict("NATIVE_PARSER_PHYSICAL_INITIAL_STATE_REJECTED")
            before()
            self._audit(intake, "intake.parser_admitted.v1", {"processing_state": "PARSING", "version": expected_version},
                {**receipt, "native_parser_admission": original,
                    "native_parser_provenance": parser_admission_provenance(payload)}, at=at,
                audit_id=audit_id, causation=context.original.parent_event_id)
            before()
            # Reconstruct current prerequisites at the last positive boundary;
            # no initial context becomes a cached authority grant.
            self._confirm(intake, release_id, 1, binding, context.attempt_token, before)
            before()
            return receipt

    def admit_partition(self, tenant, *, source_id, release_id, limit, cursor=None):
        from modules.listing.application.native_intake_parser_partition import NativeParserPartition

        yield from NativeParserPartition(self).admit(tenant, source_id=source_id,
            release_id=release_id, limit=limit, cursor=cursor)

    def _confirm(self, intake, release_id, generation, binding, token, before):
        final = self._prerequisites().verify(
            intake, release_id, generation=generation, before=before)
        if (canonical_sha256(final.binding()) != canonical_sha256(binding) or final.attempt_token != token):
            raise NativeParserConflict("NATIVE_PARSER_ADMISSION_CONFIG_CHANGED")
        before.check_in_memory()

    def _accept(self, delivery, tenant, generation):
        from modules.listing.application.native_intake_parser_execution import (
            NativeParserExecutionReads,
        )

        return NativeParserExecutionReads(self).accept(delivery, tenant, generation)

    def replay(self, delivery, *, leases=None):
        """Read immutable terminal evidence; never restore execution grants."""
        from modules.listing.application.native_intake_parser_history import (
            NativeParserTerminalHistory,
        )
        from modules.listing.application.native_intake_parser_leases import (
            NativeParserCompositeLeases,
        )
        from shared.jobs.queue import JobFenceRejectedError

        if leases is not None:
            if (not isinstance(leases, NativeParserCompositeLeases)
                    or leases.owner is not self or leases.delivery != delivery):
                raise JobFenceRejectedError("NATIVE_PARSER_REPLAY_LEASE_BINDING_REJECTED")
            leases.pause()
        return NativeParserTerminalHistory(self.graph).replay(delivery)

    def settle_unavailable(self, delivery, *, business_fence, leases=None):
        """Close an accepted owned unavailable command, preserving Intake."""
        from modules.listing.application.native_intake_parser_negative import (
            NativeParserUnavailableSettlement,
        )

        return NativeParserUnavailableSettlement(self).settle(delivery,
            business_fence=business_fence, leases=leases)

    def settle_permanent(self, delivery, *, leases):
        """Fail the authorized Intake for a proved defect or original retry exhaustion."""
        from modules.listing.application.native_intake_parser_permanent import (
            NativeParserPermanentSettlement,
        )

        return NativeParserPermanentSettlement(self).settle(delivery, leases=leases)

    def prepare_retry(self, delivery, *, leases):
        """Queue the same actual run after a proved reaped transient failure."""
        from modules.listing.application.native_intake_parser_retry import NativeParserRetry

        return NativeParserRetry(self).prepare(delivery, leases=leases)

    def cancel_unaccepted(self, delivery, *, leases=None):
        """Cancel an owned initial/prepared command with an actual negative cause."""
        from modules.listing.application.native_intake_parser_cancellation import (
            NativeParserUnacceptedCancellation,
        )

        return NativeParserUnacceptedCancellation(self).cancel(delivery, leases=leases)

    def recover_expired(self, delivery, *, leases=None):
        """Close an expired actual accepted attempt under the current physical owner."""
        from modules.listing.application.native_intake_parser_cancellation import (
            NativeParserUnacceptedCancellation,
        )

        return NativeParserUnacceptedCancellation(self).recover_expired(delivery, leases=leases)

    def execute(self, delivery):
        from modules.listing.application.native_intake_parser_runtime import (
            NativeIntakeParserRuntime,
        )

        return NativeIntakeParserRuntime(self, storage_factory=self._storage_factory,
            parser_process_factory=self._parser_process_factory).execute(delivery)


def run_native_intake_parser_delivery(delivery, owner: NativeIntakeParserOwner):
    return owner.execute(delivery)
