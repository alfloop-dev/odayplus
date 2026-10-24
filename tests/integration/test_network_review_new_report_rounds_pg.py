"""PREPARED ONLY: owned signed-SQL review rounds, no real opening/release grant.

Use only a Root-reviewed guarded runner with a fresh local database per case.
The opened state and existing opening metadata are explicit synthetic fixtures;
this suite does not claim a human opening journey or production approval.
"""

import hashlib
import json
import os
import pickle
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.infrastructure.persistence.repositories import (
    DurableApprovalRepository,
    DurableRealizedSiteStore,
)
from shared.workflow.sitescore import CandidateSiteRealizationHook, DecisionStatus
from tests.integration._operation_flag_fixtures import SITE_GO_FLAG, enable_operation_flag
from tests.integration.test_network_review_submission_sod import _submit
from tests.integration.test_sitescore_native_human_scope import (
    CHECKER,
    ORIGINAL,
    _business,
    _decision,
    _open,
    _scope,
    _signed_client,
)
from tests.integration.test_sitescore_native_human_scope import native_scope_pg as native_scope_pg
from tests.unit.test_network_canonical_review_projection import report

pytestmark = pytest.mark.requires_live_env
OPERATOR = "/api/v1/operator/network-reviews"
NATIVE = "/api/v1/sitescore/decisions"


def _sha(value):
    return hashlib.sha256(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)).hexdigest()


def _record(case, facts):
    directory = os.environ.get("ODP_NEW_REPORT_ROUNDS_EVIDENCE_DIR")
    assert directory, "New local fixture execution requires the Root-reviewed guarded runner"
    target = Path(directory) / (case + ".json")
    target.write_text(json.dumps(facts, ensure_ascii=False, indent=2, default=str) + "\n")
    target.chmod(0o600)


def _physical(harness, tenant, candidate):
    repository = harness.listing(tenant)
    return repository._store.get(repository._CANDIDATES, candidate)


def _preserved_graph(bundle, harness, tenant, candidate, decision_id):
    decisions = harness.decisions(tenant)
    original = decisions.get_decision(decision_id)
    physical = deepcopy(_physical(harness, tenant, candidate))
    physical.review_id = None
    return {
        "physicalCandidateExceptCurrentReviewPointer": _sha(physical),
        "decision": _sha(original), "retainedReport": _sha(decisions.get_report(decision_id)),
        "approval": _sha(DurableApprovalRepository(harness.scoped(tenant)).get(original.approval_id)),
        "realizedOpeningBaseline": _sha(DurableRealizedSiteStore(harness.scoped(tenant)).get(candidate)),
        "syntheticExistingOpeningMetadata": _sha(DurableAssistedIntakeRepository(
            harness.scoped(tenant)).get_candidate_metadata(candidate).get("existingOpeningFixture")),
    }


def _prepare_original(pg, monkeypatch, state):
    _, bundle, harness, tenant, candidate, _ = pg
    for actor in (ORIGINAL, CHECKER):
        _scope(bundle, actor, modules=["sitescore", "operator_console"])
    hook = CandidateSiteRealizationHook(DurableRealizedSiteStore(harness.scoped(tenant)))
    bundle_type = type(bundle)
    original_resolver = bundle_type.network_review_resolver

    def resolver_with_fixture_hook(current, *, hooks=()):
        if current is bundle:
            hooks = (hook, *hooks)
        return original_resolver(current, hooks=hooks)

    monkeypatch.setattr(bundle_type, "network_review_resolver", resolver_with_fixture_hook)
    return _signed_client(pg, monkeypatch)


def _original_approval(pg, client, headers, state):
    _, bundle, harness, tenant, candidate, saved = pg
    enable_operation_flag(bundle.feature_flag_registry, key=SITE_GO_FLAG, tenant_id=tenant,
                          queue=bundle.job_queue, audit_log=bundle.audit_log)
    initial, _ = _open(client, headers, tenant, saved, key="original-submission")
    assert initial.status_code == 201, initial.text
    original_id = initial.json()["decision_id"]
    current = harness.decisions(tenant).get_decision(original_id)
    approved = _decision(client, headers, tenant, original_id, current.version,
                         CHECKER, "native", "GO", "original-independent-go")
    assert approved.status_code == 200, approved.text
    assert DurableRealizedSiteStore(harness.scoped(tenant)).get(candidate).decision_id == original_id
    physical = _physical(harness, tenant, candidate)
    assert physical.candidate_site.site_status == "approved"
    if state == "opened":
        # Explicit local synthetic setup, not a fabricated successful opening API.
        harness.listing(tenant).save_review_candidate(
            replace(physical, candidate_site=replace(physical.candidate_site, site_status="opened")),
            expected_review_id=physical.review_id, expected_state="approved")
    physical = _physical(harness, tenant, candidate)
    physical.existing_opening_record = {"fixtureOnly": True, "originalDecisionId": original_id,
                                         "recordId": "synthetic-existing-opening-record"}
    harness.listing(tenant).save_review_candidate(physical, expected_review_id=physical.review_id,
                                                 expected_state=state)
    metadata = DurableAssistedIntakeRepository(harness.scoped(tenant))
    value = metadata.get_candidate_metadata(candidate)
    value["existingOpeningFixture"] = {"fixtureOnly": True, "state": state,
                                       "originalDecisionId": original_id, "recordedOn": "2026-10-14"}
    metadata.save_candidate_metadata(candidate, value)
    return original_id


def _save_next(harness, tenant, candidate, version):
    return harness.sitescore(tenant).save_report(replace(
        report(), candidate_site_id=candidate, model_version="synthetic-new-round-model-" + str(version)))


def _new_submission(pg, client, headers, saved, surface, key):
    _, _, _, tenant, candidate, _ = pg
    if surface == "native":
        response, body = _open(client, headers, tenant, saved, key=key,
                               previous_decision_id="body-cannot-choose-predecessor")
        assert response.status_code == 201, response.text
        return response.json()["decision_id"], body, response.json()
    _, body, receipt = _submit(client, headers, tenant, candidate, ORIGINAL, key)
    return receipt["records"]["decisionId"], body, receipt


def _operator_decision(client, headers, decision_id, action, key):
    body = {"decision": action, "reason": "Recorded independent newer report review outcome",
            "overrideAck": True, "conditions": "New recorded lease condition" if action == "WAIT" else "",
            "requiredData": ["new lease", "new photographs"] if action == "RETURN" else []}
    return client.post(OPERATOR + "/" + decision_id + "/decide",
                       headers=headers(CHECKER, key), json=body)


@pytest.mark.parametrize("state", ["approved", "opened"])
@pytest.mark.parametrize("surface,action", [("operator", "GO"), ("operator", "WAIT"),
                                           ("operator", "RETURN"), ("operator", "REJECT"),
                                           ("native", "GO")])
def test_signed_new_round_and_subsequent_third_report_preserve_original_graph(
    native_scope_pg, monkeypatch, state, surface, action,
):
    pg = native_scope_pg
    database, bundle, harness, tenant, candidate, first = pg
    with _prepare_original(pg, monkeypatch, state) as (client, headers, _, _):
        original_id = _original_approval(pg, client, headers, state)
        original_graph = _preserved_graph(bundle, harness, tenant, candidate, original_id)
        second = _save_next(harness, tenant, candidate, 2)
        new_id, body, response = _new_submission(pg, client, headers, second, surface, "second-submit")
        new = harness.decisions(tenant).get_decision(new_id)
        original = harness.decisions(tenant).get_decision(original_id)
        assert new_id != original_id and new.approval_id != original.approval_id
        assert new.previous_decision_id == original_id and new.report_id == second.report_id
        assert new.created_by == ORIGINAL and new.submitted_by == ORIGINAL
        assert new.created_by_verified is True and new.submission_actor_verified is True
        assert new.status is DecisionStatus.PENDING_REVIEW and new.version == 2
        assert _physical(harness, tenant, candidate).review_id == new_id
        assert _physical(harness, tenant, candidate).candidate_site.site_status == state
        before_self = _business(bundle)
        own_denial = _decision(client, headers, tenant, new_id, new.version,
                               ORIGINAL, "native", "GO", "new-round-self-denial")
        assert own_denial.status_code == 403 and _business(bundle) == before_self
        outcome = (_decision(client, headers, tenant, new_id, new.version, CHECKER,
                             "native", "GO", "second-native-go") if surface == "native"
                   else _operator_decision(client, headers, new_id, action, "second-" + action.lower()))
        _record(state + "-" + surface + "-" + action.lower() + "-outcome", {
            "database": database.dbname, "status": outcome.status_code,
            "originalGraphBefore": original_graph,
            "originalGraphAfter": _preserved_graph(bundle, harness, tenant, candidate, original_id),
            "secondSubmission": response, "actor": CHECKER, "openingFixtureOnly": True,
        })
        assert outcome.status_code == 200, outcome.text
        assert _preserved_graph(bundle, harness, tenant, candidate, original_id) == original_graph
        assert _physical(harness, tenant, candidate).candidate_site.site_status == state
        assert harness.decisions(tenant).get_report(new_id) == second
        if action == "RETURN":
            third = _save_next(harness, tenant, candidate, 3)
            context, _, returned_submission = _submit(client, headers, tenant, candidate, ORIGINAL, "second-resubmit")
            assert context["target"]["expectedReportId"] == second.report_id
            assert context["target"]["expectedLatestReportId"] == third.report_id
            assert returned_submission["records"]["decisionId"] == new_id
            assert harness.decisions(tenant).get_report(new_id) == second
            final = _operator_decision(client, headers, new_id, "GO", "second-after-return-go")
            assert final.status_code == 200, final.text
        else:
            third = _save_next(harness, tenant, candidate, 3)
        third_id, _, _ = _new_submission(pg, client, headers, third, surface, "third-submit")
        third_decision = harness.decisions(tenant).get_decision(third_id)
        assert third_id not in {original_id, new_id}
        assert third_decision.previous_decision_id == new_id
        assert third_decision.report_id == third.report_id and third_decision.status is DecisionStatus.PENDING_REVIEW
        assert harness.decisions(tenant).get_decision(new_id).previous_decision_id == original_id
        assert _preserved_graph(bundle, harness, tenant, candidate, original_id) == original_graph
        assert _physical(harness, tenant, candidate).candidate_site.site_status == state
        assert sum(d.status is DecisionStatus.PENDING_REVIEW for d in harness.decisions(tenant).list_decisions()) == 1
        listing = client.get("/api/v1/operator/network-listings", headers=headers(CHECKER, "current-lifecycle-read"))
        assert listing.status_code == 200, listing.text
        row = next(r for r in listing.json()["candidates"] if r["id"] == candidate)
        assert row["status"] == state and row["reviewSubmission"]["decisionId"] == third_id
        old_read = client.get(NATIVE + "/" + original_id, headers=headers(CHECKER, "original-record-read"))
        assert old_read.status_code == 200 and old_read.json() == original.to_dict()
        assert harness.runtime.calls == []
        _record(state + "-" + surface + "-" + action.lower() + "-complete", {
            "originalDecisionId": original_id, "secondDecisionId": new_id, "thirdDecisionId": third_id,
            "originalGraph": original_graph, "finalOriginalGraph": _preserved_graph(bundle, harness, tenant, candidate, original_id),
            "physicalState": state, "projectedState": row["status"], "modelRuntimeCalls": len(harness.runtime.calls),
        })


@pytest.mark.parametrize("state", ["approved", "opened"])
@pytest.mark.parametrize("surface", ["native", "operator"])
def test_same_terminal_report_cannot_start_new_round_with_new_idempotency_key(native_scope_pg, monkeypatch, state, surface):
    pg = native_scope_pg
    _, bundle, _, tenant, candidate, saved = pg
    with _prepare_original(pg, monkeypatch, state) as (client, headers, _, _):
        _original_approval(pg, client, headers, state)
        before = _business(bundle)
        response = (_open(client, headers, tenant, saved, key="same-report-new-key")[0] if surface == "native"
                    else client.get(OPERATOR + "/candidates/" + candidate + "/submission-context",
                                    headers=headers(ORIGINAL, "same-terminal-context")))
        assert response.status_code == 409, response.text
        assert _business(bundle) == before


def test_stale_report_context_cannot_create_new_round(native_scope_pg, monkeypatch):
    pg = native_scope_pg
    _, bundle, harness, tenant, candidate, _ = pg
    with _prepare_original(pg, monkeypatch, "opened") as (client, headers, _, _):
        _original_approval(pg, client, headers, "opened")
        _save_next(harness, tenant, candidate, 2)
        context = client.get(OPERATOR + "/candidates/" + candidate + "/submission-context",
                             headers=headers(ORIGINAL, "stale-context-read"))
        assert context.status_code == 200, context.text
        body = {**context.json()["target"], "expectedActorSubjectId": ORIGINAL, "expectedTenantId": tenant,
                "submissionSummary": "Saved second report stale context", "reviewerGroupId": "site_reviewer"}
        _save_next(harness, tenant, candidate, 3)
        before = _business(bundle)
        response = client.post(OPERATOR + "/candidates/" + candidate + "/submit",
                               headers=headers(ORIGINAL, "stale-context-submit"), json=body)
        assert response.status_code == 409 and _business(bundle) == before


@pytest.mark.parametrize("surface", ["native", "operator"])
def test_new_round_receipt_replay_rechecks_current_sql_actor_scope(native_scope_pg, monkeypatch, surface):
    pg = native_scope_pg
    _, bundle, harness, tenant, candidate, _ = pg
    with _prepare_original(pg, monkeypatch, "opened") as (client, headers, _, _):
        _original_approval(pg, client, headers, "opened")
        second = _save_next(harness, tenant, candidate, 2)
        new_id, body, _ = _new_submission(pg, client, headers, second, surface, "scope-bound-submit")
        _scope(bundle, ORIGINAL, modules=["sitescore", "operator_console"], clearance="PUBLIC")
        before = _business(bundle)
        path = NATIVE if surface == "native" else OPERATOR + "/candidates/" + candidate + "/submit"
        denied = client.post(path, headers=headers(ORIGINAL, "scope-bound-submit"), json=body)
        assert denied.status_code == 403 and _business(bundle) == before
        assert _physical(harness, tenant, candidate).review_id == new_id
        assert _physical(harness, tenant, candidate).candidate_site.site_status == "opened"


def _corrupt_chain(harness, bundle, tenant, original_id, new_id, corruption):
    decisions = harness.decisions(tenant)
    previous = decisions.get_decision(original_id)
    if corruption in {"missing-decision", "missing-retained", "approval-missing"}:
        collection, key = {"missing-decision": ("sitescore.decisions", original_id),
                           "missing-retained": ("sitescore.decision_reports", original_id),
                           "approval-missing": ("workflow.approvals", previous.approval_id)}[corruption]
        bundle.engine.execute("DELETE FROM durable_documents WHERE collection=? AND doc_id=?",
                              (harness.scoped(tenant)._collection(collection), key))
    elif corruption == "wrong-tenant":
        decisions.save_decision(replace(previous, tenant_id="synthetic-foreign-tenant"))
    elif corruption == "cycle":
        decisions.save_decision(replace(previous, previous_decision_id=new_id))
    elif corruption == "deleted-link":
        decisions.save_decision(replace(decisions.get_decision(new_id), previous_decision_id=None))
    elif corruption == "wrong-retained":
        # Explicit own raw fixture corruption, never a normal retained-report writer.
        harness.scoped(tenant).put("sitescore.decision_reports", original_id,
                                  replace(decisions.get_report(original_id), model_version="synthetic-corrupt-model"))
    else:
        approvals = DurableApprovalRepository(harness.scoped(tenant))
        approval = approvals.get(previous.approval_id)
        changes = {"approval-foreign": {"decision_id": "synthetic-foreign-decision"},
                   "approval-status": {"approval_status": "returned"},
                   "approval-actor": {"approver_id": "synthetic-foreign-actor"}}[corruption]
        approvals.save(replace(approval, **changes))


@pytest.mark.parametrize("surface", ["native", "operator"])
@pytest.mark.parametrize("corruption", ["missing-decision", "wrong-tenant", "cycle", "deleted-link",
    "missing-retained", "wrong-retained", "approval-missing", "approval-foreign", "approval-status", "approval-actor"])
def test_pending_new_round_rejects_changed_actual_previous_chain_before_mutation(
    native_scope_pg, monkeypatch, surface, corruption,
):
    pg = native_scope_pg
    database, bundle, harness, tenant, candidate, _ = pg
    with _prepare_original(pg, monkeypatch, "opened") as (client, headers, _, _):
        original_id = _original_approval(pg, client, headers, "opened")
        second = _save_next(harness, tenant, candidate, 2)
        new_id, _, _ = _new_submission(pg, client, headers, second, "native", "second-submit")
        _corrupt_chain(harness, bundle, tenant, original_id, new_id, corruption)
        before = _business(bundle)
        current = harness.decisions(tenant).get_decision(new_id)
        response = (_decision(client, headers, tenant, new_id, current.version, CHECKER, "native", "GO", "corrupt-chain-go")
                    if surface == "native" else _operator_decision(client, headers, new_id, "GO", "corrupt-chain-go"))
        _record("corrupt-chain-" + surface + "-" + corruption, {
            "database": database.dbname, "corruption": corruption, "status": response.status_code,
            "businessBeforeSHA256": _sha(before), "businessAfterSHA256": _sha(_business(bundle)),
            "newDecisionId": new_id, "originalDecisionId": original_id,
            "physicalState": _physical(harness, tenant, candidate).candidate_site.site_status,
        })
        assert response.status_code == 409, response.text
        assert _business(bundle) == before
        assert harness.decisions(tenant).get_decision(new_id).status is DecisionStatus.PENDING_REVIEW
        assert _physical(harness, tenant, candidate).candidate_site.site_status == "opened"
        assert harness.runtime.calls == []


@pytest.mark.parametrize("surface", ["native", "operator"])
def test_actual_candidate_review_lookup_alias_cannot_select_another_decision_body(native_scope_pg, monkeypatch, surface):
    pg = native_scope_pg
    database, bundle, harness, tenant, candidate, _ = pg
    with _prepare_original(pg, monkeypatch, "opened") as (client, headers, _, _):
        original_id = _original_approval(pg, client, headers, "opened")
        second = _save_next(harness, tenant, candidate, 2)
        original = harness.decisions(tenant).get_decision(original_id)
        alias = "synthetic-owned-previous-decision-alias"
        # Raw exact owned row corruption: normal save_decision always uses body ID.
        harness.scoped(tenant).put("sitescore.decisions", alias, original)
        physical = deepcopy(_physical(harness, tenant, candidate))
        physical.review_id = alias
        harness.listing(tenant).save_review_candidate(physical, expected_review_id=original_id,
                                                     expected_state="opened")
        preserved = _preserved_graph(bundle, harness, tenant, candidate, original_id)
        before = _business(bundle)
        response = (_open(client, headers, tenant, second, key="alias-native-submit")[0] if surface == "native"
                    else client.get(OPERATOR + "/candidates/" + candidate + "/submission-context",
                                    headers=headers(ORIGINAL, "alias-operator-context")))
        _record("alias-" + surface, {"database": database.dbname, "lookupKey": alias,
            "actualReturnedDecisionBodyId": original_id, "status": response.status_code,
            "businessBeforeSHA256": _sha(before), "businessAfterSHA256": _sha(_business(bundle)),
            "originalGraphBefore": preserved,
            "originalGraphAfter": _preserved_graph(bundle, harness, tenant, candidate, original_id),
            "physicalState": _physical(harness, tenant, candidate).candidate_site.site_status})
        assert response.status_code == 409, response.text
        assert _business(bundle) == before
        assert _preserved_graph(bundle, harness, tenant, candidate, original_id) == preserved
        assert _physical(harness, tenant, candidate).review_id == alias
        assert _physical(harness, tenant, candidate).candidate_site.site_status == "opened"
        assert harness.runtime.calls == []


@pytest.mark.parametrize("corruption", ["deleted-link", "approval-missing", "approval-status"])
def test_returned_new_round_resubmit_rechecks_actual_previous_chain(native_scope_pg, monkeypatch, corruption):
    pg = native_scope_pg
    _, bundle, harness, tenant, candidate, _ = pg
    with _prepare_original(pg, monkeypatch, "opened") as (client, headers, _, _):
        original_id = _original_approval(pg, client, headers, "opened")
        second = _save_next(harness, tenant, candidate, 2)
        new_id, _, _ = _new_submission(pg, client, headers, second, "native", "second-submit")
        returned = _operator_decision(client, headers, new_id, "RETURN", "second-return")
        assert returned.status_code == 200, returned.text
        context = client.get(OPERATOR + "/candidates/" + candidate + "/submission-context",
                             headers=headers(ORIGINAL, "returned-context"))
        assert context.status_code == 200, context.text
        body = {**context.json()["target"], "expectedActorSubjectId": ORIGINAL, "expectedTenantId": tenant,
                "submissionSummary": "Resubmit the recorded second round frozen report", "reviewerGroupId": "site_reviewer"}
        _corrupt_chain(harness, bundle, tenant, original_id, new_id, corruption)
        before = _business(bundle)
        response = client.post(OPERATOR + "/candidates/" + candidate + "/submit",
                               headers=headers(ORIGINAL, "returned-resubmit"), json=body)
        assert response.status_code == 409 and _business(bundle) == before
        assert harness.decisions(tenant).get_decision(new_id).status is DecisionStatus.DRAFT
        assert _physical(harness, tenant, candidate).candidate_site.site_status == "opened"


def test_two_actual_pending_decisions_fail_closed_without_third_round(native_scope_pg, monkeypatch):
    pg = native_scope_pg
    _, bundle, harness, tenant, candidate, _ = pg
    with _prepare_original(pg, monkeypatch, "approved") as (client, headers, _, _):
        original_id = _original_approval(pg, client, headers, "approved")
        second = _save_next(harness, tenant, candidate, 2)
        new_id, _, _ = _new_submission(pg, client, headers, second, "native", "second-submit")
        own = harness.decisions(tenant).get_decision(new_id)
        # Actual persisted ambiguity is an explicitly owned corruption fixture.
        harness.decisions(tenant).save_decision(replace(own, decision_id="synthetic-extra-pending-round"))
        before = _business(bundle)
        response = client.get(OPERATOR + "/candidates/" + candidate + "/submission-context",
                              headers=headers(ORIGINAL, "ambiguous-context"))
        assert response.status_code == 409 and _business(bundle) == before
        assert harness.decisions(tenant).get_decision(original_id).status is DecisionStatus.APPROVED


@pytest.mark.parametrize("failure", ["candidate", "metadata", "after-save"])
def test_new_round_fault_rolls_back_all_actual_sql_records_and_preserves_opening(native_scope_pg, monkeypatch, failure):
    pg = native_scope_pg
    _, bundle, harness, tenant, candidate, _ = pg
    with _prepare_original(pg, monkeypatch, "opened") as (client, headers, _, _):
        original_id = _original_approval(pg, client, headers, "opened")
        _save_next(harness, tenant, candidate, 2)
        context = client.get(OPERATOR + "/candidates/" + candidate + "/submission-context",
                             headers=headers(ORIGINAL, "fault-context"))
        assert context.status_code == 200, context.text
        body = {**context.json()["target"], "expectedActorSubjectId": ORIGINAL, "expectedTenantId": tenant,
                "submissionSummary": "Saved second report fault control", "reviewerGroupId": "site_reviewer"}
        def injected(original):
            def fail(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("owned new-round post-write fault")
            return fail
        if failure == "candidate":
            from shared.infrastructure.persistence.repositories import DurableListingRepository
            monkeypatch.setattr(DurableListingRepository, "save_review_candidate",
                                injected(DurableListingRepository.save_review_candidate))
        elif failure == "metadata":
            monkeypatch.setattr(DurableAssistedIntakeRepository, "save_review_metadata",
                                injected(DurableAssistedIntakeRepository.save_review_metadata))
        else:
            from apps.api.app.routes.operator_modules.live_service import (
                DurableTenantServiceResolver,
            )
            def fail_after(*args):
                raise RuntimeError("owned new-round after-save fault")
            original_invoke = DurableTenantServiceResolver._invoke_service
            def with_owned_fault(self, service, partition_tenant_id, method_name, args, kwargs):
                previous = self._after_save
                if method_name == "submit_review":
                    self._after_save = fail_after
                try:
                    return original_invoke(self, service, partition_tenant_id, method_name, args, kwargs)
                finally:
                    self._after_save = previous
            monkeypatch.setattr(DurableTenantServiceResolver, "_invoke_service", with_owned_fault)
        before = _business(bundle)
        graph = _preserved_graph(bundle, harness, tenant, candidate, original_id)
        if failure == "after-save":
            response = client.post(OPERATOR + "/candidates/" + candidate + "/submit",
                                   headers=headers(ORIGINAL, "fault-submit"), json=body)
            assert response.status_code == 503
        else:
            with pytest.raises(RuntimeError, match="owned new-round"):
                client.post(OPERATOR + "/candidates/" + candidate + "/submit",
                            headers=headers(ORIGINAL, "fault-submit"), json=body)
        assert _business(bundle) == before
        assert _preserved_graph(bundle, harness, tenant, candidate, original_id) == graph
