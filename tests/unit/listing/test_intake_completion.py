from copy import deepcopy
from uuid import uuid4

import pytest

from apps.api.app.routes.listings import AssistedIntakeStore
from modules.listing.application.intake_completion import (
    CompletionRequirement,
    IntakeWorkIncomplete,
    verify_completion,
)
from modules.listing.application.intake_workflow_bootstrap import IntakeWorkflowUnavailable


def _requirement(**changes):
    return CompletionRequirement.parse(
        {
            "kind": "decision",
            "allowed_states": ["EXECUTED"],
            "minimum_count": 1,
            "require_audit": True,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "caller_flag"},
        {"allowed_states": ["PENDING_REVIEW"]},
        {"allowed_states": []},
        {"minimum_count": 0},
        {"minimum_count": True},
        {"require_audit": None},
        {"require_audit": "yes"},
        {"action": ""},
    ],
)
def test_policy_cannot_omit_or_accept_unfinished_work_requirements(change):
    with pytest.raises(IntakeWorkflowUnavailable):
        _requirement(**change)


@pytest.fixture
def records():
    store = AssistedIntakeStore()
    tenant, iid, did, aid = [str(uuid4()) for _ in range(4)]
    intake = {"intake_id": iid, "scope": {"tenant_id": tenant}, "state": "READY", "version": 4}
    decision = {
        "decision_id": did,
        "intake_id": iid,
        "tenant_id": tenant,
        "status": "EXECUTED",
        "version": 2,
        "audit_event_id": aid,
    }
    audit = {
        "audit_event_id": aid,
        "tenant_id": tenant,
        "intake_id": iid,
        "resource_type": "decision",
        "resource_id": did,
        "result": "SUCCEEDED",
        "after_value": deepcopy(decision),
    }
    store.intakes[iid] = intake
    store.decisions[did] = decision
    store.workflow_audits[aid] = audit
    return store, intake, decision, audit


def test_evidence_records_are_bound_and_versioned(records):
    store, intake, decision, _ = records
    proof = verify_completion(store, intake, (_requirement(),))
    assert proof == [
        {
            "resource_type": "decision",
            "resource_id": decision["decision_id"],
            "version": 2,
            "state": "EXECUTED",
            "audit_event_id": decision["audit_event_id"],
        }
    ]


@pytest.mark.parametrize(
    "mode",
    [
        "no_row",
        "pending",
        "foreign_tenant",
        "foreign_scope",
        "fake_uuid",
        "stale_version",
        "bad_resource",
        "foreign_audit",
        "wrong_kind",
        "failed_audit",
        "hidden_pending",
    ],
)
def test_incomplete_or_unbound_work_cannot_be_completed(records, mode):
    store, intake, decision, audit = records
    if mode == "no_row":
        store.decisions.clear()
    elif mode == "pending":
        decision["status"] = "PENDING_REVIEW"
    elif mode == "foreign_tenant":
        decision["tenant_id"] = str(uuid4())
    elif mode == "foreign_scope":
        decision["scope"] = {**intake["scope"], "brand_id": str(uuid4())}
    elif mode == "fake_uuid":
        decision["decision_id"] = "synthetic-not-an-id"
    elif mode == "stale_version":
        audit["after_value"]["version"] = 1
    elif mode == "bad_resource":
        audit["resource_id"] = str(uuid4())
    elif mode == "foreign_audit":
        audit["tenant_id"] = str(uuid4())
    elif mode == "wrong_kind":
        audit["resource_type"] = "sla_instance"
    elif mode == "failed_audit":
        audit["result"] = "FAILED"
    elif mode == "hidden_pending":
        store.corrections[str(uuid4())] = {
            "intake_id": intake["intake_id"],
            "status": "PENDING_REVIEW",
        }
    with pytest.raises(IntakeWorkIncomplete):
        verify_completion(store, intake, (_requirement(),))
