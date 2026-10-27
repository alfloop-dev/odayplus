"""Cross-check the canonical outbox writer against the native historical reader.

The engine captures the real writer's INSERT; these tests do not execute SQL.
"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from modules.listing.application.native_intake_assisted_entry_history import (
    NativeAssistedEntryHistory,
)
from modules.listing.application.native_intake_assisted_entry_policy import JOB_TYPE
from shared.domain.events import DomainEvent
from shared.infrastructure.persistence.identity_graph_sql import CanonicalIdentityGraphRepository
from shared.jobs.queue import JobFenceRejectedError


class CapturedOutbox:
    def __init__(self):
        self.engine, self.queue, self.row = self, None, None

    def execute(self, sql, params):
        assert sql.startswith("INSERT INTO workflow.outbox_events (")
        columns = sql.split("(", 1)[1].split(")", 1)[0].split(",")
        self.row = dict(zip(columns, params, strict=True))

    def _one(self, sql, params):
        assert sql == "SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=?"
        assert params == (self.row["tenant_id"], self.row["event_id"])
        return deepcopy(self.row)


@pytest.fixture(params=["state", "snapshot"])
def canonical_event(request):
    tenant, intake, command, snapshot, transition = [str(uuid4()) for _ in range(5)]
    at = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
    state = request.param == "state"
    data = ({"intake_id": intake, "from_state": "AWAITING_ASSISTED_ENTRY", "to_state": "PARSING",
             "transition_id": transition, "version": 2, "occurred_at": at.isoformat()} if state else
            {"source_snapshot_id": snapshot, "intake_id": intake, "source_id": "synthetic-source",
             "content_sha256": "a" * 64, "captured_at": at.isoformat(), "retention_class": "SOURCE_RAW",
             "residency_mode": "TW_ONLY", "object_generation": 7})
    actor = str(uuid4()) if state else JOB_TYPE
    event = DomainEvent(event_type="intake.state_changed" if state else "snapshot.manual_entry_created",
        producer="listing_intake_service" if state else "assisted_entry_service", tenant_id=tenant,
        aggregate_type="intake" if state else "source_snapshot", aggregate_id=intake if state else snapshot,
        aggregate_version=2 if state else 1, partition_key=f"{tenant}:{intake}", correlation_id=str(uuid4()),
        causation_id=command, actor_ref=actor, occurred_at=at,
        schema_ref="#/payloads/IntakeStateChangedV1" if state else "#/payloads/SnapshotCreatedV1",
        sensitive_fields=[] if state else ["payload.object_uri"], payload=data)
    graph = CapturedOutbox()
    CanonicalIdentityGraphRepository._outbox(graph, event)
    job = {"tenant_id": tenant, "correlation_id": event.correlation_id,
           "payload": {"intake_id": intake, "request_id": command}}

    def verify():
        NativeAssistedEntryHistory(graph)._event(job, event.event_id, at, lambda: None,
            event_type=event.event_type, producer=event.producer, aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id, version=event.aggregate_version, actor=actor,
            schema=event.schema_ref.rsplit("/", 1)[1], data=data, sensitive=event.sensitive_fields)

    return graph, at, verify


def test_actual_canonical_writer_row_satisfies_historical_transport_proof(canonical_event):
    graph, at, verify = canonical_event
    contract = yaml.safe_load((Path(__file__).resolve().parents[2] /
        "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml").read_text())["transport"]
    assert datetime.fromisoformat(graph.row["retention_until"]) == at + timedelta(days=contract["outbox_retention_days"])
    assert contract["outbox_retention_days"] != contract["published_event_retention_days"]
    verify()


@pytest.mark.parametrize("days", [14, 29, 31])
def test_historical_transport_proof_rejects_published_or_changed_retention(canonical_event, days):
    graph, at, verify = canonical_event
    graph.row["retention_until"] = (at + timedelta(days=days)).isoformat()
    with pytest.raises(JobFenceRejectedError, match="NATIVE_ASSISTED_TERMINAL_PROOF_REJECTED"):
        verify()
