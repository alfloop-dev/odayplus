"""Synthetic HTTP commands: preserve lineage and commit attributable evidence."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import AssistedIntakeStore, create_assisted_intake_router
from modules.listing.application.intake_completion import (
    CompletionRequirement,
    IntakeWorkIncomplete,
    verify_completion,
)
from shared.audit.integrity import sha256_hex
from tests.intake_workflow_fixture import SyntheticIntakeWorkflowPolicy

TENANT = "00000000-0000-0000-0000-000000000001"
ACTOR = "00000000-0000-0000-0000-000000000101"
REVIEWER = "00000000-0000-0000-0000-000000000102"
HEADERS = {"x-subject-id": ACTOR, "x-tenant-id": TENANT, "x-roles": "site_reviewer,expansion_user"}
COLLECTIONS = (
    "intakes",
    "corrections",
    "decisions",
    "replays",
    "workflow_audits",
    "workflow_outbox",
)


@pytest.fixture
def case():
    store = AssistedIntakeStore()
    app = FastAPI()
    app.include_router(
        create_assisted_intake_router(store, workflow_policy=SyntheticIntakeWorkflowPolicy()),
        prefix="/api/v1",
    )
    client = TestClient(app)
    submitted = client.post(
        "/api/v1/intakes/url",
        json={"original_url": "https://example.invalid/correction", "scope": {"tenant_id": TENANT}},
        headers={**HEADERS, "Idempotency-Key": f"synthetic-submit-{uuid4()}"},
    )
    assert submitted.status_code == 202, submitted.text
    intake = store.intakes[submitted.json()["intake_id"]]
    # Controlled parser precondition, not a claim of parser or real UAT coverage.
    intake.update(state="NEEDS_REVIEW", source_snapshot_id=str(uuid4()), parser_run_id=str(uuid4()))
    intake["fields"] = [
        dict(
            field_path=path,
            classification="CONFIDENTIAL",
            masked=False,
            parsed=10,
            normalized=11,
            corrected=None,
            effective=11,
            confidence=0.91,
            source_locator={"line": 3},
        )
        for path in ("floor", "rent_amount")
    ]
    return client, store, intake["intake_id"]


def _snapshot(store):
    return deepcopy({name: getattr(store, name) for name in COLLECTIONS})


def _correct(case, *, path="floor", key=None, **changes):
    client, store, intake_id = case
    return client.post(
        f"/api/v1/intakes/{intake_id}/corrections",
        json={
            "field_path": path,
            "corrected_value": 12,
            "reason": "Synthetic document verified",
            "risk_acknowledged": True,
            **changes,
        },
        headers={
            **HEADERS,
            "Idempotency-Key": key or f"synthetic-correct-{uuid4()}",
            "If-Match": f'W/"{store.intakes[intake_id]["version"]}"',
        },
    )


def _review(case, correction_id, *, decision="APPROVE", actor=REVIEWER, key=None):
    client, _, _ = case
    return client.post(
        f"/api/v1/identity-decisions/{correction_id}/actions/review",
        json={
            "decision": decision,
            "reason": "Synthetic independent document check",
            "risk_acknowledged": True,
        },
        headers={
            **HEADERS,
            "x-subject-id": actor,
            "Idempotency-Key": key or f"synthetic-review-{uuid4()}",
            "If-Match": 'W/"1"',
        },
    )


def test_ordinary_correction_preserves_source_and_returns_a_stored_audit(case):
    client, store, intake_id = case
    before = deepcopy(store.intakes[intake_id]["fields"][0])
    key = f"synthetic-correct-{uuid4()}"
    response = _correct(case, key=key, expected_effective_value_sha256=sha256_hex(11))
    assert response.status_code == 201, response.text
    receipt = response.json()
    row = store.corrections[receipt["correction_id"]]
    detail = client.get(f"/api/v1/intakes/{intake_id}", headers=HEADERS)
    assert detail.status_code == 200
    field = detail.json()["fields"][0]
    assert (
        field["parsed"] == 10
        and field["normalized"] == 11
        and field["corrected"] == field["effective"] == 12
    )
    assert field["classification"] == "CONFIDENTIAL" and field["confidence"] == 0.91
    assert (
        row["field_before"] == before
        and row["before_effective_value"] == 11
        and row["after_effective_value"] == 12
    )
    assert row["parsed_value"] == 10 and row["normalized_value"] == 11
    assert row["proposed_by"] == ACTOR and row["version"] == receipt["version"] == 1
    assert row["source_snapshot_id"] == store.intakes[intake_id]["source_snapshot_id"]
    audit = store.workflow_audits[receipt["audit_event_id"]]
    assert audit["resource_id"] == receipt["correction_id"] and audit["actor_subject_id"] == ACTOR
    assert audit["after_value"]["version"] == 1 and audit["worm_state"] == "worm_pending"
    proofs = verify_completion(
        store,
        store.intakes[intake_id],
        [
            CompletionRequirement(
                kind="correction",
                action=None,
                allowed_states=frozenset({"APPLIED"}),
                minimum_count=1,
                require_audit=True,
            )
        ],
    )
    assert proofs[0]["resource_id"] == receipt["correction_id"]
    snapshot = _snapshot(store)
    replay = _correct(case, key=key, expected_effective_value_sha256=sha256_hex(11))
    assert replay.json() == receipt and replay.headers["Idempotency-Replayed"] == "true"
    assert _snapshot(store) == snapshot


def test_identity_proposal_and_independent_review_have_separate_committed_evidence(case):
    _, store, intake_id = case
    before_field = deepcopy(store.intakes[intake_id]["fields"][1])
    response = _correct(case, path="rent_amount")
    assert response.status_code == 201, response.text
    cid = response.json()["correction_id"]
    proposal_audit = response.json()["audit_event_id"]
    assert store.intakes[intake_id]["fields"][1] == before_field
    assert store.corrections[cid]["after_effective_value"] == 11
    before = _snapshot(store)
    assert _review(case, cid, actor=ACTOR).status_code == 403
    assert _snapshot(store) == before
    key = f"synthetic-review-{uuid4()}"
    reviewed = _review(case, cid, key=key)
    assert reviewed.status_code == 200, reviewed.text
    correction, decision = store.corrections[cid], store.decisions[cid]
    assert correction["status"] == "APPLIED" and correction["version"] == 2
    assert correction["reviewed_by"] == REVIEWER and correction["proposed_by"] == ACTOR
    assert decision["status"] == "APPROVED" and decision["version"] == 2
    assert store.intakes[intake_id]["fields"][1]["classification"] == "CONFIDENTIAL"
    assert store.intakes[intake_id]["fields"][1]["parsed"] == 10
    assert store.workflow_audits[proposal_audit]["after_value"]["status"] == "PENDING_REVIEW"
    review_audit = store.workflow_audits[reviewed.json()["audit_event_id"]]
    assert (
        review_audit["actor_subject_id"] == REVIEWER
        and review_audit["before_value"]["status"] == "PENDING_REVIEW"
    )
    # Approval must not manufacture canonical execution/completion evidence.
    with pytest.raises(IntakeWorkIncomplete):
        verify_completion(
            store,
            store.intakes[intake_id],
            [
                CompletionRequirement(
                    kind="decision",
                    action="identity_correction",
                    allowed_states=frozenset({"EXECUTED"}),
                    minimum_count=1,
                    require_audit=True,
                )
            ],
        )
    snapshot = _snapshot(store)
    replay = _review(case, cid, key=key)
    assert replay.json() == reviewed.json() and _snapshot(store) == snapshot


@pytest.mark.parametrize("change", ["version", "source", "effective", "scope"])
def test_review_denies_changed_parent_or_lineage_without_writing(case, change):
    _, store, intake_id = case
    cid = _correct(case, path="rent_amount").json()["correction_id"]
    intake = store.intakes[intake_id]
    if change == "version":
        intake["version"] += 1
    elif change == "source":
        intake["source_snapshot_id"] = str(uuid4())
    elif change == "effective":
        intake["fields"][1]["effective"] = 99
    else:
        intake["scope"]["tenant_id"] = str(uuid4())
    before = _snapshot(store)
    response = _review(case, cid)
    assert response.status_code in {403, 409}, response.text
    assert _snapshot(store) == before


def test_stale_proposal_can_be_rejected_without_applying_fields(case):
    _, store, intake_id = case
    cid = _correct(case, path="rent_amount").json()["correction_id"]
    store.intakes[intake_id]["version"] += 1
    field = deepcopy(store.intakes[intake_id]["fields"])
    response = _review(case, cid, decision="REJECT")
    assert response.status_code == 200, response.text
    assert store.intakes[intake_id]["fields"] == field
    assert store.corrections[cid]["status"] == store.decisions[cid]["status"] == "REJECTED"


@pytest.mark.parametrize("mode", ["bad_hash", "restricted", "masked", "unknown"])
def test_rejected_corrections_leave_fields_audits_and_replays_unchanged(case, mode):
    _, store, intake_id = case
    changes = {}
    path = "floor"
    if mode == "bad_hash":
        changes["expected_effective_value_sha256"] = "0" * 64
    elif mode == "restricted":
        store.intakes[intake_id]["fields"][0]["classification"] = "RESTRICTED"
    elif mode == "masked":
        store.intakes[intake_id]["fields"][0]["masked"] = True
    else:
        path = "undeclared_private_data"
    before = _snapshot(store)
    response = _correct(case, path=path, **changes)
    assert response.status_code in {403, 409, 422}, response.text
    assert _snapshot(store) == before


@pytest.mark.parametrize("command", ["ordinary", "proposal", "review"])
def test_failed_commit_rolls_back_all_command_evidence_and_retry_is_fresh(
    case, monkeypatch, command
):
    _, store, _ = case
    cid = (
        _correct(case, path="rent_amount").json()["correction_id"] if command == "review" else None
    )
    key = f"synthetic-failed-commit-{uuid4()}"

    def execute():
        return (
            _review(case, cid, key=key)
            if cid
            else _correct(case, path="rent_amount" if command == "proposal" else "floor", key=key)
        )

    before = _snapshot(store)

    def crash():
        raise RuntimeError("synthetic commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "flush", crash)
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            execute()
    assert _snapshot(store) == before
    retried = execute()
    assert retried.status_code in {200, 201}, retried.text
    assert retried.headers["Idempotency-Replayed"] == "false"
