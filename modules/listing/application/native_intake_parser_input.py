"""Reconstruct immutable parser input, then separately check current authority.

Successful manual capture proves lineage only. Its old source, human and
signing settings are never reused as a grant for this parser command.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from modules.external_data.application.native_manual_json_parser import ManualParserLineage
from modules.listing.application.native_intake_assisted_entry_history import (
    NativeAssistedEntryHistory,
)
from modules.listing.application.native_intake_assisted_entry_identity import (
    assisted_delivery_identity,
)
from modules.listing.application.native_intake_assisted_entry_input import (
    NativeAssistedEntryCommand,
)
from modules.listing.application.native_intake_assisted_entry_request import human_transition_role
from modules.listing.application.native_intake_assisted_entry_request_proof import _object
from modules.listing.application.native_intake_assisted_entry_source import (
    NativeAssistedEntrySourceProof,
)
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_requester_authority import (
    current_native_assisted_principal,
)
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)


def _reject():
    raise NativeParserConflict("NATIVE_PARSER_MANUAL_INPUT_REJECTED") from None


@dataclass(frozen=True)
class NativeParserManualInput:
    parent_job_id: str
    parent_event_id: str
    parent_result_sha256: str
    snapshot_json: str
    lineage: ManualParserLineage
    command_json: str

    def snapshot(self):
        return json.loads(self.snapshot_json)

    def command(self):
        return NativeAssistedEntryCommand.model_validate_json(self.command_json)

    def binding(self):
        # Persist immutable bindings, never copy proposed values into logs or
        # mistake their presence for effective/applied Listing values.
        snapshot = self.snapshot()
        return {"parent_job_id": self.parent_job_id, "parent_event_id": self.parent_event_id,
            "parent_result_sha256": self.parent_result_sha256,
            "snapshot_sha256": canonical_sha256(snapshot), "lineage": asdict(self.lineage),
            "source_snapshot_id": snapshot["source_snapshot_id"], "command_sha256": self.command().sha256}

    @property
    def sha256(self):
        return canonical_sha256(self.binding())


class NativeParserManualInputReader:
    def __init__(self, graph):
        self.graph, self.engine, self.queue = graph, graph.engine, graph.queue
        self.history = NativeAssistedEntryHistory(graph)
        self.source = NativeAssistedEntrySourceProof(graph)

    def read(self, intake, *, before):
        """Use the caller's one bounded SERIALIZABLE tenant snapshot."""
        if (getattr(self.engine, "dialect", None) != "postgresql"
                or getattr(getattr(self.engine, "_local", None), "connection", None) is None):
            _reject()
        try:
            tenant, intake_id = native_intake_uuid(intake["tenant_id"]), native_intake_uuid(intake["intake_id"])
            if (intake["processing_state"] != "PARSING" or intake["intake_method"] != "URL"
                    or type(intake["version"]) is not int or intake["version"] < 2
                    or any(intake[name] is not None for name in ("deleted_at", "resolved_listing_id", "completed_at"))):
                _reject()
            before()
            root = self.engine.query_one("SELECT current_setting('transaction_isolation') AS isolation, "
                "current_setting('app.tenant_id',true) AS tenant")
            if root["isolation"] != "serializable" or root["tenant"] != tenant:
                _reject()
            before()
            rows = self.engine.query("SELECT job_id,source_snapshot_id FROM intake.intake_stage_transitions "
                "WHERE tenant_id=? AND intake_id=? AND resulting_version=? AND to_state='PARSING' "
                "AND from_state='AWAITING_ASSISTED_ENTRY' AND service_principal IS NULL "
                "AND actor_subject_id IS NOT NULL LIMIT 2", (tenant, intake_id, intake["version"]))
            if len(rows) != 1:
                _reject()
            before()
            job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=? FOR SHARE",
                (tenant, rows[0]["job_id"]))
            before()
            delivery = self.queue.get(job["job_id"])
            if delivery is None:
                _reject()
            actual_tenant, generation = assisted_delivery_identity(delivery)
            if actual_tenant != tenant:
                _reject()
            proven = self.history.committed_result_with_command(delivery, tenant, generation, before=before)
            if proven is None:
                _reject()
            result, command = proven
            payload = _object(job["payload"])
            if (result["intake_id"] != intake_id or result["version"] != intake["version"]
                    or result["source_snapshot_id"] != rows[0]["source_snapshot_id"]
                    or result["correlation_id"] != intake["correlation_id"]
                    or payload["input_sha256"] != self.source.source._input_digest(intake)):
                _reject()
            # Full historical success was proved above in this same snapshot.
            # Its typed command is the exact return of the immutable-proposal
            # verifier inside that full proof, never an old authority grant.
            snapshot = _object(result["native_snapshot_proof"]["snapshot_fields"])
            if (snapshot["source_id"] != intake["source_id"] or snapshot["tenant_id"] != tenant
                    or snapshot["intake_id"] != intake_id or snapshot["original_url"] != intake["original_url"]
                    or snapshot["canonical_url"] != intake["canonical_url"]):
                _reject()
            corrections = result["native_snapshot_proof"]["correction_bindings"]
            ids = tuple(sorted((row["field_path"], ident) for ident, row in corrections.items()))
            lineage = ManualParserLineage(tenant_id=tenant, intake_id=intake_id,
                request_id=payload["request_id"], requested_by=payload["requested_by"], source_id=intake["source_id"],
                content_sha256=snapshot["content_sha256"], fields_sha256=command.fields.sha256, correction_ids=ids)
            getattr(before, "check_in_memory", before)()
            return NativeParserManualInput(job["job_id"], result["outbox_event_id"], canonical_sha256(result),
                canonical_json(snapshot), lineage, command.model_dump_json())
        except NativeParserConflict:
            # A bounded-root expiry is not evidence of a malformed input.
            raise
        except (ValueError, KeyError, TypeError, AttributeError):
            _reject()

    def current_authority(self, intake, value: NativeParserManualInput, *, before):
        """Resolve actual original human and separately current manual approval."""
        if (not isinstance(value, NativeParserManualInput) or value.lineage.tenant_id != intake["tenant_id"]
                or value.lineage.intake_id != intake["intake_id"] or value.lineage.source_id != intake["source_id"]):
            _reject()
        command = value.command()
        principal = current_native_assisted_principal(self.engine, value.lineage.requested_by, intake,
            risk_summary=command.risk_summary, risk_acknowledged=command.risk_acknowledged, before=before)
        human_transition_role(principal)
        source, classified = self.source.current_manual_source(intake, before=before)
        getattr(before, "check_in_memory", before)()
        return principal, source, classified
