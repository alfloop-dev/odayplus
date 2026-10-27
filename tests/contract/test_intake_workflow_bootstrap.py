from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from apps.api.app.routes.listings import AssistedIntakeStore, create_assisted_intake_router
from tests.intake_workflow_fixture import SyntheticIntakeWorkflowPolicy

TENANT = "00000000-0000-0000-0000-000000000001"
ACTOR = "00000000-0000-0000-0000-000000000101"
HEADERS = {"x-subject-id": ACTOR, "x-tenant-id": TENANT, "x-roles": "site_reviewer,expansion_user"}


def _client(policy):
    store = AssistedIntakeStore()
    app = FastAPI()
    app.include_router(create_assisted_intake_router(store, workflow_policy=policy), prefix="/api/v1")
    return TestClient(app), store


def _submit(client, *, key=None):
    return client.post("/api/v1/intakes/url", json={"original_url": "https://example.invalid/synthetic", "scope": {"tenant_id": TENANT}},
                       headers={**HEADERS, "Idempotency-Key": key or f"synthetic-submit-{uuid4()}"})


def _assert_empty(store):
    assert all(not getattr(store, x) for x in ("intakes", "jobs", "assignments", "slas", "replays", "workflow_audits", "workflow_outbox"))


def test_new_intake_has_one_unassigned_task_sla_and_bound_evidence():
    client, store = _client(SyntheticIntakeWorkflowPolicy())
    response = _submit(client)
    assert response.status_code == 202, response.text
    intake_id = response.json()["intake_id"]
    detail = client.get(f"/api/v1/intakes/{intake_id}", headers=HEADERS)
    assert detail.status_code == 200, detail.text
    assert detail.json()["assignment_status"] == "UNASSIGNED"
    assert detail.json()["sla_state"] == "ON_TRACK"
    assignment = store.assignments[detail.json()["assignment_id"]]
    sla = store.slas[detail.json()["sla_instance_id"]]
    assert assignment["intake_id"] == sla["intake_id"] == intake_id
    assert assignment["owner_subject_id"] is None
    assert assignment["due_at"] == sla["due_at"] == detail.json()["due_at"]
    assert sla["assignment_id"] == assignment["assignment_id"]
    assert sla["policy_version"] == "synthetic-policy-v1"
    assert sla["calendar_version"] == "synthetic-calendar-v1"
    assert len(store.workflow_audits) == 3
    assert len(store.workflow_outbox) == 2
    assert {a["audit_event_id"] for a in detail.json()["audit"]} == set(store.workflow_audits)
    assert all(a["actor_subject_id"] == ACTOR and a["intake_id"] == intake_id for a in store.workflow_audits.values())
    assert detail.json()["policy_state"] == "POLICY_UNKNOWN"


@pytest.mark.parametrize("mode", ["missing", "provider_error", "foreign_tenant", "wrong_start", "scope_mutation"])
def test_bad_policy_cannot_leave_partial_intake_job_or_success_receipt(mode):
    class BadPolicy(SyntheticIntakeWorkflowPolicy):
        def resolve_initial(self, scope, submitted_at):
            if mode == "provider_error":
                raise RuntimeError("synthetic registry unavailable")
            plan = super().resolve_initial(scope, submitted_at)
            if mode == "foreign_tenant":
                return replace(plan, schedule=replace(plan.schedule, tenant_id=str(uuid4())))
            if mode == "wrong_start":
                return replace(plan, schedule=replace(plan.schedule, started_at=submitted_at - timedelta(hours=1)))
            if mode == "scope_mutation":
                scope["brand_id"] = str(uuid4())
            return plan
    client, store = _client(None if mode == "missing" else BadPolicy())
    response = _submit(client)
    assert response.status_code == 503
    _assert_empty(store)


def test_csv_later_row_failure_rolls_back_every_accepted_row_and_can_retry():
    class FailingSecondRow(SyntheticIntakeWorkflowPolicy):
        count = 0
        fail = True

        def resolve_initial(self, scope, submitted_at):
            self.count += 1
            if self.fail and self.count == 2:
                raise RuntimeError("synthetic second row failure")
            return super().resolve_initial(scope, submitted_at)
    policy = FailingSecondRow()
    client, store = _client(policy)
    body = {"batch_id": str(uuid4()), "method": "CSV", "scope": {"tenant_id": TENANT},
            "rows": [{"address_raw": "Synthetic address one"}, {"address_raw": "Synthetic address two"}]}
    headers = {**HEADERS, "Idempotency-Key": f"synthetic-batch-{uuid4()}"}
    failed = client.post("/api/v1/intake-batches", json=body, headers=headers)
    assert failed.status_code == 503, failed.text
    _assert_empty(store)
    policy.fail = False
    retried = client.post("/api/v1/intake-batches", json=body, headers=headers)
    assert retried.status_code == 202, retried.text
    assert len(store.intakes) == len(store.assignments) == len(store.slas) == 2
    for row in retried.json()["rows"]:
        detail = client.get(f"/api/v1/intakes/{row['intake_id']}", headers=HEADERS)
        assert detail.status_code == 200
        assert detail.json()["fields"][0]["effective"] in {"Synthetic address one", "Synthetic address two"}


def test_submission_replay_does_not_reinitialize_owner_clock_or_evidence():
    client, store = _client(SyntheticIntakeWorkflowPolicy())
    key = f"synthetic-idempotent-{uuid4()}"
    first = _submit(client, key=key)
    before = (set(store.intakes), set(store.assignments), set(store.slas), set(store.workflow_audits), set(store.workflow_outbox))
    second = _submit(client, key=key)
    assert first.status_code == 202 and second.status_code == 200
    assert first.json() == second.json()
    assert before == (set(store.intakes), set(store.assignments), set(store.slas), set(store.workflow_audits), set(store.workflow_outbox))


def test_assignment_uses_initial_id_and_policy_schedule_not_unchecked_body_deadline():
    client, store = _client(SyntheticIntakeWorkflowPolicy())
    submitted = _submit(client).json()
    intake_id = submitted["intake_id"]
    initial = client.get(f"/api/v1/intakes/{intake_id}", headers=HEADERS).json()
    response = client.put(f"/api/v1/intakes/{intake_id}/assignment",
                          json={"owner_subject_id": ACTOR, "owner_role": "expansion_user", "due_at": "2099-01-01T00:00:00Z", "reason": "Synthetic policy controlled assignment"},
                          headers={**HEADERS, "Idempotency-Key": f"synthetic-assign-{uuid4()}", "If-Match": 'W/"1"'})
    assert response.status_code == 200, response.text
    assert response.json()["assignment_id"] == initial["assignment_id"]
    assert response.json()["due_at"] == initial["due_at"]
    assert len(store.assignments) == len(store.slas) == 1
    assert store.slas[initial["sla_instance_id"]]["due_at"] == response.json()["due_at"]
    assert response.json()["audit_event_id"] in store.workflow_audits
    assert len(store.workflow_audits) == 6
    # Initial assignment changes both its task and the parent Intake. Each
    # resource retains its own audit, just as the other assignment commands do.
    assigned = [row for row in store.workflow_audits.values()
                if row["action"] == "assignment.assign"]
    assert {row["resource_type"] for row in assigned} == {"assignment", "intake"}
    assert len(assigned) == 2


def test_bootstrap_outbox_envelopes_and_payloads_match_reviewed_catalog():
    client, store = _client(SyntheticIntakeWorkflowPolicy())
    assert _submit(client).status_code == 202
    root = Path(__file__).resolve().parents[2]
    base = yaml.safe_load((root / "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1.yaml").read_text())
    payloads = yaml.safe_load((root / "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENT_PAYLOAD_SCHEMAS_V1.yaml").read_text())
    addendum = yaml.safe_load((root / "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1_1_ADDENDUM.yaml").read_text())
    for event in store.workflow_outbox.values():
        Draft202012Validator(base["envelope"], format_checker=FormatChecker()).validate(event)
        if event["event_type"] == "intake.submitted":
            schema = {**payloads["payloads"]["IntakeSubmittedV1"], "definitions": payloads["definitions"]}
        else:
            schema = addendum["payloads"]["SlaStateChangedV1"]
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(event["payload"])


def test_xlsx_failed_flush_does_not_cache_ids_for_rolled_back_rows(monkeypatch):
    client, store = _client(SyntheticIntakeWorkflowPolicy())
    body = {"batch_id": f"synthetic-xlsx-{uuid4()}", "scope": {"tenant_id": TENANT},
            "rows": [{"address_raw": "台北市信義區測試路1號", "rent_amount": 18000, "area_ping": 12}]}
    headers = {**HEADERS, "Idempotency-Key": f"synthetic-xlsx-key-{uuid4()}"}

    def crash_flush():
        raise RuntimeError("synthetic flush failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "flush", crash_flush)
        with pytest.raises(RuntimeError, match="synthetic flush"):
            client.post("/api/v1/intake-batches/xlsx/commit", json=body, headers=headers)
    _assert_empty(store)
    retried = client.post("/api/v1/intake-batches/xlsx/commit", json=body, headers=headers)
    assert retried.status_code == 202, retried.text
    assert retried.json()["accepted_count"] == 1
    for intake_id in retried.json()["intake_ids"]:
        assert client.get(f"/api/v1/intakes/{intake_id}", headers=HEADERS).status_code == 200
    assert len(store.intakes) == len(store.assignments) == len(store.slas) == 1


def test_policy_denied_target_does_not_change_initial_owner_or_sla():
    class DeniedTarget(SyntheticIntakeWorkflowPolicy):
        def resolve_assignment(self, *args, **kwargs):
            raise PermissionError("synthetic target not in authorized scope")
    client, store = _client(DeniedTarget())
    intake_id = _submit(client).json()["intake_id"]
    initial = client.get(f"/api/v1/intakes/{intake_id}", headers=HEADERS).json()
    response = client.put(f"/api/v1/intakes/{intake_id}/assignment",
                          json={"owner_subject_id": str(uuid4()), "owner_role": "expansion_user", "due_at": "2099-01-01T00:00:00Z", "reason": "Synthetic denied owner test"},
                          headers={**HEADERS, "Idempotency-Key": f"synthetic-denied-{uuid4()}", "If-Match": 'W/"1"'})
    assert response.status_code == 403
    assert store.assignments[initial["assignment_id"]]["status"] == "UNASSIGNED"
    assert store.slas[initial["sla_instance_id"]]["version"] == 1
    assert store.intakes[intake_id]["version"] == 1
    assert len(store.workflow_audits) == 3 and len(store.workflow_outbox) == 2


def test_concurrent_memory_replays_return_one_intake_and_one_workflow():
    from concurrent.futures import ThreadPoolExecutor

    client, store = _client(SyntheticIntakeWorkflowPolicy())
    # Resolve FastAPI's lazy route table before the actual concurrency probe.
    assert client.get("/api/v1/intakes", headers=HEADERS).status_code == 200
    key = f"synthetic-concurrent-{uuid4()}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: _submit(client, key=key), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 202]
    assert responses[0].json() == responses[1].json()
    assert len(store.intakes) == len(store.assignments) == len(store.slas) == len(store.jobs) == 1
    assert len(store.workflow_audits) == 3 and len(store.workflow_outbox) == 2
