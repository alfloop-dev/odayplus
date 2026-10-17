"""Read the exact durable human request without granting current authority.

The request journal contains only identifiers/hashes. Human business values
are reconstructed from their declared PROPOSED correction columns, never from
outbox/audit payloads or a service-supplied human role.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from modules.listing.application.native_intake_assisted_entry_input import (
    IDENTITY_FIELDS,
    NativeAssistedEntryCommand,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_source import (
    NativeAssistedEntrySourceProof,
    NativeAssistedSourceParent,
)
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_retrieval import audited_value_matches
from modules.listing.application.native_intake_retrieval_policy import canonical_sha256
from shared.domain.events import validate_event

REQUEST_EVENT = "intake.assisted_entry_requested"
REQUEST_AGGREGATE = "intake_assisted_entry"
REQUEST_SCHEMA = "#/payloads/IntakeAssistedEntryRequestedV1"
REQUEST_ACTION = "intake.assisted_entry_requested.v1"
_HUMAN_ROLES = frozenset({"EXPANSION_STAFF", "EXPANSION_MANAGER", "DATA_STEWARD"})


def request_audit_id(request_id: str) -> str:
    return str(uuid5(UUID(native_intake_uuid(request_id)), "native-assisted-entry-request-audit-v1"))


def _reject():
    raise NativeAssistedEntryConflict("NATIVE_ASSISTED_HUMAN_REQUEST_PROOF_REJECTED") from None


def _object(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        _reject()
    if not isinstance(result, dict):
        _reject()
    return result


def _time(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if result.utcoffset() is None:
            _reject()
        return result
    except (ValueError, TypeError, AttributeError):
        _reject()


def request_receipt(event: dict) -> dict:
    data = event["payload"]
    return {"request_id": event["event_id"], "intake_id": data["intake_id"], "status": "REQUESTED",
        "version": data["expected_intake_version"], "requested_at": data["requested_at"],
        "correlation_id": event["correlation_id"], "correction_ids": data["correction_ids"],
        "audit_event_id": request_audit_id(event["event_id"]), "outbox_event_id": event["event_id"]}


@dataclass(frozen=True)
class NativeAssistedHumanRequest:
    event: dict
    event_sha256: str
    receipt: dict
    command: NativeAssistedEntryCommand = field(repr=False)
    rows: tuple[dict, ...] = field(repr=False)


class NativeAssistedEntryRequestProof:
    def __init__(self, graph):
        self.graph, self.engine = graph, graph.engine
        self.source = NativeAssistedEntrySourceProof(graph)

    def envelope(self, tenant: str, intake_id: str, request_id: str, *, before) -> tuple[dict, dict]:
        event, audit, _ = self._envelope_with_parent(tenant, intake_id, request_id, before=before)
        return event, audit

    def _envelope_with_parent(self, tenant: str, intake_id: str, request_id: str, *, before):
        """Authenticate origin even if its old actor/source/config was revoked.

        The caller must separately authorize its current action against the
        actual locked Intake. Withdrawal never borrows this old actor's role.
        """
        for value in (tenant, intake_id, request_id):
            native_intake_uuid(value)
        before()
        if self.engine.query_one("SHOW transaction_isolation")["transaction_isolation"] != "serializable":
            _reject()
        before()
        if self.engine.query_one("SELECT current_setting('app.tenant_id',true) AS tenant")["tenant"] != tenant:
            _reject()
        before()
        row = self.graph._one("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=? "
            "AND aggregate_type='intake_assisted_entry' AND payload->'payload'->>'intake_id'=?",
            (tenant, request_id, intake_id))
        event = _object(row["payload"])
        data = _object(event.get("payload"))
        if (validate_event(event) or row["event_type"] != REQUEST_EVENT or event.get("event_type") != REQUEST_EVENT
                or row["event_id"] != request_id or event.get("event_id") != request_id
                or row["aggregate_id"] != request_id or event.get("aggregate_id") != request_id
                or event.get("aggregate_type") != REQUEST_AGGREGATE or row["aggregate_type"] != REQUEST_AGGREGATE
                or type(row["event_version"]) is not int or row["event_version"] != 1
                or type(event.get("event_version")) is not int or event["event_version"] != 1
                or type(row["aggregate_version"]) is not int or row["aggregate_version"] != 1
                or type(event.get("aggregate_version")) is not int or event["aggregate_version"] != 1
                or event.get("tenant_id") != tenant or event.get("producer") != "listing_intake_service"
                or event.get("schema_ref") != REQUEST_SCHEMA or data.get("request_id") != request_id
                or data.get("intake_id") != intake_id or data.get("requested_by") != event.get("actor_ref")
                or event.get("causation_id") != data.get("source_policy_event_id")
                or row["partition_key"] != tenant + ":" + intake_id or event.get("partition_key") != row["partition_key"]
                or event.get("correlation_id") != row["correlation_id"]
                or _time(event.get("occurred_at")) != _time(row["occurred_at"])
                or _time(data.get("requested_at")) != _time(row["occurred_at"])
                or _time(row["retention_until"]) != _time(row["occurred_at"]) + timedelta(days=30)):
            _reject()
        start, timeout = _time(data["requested_at"]), _time(data["timeout_at"])
        ids = data["correction_ids"]
        if (not 0 < (timeout - start).total_seconds() <= 120 or not isinstance(ids, list)
                or not 3 <= len(ids) <= 7 or len(ids) != len(set(ids))):
            _reject()
        for value in [data["requested_by"], *ids]:
            native_intake_uuid(value)
        before()
        audit = self.engine.query_one("SELECT after_value,reason,actor_role FROM audit.audit_events "
            "WHERE tenant_id=? AND audit_event_id=? AND resource_type='intake' AND resource_id=? "
            "AND action='intake.assisted_entry_requested.v1' AND result='SUCCEEDED' "
            "AND actor_subject_id=? AND service_principal IS NULL AND correlation_id=? AND causation_id=?",
            (tenant, request_audit_id(request_id), intake_id, data["requested_by"], row["correlation_id"],
             data["source_policy_event_id"]))
        expected = {**request_receipt(event), "native_assisted_request_provenance": data}
        if (audit is None or audit["actor_role"] not in _HUMAN_ROLES
                or not audited_value_matches(_object(audit["after_value"]), expected)):
            _reject()
        parent = self.source.parent(tenant, data["source_policy_event_id"], before)
        if (parent.intake_id != intake_id or parent.intake_version != data["expected_intake_version"]
                or parent.correlation_id != row["correlation_id"] or parent.input_sha256 != data["input_sha256"]
                or parent.event_sha256 != data["source_policy_event_sha256"]
                or parent.root_identity_event_id != data["root_identity_event_id"]):
            _reject()
        before()
        return event, audit, parent

    def read(self, tenant: str, intake_id: str, request_id: str, *, before) -> NativeAssistedHumanRequest:
        request, _ = self.read_with_parent(tenant, intake_id, request_id, before=before)
        return request

    def read_with_parent(self, tenant: str, intake_id: str, request_id: str, *, before
                         ) -> tuple[NativeAssistedHumanRequest, NativeAssistedSourceParent]:
        """Read only the original, unlinked PROPOSED set before positive work.

        Successful replay after snapshot linkage belongs to the full owner's
        terminal verifier; this initial-input reader must not adopt it.
        """
        event, audit, parent = self._envelope_with_parent(tenant, intake_id, request_id, before=before)
        data = event["payload"]
        before()
        rows = self.engine.query("SELECT correction_id,tenant_id,intake_id,listing_id,field_path,field_classification,"
            "parsed_value,normalized_value,before_effective_value,corrected_value::text AS corrected_json,"
            "after_effective_value::text AS after_json,reason,proposed_by,reviewed_by,status,identity_affecting,"
            "source_snapshot_id,parser_run_id,supersedes_correction_id,reversal_of_correction_id,version,created_at,reviewed_at "
            "FROM intake.human_corrections WHERE tenant_id=? AND intake_id=? AND correction_id=ANY(?::uuid[]) "
            "ORDER BY correction_id FOR SHARE", (tenant, intake_id, data["correction_ids"]))
        if (len(rows) != len(data["correction_ids"])
                or {r["correction_id"] for r in rows} != set(data["correction_ids"])
                or len({r["field_path"] for r in rows}) != len(rows)):
            _reject()
        fields = {}
        for row in rows:
            if (row["tenant_id"] != tenant or row["intake_id"] != intake_id
                    or row["proposed_by"] != data["requested_by"] or row["field_classification"] != "INTERNAL"
                    or row["status"] != "PROPOSED" or type(row["version"]) is not int or row["version"] != 1
                    or row["identity_affecting"] is not (row["field_path"] in IDENTITY_FIELDS)
                    or _time(row["created_at"]) != _time(data["requested_at"])
                    or any(row[k] is not None for k in ("listing_id", "parsed_value", "normalized_value",
                        "before_effective_value", "reviewed_by", "source_snapshot_id", "parser_run_id",
                        "supersedes_correction_id", "reversal_of_correction_id", "reviewed_at"))):
                _reject()
            try:
                value, after = json.loads(row["corrected_json"]), json.loads(row["after_json"])
            except (ValueError, TypeError):
                _reject()
            if value != after:
                _reject()
            fields[row["field_path"]] = value
        reasons = {r["reason"] for r in rows}
        if len(reasons) != 1:
            _reject()
        parts = next(iter(reasons)).split("\n")
        if (len(parts) != 4 or parts[0] != "Native assisted-entry proposal v1"
                or not parts[1].startswith("Reason: ") or parts[2] != "Risk acknowledged: yes"
                or not parts[3].startswith("Risk summary: ")):
            _reject()
        try:
            command = NativeAssistedEntryCommand.model_validate({"fields": fields, "reason": parts[1][8:],
                "risk_summary": parts[3][14:], "risk_acknowledged": True})
        except ValueError:
            _reject()
        if (command.sha256 != data["command_sha256"] or command.fields.sha256 != data["fields_sha256"]
                or command.correction_reason() != next(iter(reasons)) or command.reason != audit["reason"]):
            _reject()
        before()
        request = NativeAssistedHumanRequest(event, canonical_sha256(event), request_receipt(event), command, tuple(rows))
        # This parent is the actual result just authenticated above in this
        # same call/root. It grants no current human/source/lease authority.
        return request, parent
