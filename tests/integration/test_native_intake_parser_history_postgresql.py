"""Actual-SQL controls for immutable parser results and fail-closed reads."""
import copy
import os
import time
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from modules.listing.application.native_intake_assisted_entry_history import (
    NativeAssistedEntryHistory,
)
from modules.listing.application.native_intake_parser_history import NativeParserTerminalHistory
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import NativeParserCommandBudget
from modules.listing.application.native_intake_parser_results import commit_parser_output
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from shared.jobs.queue import JobFenceRejectedError
from tests.integration.test_canonical_identity_cases_postgresql import case_api as case_api
from tests.integration.test_native_intake_parser_admission_postgresql import BODY, admit, claimed
from tests.integration.test_native_intake_parser_results_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    parser_io_case as parser_io_case,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_intake_parser_results_postgresql import persisted
from tests.integration.test_native_intake_parser_results_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_results_postgresql import (
    start_case as start_case,
)

pytestmark = pytest.mark.requires_live_env


def retire_all_positive_settings(monkeypatch):
    for key in list(os.environ):
        if key.startswith("ODP_NATIVE_INTAKE_") and (
            key.endswith(("SIGNING_KEY", "POLICY_JSON", "STORAGE_JSON", "REALM_JSON", "GCS_TOKEN"))
        ):
            monkeypatch.delenv(key)


@pytest.fixture
def completed_parser(parser_io_case):
    case, io, leases, delivery = parser_io_case
    observed = io.read_and_parse()
    result = commit_parser_output(case[0], delivery, leases, observed)
    return case, delivery, observed, result


def _human_new_case(completed):
    from shared.infrastructure.persistence.identity_cases_sql import CanonicalMatchCaseRepository

    case, _, _, result = completed
    owner, ids, _, _, _, _, _ = case
    return CanonicalMatchCaseRepository(owner.graph).create_case(ids["tenant"], result["intake_id"],
        expected_intake_version=result["version"], actor_id=ids["actor"], confidence=Decimal("0"),
        candidates=(), supporting_signals=[{"kind": "SYNTHETIC_HUMAN_NEW_PROPOSAL"}],
        contradictory_signals=[], reason="Explicit synthetic human proposal, awaiting confirmation", outcome="NEW")


def test_listing_context_reuses_actual_terminal_proof_without_reviewing_fields(completed_parser, case_api):
    case, _, _, result = completed_parser
    owner, ids, database, _, _, _, _ = case
    client, _, _ = case_api
    proposed = _human_new_case(completed_parser)
    before = persisted(case)
    response = client.get(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-context")
    assert response.status_code == 200, response.text
    context = response.json()
    reference = context["listing_input"]
    assert reference["parser_job_id"] == result["job_id"]
    assert reference["parser_run_id"] == result["parser_run_id"]
    assert reference["source_snapshot_id"] == result["source_snapshot_id"]
    assert context["intake_version"] == result["version"]
    assert context["anchors"] == context["properties"] == context["listings"] == []
    assert not {"normalized_payload", "parsed_payload", "original_url", "source_id"} & reference.keys()
    with database.connect() as connection:
        corrections = connection.execute("SELECT correction_id,status FROM intake.human_corrections "
            "WHERE tenant_id=%s AND intake_id=%s", (ids["tenant"], result["intake_id"])).fetchall()
    assert corrections and all(row[1] == "PROPOSED" for row in corrections)
    assert set(reference["proposed_correction_ids"]) == {str(row[0]) for row in corrections}
    fields = reference["fields"]
    assert {row["correction_id"] for row in fields} == set(reference["proposed_correction_ids"])
    assert {row["field_path"] for row in fields} >= {"address", "rent", "areaPing"}
    assert all(row["status"] == "PROPOSED" and row["version"] >= 2 for row in fields)
    assert all(row["classification"] == "INTERNAL" and row.get("masked", False) is False for row in fields)
    assert all(row["corrected"] is not None and "effective" not in row for row in fields)
    assert persisted(case) == before


@pytest.mark.parametrize("clearance,masked", [("PUBLIC", True), ("INTERNAL", False)])
def test_listing_field_preview_uses_existing_classification_mask(completed_parser, case_api, clearance, masked):
    from shared.auth import DataClassification

    case, _, _, _ = completed_parser
    client, caller, _ = case_api
    proposed = _human_new_case(completed_parser)
    caller["principal"] = replace(caller["principal"], scope=replace(caller["principal"].scope,
        clearance=DataClassification[clearance]))
    before = persisted(case)
    response = client.get(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-context")
    assert response.status_code == 200, response.text
    fields = response.json()["listing_input"]["fields"]
    assert fields and all(row.get("masked", False) is masked for row in fields)
    for row in fields:
        if masked:
            assert row["parsed"] is row["normalized"] is row["corrected"] is None
            assert row["mask_reason_code"] == "FIELD_MASKED"
        else:
            assert row["corrected"] is not None
        assert "effective" not in row
    assert persisted(case) == before


def test_listing_field_preview_cannot_bypass_parent_scope(completed_parser, case_api):
    case, _, _, _ = completed_parser
    client, caller, _ = case_api
    proposed = _human_new_case(completed_parser)
    caller["principal"] = replace(caller["principal"], scope=replace(caller["principal"].scope,
        brand_ids=frozenset({str(uuid4())})))
    before = persisted(case)
    response = client.get(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-context")
    assert response.status_code == 403, response.text
    assert "listing_input" not in response.json() and "corrected" not in response.text
    assert persisted(case) == before


def test_listing_field_preview_reports_later_rejection_without_applying_parser_values(completed_parser, case_api):
    case, delivery, _, result = completed_parser
    owner, ids, database, _, _, _, _ = case
    client, _, _ = case_api
    proposed = _human_new_case(completed_parser)
    path = f"/api/v1/match-cases/{proposed['match_case_id']}/identity-context"
    first = client.get(path)
    assert first.status_code == 200, first.text
    with database.connect() as connection:
        assert connection.execute("UPDATE intake.human_corrections SET status='REJECTED',reviewed_by=%s,"
            "reviewed_at=clock_timestamp(),version=version+1 WHERE tenant_id=%s AND intake_id=%s",
            (ids["reviewer"], ids["tenant"], result["intake_id"])).rowcount > 0
    assert owner.replay(delivery) == {"terminal": True, "result": result}
    before = persisted(case)
    second = client.get(path)
    assert second.status_code == 200, second.text
    old = {row["correction_id"]: row for row in first.json()["listing_input"]["fields"]}
    for row in second.json()["listing_input"]["fields"]:
        assert row["status"] == "REJECTED" and row["version"] == old[row["correction_id"]]["version"] + 1
        assert row["corrected"] == old[row["correction_id"]]["corrected"]
        assert "effective" not in row
    assert persisted(case) == before


@pytest.fixture
def routine_confirmation(completed_parser, case_api, request, monkeypatch):
    from modules.external_data.application.assisted_intake import content_fingerprint
    from modules.listing.application.identity_delivery import (
        IDENTITY_POLICY_ENV,
        IDENTITY_SIGNING_KEY_ENV,
    )
    from shared.auth import Principal, Role, Scope
    from shared.infrastructure.persistence.identity_cases_sql import (
        CanonicalMatchCaseRepository,
        MatchCandidateEvidence,
    )

    case, _, _, result = completed_parser
    owner, ids, database, _, _, _, _ = case
    client, caller, _ = case_api
    kind = getattr(request, "param", "CREATE")
    monkeypatch.setenv(IDENTITY_SIGNING_KEY_ENV, "Explicit synthetic routine execution signing material only")
    monkeypatch.setenv(IDENTITY_POLICY_ENV, canonical_json({"policy_id": "synthetic-routine-execution",
        "version": 1, "max_attempts": 3, "deadline_seconds": 120, "backoff_seconds": [1, 2]}))
    # Explicit engineering account in this isolated test database. It grants
    # no real reviewer or release authority and is distinct from field author.
    with database.connect() as connection:
        connection.execute("INSERT INTO identity.accounts (account_id,tenant_id,username,email,status,created_by) "
            "VALUES (%s,%s,'synthetic-confirmer','confirm@example.invalid','active','synthetic-fixture')",
            (ids["reviewer"], ids["tenant"]))
        connection.execute("INSERT INTO identity.account_roles (account_id,role,granted_by) "
            "VALUES (%s,'site_reviewer','synthetic-fixture')", (ids["reviewer"],))
        connection.execute("INSERT INTO identity.account_scopes (account_id,modules,clearance) "
            "VALUES (%s,'[\"listing\"]'::jsonb,'CONFIDENTIAL')", (ids["reviewer"],))
        if kind == "REVISE":
            previous_id = str(uuid4())
            old_values = {**BODY["fields"], "rent": BODY["fields"]["rent"] + 1}
            connection.execute("INSERT INTO expansion.listing_revisions (listing_revision_id,tenant_id,listing_id,revision_no,"
                "revision_kind,normalized_values,effective_values,material_fingerprint) "
                "VALUES (%s,%s,%s,3,'CREATED',%s::jsonb,%s::jsonb,%s)", (previous_id, ids["tenant"], ids["listing"],
                canonical_json(old_values), canonical_json(old_values), content_fingerprint(old_values)))
            connection.execute("UPDATE expansion.listings SET source_id=(SELECT source_id FROM intake.intakes "
                "WHERE intake_id=%s),canonical_url_sha256=(SELECT canonical_url_sha256 FROM intake.intakes WHERE intake_id=%s),"
                "current_revision_id=%s WHERE listing_id=%s", (result["intake_id"], result["intake_id"], previous_id, ids["listing"]))
            connection.execute("UPDATE identity.source_identity_edges SET source_id=l.source_id,"
                "source_entity_id=COALESCE(l.source_listing_id,l.listing_id::text) FROM expansion.listings l "
                "WHERE l.listing_id=%s AND identity.source_identity_edges.listing_id=l.listing_id", (ids["listing"],))
    if kind == "CREATE":
        proposed = _human_new_case(completed_parser)
    else:
        proposed = CanonicalMatchCaseRepository(owner.graph).create_case(ids["tenant"], result["intake_id"],
            expected_intake_version=result["version"], actor_id=ids["actor"], confidence=Decimal("1"),
            candidates=(MatchCandidateEvidence(ids["source"], 7, Decimal("1"),
                {"kind": "SYNTHETIC_HUMAN_REVISION_PROPOSAL"}, ids["listing"], 13),),
            supporting_signals=[{"kind": "SYNTHETIC_HUMAN_REVISION_PROPOSAL"}], contradictory_signals=[],
            reason="Explicit synthetic human revision proposal",
            outcome="REVISION")
    caller["principal"] = Principal(ids["reviewer"], frozenset({Role.SITE_REVIEWER}), Scope(tenant_id=ids["tenant"]))
    observed = client.get(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-context")
    assert observed.status_code == 200, observed.text
    context = observed.json()
    reference = context["listing_input"]
    binding = {name: reference[name] for name in ("parser_job_id", "parser_run_id", "parser_run_version",
        "source_snapshot_id", "original_input_sha256", "normalized_payload_sha256")}
    binding["expected_correction_versions"] = {row["correction_id"]: row["version"] for row in reference["fields"]}
    plan = {"expected_intake_version": context["intake_version"], "input_binding": binding,
        "expected_property_versions": {row["property_id"]: row["version"] for row in context["properties"]},
        "expected_listing_versions": {row["listing_id"]: row["version"] for row in context["listings"]},
        "expected_edge_versions": {row["edge_id"]: row["edge_version"] for row in context["source_identity_edges"]},
        "candidate_reassignment_plan": []}
    return completed_parser, case_api, proposed, {"decision_type": kind, "plan": plan,
        "reason": "Confirm the observed synthetic source facts", "risk_acknowledged": True}


def _confirm(fixture):
    _, api, proposed, body = fixture
    return api[0].post(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-decisions", json=body,
        headers={"Idempotency-Key": "synthetic-routine-confirmation-001", "If-Match": f'W/"{proposed["version"]}"'})


@pytest.mark.parametrize("routine_confirmation", ["CREATE", "REVISE"], indirect=True)
def test_routine_confirmation_records_actual_human_and_queues_only_the_existing_executor(routine_confirmation):
    from tests.integration.test_canonical_identity_cases_postgresql import _snapshot

    completed, api, proposed, body = routine_confirmation
    case, _, _, result = completed
    owner, ids, database, _, _, _, _ = case
    before = persisted(case)
    canonical = (owner.graph, ids, None, database)
    graph_before = _snapshot(canonical)
    response = _confirm(routine_confirmation)
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["status"] == "APPROVED" and receipt["job_id"] and receipt["job_status"] == "QUEUED"
    assert receipt["match_case_version"] == proposed["version"] + 1
    with owner.graph.transaction(ids["tenant"]):
        decision = owner.graph._one("SELECT * FROM identity.match_decisions WHERE tenant_id=? AND match_decision_id=?",
            (ids["tenant"], receipt["decision_id"]))
        assert decision["proposer_subject_id"] == ids["reviewer"]
        assert decision["reviewer_subject_id"] is None
        assert decision["after_graph"]["confirmation"]["confirmed_by"] == ids["reviewer"]
        assert decision["after_graph"]["plan"] == body["plan"]
        assert decision["reviewed_at"] is not None
    # Confirmation admits work but does not apply fields or change the Intake,
    # identity graph or Listing. The independent executor owns those writes.
    after = persisted(case)
    for table in before:
        if table not in {"audit.audit_events", "workflow.outbox_events", "identity.match_cases", "delivery", "workflow.jobs"}:
            assert after[table] == before[table], table
    graph_after = _snapshot(canonical)
    for table in graph_before:
        if table not in {"identity.match_cases", "identity.match_decisions", "audit.audit_events",
                "workflow.outbox_events", "delivery", "workflow.jobs"}:
            assert graph_after[table] == graph_before[table], table
    read = api[0].get(f"/api/v1/canonical-identity-decisions/{receipt['decision_id']}")
    assert read.status_code == 200, read.text
    assert read.json()["decision_type"] == body["decision_type"]
    replay = _confirm(routine_confirmation)
    assert replay.status_code == 201 and replay.json() == receipt
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert persisted(case) == after
    # A stale authentication result cannot keep a withdrawn SQL role alive,
    # even for a durable lost-response replay.
    with database.connect() as connection:
        connection.execute("UPDATE identity.account_roles SET role='expansion_user' WHERE account_id=%s", (ids["reviewer"],))
    refused = _confirm(routine_confirmation)
    assert refused.status_code == 403, refused.text
    assert persisted(case) == after


@pytest.mark.parametrize("routine_confirmation", ["CREATE", "REVISE"], indirect=True)
@pytest.mark.parametrize("execution", ["coordinator", "registered_worker"])
def test_confirmed_listing_uses_existing_fenced_executor_and_immutable_revision(routine_confirmation, execution):
    from modules.external_data.application.assisted_intake import content_fingerprint
    from modules.listing.application.identity_delivery import (
        IdentityDeliveryAuthenticator,
        IdentityJobAdmission,
    )
    from modules.listing.application.identity_execution import IdentityExecutionService
    from shared.jobs.queue import JobStatus
    from tests.integration.test_canonical_identity_admission_postgresql import _claim

    completed, api, _, body = routine_confirmation
    case, parser_delivery, _, parsed = completed
    owner, ids, database, _, _, _, _ = case
    canonical = (owner.graph, ids, None, database)
    with owner.graph.transaction(ids["tenant"]):
        original_listing = owner.graph._one("SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=?",
            (ids["tenant"], ids["listing"]))
        original_property = owner.graph._one("SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=?",
            (ids["tenant"], ids["source"]))
        prior_revisions = owner.engine.query("SELECT * FROM expansion.listing_revisions WHERE tenant_id=? AND listing_id=?",
            (ids["tenant"], ids["listing"]))
    approved = _confirm(routine_confirmation)
    assert approved.status_code == 201, approved.text
    receipt = approved.json()
    delivery, registry, bundle = _claim(canonical, receipt["job_id"])
    if execution == "registered_worker":
        registry.handle(delivery, bundle)
    else:
        owner.graph.delivery_authenticator = IdentityDeliveryAuthenticator(owner.engine)
        IdentityExecutionService(owner.graph).run(delivery)
        IdentityJobAdmission(owner.graph).settle_delivery(delivery)
    assert owner.graph.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED
    with owner.graph.transaction(ids["tenant"]):
        job = owner.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (ids["tenant"], delivery.job_id))
        assert job["status"] == "SUCCEEDED", job["last_error"]
        result = job["result"]
        intake = owner.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?", (ids["tenant"], parsed["intake_id"]))
        listing = owner.graph._one("SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=?", (ids["tenant"], result["listing_id"]))
        revision = owner.graph._one("SELECT * FROM expansion.listing_revisions WHERE tenant_id=? AND listing_revision_id=?",
            (ids["tenant"], result["listing_revision_id"]))
        assert intake["processing_state"] == "READY" and intake["resolved_listing_id"] == listing["listing_id"]
        assert intake["version"] == body["plan"]["expected_intake_version"] + 1
        assert revision["source_snapshot_id"] == parsed["source_snapshot_id"] and revision["parser_run_id"] == parsed["parser_run_id"]
        effective = revision["effective_values"]
        if isinstance(effective, str):
            import json
            effective = json.loads(effective)
        assert effective == BODY["fields"]
        assert revision["material_fingerprint"].strip() == content_fingerprint(effective)
        corrections = owner.engine.query("SELECT * FROM intake.human_corrections WHERE tenant_id=? AND intake_id=?",
            (ids["tenant"], parsed["intake_id"]))
        assert {r["correction_id"] for r in corrections} == set(body["plan"]["input_binding"]["expected_correction_versions"])
        for row in corrections:
            assert row["status"] == "APPLIED" and row["reviewed_by"] == ids["reviewer"]
            assert row["version"] == body["plan"]["input_binding"]["expected_correction_versions"][row["correction_id"]] + 1
            assert row["listing_id"] == listing["listing_id"] and row["parser_run_id"] == parsed["parser_run_id"]
        if body["decision_type"] == "CREATE":
            assert listing["version"] == revision["revision_no"] == 1
            assert owner.graph._one("SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=?",
                (ids["tenant"], ids["listing"])) == original_listing
            prop = owner.graph._one("SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=?",
                (ids["tenant"], listing["property_id"]))
            assert prop["latitude"] is prop["longitude"] is None
            edges = owner.engine.query("SELECT * FROM identity.source_identity_edges WHERE tenant_id=? AND listing_id=?",
                (ids["tenant"], listing["listing_id"]))
            assert len(edges) == 1 and edges[0]["decision_id"] == receipt["decision_id"]
            assert edges[0]["source_id"] == intake["source_id"]
        else:
            assert listing["listing_id"] == ids["listing"] and listing["version"] == original_listing["version"] + 1
            assert revision["revision_no"] == 4 and revision["supersedes_revision_id"] == original_listing["current_revision_id"]
            assert owner.graph._one("SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=?",
                (ids["tenant"], ids["source"])) == original_property
            for old in prior_revisions:
                assert owner.graph._one("SELECT * FROM expansion.listing_revisions WHERE tenant_id=? AND listing_revision_id=?",
                    (ids["tenant"], old["listing_revision_id"])) == old
    assert owner.replay(parser_delivery) == {"terminal": True, "result": parsed}
    before = persisted(case)
    replay = _confirm(routine_confirmation)
    assert replay.status_code == 201 and replay.json() == receipt
    assert persisted(case) == before


@pytest.mark.parametrize("fault", ["source", "confirmer_role", "confirmer_disabled", "field_version",
    "intake_hold", "lease", "confirmation_proof", "late_audit"])
def test_listing_execution_rechecks_after_acceptance_and_rolls_back_every_write(routine_confirmation, monkeypatch, fault):
    from fastapi import HTTPException

    from modules.listing.application.identity_delivery import IdentityDeliveryAuthenticator
    from modules.listing.application.identity_execution import IdentityExecutionService
    from modules.listing.domain.identity_graph import IdentityConflictError
    from shared.audit.errors import CanonicalAuditUnavailable
    from tests.integration.test_canonical_identity_admission_postgresql import _claim
    from tests.integration.test_canonical_identity_cases_postgresql import _snapshot

    completed, _, _, _ = routine_confirmation
    case, _, _, parsed = completed
    owner, ids, database, _, _, _, _ = case
    canonical = (owner.graph, ids, None, database)
    approved = _confirm(routine_confirmation)
    assert approved.status_code == 201, approved.text
    delivery, _, _ = _claim(canonical, approved.json()["job_id"])
    owner.graph.delivery_authenticator = IdentityDeliveryAuthenticator(owner.engine)
    service = IdentityExecutionService(owner.graph)
    fence = service.begin(ids["tenant"], approved.json()["decision_id"], delivery)
    with database.connect() as connection:
        if fault == "source":
            connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id IN "
                "(SELECT source_id FROM intake.intakes WHERE intake_id=%s)", (parsed["intake_id"],))
        elif fault == "confirmer_role":
            connection.execute("UPDATE identity.account_roles SET role='expansion_user' WHERE account_id=%s", (ids["reviewer"],))
        elif fault == "confirmer_disabled":
            connection.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (ids["reviewer"],))
        elif fault == "field_version":
            connection.execute("UPDATE intake.human_corrections SET version=version+1 WHERE intake_id=%s", (parsed["intake_id"],))
        elif fault == "intake_hold":
            connection.execute("UPDATE intake.intakes SET legal_hold=true WHERE intake_id=%s", (parsed["intake_id"],))
        elif fault == "lease":
            connection.execute("UPDATE odp_runtime.durable_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                "WHERE job_id=%s", (delivery.job_id,))
        elif fault == "confirmation_proof":
            connection.execute("UPDATE identity.match_decisions SET after_graph=jsonb_set(after_graph,"
                "'{confirmation,audit_event_id}',to_jsonb(%s::text)) WHERE match_decision_id=%s",
                (str(uuid4()), approved.json()["decision_id"]))
    injected = []
    if fault == "late_audit":
        persist = owner.graph.audit_sink.persist
        def fail(audit, scope):
            if audit["action"] == "intake.resolved.v1":
                injected.append(audit["action"])
                raise CanonicalAuditUnavailable("Synthetic failure after Listing/revision/correction writes")
            return persist(audit, scope)
        monkeypatch.setattr(owner.graph.audit_sink, "persist", fail)
    before = persisted(case), _snapshot(canonical)
    expected = {"lease": JobFenceRejectedError, "late_audit": CanonicalAuditUnavailable,
        "confirmer_disabled": PermissionError, "confirmer_role": HTTPException}.get(fault, IdentityConflictError)
    with pytest.raises(expected) as error:
        owner.graph.execute(ids["tenant"], approved.json()["decision_id"], delivery, business_fence=fence)
    assert (persisted(case), _snapshot(canonical)) == before
    if fault == "confirmer_role":
        assert error.value.status_code == 403
    if fault == "late_audit":
        assert injected == ["intake.resolved.v1"]
        service.fail(ids["tenant"], approved.json()["decision_id"], delivery, business_fence=fence)
        with owner.graph.transaction(ids["tenant"]):
            decision = owner.graph._one("SELECT status FROM identity.match_decisions WHERE tenant_id=? AND match_decision_id=?",
                (ids["tenant"], approved.json()["decision_id"]))
            assert decision["status"] == "FAILED"
        after = persisted(case), _snapshot(canonical)
        for table in ("intake.intakes", "intake.human_corrections", "expansion.listings"):
            assert after[0][table] == before[0][table]
        for table in ("identity.properties", "identity.source_identity_edges", "expansion.listing_revisions"):
            assert after[1][table] == before[1][table]


@pytest.mark.parametrize("routine_confirmation", ["REVISE"], indirect=True)
@pytest.mark.parametrize("fault", ["listing_version", "property_hold"])
def test_revision_execution_rechecks_target_authority_after_acceptance(routine_confirmation, fault):
    from modules.listing.application.identity_delivery import IdentityDeliveryAuthenticator
    from modules.listing.application.identity_execution import IdentityExecutionService
    from modules.listing.domain.identity_graph import IdentityConflictError
    from tests.integration.test_canonical_identity_admission_postgresql import _claim
    from tests.integration.test_canonical_identity_cases_postgresql import _snapshot

    completed, _, _, _ = routine_confirmation
    case, _, _, _ = completed
    owner, ids, database, _, _, _, _ = case
    canonical = (owner.graph, ids, None, database)
    approved = _confirm(routine_confirmation)
    assert approved.status_code == 201, approved.text
    delivery, _, _ = _claim(canonical, approved.json()["job_id"])
    owner.graph.delivery_authenticator = IdentityDeliveryAuthenticator(owner.engine)
    fence = IdentityExecutionService(owner.graph).begin(ids["tenant"], approved.json()["decision_id"], delivery)
    with database.connect() as connection:
        if fault == "listing_version":
            connection.execute("UPDATE expansion.listings SET version=version+1 WHERE listing_id=%s", (ids["listing"],))
        else:
            connection.execute("INSERT INTO audit.legal_holds (tenant_id,subject_type,subject_id,reason,placed_by,approved_by) "
                "VALUES (%s,'property',%s,'Explicit synthetic property hold',%s,%s)",
                (ids["tenant"], ids["source"], ids["reviewer"], ids["actor"]))
    before = persisted(case), _snapshot(canonical)
    with pytest.raises(IdentityConflictError):
        owner.graph.execute(ids["tenant"], approved.json()["decision_id"], delivery, business_fence=fence)
    assert (persisted(case), _snapshot(canonical)) == before


@pytest.mark.parametrize("fault,expected", [("field_version", 409), ("output_hash", 409), ("clearance", 403),
    ("role", 403), ("account", 403), ("self_review", 409), ("source", 409), ("audit", 503)])
def test_routine_confirmation_rejects_unreviewable_or_stale_input_atomically(routine_confirmation, monkeypatch, fault, expected):
    from shared.audit.errors import CanonicalAuditUnavailable
    from tests.integration.test_canonical_identity_cases_postgresql import _snapshot

    completed, api, _, body = routine_confirmation
    case, _, _, result = completed
    owner, ids, database, _, _, _, _ = case
    with database.connect() as connection:
        if fault == "field_version":
            connection.execute("UPDATE intake.human_corrections SET version=version+1 WHERE intake_id=%s", (result["intake_id"],))
        elif fault == "output_hash":
            body["plan"]["input_binding"]["normalized_payload_sha256"] = "0" * 64
        elif fault == "clearance":
            connection.execute("UPDATE identity.account_scopes SET clearance='PUBLIC' WHERE account_id=%s", (ids["reviewer"],))
        elif fault == "role":
            connection.execute("UPDATE identity.account_roles SET role='expansion_user' WHERE account_id=%s", (ids["reviewer"],))
        elif fault == "account":
            connection.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (ids["reviewer"],))
        elif fault == "self_review":
            connection.execute("UPDATE identity.account_roles SET role='site_reviewer' WHERE account_id=%s", (ids["actor"],))
            api[1]["principal"] = replace(api[1]["principal"], subject_id=ids["actor"])
        elif fault == "source":
            connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id IN "
                "(SELECT source_id FROM intake.intakes WHERE intake_id=%s)", (result["intake_id"],))
    if fault == "audit":
        def fail(*args, **kwargs):
            raise CanonicalAuditUnavailable("Synthetic routine confirmation audit failure")
        monkeypatch.setattr(owner.graph.audit_sink, "persist", fail)
    canonical = (owner.graph, ids, None, database)
    before = persisted(case), _snapshot(canonical)
    refused = _confirm(routine_confirmation)
    assert refused.status_code == expected, refused.text
    assert (persisted(case), _snapshot(canonical)) == before


@pytest.mark.parametrize("revoked", ["source", "actor", "normalized_output"])
def test_listing_context_needs_fresh_authority_and_unchanged_parser_proof(completed_parser, case_api, revoked):
    case, delivery, _, result = completed_parser
    owner, ids, database, _, _, _, _ = case
    client, _, _ = case_api
    proposed = _human_new_case(completed_parser)
    path = f"/api/v1/match-cases/{proposed['match_case_id']}/identity-context"
    assert client.get(path).status_code == 200
    with database.connect() as connection:
        if revoked == "source":
            connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id IN "
                "(SELECT source_id FROM intake.intakes WHERE intake_id=%s)", (result["intake_id"],))
        elif revoked == "actor":
            connection.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (ids["actor"],))
        else:
            connection.execute("UPDATE intake.parser_runs SET normalized_payload=normalized_payload || '{\"rent\":1}'::jsonb "
                "WHERE parser_run_id=%s", (result["parser_run_id"],))
    if revoked != "normalized_output":
        assert owner.replay(delivery) == {"terminal": True, "result": result}
    before = persisted(case)
    response = client.get(path)
    assert response.status_code == 409, response.text
    assert persisted(case) == before


@pytest.mark.parametrize("parser_evidence_fields,status", [(None, "SUCCEEDED"), ("partial", "PARTIAL")],
    indirect=["parser_evidence_fields"])
def test_full_terminal_proof_survives_retired_settings_and_later_lifecycle(completed_parser, monkeypatch, status):
    case, delivery, _, result = completed_parser
    owner, ids, database, _, release, _, _ = case
    retire_all_positive_settings(monkeypatch)
    owner.artifact_registry = None
    with database.connect() as connection:
        assert connection.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s",
            (ids["actor"],)).rowcount == 1
        assert connection.execute("UPDATE intake.source_registry SET kill_switch=true,"
            "assisted_entry_approval_ref=NULL WHERE source_id LIKE 'native-parser-%%'").rowcount == 1
        assert connection.execute("UPDATE intake.parser_releases SET validation_status='DEPRECATED',"
            "deprecated_at=clock_timestamp(),version=version+1 WHERE parser_release_id=%s", (release,)).rowcount == 1
        # A later lifecycle is represented as a database fixture only; this
        # read neither implements nor authorizes the downstream transition.
        assert connection.execute("UPDATE intake.intakes SET processing_state='NEEDS_REVIEW',version=version+1 "
            "WHERE tenant_id=%s AND intake_id=%s", (ids["tenant"], result["intake_id"])).rowcount == 1
    previous = persisted(case)
    proof = owner.replay(delivery)
    assert proof == {"terminal": True, "result": result}
    assert result["parser_status"] == status
    assert persisted(case) == previous


def test_later_independent_review_keeps_original_parser_proposals_but_value_tampering_fails(completed_parser, monkeypatch):
    case, delivery, _, result = completed_parser
    owner, ids, database, _, _, _, _ = case
    retire_all_positive_settings(monkeypatch)
    with database.connect() as connection:
        changed = connection.execute("UPDATE intake.human_corrections SET status='REJECTED',reviewed_by=%s,"
            "reviewed_at=clock_timestamp(),version=version+1 WHERE tenant_id=%s AND intake_id=%s",
            (ids["reviewer"], ids["tenant"], result["intake_id"]))
        assert changed.rowcount > 0
    previous = persisted(case)
    assert NativeParserTerminalHistory(owner.graph).replay(delivery)["result"] == result
    assert persisted(case) == previous
    with database.connect() as connection:
        assert connection.execute("UPDATE intake.human_corrections SET corrected_value='999'::jsonb "
            "WHERE tenant_id=%s AND intake_id=%s AND field_path='rent'",
            (ids["tenant"], result["intake_id"])).rowcount == 1
    previous = persisted(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_TERMINAL_PROOF_REJECTED$"):
        NativeParserTerminalHistory(owner.graph).replay(delivery)
    assert persisted(case) == previous


@pytest.mark.parametrize("damage", ["physical_version", "business_version", "physical_status", "physical_token",
    "run_started", "run_completed", "normalized_output", "confidence", "journal", "reservation",
    "transition", "transition_sequence", "audit", "completed_event", "state_event", "parent_event",
    "event_metadata", "sensitive_metadata", "invented_retry", "quality_type", "terminal_receipt_type", "audit_quality_type"])
def test_status_labels_cannot_replace_complete_terminal_proof(completed_parser, monkeypatch, damage):
    case, delivery, _, result = completed_parser
    owner, ids, database, _, _, _, _ = case
    retire_all_positive_settings(monkeypatch)
    with database.connect() as connection:
        if damage == "physical_version":
            changed = connection.execute("UPDATE odp_runtime.durable_jobs SET version=version+1 WHERE job_id=%s", (delivery.job_id,))
        elif damage == "business_version":
            changed = connection.execute("UPDATE workflow.jobs SET version=version+1 WHERE tenant_id=%s AND job_id=%s",
                (ids["tenant"], delivery.job_id))
        elif damage == "physical_status":
            changed = connection.execute("UPDATE odp_runtime.durable_jobs SET status='failed' WHERE job_id=%s", (delivery.job_id,))
        elif damage == "physical_token":
            changed = connection.execute("UPDATE odp_runtime.durable_jobs SET payload_json=jsonb_set(payload_json,"
                "'{attempt_token}',to_jsonb(%s::text)) WHERE job_id=%s", ("1." + "f" * 64, delivery.job_id))
        elif damage in {"run_started", "run_completed"}:
            statement = ("UPDATE intake.parser_runs SET started_at=started_at-interval '1 second' WHERE parser_run_id=%s"
                if damage == "run_started" else
                "UPDATE intake.parser_runs SET completed_at=completed_at+interval '1 second' WHERE parser_run_id=%s")
            changed = connection.execute(statement, (result["parser_run_id"],))
        elif damage in {"normalized_output", "confidence"}:
            statement = ("UPDATE intake.parser_runs SET normalized_payload='{}'::jsonb WHERE parser_run_id=%s"
                if damage == "normalized_output" else
                "UPDATE intake.parser_runs SET field_confidence='{\"rent\":0.99}'::jsonb WHERE parser_run_id=%s")
            changed = connection.execute(statement, (result["parser_run_id"],))
        elif damage in {"journal", "reservation", "invented_retry"}:
            payload = connection.execute("SELECT payload FROM workflow.jobs WHERE tenant_id=%s AND job_id=%s",
                (ids["tenant"], delivery.job_id)).fetchone()[0]
            if damage == "journal":
                payload["attempt_intents"][-1]["process_state"] = "UNKNOWN"
            elif damage == "reservation":
                payload["attempt_intents"][0]["reservation"]["reservation_id"] = payload["attempt_intents"][1]["reservation"]["reservation_id"]
            else:
                payload["retry_receipt"] = {}
            changed = connection.execute("UPDATE workflow.jobs SET payload=%s::jsonb WHERE tenant_id=%s AND job_id=%s",
                (canonical_json(payload), ids["tenant"], delivery.job_id))
        elif damage in {"quality_type", "terminal_receipt_type", "audit_quality_type"}:
            current_result, payload = connection.execute("SELECT result,payload FROM workflow.jobs "
                "WHERE tenant_id=%s AND job_id=%s", (ids["tenant"], delivery.job_id)).fetchone()
            key = next(k for k, v in current_result["quality_summary"].items() if type(v) is int and v in {0, 1})
            substitute = bool(current_result["quality_summary"][key])
            if damage == "audit_quality_type":
                after = connection.execute("SELECT after_value FROM audit.audit_events WHERE tenant_id=%s "
                    "AND audit_event_id=%s", (ids["tenant"], result["parser_audit_event_id"])).fetchone()[0]
                after["quality_summary"][key] = substitute
                changed = connection.execute("UPDATE audit.audit_events SET after_value=%s::jsonb "
                    "WHERE tenant_id=%s AND audit_event_id=%s", (canonical_json(after), ids["tenant"], result["parser_audit_event_id"]))
            else:
                payload["terminal_receipt"]["quality_summary"][key] = substitute
                if damage == "quality_type":
                    current_result["quality_summary"][key] = substitute
                changed = connection.execute("UPDATE workflow.jobs SET result=%s::jsonb,payload=%s::jsonb "
                    "WHERE tenant_id=%s AND job_id=%s", (canonical_json(current_result), canonical_json(payload), ids["tenant"], delivery.job_id))
        elif damage == "transition":
            changed = connection.execute("UPDATE intake.intake_stage_transitions SET reason_text='changed' "
                "WHERE tenant_id=%s AND transition_id=%s", (ids["tenant"], result["transition_id"]))
        elif damage == "transition_sequence":
            changed = connection.execute("UPDATE intake.intake_stage_transitions SET sequence_no=sequence_no+100 "
                "WHERE tenant_id=%s AND transition_id=%s", (ids["tenant"], result["transition_id"]))
        elif damage == "audit":
            # Model missing canonical evidence without altering the WORM chain.
            changed = connection.execute("UPDATE audit.audit_events SET reason='changed' WHERE tenant_id=%s AND audit_event_id=%s",
                (ids["tenant"], result["parser_audit_event_id"]))
        elif damage == "sensitive_metadata":
            changed = connection.execute("UPDATE workflow.outbox_events SET sensitive_fields=ARRAY['payload.changed'] "
                "WHERE tenant_id=%s AND event_id=%s", (ids["tenant"], result["parser_completed_event_id"]))
        elif damage == "event_metadata":
            changed = connection.execute("UPDATE workflow.outbox_events SET causation_id=%s WHERE tenant_id=%s AND event_id=%s",
                (str(uuid4()), ids["tenant"], result["parser_completed_event_id"]))
        else:
            ident = (result["parser_completed_event_id"] if damage == "completed_event" else result["outbox_event_id"]
                if damage == "state_event" else case[3]["outbox_event_id"])
            changed = connection.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,'{producer}',"
                "'\"changed\"'::jsonb) WHERE tenant_id=%s AND event_id=%s", (ids["tenant"], ident))
        assert changed.rowcount == 1
    previous = persisted(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_TERMINAL_PROOF_REJECTED$"):
        NativeParserTerminalHistory(owner.graph).replay(delivery)
    assert persisted(case) == previous


def test_deep_parent_proof_retains_original_root_deadline(completed_parser, monkeypatch):
    case, delivery, _, _ = completed_parser
    owner, _, _, _, _, _, _ = case
    retire_all_positive_settings(monkeypatch)
    original = NativeAssistedEntryHistory.committed_result_with_command
    entered = []
    def expired(self, *args, **kwargs):
        before = kwargs["before"]
        assert isinstance(before, NativeParserCommandBudget)
        entered.append(before)
        before._monotonic_deadline = time.monotonic() - 1
        return original(self, *args, **kwargs)
    monkeypatch.setattr(NativeAssistedEntryHistory, "committed_result_with_command", expired)
    previous = persisted(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ROOT_DEADLINE_EXPIRED$"):
        NativeParserTerminalHistory(owner.graph).replay(delivery)
    assert len(entered) == 1 and persisted(case) == previous


def test_a_reintroduced_owned_storage_slot_prevents_terminal_proof_without_cleanup(completed_parser):
    case, delivery, _, _ = completed_parser
    owner, ids, database, _, _, _, _ = case
    with database.connect() as connection:
        payload = connection.execute("SELECT payload FROM workflow.jobs WHERE tenant_id=%s AND job_id=%s",
            (ids["tenant"], delivery.job_id)).fetchone()[0]
        reservation = payload["attempt_intents"][0]["reservation"]
        assert connection.execute("INSERT INTO intake.native_retrieval_inflight_slots "
            "(reservation_id,source_id,admitted_at,expires_at) "
            "VALUES (%s,%s,clock_timestamp(),clock_timestamp()+interval '1 hour')",
            (reservation["reservation_id"], reservation["source_id"])).rowcount == 1
        retained = connection.execute("SELECT * FROM intake.native_retrieval_inflight_slots "
            "WHERE reservation_id=%s", (reservation["reservation_id"],)).fetchone()
    previous = persisted(case)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_TERMINAL_PROOF_REJECTED$"):
        NativeParserTerminalHistory(owner.graph).replay(delivery)
    assert persisted(case) == previous
    with database.connect() as connection:
        assert connection.execute("SELECT * FROM intake.native_retrieval_inflight_slots "
            "WHERE reservation_id=%s", (reservation["reservation_id"],)).fetchone() == retained


@pytest.mark.parametrize("root", ["absent", "foreign_tenant"])
def test_terminal_reader_cannot_borrow_an_absent_or_foreign_root(completed_parser, root):
    case, delivery, _, _ = completed_parser
    owner, ids, _, _, _, _, _ = case
    reader = NativeParserTerminalHistory(owner.graph)
    previous = persisted(case)
    if root == "absent":
        with owner.graph.transaction(ids["tenant"]):
            before = NativeParserCommandBudget(owner.clock)
        with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ADMISSION_PROOF_REJECTED$"):
            reader.read(delivery, ids["tenant"], 1, before=before)
    else:
        with owner.graph.transaction(str(uuid4())):
            before = NativeParserCommandBudget(owner.clock)
            with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_ADMISSION_PROOF_REJECTED$"):
                reader.read(delivery, ids["tenant"], 1, before=before)
    assert persisted(case) == previous


@pytest.mark.parametrize("phase", ["QUEUED", "RUNNING"])
def test_genuine_unfinished_initial_command_returns_no_terminal_result(parser_admission_case, monkeypatch, phase):
    case = parser_admission_case
    owner, _, _, _, _, _, _ = case
    if phase == "QUEUED":
        receipt = admit(case)
        delivery = owner.queue.get(receipt["job_id"])
    else:
        _, delivery = claimed(case)
        owner._accept(delivery, case[1]["tenant"], 1)
    retire_all_positive_settings(monkeypatch)
    previous = persisted(case)
    assert NativeParserTerminalHistory(owner.graph).replay(delivery) == {"terminal": False, "result": None}
    assert persisted(case) == previous


@pytest.mark.parametrize("extra_state", ["FINISHED", "ERROR"])
def test_extra_unproved_prior_operations_cannot_be_ignored_by_positive_writer(parser_io_case, extra_state):
    case, io, leases, delivery = parser_io_case
    owner, ids, _, _, _, _, _ = case
    observed = io.read_and_parse()
    with owner.graph.transaction(ids["tenant"]):
        payload = owner.graph._one("SELECT payload FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], delivery.job_id))["payload"]
        prior = copy.deepcopy(payload["attempt_intents"][0])
        prior.update(intent_id=str(uuid4()), delivery_generation=0, attempt=0, state=extra_state, process_state="UNKNOWN")
        payload["attempt_intents"].insert(0, prior)
        assert owner.engine.execute("UPDATE workflow.jobs SET payload=?::jsonb WHERE tenant_id=? AND job_id=?",
            (canonical_json(payload), ids["tenant"], delivery.job_id)).rowcount == 1
    previous = persisted(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_OUTPUT_JOURNAL_REJECTED$"):
        commit_parser_output(owner, delivery, leases, observed)
    assert persisted(case) == previous
