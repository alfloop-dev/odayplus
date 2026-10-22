"""PREPARED ONLY: real signed SQL native owner, never a browser/human release.

Runtime must be authorized through the adjacent guarded runner. The borrowed
native_scope_pg fixture creates and normally drops only a newly registered
local scratch database. Its original product/tests are not edited.
"""

import hashlib
import json
import os
import pickle
import re
from dataclasses import replace
from pathlib import Path

import pytest

from shared.infrastructure.persistence.repositories import DurableApprovalRepository
from tests.integration.test_sitescore_native_human_scope import (
    CHECKER,
    ORIGINAL,
    _business,
    _decision,
    _open,
    _signed_client,
)
from tests.integration.test_sitescore_native_human_scope import (
    native_scope_pg as borrowed_native_scope_pg,
)

# Explicitly expose the unchanged imported pytest fixture; no direct wrapper invocation.
native_scope_pg = borrowed_native_scope_pg
pytestmark = pytest.mark.requires_live_env


def _sha(value):
    return hashlib.sha256(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)).hexdigest()


def _record(label, value):
    directory = os.environ.get("ODP_NATIVE_VERSION_EVIDENCE_DIR")
    assert directory, "Only the authorized owned runner may execute this control"
    assert re.fullmatch(r"[a-z0-9_-]+", label)
    target = Path(directory) / (label + ".json")
    target.write_text(json.dumps(value, indent=2, default=str) + "\n")
    target.chmod(0o600)


def _document(bundle, harness, tenant, collection, doc_id):
    actual_collection = harness.scoped(tenant)._collection(collection)
    row = bundle.engine.query_one(
        "SELECT * FROM durable_documents WHERE collection=? AND doc_id=?",
        (actual_collection, doc_id),
    )
    assert row is not None
    return dict(row)


def _graph(bundle, harness, tenant, candidate, decision_id):
    decision = harness.decisions(tenant).get_decision(decision_id)
    retained = harness.decisions(tenant).get_report(decision_id)
    approval = DurableApprovalRepository(harness.scoped(tenant)).get(decision.approval_id)
    candidate_document = _document(bundle, harness, tenant, "listing.candidates", candidate)
    physical_candidate = pickle.loads(candidate_document["data"])
    snapshot = _business(bundle)
    owned_audit = [event.to_dict() for event in bundle.audit_log.list_events(tenant_id=tenant)
                   if event.resource == "sitescore/decision/" + decision_id]
    deliveries = []
    for event in owned_audit:
        delivery = harness.scoped(tenant).get("audit.durable_delivery", event["event_id"])
        deliveries.append({"auditEventId": event["event_id"], "actualRowPresent": delivery is not None,
                           "actualStatus": delivery.get("status") if delivery else None,
                           "actualSinkId": delivery.get("sink_id") if delivery else None,
                           "documentSHA256": _sha(delivery) if delivery else None})
    return {
        "candidateId": candidate,
        "physicalCandidateReviewId": physical_candidate.review_id,
        "physicalCandidateState": physical_candidate.candidate_site.site_status,
        "candidateDocumentSHA256": _sha(candidate_document),
        "decisionId": decision.decision_id,
        "decisionVersion": decision.version,
        "decisionVersionType": type(decision.version).__name__,
        "decisionStatus": decision.status.value,
        "decisionDocumentSHA256": _sha(_document(bundle, harness, tenant, "sitescore.decisions", decision_id)),
        "approvalId": approval.approval_id,
        "approvalDecisionId": approval.decision_id,
        "approvalStatus": approval.approval_status,
        "approvalActor": approval.approver_id,
        "approvalSHA256": _sha(approval),
        "retainedReportId": retained.report_id,
        "retainedReportVersion": retained.report_version,
        "retainedReportSHA256": _sha(retained),
        "retainedReportDocumentSHA256": _sha(_document(bundle, harness, tenant, "sitescore.decision_reports", decision_id)),
        "ownedBusinessAuditRows": owned_audit,
        "ownedAuditDeliveryRows": deliveries,
        "businessSHA256": _sha(snapshot),
        "businessTableRows": {name: len(rows) for name, rows in snapshot.items()},
        "businessTableSHA256": {name: _sha(rows) for name, rows in snapshot.items()},
    }


@pytest.mark.parametrize(
    "label,stored_version,caller_version,expected_status",
    [
        ("stored_bool_true_caller_int1", True, 1, 409),
        ("stored_float2_caller_int2", 2.0, 2, 409),
        ("stored_none_caller_int2", None, 2, 409),
        ("stored_string2_caller_int2", "2", 2, 409),
        ("clean_integer2_caller_int2", 2, 2, 200),
    ],
    ids=["bool-true", "float-two", "none", "string-two", "clean-int-two"],
)
def test_signed_native_persisted_version_requires_actual_positive_integer(
    native_scope_pg, monkeypatch, label, stored_version, caller_version, expected_status,
):
    """Every row uses its own fresh DB and original signed native initial submit.

    Corruption changes just this own Decision aggregate's version in actual
    durable SQL. A syntactically normal StrictInt caller cannot legitimize an
    invalid stored type. This is not a principal or authorization stub.
    """
    database, bundle, harness, tenant, candidate, saved = native_scope_pg
    assert bundle.engine.dialect == "postgresql"
    # Use the existing isolated operation premise through its real dual-approval HTTP helper.
    with _signed_client(native_scope_pg, monkeypatch, go_enabled=True) as (client, headers, _, _):
        submitted, _ = _open(client, headers, tenant, saved, key="stored-version-" + label)
        assert submitted.status_code == 201, submitted.text
        decision_id = submitted.json()["decision_id"]
        current = harness.decisions(tenant).get_decision(decision_id)
        assert type(current.version) is int and current.version == 2
        assert current.status.value == "PENDING_REVIEW"
        assert current.created_by == ORIGINAL and current.created_by_verified is True
        assert current.submitted_by == ORIGINAL and current.submission_actor_verified is True
        clean_graph = _graph(bundle, harness, tenant, candidate, decision_id)
        assert clean_graph["physicalCandidateReviewId"] == decision_id
        assert clean_graph["approvalStatus"] == "pending"
        assert clean_graph["approvalDecisionId"] == decision_id
        assert harness.decisions(tenant).get_report(decision_id).to_dict() == saved.to_dict()
        clean_snapshot = _business(bundle)
        clean_row = _document(bundle, harness, tenant, "sitescore.decisions", decision_id)
        corrupt = expected_status != 200
        if corrupt:
            harness.decisions(tenant).save_decision(replace(current, version=stored_version))
            actual = harness.decisions(tenant).get_decision(decision_id)
            assert type(actual.version) is type(stored_version) and actual.version == stored_version
            assert actual == replace(current, version=stored_version)
            corrupted_row = _document(bundle, harness, tenant, "sitescore.decisions", decision_id)
            assert corrupted_row["data"] != clean_row["data"]
            assert {k: v for k, v in corrupted_row.items() if k != "data"} == {
                k: v for k, v in clean_row.items() if k != "data"
            }
            corrupted_snapshot = _business(bundle)
            assert all(corrupted_snapshot[k] == clean_snapshot[k]
                       for k in clean_snapshot if k != "durable_documents")
            # Exactly one own physical aggregate changed, with no other
            # document mutation hidden by an object-level equality check.
            clean_documents = {(dict(r)["collection"], dict(r)["doc_id"]): r
                               for r in clean_snapshot["durable_documents"]}
            corrupt_documents = {(dict(r)["collection"], dict(r)["doc_id"]): r
                                 for r in corrupted_snapshot["durable_documents"]}
            assert clean_documents.keys() == corrupt_documents.keys()
            assert [k for k in clean_documents if clean_documents[k] != corrupt_documents[k]] == [
                (clean_row["collection"], decision_id)
            ]
        before_snapshot = _business(bundle)
        before = _graph(bundle, harness, tenant, candidate, decision_id)
        _record(label + "-before-command", {
            "case": label, "dbname": database.dbname, "tenant": tenant,
            "actualInitialSubmissionResponse": submitted.json(),
            "cleanPendingGraph": clean_graph, "before": before,
            "storedVersionType": type(stored_version).__name__,
            "callerVersion": caller_version, "callerVersionType": type(caller_version).__name__,
            "actualCommandExecuted": False, "formalRelease": "NO_GO",
        })
        response = None
        try:
            response = _decision(client, headers, tenant, decision_id, caller_version, CHECKER,
                                 "native", "GO", "stored-version-decide-" + label)
            payload = response.json()
        except Exception as error:
            observed = _business(bundle)
            _record(label + "-command-error", {
                "case": label, "exceptionClass": type(error).__name__,
                "actualStatus": response.status_code if response is not None else None,
                "before": before, "after": _graph(bundle, harness, tenant, candidate, decision_id),
                "businessRowsUnchanged": observed == before_snapshot,
                "classification": "Command exception/nonJSON, not an HTTP200+accepted-publication bypass claim",
                "formalRelease": "NO_GO",
            })
            raise
        after_snapshot = _business(bundle)
        after = _graph(bundle, harness, tenant, candidate, decision_id)
        evidence = {
            "case": label, "dbname": database.dbname, "tenant": tenant,
            "proposer": ORIGINAL, "signedIndependentChecker": CHECKER,
            "origin": "Actual local signed JWT + actual SQL accounts/scopes/session + normal native canonical owner",
            "passwordLoginOrBrowser": False, "realProductionGrants": False,
            "storedVersion": stored_version, "storedVersionType": type(stored_version).__name__,
            "callerVersion": caller_version, "callerVersionType": type(caller_version).__name__,
            "expectedStatus": expected_status, "actualStatus": response.status_code,
            "response": payload, "actualInitialSubmissionResponse": submitted.json(),
            "cleanPendingGraph": clean_graph, "before": before, "after": after,
            "businessRowsUnchanged": before_snapshot == after_snapshot,
            "corruption": "Own retained Decision.version only, actual persisted SQL, before the measured command" if corrupt else "None: clean actual version2",
            "securityAndSessionTouches": "Excluded by original _business helper; security authorization audit is not domain publication",
            "nativeApprovalIsFormalHumanNetworkGO": False,
        }
        # Persist all actual outcomes before the status assertion, including
        # any unexpected accepted publication in a baseline failure.
        _record(label, evidence)
        assert response.status_code == expected_status, payload
        assert before["retainedReportSHA256"] == after["retainedReportSHA256"]
        assert before["retainedReportDocumentSHA256"] == after["retainedReportDocumentSHA256"]
        assert after["physicalCandidateReviewId"] == decision_id
        actual = harness.decisions(tenant).get_decision(decision_id)
        if corrupt:
            assert payload["detail"] == "DECISION_VERSION_CONFLICT"
            assert before_snapshot == after_snapshot
            assert after == before
            assert actual == replace(current, version=stored_version)
        else:
            assert before_snapshot != after_snapshot
            assert type(actual.version) is int and actual.version == 3
            assert actual.status.value == "APPROVED" and actual.history[-1].actor == CHECKER
            assert actual.created_by == ORIGINAL and actual.submitted_by == ORIGINAL
            assert actual.created_by_verified is True and actual.submission_actor_verified is True
            assert payload["decision"]["version"] == 3 and payload["decision_id"] == decision_id
            assert payload["decision_status"] == "APPROVED"
            assert after["physicalCandidateState"] == "approved"
            assert after["approvalStatus"] == "approved" and after["approvalActor"] == CHECKER
            assert after["approvalId"] == before["approvalId"]
            audit = [event for event in after["ownedBusinessAuditRows"]
                     if event["event_id"] == payload["audit_event_id"]]
            assert len(audit) == 1
            event = audit[0]
            assert event["event_type"] == "sitescore.decision.v1"
            assert event["actor"] == CHECKER and event["action"] == "approve"
            assert event["resource"] == "sitescore/decision/" + decision_id
            assert event["outcome"] == "approved"
            assert event["correlation_id"] == "sod-stored-version-decide-" + label
            metadata = event["metadata"]
            assert metadata["tenant_id"] == tenant and metadata["candidate_site_id"] == candidate
            assert metadata["report_id"] == saved.report_id and metadata["report_version"] == saved.report_version
            assert type(metadata["version"]) is int and metadata["version"] == 3
            assert metadata["approval_id"] == after["approvalId"]
            assert metadata["submitted_by"] == ORIGINAL and metadata["submission_actor_verified"] is True
        assert harness.decisions(tenant).get_report(decision_id).to_dict() == saved.to_dict()
        assert harness.sitescore(tenant).latest(candidate).to_dict() == saved.to_dict()
        assert harness.runtime.calls == []
