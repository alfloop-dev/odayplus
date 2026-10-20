"""Current parser prerequisites inside one original bounded tenant SQL root.

This reader admits no job and performs no provider IO. A returned context is
valid only for its caller's current transaction; every later positive boundary
must reconstruct it. Catalog status and historical success are never grants.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from modules.external_data.application.native_assisted_snapshot_transport import bound_assisted_key
from modules.external_data.application.native_parser_artifacts import NativeParserArtifactRegistry
from modules.external_data.application.native_parser_snapshot_transport import (
    native_parser_snapshot_credential,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_request import human_transition_role
from modules.listing.application.native_intake_assisted_entry_request_proof import _time
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_input import NativeParserManualInputReader
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    NativeParserAuthenticator,
    NativeParserConflict,
    NativeParserUnavailable,
    native_intake_parser_policy,
)
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_retrieval_policy import canonical_sha256


class NativeParserSQLClock:
    def __init__(self, engine):
        self.engine = engine

    def _clock(self):
        if (getattr(self.engine, "dialect", None) != "postgresql"
                or getattr(getattr(self.engine, "_local", None), "connection", None) is None):
            raise NativeParserUnavailable("NATIVE_PARSER_CURRENT_SQL_ROOT_REQUIRED")
        return _time(self.engine.query_one("SELECT clock_timestamp() AS now")["now"])

    def _admission_budget(self, deadline):
        if (getattr(self.engine, "dialect", None) != "postgresql"
                or getattr(getattr(self.engine, "_local", None), "connection", None) is None):
            raise NativeParserUnavailable("NATIVE_PARSER_CURRENT_SQL_ROOT_REQUIRED")
        # One fresh database observation and both local settings share the same
        # statement. Expiry must leave the existing timeouts unchanged.
        observed = self.engine.query_one("WITH observed AS MATERIALIZED ("
            "SELECT clock_timestamp() AS observed_at), remaining AS MATERIALIZED ("
            "SELECT floor(EXTRACT(EPOCH FROM (?::timestamptz-observed_at))*1000)::bigint-1 AS ms "
            "FROM observed) SELECT ms AS remaining_ms, "
            "CASE WHEN ms>=1 THEN set_config('statement_timeout',ms::text||'ms',true) END AS statement_timeout, "
            "CASE WHEN ms>=1 THEN set_config('lock_timeout',ms::text||'ms',true) END AS lock_timeout "
            "FROM remaining", (deadline.isoformat(),))
        if observed["remaining_ms"] < 1:
            raise NativeParserConflict("NATIVE_PARSER_ROOT_DEADLINE_EXPIRED")


class NativeParserCommandBudget(NativeIntakeCommandBudget):
    def check_in_memory(self):
        try:
            super().check_in_memory()
        except NativeAssistedEntryConflict as error:
            if str(error) != "NATIVE_COMMAND_ROOT_DEADLINE_EXPIRED":
                raise
            raise NativeParserConflict("NATIVE_PARSER_ROOT_DEADLINE_EXPIRED") from None


def native_parser_run_id(tenant, snapshot, release):
    tenant, snapshot, release = (native_intake_uuid(value) for value in (tenant, snapshot, release))
    return str(uuid5(UUID(snapshot), f"native-parser-run-v1:{tenant}:{release}"))


@dataclass(frozen=True)
class NativeParserAdmissionContext:
    intake: dict
    original: object
    policy: object
    artifact: object
    principal: object
    source: dict
    classified: object
    parser_run_id: str
    job_id: str
    attempt_token: str

    def binding(self):
        scope = self.principal.scope
        authority = {"subject_id": self.principal.subject_id,
            "role": human_transition_role(self.principal).value,
            "scope": {"tenant_id": scope.tenant_id, "clearance": scope.clearance.name,
                **{key: sorted(getattr(scope, key)) for key in (
                    "brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules")}}}
        return {"intake_id": self.intake["intake_id"], "intake_version": self.intake["version"],
            "tenant_id": self.intake["tenant_id"], "original_input": self.original.binding(),
            "policy_sha256": self.policy.sha256, "artifact_sha256": self.artifact.sha256,
            "source_version": self.source["version"], "source_sha256": self.classified.source_snapshot_sha256,
            "source_valid_until": self.classified.valid_until.isoformat(),
            "human_authority_sha256": canonical_sha256(authority),
            "parser_run_id": self.parser_run_id, "job_id": self.job_id}


class NativeParserAdmissionPrerequisites:
    def __init__(self, graph, registry: NativeParserArtifactRegistry):
        if not isinstance(registry, NativeParserArtifactRegistry):
            raise NativeParserUnavailable("NATIVE_PARSER_ARTIFACT_REGISTRY_REQUIRED")
        self.graph, self.engine, self.registry = graph, graph.engine, registry
        self.inputs = NativeParserManualInputReader(graph)
        self.clock = NativeParserSQLClock(self.engine)

    def verify(self, intake, release_id, *, generation, before):
        if (getattr(self.engine, "dialect", None) != "postgresql"
                or getattr(getattr(self.engine, "_local", None), "connection", None) is None):
            raise NativeParserUnavailable("NATIVE_PARSER_CURRENT_SQL_ROOT_REQUIRED")
        policy = native_intake_parser_policy(intake["tenant_id"])
        release_id = native_intake_uuid(release_id)
        # Reject a missing capability before expensive lineage reads. This is
        # not a grant; the actual purpose-bound signature is still checked
        # after the complete current source/human/artifact proof below.
        NativeParserAuthenticator.require_available()
        before()
        # Backend classification is deliberately not deployment authority.
        observed = self.engine.query_one("SELECT current_database() AS name")
        if (observed["name"] != policy.realm.database_name or not isinstance(intake["source_id"], str)
                or not intake["source_id"].startswith(policy.realm.source_prefix)):
            raise NativeParserConflict("NATIVE_PARSER_ENGINEERING_NAMESPACE_REJECTED")
        original = self.inputs.read(intake, before=before)
        principal, source, classified = self.inputs.current_authority(intake, original, before=before)
        before()
        release = self.engine.query_one("SELECT * FROM intake.parser_releases WHERE parser_release_id=? "
            "AND source_id=? FOR SHARE", (release_id, intake["source_id"]))
        if release is None:
            raise NativeParserUnavailable("NATIVE_PARSER_RELEASE_UNAVAILABLE")
        before()
        artifact = self.registry.verify_release(release, now=self.clock._clock())
        snapshot = original.snapshot()
        prefix = "gs://" + policy.storage.bucket + "/"
        if (snapshot["capture_method"] != "MANUAL_ENTRY" or snapshot["media_type"] != "application/json"
                or snapshot["residency_mode"] != policy.storage.residency_mode
                or snapshot["retention_class"] != policy.storage.retention_class
                or not policy.storage.accepts_actual_key_version(snapshot["encryption_key_ref"])
                or not isinstance(snapshot["raw_object_uri"], str) or not snapshot["raw_object_uri"].startswith(prefix)):
            raise NativeParserConflict("NATIVE_PARSER_CURRENT_STORAGE_BINDING_REJECTED")
        bound_assisted_key(policy.storage, snapshot["raw_object_uri"][len(prefix):])
        # Availability only, never evidence of GCS permission or actual bytes.
        native_parser_snapshot_credential()
        run_id = native_parser_run_id(intake["tenant_id"], snapshot["source_snapshot_id"], release_id)
        job_id = str(uuid5(UUID(run_id), JOB_TYPE))
        token = NativeParserAuthenticator.sign(job_id, intake["tenant_id"], generation)
        before()
        if (native_intake_parser_policy(intake["tenant_id"]).sha256 != policy.sha256
                or NativeParserAuthenticator.sign(job_id, intake["tenant_id"], generation) != token
                or self.clock._clock() >= classified.valid_until):
            raise NativeParserConflict("NATIVE_PARSER_PREREQUISITES_CHANGED")
        before.check_in_memory()
        return NativeParserAdmissionContext(intake, original, policy, artifact, principal, source, classified,
            run_id, job_id, token)
