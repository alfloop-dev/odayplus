"""Actual service authentication, SQL approval, parser proof and shared matcher.

All accounts, policies, tokens and sources are isolated engineering fixtures;
none represents a real source-use approval, reviewer or release activation.
"""
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from modules.external_data.application.assisted_intake import content_fingerprint
from modules.listing.application.intake_workflow_bootstrap import IntakeWorkflowUnavailable
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from modules.listing.domain.identity_graph import IdentityConflictError
from modules.opsboard.auth import AuthenticationBoundary, Credentials
from modules.opsboard.auth.config import AuthBoundaryConfig
from modules.opsboard.auth.service_identity import ServiceIdentity, ServiceIdentityVerifier
from shared.auth import DataClassification, Scope
from shared.domain.events import validate_event
from shared.infrastructure.persistence.identity_cases_sql import CanonicalMatchCaseRepository
from tests.integration.test_canonical_identity_cases_postgresql import _snapshot
from tests.integration.test_canonical_identity_cases_postgresql import case_api as case_api
from tests.integration.test_native_intake_parser_admission_postgresql import BODY
from tests.integration.test_native_intake_parser_history_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    completed_parser as completed_parser,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    parser_io_case as parser_io_case,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_parser_results_postgresql import persisted

pytestmark = pytest.mark.requires_live_env
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def intake_db(intake_blank_db):
    """Use the actual complete registry/Core/Intake migration chain."""
    config = Config(str(ROOT / "infra/db/migrations/alembic.ini"))
    config.set_main_option("sqlalchemy.url", intake_blank_db.url(driver="psycopg").replace("%", "%%"))
    command.upgrade(config, "0029")
    with intake_blank_db.connect() as connection:
        connection.execute((ROOT / "infra/db/migrations/assisted_listing_intake/000_canonical_compatibility.sql").read_text())
    intake_blank_db.apply_migration()
    return intake_blank_db


@pytest.fixture
def matching_case(completed_parser, case_api, request):
    case, _, _, parsed = completed_parser
    owner, ids, database, _, _, _, _ = case
    options = getattr(request, "param", "NEW")
    options = options if isinstance(options, dict) else {"mode": options}
    mode = options.get("mode", "NEW")
    with database.connect() as connection:
        values = dict(connection.execute("SELECT field_path,corrected_value FROM intake.human_corrections "
            "WHERE intake_id=%s", (parsed["intake_id"],)).fetchall())
    if mode == "NEW":
        values.update(address="高雄市合成測試路999號", rent=1.0, areaPing=1.0, floor="B99")
    elif mode == "REVISION":
        values["rent"] += 1
    revision_id = str(uuid4())
    with database.connect() as connection:
        source_id = connection.execute("SELECT source_id FROM intake.intakes WHERE intake_id=%s", (parsed["intake_id"],)).fetchone()[0]
        if mode in {"REVISION", "EXACT_DUPLICATE"}:
            connection.execute("UPDATE expansion.listings SET source_id=%s,canonical_url_sha256="
                "(SELECT canonical_url_sha256 FROM intake.intakes WHERE intake_id=%s) WHERE listing_id=%s",
                (source_id, parsed["intake_id"], ids["listing"]))
            connection.execute("UPDATE identity.source_identity_edges SET source_id=%s WHERE listing_id=%s", (source_id, ids["listing"]))
        corpus_source = source_id if mode in {"REVISION", "EXACT_DUPLICATE"} else "synthetic-feed"
        # The older identity fixture deliberately leaves its source killed.
        # This isolated processing fixture explicitly enables that source;
        # the separate negative test proves approval cannot override its kill.
        connection.execute("UPDATE intake.source_registry SET kill_switch=false WHERE source_id=%s", (corpus_source,))
        connection.execute("INSERT INTO expansion.listing_revisions (listing_revision_id,tenant_id,listing_id,revision_no,"
            "revision_kind,normalized_values,effective_values,material_fingerprint) "
            "VALUES (%s,%s,%s,1,'CREATED',%s::jsonb,%s::jsonb,%s)",
            (revision_id, ids["tenant"], ids["listing"], canonical_json(values), canonical_json(values), content_fingerprint(values)))
        connection.execute("UPDATE expansion.listings SET current_revision_id=%s WHERE listing_id=%s", (revision_id, ids["listing"]))
        policy_id = "synthetic-native-matching-v1:" + ids["tenant"]
        params = {"schema_version": 1, "enabled": True, "service_subjects": ["service:synthetic-native-matcher"],
            "source_ids": [source_id], "corpus_source_ids": [corpus_source], "max_corpus_rows": 100}
        params.update(options.get("policy_changes", {}))
        owner_role = options.get("owner_role", "data_owner")
        if owner_role != "data_owner":
            connection.execute("UPDATE identity.account_roles SET role=%s WHERE account_id=%s", (owner_role, ids["actor"]))
        if options.get("extra_corpus"):
            extra_listing, extra_revision = str(uuid4()), str(uuid4())
            extra_key = "synthetic-bound-extra"
            if options.get("ambiguous_identity"):
                extra_key = values["providerListingId"]
                assert extra_key
                connection.execute("UPDATE expansion.listings SET source_listing_id='synthetic-other-provider' WHERE listing_id=%s",
                    (ids["listing"],))
                connection.execute("UPDATE identity.source_identity_edges SET source_entity_id='synthetic-other-provider' "
                    "WHERE listing_id=%s", (ids["listing"],))
                ids["selected_revision_listing"] = extra_listing
            connection.execute("INSERT INTO expansion.listings (listing_id,tenant_id,property_id,source_id,source_listing_id,"
                "lifecycle_state) VALUES (%s,%s,%s,%s,%s,'ACTIVE')",
                (extra_listing, ids["tenant"], ids["target"], corpus_source, extra_key))
            connection.execute("INSERT INTO expansion.listing_revisions (listing_revision_id,tenant_id,listing_id,revision_no,"
                "revision_kind,normalized_values,effective_values,material_fingerprint) "
                "VALUES (%s,%s,%s,1,'CREATED',%s::jsonb,%s::jsonb,%s)", (extra_revision, ids["tenant"], extra_listing,
                canonical_json(values), canonical_json(values), content_fingerprint(values)))
            connection.execute("UPDATE expansion.listings SET current_revision_id=%s WHERE listing_id=%s", (extra_revision, extra_listing))
            if options.get("ambiguous_identity"):
                connection.execute("INSERT INTO identity.source_identity_edges (edge_id,tenant_id,source_id,source_entity_id,"
                    "listing_id,property_id,match_strategy,confidence,effective_from) "
                    "SELECT %s,%s,%s,%s,%s,%s,match_strategy,confidence,clock_timestamp() "
                    "FROM identity.source_identity_edges WHERE tenant_id=%s AND listing_id=%s LIMIT 1",
                    (str(uuid4()), ids["tenant"], corpus_source, extra_key, extra_listing, ids["target"], ids["tenant"], ids["listing"]))
        if options.get("split_graph"):
            # Real corpus/edges on all observed roots, with two source edges;
            # the same matcher must expose the split's complete planning closure.
            for root in ("source", "split_target"):
                listing, revision, edge = (str(uuid4()) for _ in range(3))
                key = "synthetic-split-" + root
                connection.execute("INSERT INTO expansion.listings (listing_id,tenant_id,property_id,source_id,source_listing_id,"
                    "lifecycle_state,current_revision_id) VALUES (%s,%s,%s,%s,%s,'ACTIVE',NULL)",
                    (listing, ids["tenant"], ids[root], corpus_source, key))
                connection.execute("INSERT INTO expansion.listing_revisions (listing_revision_id,tenant_id,listing_id,revision_no,"
                    "revision_kind,normalized_values,effective_values,material_fingerprint) "
                    "VALUES (%s,%s,%s,1,'CREATED',%s::jsonb,%s::jsonb,%s)",
                    (revision, ids["tenant"], listing, canonical_json(values), canonical_json(values), content_fingerprint(values)))
                connection.execute("UPDATE expansion.listings SET current_revision_id=%s WHERE listing_id=%s", (revision, listing))
                connection.execute("INSERT INTO identity.source_identity_edges (edge_id,tenant_id,source_id,source_entity_id,"
                    "listing_id,property_id,match_strategy,confidence,effective_from) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'HUMAN',0.75,clock_timestamp()-interval '1 hour')",
                    (edge, ids["tenant"], corpus_source, key, listing, ids[root]))
        connection.execute("INSERT INTO workflow.decision_policies (policy_version_id,policy_label,policy_id,policy_version,"
            "policy_kind,tenant_id,effective_from,effective_to,owner_role,approved_by,approved_at,input_contract,output_contract,"
            "change_reason,parameters,declared_inputs) VALUES (%s,'synthetic-native-matching-v1','synthetic-native-matching','1',"
            "'native_matching_proposal_authority',%s,clock_timestamp()-interval '1 second',NULL,"
            "%s,%s,clock_timestamp(),'SyntheticMatchingInputV1','SyntheticMatchCaseReceiptV1',"
            "'SYNTHETIC_ENGINEERING_ONLY_NOT_FORMAL_APPROVAL',%s::jsonb,%s)",
            (policy_id, ids["tenant"], owner_role, ids["actor"], canonical_json(params),
             ["scope", "service_principal", "source_id", "parser_result", "matching_corpus"]))
    identity = ServiceIdentity("synthetic-native-matcher", b"Explicit synthetic matcher secret only",
        scope=Scope(tenant_id=ids["tenant"], modules=frozenset({"listing"}),
            clearance=DataClassification[options.get("service_clearance", "INTERNAL")]))
    boundary = AuthenticationBoundary(AuthBoundaryConfig(), service_verifier=ServiceIdentityVerifier({identity.service_id: identity}))
    credentials = Credentials(service_id=identity.service_id, service_secret=identity.secret)
    repo = CanonicalMatchCaseRepository(owner.graph, replay_store=case_api[2])
    return case, parsed, repo, boundary, credentials, policy_id, options.get("decision_type", mode)


def _match(fixture, **kwargs):
    case, parsed, repo, boundary, credentials, policy_id, _ = fixture
    return repo.match_parser_result(case[1]["tenant"], parsed["intake_id"], expected_intake_version=parsed["version"],
        policy_version_id=policy_id, boundary=boundary, credentials=kwargs.get("credentials", credentials))


@pytest.mark.parametrize("matching_case", ["POSSIBLE_MATCH"], indirect=True)
@pytest.mark.parametrize("clearance", [DataClassification.INTERNAL, DataClassification.PUBLIC])
def test_possible_review_reads_original_parser_proposals_through_real_successor(matching_case, case_api, clearance):
    case, parsed, _, _, _, _, _ = matching_case
    owner, ids = case[:2]
    proposal = _match(matching_case)
    client, caller, _ = case_api
    caller["principal"] = replace(caller["principal"], scope=replace(caller["principal"].scope, clearance=clearance))
    before = _snapshot((owner.graph, ids, case[2], None))
    response = client.get(f"/api/v1/match-cases/{proposal['match_case_id']}/identity-context?include_listing_input=true")
    if response.status_code != 200:
        # Retain the real owner's traceback when the controller safely returns
        # a class-only conflict, without exposing input values in diagnostics.
        from shared.infrastructure.persistence.identity_decisions_sql import (
            CanonicalIdentityDecisionRepository,
        )

        CanonicalIdentityDecisionRepository(owner.graph).planning_context(ids["tenant"], proposal["match_case_id"],
            expected_case_version=proposal["version"], include_listing_input=True)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intake_version"] == parsed["version"] + 1
    assert body["listing_input"]["parser_run_id"] == parsed["parser_run_id"]
    fields = body["listing_input"]["fields"]
    address = next(field for field in fields if field["field_path"] == "address")
    assert address["classification"] == "INTERNAL"
    assert address.get("masked", False) is (clearance == DataClassification.PUBLIC)
    for field in fields:
        assert field["status"] == "PROPOSED"
        if field.get("masked", False):
            assert all(field[key] is None for key in ("parsed", "normalized", "corrected"))
    after = _snapshot((owner.graph, ids, case[2], None))
    # Access audit may be appended; facts, versions and parser results cannot change.
    for table in before.keys() - {"audit.audit_events"}:
        assert after[table] == before[table], table
    parser_job = next(row for row in after["workflow.jobs"] if row["job_id"] == body["listing_input"]["parser_job_id"])
    result = json.loads(parser_job["result"]) if isinstance(parser_job["result"], str) else parser_job["result"]
    assert result["version"] == parsed["version"] and result["processing_state"] == "MATCHING"


@pytest.mark.parametrize("matching_case", ["POSSIBLE_MATCH"], indirect=True)
@pytest.mark.parametrize("tamper", ["transition", "sequence", "audit", "outbox", "event_actor", "parser_version", "source_grant"])
def test_possible_review_cannot_replace_missing_provenance_or_current_authority(matching_case, case_api, monkeypatch, tamper):
    case, parsed, _, _, _, _, _ = matching_case
    owner, ids, database = case[:3]
    proposal = _match(matching_case)
    with database.connect() as connection:
        if tamper == "transition":
            connection.execute("DELETE FROM intake.intake_stage_transitions WHERE match_case_id=%s", (proposal["match_case_id"],))
        elif tamper == "sequence":
            connection.execute("UPDATE intake.intake_stage_transitions SET sequence_no=sequence_no+100 WHERE match_case_id=%s",
                (proposal["match_case_id"],))
        elif tamper == "outbox":
            connection.execute("DELETE FROM workflow.outbox_events WHERE aggregate_id=%s AND aggregate_version=%s "
                "AND event_type='intake.state_changed'", (parsed["intake_id"], parsed["version"] + 1))
        elif tamper == "event_actor":
            connection.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,'{actor_ref}',to_jsonb('service:forged'::text)) "
                "WHERE aggregate_id=%s AND aggregate_version=%s AND event_type='intake.state_changed'",
                (parsed["intake_id"], parsed["version"] + 1))
        elif tamper == "parser_version":
            connection.execute("UPDATE workflow.jobs SET result=jsonb_set(result,'{version}',to_jsonb(%s::bigint)) "
                "WHERE aggregate_type='parser_run' AND aggregate_id=%s", (parsed["version"] + 1, parsed["parser_run_id"]))
        elif tamper == "source_grant":
            connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id="
                "(SELECT source_id FROM intake.intakes WHERE intake_id=%s)", (parsed["intake_id"],))
    if tamper == "audit":
        original = owner.graph.audit_sink.writer.get_event

        def unavailable(tenant, audit_id):
            event = original(tenant, audit_id)
            return None if event and event["action"] == "intake.review_required.v1" else event

        monkeypatch.setattr(owner.graph.audit_sink.writer, "get_event", unavailable)
    before = _snapshot((owner.graph, ids, database, None))
    response = case_api[0].get(f"/api/v1/match-cases/{proposal['match_case_id']}/identity-context?include_listing_input=true")
    assert response.status_code == 409, response.text
    assert "listing_input" not in response.json()
    after = _snapshot((owner.graph, ids, database, None))
    for table in before.keys() - {"audit.audit_events"}:
        assert after[table] == before[table], table


@pytest.mark.parametrize("matching_case", ["NEW", "REVISION", "POSSIBLE_MATCH", "EXACT_DUPLICATE"], indirect=True)
def test_native_shared_matcher_proposes_with_real_service_and_current_sql_authority(matching_case):
    case, parsed, repo, _, _, _, mode = matching_case
    ids = case[1]
    canonical = repo.graph, ids, None, case[2]
    before, fields_before = _snapshot(canonical), persisted(case)
    receipt = _match(matching_case)
    assert receipt["outcome"] == mode
    assert receipt["status"] == ("PENDING_REVIEW" if mode == "POSSIBLE_MATCH" else "PROPOSED")
    with repo.graph.transaction(ids["tenant"]):
        stored = repo.get_for_intake(ids["tenant"], parsed["intake_id"])
        assert stored["match_case"]["proposed_by"] == "service:synthetic-native-matcher"
        assert stored["match_case"]["supporting_signals"][0]["kind"] == "NATIVE_SHARED_MATCHER_PROPOSAL"
        audit = repo.graph.audit_sink.writer.get_event(ids["tenant"], receipt["audit_event_id"])
        assert audit["service_principal"] == "service:synthetic-native-matcher" and audit["actor_subject_id"] is None
    after, fields_after = _snapshot(canonical), persisted(case)
    for table in ("identity.match_decisions", "identity.properties", "identity.source_identity_edges",
            "expansion.listings", "expansion.listing_revisions", "workflow.jobs"):
        assert after[table] == before[table], table
    assert fields_after["intake.human_corrections"] == fields_before["intake.human_corrections"]
    replay = _match(matching_case)
    assert replay == receipt
    assert (_snapshot(canonical), persisted(case)) == (after, fields_after)


@pytest.mark.parametrize("revocation", ["credential", "owner_role", "owner_scope", "approval_window"])
def test_service_and_approval_revocation_cannot_use_a_cached_proposal(matching_case, revocation):
    case, _, repo, _, credentials, policy_id, _ = matching_case
    _match(matching_case)
    ids, database = case[1], case[2]
    with database.connect() as connection:
        if revocation == "owner_role":
            connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (ids["actor"],))
        elif revocation == "owner_scope":
            connection.execute("UPDATE identity.account_scopes SET modules='[\"priceops\"]'::jsonb WHERE account_id=%s", (ids["actor"],))
        elif revocation == "approval_window":
            connection.execute("UPDATE workflow.decision_policies SET effective_to=clock_timestamp() WHERE policy_version_id=%s", (policy_id,))
    before = _snapshot((repo.graph, ids, None, database)), persisted(case)
    expected = PermissionError if revocation == "credential" else IntakeWorkflowUnavailable
    with pytest.raises(expected):
        _match(matching_case, credentials=replace(credentials, service_secret=b"wrong") if revocation == "credential" else credentials)
    assert (_snapshot((repo.graph, ids, None, database)), persisted(case)) == before


def test_matching_approval_cannot_override_a_corpus_source_kill_switch(matching_case):
    case, _, repo, _, _, _, _ = matching_case
    ids, database = case[1], case[2]
    with database.connect() as connection:
        connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id='synthetic-feed'")
    before = _snapshot((repo.graph, ids, None, database)), persisted(case)
    with pytest.raises(PermissionError, match="MATCHING_CORPUS_SOURCE_DISABLED"):
        _match(matching_case)
    assert (_snapshot((repo.graph, ids, None, database)), persisted(case)) == before


@pytest.mark.parametrize("parser_admission_case", [{"fields": {**BODY["fields"], "providerListingId": "synthetic-bound-extra"}}], indirect=True)
@pytest.mark.parametrize("matching_case", [{"mode": "EXACT_DUPLICATE", "extra_corpus": True}], indirect=True)
def test_ambiguous_source_identity_preserves_all_native_candidates_for_human_review(matching_case):
    case, parsed, repo, _, _, _, _ = matching_case
    canonical = repo.graph, case[1], None, case[2]
    before, fields_before = _snapshot(canonical), persisted(case)
    receipt = _match(matching_case)
    assert receipt["outcome"] == "POSSIBLE_MATCH" and receipt["status"] == "PENDING_REVIEW"
    with repo.graph.transaction(case[1]["tenant"]):
        stored = repo.get_for_intake(case[1]["tenant"], parsed["intake_id"])
        listings = repo.engine.query("SELECT listing_id FROM expansion.listings WHERE tenant_id=?", (case[1]["tenant"],))
        assert {r["listing_id"] for r in stored["candidates"]} == {r["listing_id"] for r in listings}
        assert stored["intake"]["processing_state"] == "NEEDS_REVIEW"
    after, fields_after = _snapshot(canonical), persisted(case)
    for table in ("identity.match_decisions", "identity.properties", "identity.source_identity_edges",
            "expansion.listings", "expansion.listing_revisions", "workflow.jobs"):
        assert after[table] == before[table], table
    assert fields_after["intake.human_corrections"] == fields_before["intake.human_corrections"]


@pytest.mark.parametrize("parser_admission_case", [{"fields": {**BODY["fields"], "address": "臺北市合成路１２３號"}}], indirect=True)
@pytest.mark.parametrize("matching_case", ["EXACT_DUPLICATE"], indirect=True)
def test_matching_preserves_unchanged_proposed_address_across_parser_normalization(matching_case):
    case, parsed, repo, _, _, _, _ = matching_case
    with case[2].connect() as connection:
        normalized = connection.execute("SELECT normalized_payload->>'address' FROM intake.parser_runs "
            "WHERE parser_run_id=%s", (parsed["parser_run_id"],)).fetchone()[0]
    assert normalized != "臺北市合成路１２３號"
    assert _match(matching_case)["outcome"] == "EXACT_DUPLICATE"


def test_matching_rejects_later_field_rejection_but_keeps_historical_parser_proof(matching_case, completed_parser):
    case, parsed, repo, _, _, _, _ = matching_case
    ids, database = case[1], case[2]
    with database.connect() as connection:
        connection.execute("UPDATE intake.human_corrections SET status='REJECTED',version=version+1,"
            "reviewed_by=%s,reviewed_at=clock_timestamp() WHERE intake_id=%s", (ids["reviewer"], parsed["intake_id"]))
    assert case[0].replay(completed_parser[1]) == {"terminal": True, "result": parsed}
    before = _snapshot((repo.graph, ids, None, database)), persisted(case)
    with pytest.raises(IdentityConflictError, match="IDENTITY_LISTING_INPUT_VERSION_CONFLICT"):
        _match(matching_case)
    assert (_snapshot((repo.graph, ids, None, database)), persisted(case)) == before


@pytest.mark.parametrize("matching_case,error,message", [
    ({"policy_changes": {"corpus_source_ids": []}}, PermissionError, "MATCHING_CORPUS_SOURCE_COVERAGE_REQUIRED"),
    ({"policy_changes": {"service_subjects": ["service:other-synthetic-service"]}}, PermissionError, "MATCHING_SERVICE_OR_SOURCE_SCOPE_DENIED"),
    ({"owner_role": "site_reviewer"}, PermissionError, "MATCHING_DATA_OWNER_PURPOSE_APPROVAL_REQUIRED"),
    ({"policy_changes": {"max_corpus_rows": 1}, "extra_corpus": True}, IdentityConflictError, "MATCHING_COMPLETE_CORPUS_BOUND_EXCEEDED"),
    ({"service_clearance": "PUBLIC"}, PermissionError, "MATCHING_SERVICE_OR_SOURCE_SCOPE_DENIED"),
], indirect=["matching_case"])
def test_matching_requires_actual_purpose_owner_service_and_complete_readable_corpus(matching_case, error, message):
    case, _, repo, _, _, _, _ = matching_case
    canonical = repo.graph, case[1], None, case[2]
    before = _snapshot(canonical), persisted(case)
    with pytest.raises(error, match=message):
        _match(matching_case)
    assert (_snapshot(canonical), persisted(case)) == before


def _add_sql_manager(database, tenant, account):
    """Real isolated SQL accounts, never a role assertion or formal approval."""
    with database.connect() as connection:
        connection.execute("INSERT INTO identity.accounts (account_id,tenant_id,username,email,status,created_by) "
            "VALUES (%s,%s,%s,%s,'active','synthetic-fixture')",
            (account, tenant, 'synthetic-manager-' + account, account + '@example.invalid'))
        connection.execute("INSERT INTO identity.account_roles (account_id,role,granted_by) "
            "VALUES (%s,'site_reviewer','synthetic-fixture')", (account,))
        connection.execute("INSERT INTO identity.account_scopes (account_id,modules,clearance) "
            "VALUES (%s,'[\"listing\"]'::jsonb,'CONFIDENTIAL')", (account,))


DUPLICATE_CASE = {"mode": "EXACT_DUPLICATE", "extra_corpus": True, "ambiguous_identity": True,
    "decision_type": "DUPLICATE"}
DUPLICATE_INPUT = {"fields": {**BODY["fields"], "providerListingId": "synthetic-review-source-key"}}


def _assert_denied_business_unchanged(before, after):
    for table in before[0].keys() - {"audit.audit_events"}:
        assert after[0][table] == before[0][table], table
    for table in before[1]:
        if not table.startswith("audit."):
            assert after[1][table] == before[1][table], table


@pytest.mark.parametrize("matching_case", ["NEW"], indirect=True)
@pytest.mark.parametrize("parser_admission_case,manager_region_id,create_case", [
    pytest.param(None, None, False, id="UNSCOPED"),
    pytest.param({"scope": {"region_id": "0ff12f83-5eb6-4e63-a91c-6466906f8ce0"}},
        "0ff12f83-5eb6-4e63-a91c-6466906f8ce0", False, id="REGION"),
    pytest.param(None, None, True, id="CASE_CONTEXT"),
], indirect=["parser_admission_case"])
def test_native_browser_password_session_and_registered_worker_read_real_persisted_result(
        matching_case, canonical_graph, case_api, monkeypatch, tmp_path, manager_region_id, create_case):
    from modules.listing.application.identity_delivery import (
        IDENTITY_POLICY_ENV,
        IDENTITY_SIGNING_KEY_ENV,
    )
    from shared.jobs.queue import JobStatus
    from tests.integration.test_assisted_listing_identity import _execute
    from tests.support.intake_score_synthetic import run_native_identity_browser

    case, parsed, _, _, _, _, _ = matching_case
    ids, database = case[1], case[2]
    # Finish the fixture's older already-accepted internal graph delivery via
    # its owner, then acknowledge that real committed result under its fence.
    # It is not a signed runtime job and must not be left for the browser worker.
    previous = _execute(canonical_graph)
    current = canonical_graph[0].queue.get(previous["job_id"])
    canonical_graph[0].queue.update_status(current.job_id, JobStatus.SUCCEEDED,
        expected_version=current.version, fence_token=current.fence_token)
    proposed = None if create_case else _match(matching_case)
    _add_sql_manager(database, ids["tenant"], ids["reviewer"])
    independent_reviewer = str(uuid4()) if create_case else None
    if independent_reviewer:
        _add_sql_manager(database, ids["tenant"], independent_reviewer)
    monkeypatch.setenv(IDENTITY_SIGNING_KEY_ENV, "Owned synthetic browser execution signing material only")
    monkeypatch.setenv(IDENTITY_POLICY_ENV, canonical_json({"policy_id": "owned-native-browser-identity", "version": 1,
        "max_attempts": 3, "deadline_seconds": 120, "backoff_seconds": [1, 2]}))
    evidence = run_native_identity_browser(database, ids, parsed, proposed, tmp_path / "native-browser", monkeypatch,
        worm_sink_uri=canonical_graph[0].audit_sink.delivery.sink.sink_id, manager_region_id=manager_region_id,
        independent_reviewer=independent_reviewer)
    assert evidence["browser_returncode"] == 0 and evidence["active_real_sessions"] > 0
    import os

    if path := os.environ.get("ODP_NATIVE_BROWSER_EVIDENCE_PATH"):
        destination = Path(path)
        assert not destination.exists()
        destination.write_text(json.dumps({**evidence, "CODE_approval": False, "release_qualified": False}, indent=2) + "\n")


@pytest.mark.parametrize(("matching_case", "parser_admission_case", "confirmation_change"), [
    pytest.param("NEW", None, None, id="NEW"),
    pytest.param("REVISION", None, None, id="REVISION"),
    pytest.param("POSSIBLE_MATCH", None, None, id="POSSIBLE_NEW"),
    pytest.param("POSSIBLE_MATCH", None, "manual_context", id="SERVICE_CASE_MANUAL_CONTEXT_CREATE"),
    pytest.param({"mode": "REVISION", "extra_corpus": True, "ambiguous_identity": True},
        {"fields": {**BODY["fields"], "providerListingId": "synthetic-review-source-key"}}, None, id="POSSIBLE_REVISION"),
    pytest.param("POSSIBLE_MATCH", None, "missing_versions", id="POSSIBLE_NEW_MISSING_VERSIONS"),
    pytest.param("POSSIBLE_MATCH", None, "stale_versions", id="POSSIBLE_NEW_STALE_VERSIONS"),
    pytest.param("POSSIBLE_MATCH", None, "unrelated_property", id="POSSIBLE_NEW_UNRELATED_PROPERTY"),
    pytest.param("POSSIBLE_MATCH", None, "revoked_confirmer", id="POSSIBLE_REVOKED_CONFIRMER"),
    pytest.param("POSSIBLE_MATCH", None, "post_stale_version", id="POSSIBLE_NEW_POST_APPROVAL_VERSION"),
    pytest.param("POSSIBLE_MATCH", None, "post_revoked_confirmer", id="POSSIBLE_POST_APPROVAL_ROLE"),
    pytest.param({"mode": "REVISION", "extra_corpus": True, "ambiguous_identity": True},
        {"fields": {**BODY["fields"], "providerListingId": "synthetic-review-source-key"}}, "unrelated_listing",
        id="POSSIBLE_REVISION_UNRELATED_LISTING"),
    pytest.param(DUPLICATE_CASE, DUPLICATE_INPUT, None, id="POSSIBLE_DUPLICATE"),
    *[pytest.param({**DUPLICATE_CASE, "mode": "REVISION" if kind == "REVISION" else "EXACT_DUPLICATE",
        "decision_type": kind, "split_graph": repair == "split"},
        DUPLICATE_INPUT, "after_" + repair, id="AFTER_" + repair.upper() + "_" + kind)
      for repair in ("merge", "split") for kind in ("REVISION", "DUPLICATE")],
    *[pytest.param({"mode": "POSSIBLE_MATCH", "extra_corpus": True, "split_graph": repair == "split"},
        DUPLICATE_INPUT, "after_" + repair, id="AFTER_" + repair.upper() + "_CREATE")
      for repair in ("merge", "split")],
    *[pytest.param({**DUPLICATE_CASE, "mode": "REVISION", "decision_type": "REVISION"}, DUPLICATE_INPUT,
        "after_merge:" + change, id="AFTER_MERGE_DENY_" + change.upper())
      for change in ("graph_event", "graph_audit", "graph_result", "graph_transport", "graph_case_version",
          "graph_reviewer", "source_grant", "revoked_confirmer", "stale_versions")],
    *[pytest.param(DUPLICATE_CASE, DUPLICATE_INPUT, change, id="POSSIBLE_DUPLICATE_" + label)
      for change, label in [
          ("dup_self_review", "SELF_REVIEW"), ("dup_reviewer_role", "REVIEWER_ROLE"),
          ("dup_reviewer_mask", "REVIEWER_MASK"), ("dup_reviewer_steward", "STEWARD_APPROVAL"),
          ("dup_source_revoke", "SOURCE_GRANT"), ("dup_service_authority", "SERVICE_AUTHORITY"),
          ("dup_post_proposer_role", "POST_PROPOSER_ROLE"), ("dup_post_reviewer_role", "POST_REVIEWER_ROLE"),
          ("dup_post_reviewer_scope", "POST_REVIEWER_SCOPE"), ("dup_post_review_audit", "POST_REVIEW_AUDIT"),
          ("dup_post_stale_version", "POST_TARGET_VERSION"), ("dup_original_proposer", "ORIGINAL_PROPOSER"),
          ("dup_reject", "REJECT_WITHOUT_POSITIVE_CAPABILITIES"),
      ]],
], indirect=["matching_case", "parser_admission_case"])
def test_service_match_case_human_confirmation_and_registered_executor_share_real_native_flow(
        matching_case, case_api, monkeypatch, confirmation_change):
    from modules.listing.application.identity_delivery import (
        IDENTITY_POLICY_ENV,
        IDENTITY_SIGNING_KEY_ENV,
    )
    from shared.auth import Principal, Role
    from shared.jobs.queue import JobStatus
    from tests.integration.test_canonical_identity_admission_postgresql import _claim

    case, parsed, repo, _, _, _, mode = matching_case
    ids, database = case[1], case[2]
    monkeypatch.setenv(IDENTITY_SIGNING_KEY_ENV, "Explicit synthetic matched-intake identity signing material only")
    monkeypatch.setenv(IDENTITY_POLICY_ENV, canonical_json({"policy_id": "synthetic-matched-intake-identity",
        "version": 1, "max_attempts": 3, "deadline_seconds": 120, "backoff_seconds": [1, 2]}))
    # Split targets are explicit human graph context. The ordinary shared
    # matcher legitimately reports only its actual identity candidates; never
    # fabricate an extra matching result or duplicate a canonical source URL.
    human_graph_context = bool(confirmation_change and confirmation_change.startswith("after_")
        and (confirmation_change.startswith("after_split") or mode == "POSSIBLE_MATCH"))
    proposed = None if human_graph_context else _match(matching_case)
    proposer_id = ids["actor"] if confirmation_change == "dup_original_proposer" else ids["reviewer"]
    if proposer_id == ids["reviewer"]:
        _add_sql_manager(database, ids["tenant"], proposer_id)
    client, caller, _ = case_api
    caller["principal"] = Principal(proposer_id,
        frozenset({Role.DATA_OWNER if confirmation_change == "dup_original_proposer" else Role.SITE_REVIEWER}),
        Scope(tenant_id=ids["tenant"], modules=frozenset({"listing"}), clearance=DataClassification.CONFIDENTIAL))
    proposer_principal = caller["principal"]
    if human_graph_context:
        with repo.graph.transaction(ids["tenant"]):
            listings = repo.graph.engine.query("SELECT listing_id FROM expansion.listings WHERE tenant_id=? ORDER BY listing_id",
                (ids["tenant"],))
            observed_roots = repo.graph.resolve_listings(ids["tenant"], [r["listing_id"] for r in listings])
        selected_context = client.post(f"/api/v1/intakes/{parsed['intake_id']}/match-case",
            json={"confidence": "0.75", "risk_acknowledged": True,
                "reason": "Explicit actual human-selected graph roots before synthetic split review",
                "supporting_signals": [{"kind": "SYNTHETIC_HUMAN_SELECTED_GRAPH_CONTEXT"}], "contradictory_signals": [],
                "candidates": [{"property_id": row["effective_property_id"], "property_version": row["current_property_version"],
                    "listing_id": key, "listing_version": row["listing_version"], "confidence": "0.75",
                    "evidence": {"kind": "SYNTHETIC_EXPLICIT_HUMAN_SELECTION"}} for key, row in observed_roots.items()]},
            headers={"Idempotency-Key": "synthetic-native-human-split-context", "If-Match": f'W/"{parsed["version"]}"'})
        assert selected_context.status_code == 201, selected_context.text
        proposed = selected_context.json()
    if confirmation_change == "manual_context":
        original = repo.get_for_intake(ids["tenant"], parsed["intake_id"])
        assert original["match_case"]["supporting_signals"][0]["kind"] == "NATIVE_SHARED_MATCHER_PROPOSAL"
        options = client.get(f"/api/v1/intakes/{parsed['intake_id']}/match-case/property-options")
        assert options.status_code == 200, options.text
        additional = next(row for row in options.json()["items"] if row["property_id"] == ids["split_target"])
        assert additional["property_id"] not in {row["property_id"] for row in original["candidates"]}
        added = client.post(f"/api/v1/intakes/{parsed['intake_id']}/match-case",
            headers={"If-Match": f'W/"{original["intake"]["version"]}"', "Idempotency-Key": "synthetic-service-case-human-context-001"},
            json={"outcome": "POSSIBLE_MATCH", "confidence": "0.3750", "risk_acknowledged": True,
                "reason": "Additional human association preserves actual service matching evidence",
                "append_to_case": {"match_case_id": proposed["match_case_id"], "version": proposed["version"]},
                "candidates": [{"property_id": additional["property_id"], "property_version": additional["version"],
                    "confidence": "0.3750", "evidence": {"kind": "EXPLICIT_HUMAN_ASSOCIATION"}}],
                "supporting_signals": [{"kind": "EXPLICIT_HUMAN_ASSOCIATION"}], "contradictory_signals": []})
        assert added.status_code == 201, added.text
        assert added.json()["match_case_id"] == proposed["match_case_id"]
        current = repo.get_for_intake(ids["tenant"], parsed["intake_id"])
        assert current["candidates"][:-1] == original["candidates"]
        assert current["intake"] == original["intake"]
        assert {k: v for k, v in current["match_case"].items() if k not in {"version", "updated_at"}} == {
            k: v for k, v in original["match_case"].items() if k not in {"version", "updated_at"}}
        proposed = {**proposed, **added.json()}
    prior_decision_ids = []
    if confirmation_change and confirmation_change.startswith("after_"):
        repair_name, _, graph_change = confirmation_change.partition(":")
        repair = "MERGE" if repair_name == "after_merge" else "SPLIT"
        observed_graph = client.get(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-context",
            params={"include_listing_input": "false"})
        assert observed_graph.status_code == 200, observed_graph.text
        graph_context = observed_graph.json()
        assert {r["property_id"] for r in graph_context["properties"]} == {
            ids[key] for key in (("source", "target") if repair == "MERGE" else ("source", "target", "split_target"))}
        sources = [r for r in graph_context["source_identity_edges"] if r["property_id"] == ids["source"]]
        plan = {"expected_property_versions": {r["property_id"]: r["version"] for r in graph_context["properties"]},
            "expected_listing_versions": {r["listing_id"]: r["version"] for r in graph_context["listings"]},
            "expected_edge_versions": {r["edge_id"]: r["edge_version"] for r in sources},
            "candidate_reassignment_plan": []}
        if repair == "MERGE":
            plan.update(source_property_ids=[ids["source"]], target_property_id=ids["target"])
        else:
            assert len(sources) == 2
            plan.update(source_property_id=ids["source"], partitions=[
                {"target_property_id": ids[root], "source_identity_edge_ids": [sources[index]["edge_id"]]}
                for index, root in enumerate(("target", "split_target"))])
        repair_response = client.post(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-decisions",
            json={"decision_type": repair, "risk_acknowledged": True,
                "reason": "Explicit synthetic graph repair before independent native confirmation", "plan": plan},
            headers={"Idempotency-Key": "synthetic-native-graph-repair", "If-Match": f'W/"{proposed["version"]}"'})
        assert repair_response.status_code == 201, repair_response.text
        repair_proposal = repair_response.json()
        graph_reviewer = str(uuid4())
        _add_sql_manager(database, ids["tenant"], graph_reviewer)
        caller["principal"] = replace(proposer_principal, subject_id=graph_reviewer)
        approved_graph = client.post(f"/api/v1/canonical-identity-decisions/{repair_proposal['decision_id']}/actions/review",
            json={"decision": "APPROVE", "expected_case_version": repair_proposal["match_case_version"],
                "reason": "Independent actual synthetic graph plan and version review", "risk_acknowledged": True},
            headers={"Idempotency-Key": "synthetic-native-graph-review", "If-Match": f'W/"{repair_proposal["version"]}"'})
        assert approved_graph.status_code == 200, approved_graph.text
        delivery, registry, bundle = _claim((repo.graph, ids, None, database), approved_graph.json()["job_id"])
        registry.handle(delivery, bundle)
        assert repo.graph.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED
        prior_decision_ids = [repair_proposal["decision_id"]]
        caller["principal"] = proposer_principal
        selected_case = client.get(f"/api/v1/intakes/{parsed['intake_id']}/match-case")
        assert selected_case.status_code == 200 and selected_case.json()["status"] == "EXECUTED", selected_case.text
        proposed = {**proposed, "version": selected_case.json()["version"], "outcome": selected_case.json()["outcome"]}
        repair_state = persisted(case)
        assert all(r["status"] == "PROPOSED" for r in repair_state["intake.human_corrections"])
        graph_only = client.get(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-context",
            params={"include_listing_input": "false"})
        assert graph_only.status_code == 409, graph_only.text
        if graph_change in {"revoked_confirmer", "stale_versions"}:
            confirmation_change = graph_change
        elif graph_change:
            with database.connect() as connection:
                if graph_change == "graph_event":
                    connection.execute("DELETE FROM workflow.outbox_events WHERE aggregate_id=%s AND aggregate_version=%s "
                        "AND event_type='match.decided'", (proposed["match_case_id"], proposed["version"]))
                elif graph_change == "graph_case_version":
                    connection.execute("UPDATE identity.match_cases SET version=version+1 WHERE match_case_id=%s",
                        (proposed["match_case_id"],))
                elif graph_change == "graph_result":
                    connection.execute("UPDATE workflow.jobs SET result=jsonb_set(result,'{audit_event_id}',to_jsonb(%s::text)) "
                        "WHERE job_id=%s", (str(uuid4()), approved_graph.json()["job_id"]))
                elif graph_change == "graph_transport":
                    connection.execute("UPDATE odp_runtime.durable_jobs SET status='FAILED' WHERE job_id=%s",
                        (approved_graph.json()["job_id"],))
                elif graph_change == "graph_reviewer":
                    connection.execute("UPDATE identity.match_decisions SET reviewer_subject_id=%s WHERE match_decision_id=%s",
                        (str(uuid4()), repair_proposal["decision_id"]))
                elif graph_change == "source_grant":
                    connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id="
                        "(SELECT source_id FROM intake.intakes WHERE intake_id=%s)", (parsed["intake_id"],))
            if graph_change == "graph_audit":
                original = repo.graph.audit_sink.writer.get_event

                def unavailable(tenant, audit_id):
                    event = original(tenant, audit_id)
                    return None if event and event["action"] == "identity.executed.v1" else event

                monkeypatch.setattr(repo.graph.audit_sink.writer, "get_event", unavailable)
            before = _snapshot((repo.graph, ids, None, database)), persisted(case)
            denied = client.get(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-context",
                params={"include_listing_input": "true"})
            assert denied.status_code in {403, 409}, denied.text
            _assert_denied_business_unchanged(before, (_snapshot((repo.graph, ids, None, database)), persisted(case)))
            return
    review = proposed["outcome"] == "POSSIBLE_MATCH"
    observed = client.get(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-context",
        params={"include_listing_input": str(review).lower()})
    assert observed.status_code == 200, observed.text
    context = observed.json()
    reference = context["listing_input"]
    assert all(not row.get("masked", False) for row in reference["fields"])
    binding = {name: reference[name] for name in ("parser_job_id", "parser_run_id", "parser_run_version",
        "source_snapshot_id", "original_input_sha256", "normalized_payload_sha256")}
    binding["expected_correction_versions"] = {r["correction_id"]: r["version"] for r in reference["fields"]}
    selected_listing = ids.get("selected_revision_listing", ids["listing"])
    properties, listings, edges = context["properties"], context["listings"], context["source_identity_edges"]
    if mode in {"REVISION", "DUPLICATE"}:
        selected_properties = {r["effective_property_id"] for r in context["anchors"] if r["listing_id"] == selected_listing}
        properties = [r for r in properties if r["property_id"] in selected_properties]
        listings = [r for r in listings if r["listing_id"] == selected_listing]
        edges = [r for r in edges if r["listing_id"] == selected_listing]
    body = {"decision_type": "DUPLICATE" if mode == "DUPLICATE" else (
        "CREATE" if mode in {"NEW", "POSSIBLE_MATCH"} else "REVISE"), "risk_acknowledged": True,
        "reason": "Confirm actual observed synthetic matching proposals independently", "plan": {
            "expected_intake_version": context["intake_version"], "input_binding": binding,
            "expected_property_versions": {r["property_id"]: r["version"] for r in properties},
            "expected_listing_versions": {r["listing_id"]: r["version"] for r in listings},
            "expected_edge_versions": {r["edge_id"]: r["edge_version"] for r in edges},
            "candidate_reassignment_plan": []}}
    pre_confirmation_change = confirmation_change in {"missing_versions", "stale_versions", "unrelated_property",
        "revoked_confirmer", "unrelated_listing", "dup_service_authority"}
    if pre_confirmation_change:
        with database.connect() as connection:
            if confirmation_change == "missing_versions":
                body["plan"]["expected_property_versions"] = {}
            elif confirmation_change == "stale_versions":
                connection.execute("UPDATE expansion.listings SET version=version+1 WHERE listing_id=%s", (selected_listing,))
            elif confirmation_change == "unrelated_property":
                body["plan"]["expected_property_versions"][ids["target"]] = 1
            elif confirmation_change == "revoked_confirmer":
                connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (ids["reviewer"],))
            elif confirmation_change == "dup_service_authority":
                body["plan"]["exact_authority"] = {"policy_version_id": matching_case[5],
                    "identity_policy_sha256": "0" * 64, "proposal_audit_event_id": str(uuid4())}
            elif confirmation_change == "unrelated_listing":
                foreign = str(uuid4())
                connection.execute("INSERT INTO expansion.listings (listing_id,tenant_id,property_id,source_id,source_listing_id,"
                    "lifecycle_state) SELECT %s,%s,%s,source_id,'synthetic-unrelated-key','ACTIVE' "
                    "FROM expansion.listings WHERE tenant_id=%s AND listing_id=%s",
                    (foreign, ids["tenant"], ids["target"], ids["tenant"], selected_listing))
                body["plan"]["expected_listing_versions"] = {foreign: 1}
                body["plan"]["expected_edge_versions"] = {}
        before = _snapshot((repo.graph, ids, None, database)), persisted(case)
    confirmed = client.post(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-decisions", json=body,
        headers={"Idempotency-Key": "synthetic-matched-human-confirm-001", "If-Match": f'W/"{proposed["version"]}"'})
    if pre_confirmation_change:
        expected = 422 if confirmation_change == "dup_service_authority" else (
            403 if confirmation_change == "revoked_confirmer" else 409)
        assert confirmed.status_code == expected, confirmed.text
        after = _snapshot((repo.graph, ids, None, database)), persisted(case)
        _assert_denied_business_unchanged(before, after)
        return
    assert confirmed.status_code == 201, confirmed.text
    selected_case = client.get(f"/api/v1/intakes/{parsed['intake_id']}/match-case")
    assert selected_case.status_code == 200, selected_case.text
    assert selected_case.json()["identity_decision_ids"] == prior_decision_ids + [confirmed.json()["decision_id"]]
    execution_receipt, reviewer_id, reviewed = confirmed.json(), None, None
    if mode == "DUPLICATE":
        assert confirmed.json()["status"] == "PENDING_REVIEW" and confirmed.json()["job_id"] is None
        monkeypatch.delenv("ODP_NATIVE_INTAKE_IDENTITY_BEARER_TOKEN", raising=False)
        duplicate_before = _snapshot((repo.graph, ids, None, database)), persisted(case)
        reviewer_id = proposer_id if confirmation_change == "dup_self_review" else str(uuid4())
        if reviewer_id != proposer_id:
            _add_sql_manager(database, ids["tenant"], reviewer_id)
        caller["principal"] = replace(proposer_principal, subject_id=reviewer_id, roles=frozenset({Role.SITE_REVIEWER}))
        selected_case = client.get(f"/api/v1/intakes/{parsed['intake_id']}/match-case")
        assert selected_case.status_code == 200 and selected_case.json()["identity_decision_ids"] == prior_decision_ids + [confirmed.json()["decision_id"]], selected_case.text
        with database.connect() as connection:
            if confirmation_change == "dup_reviewer_role":
                connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (reviewer_id,))
            elif confirmation_change == "dup_reviewer_mask":
                connection.execute("UPDATE identity.account_scopes SET clearance='PUBLIC' WHERE account_id=%s", (reviewer_id,))
            elif confirmation_change == "dup_reviewer_steward":
                connection.execute("UPDATE identity.account_roles SET role='data_owner' WHERE account_id=%s", (reviewer_id,))
            elif confirmation_change in {"dup_source_revoke", "dup_reject"}:
                connection.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id="
                    "(SELECT source_id FROM intake.intakes WHERE intake_id=%s)", (parsed["intake_id"],))
        before_review = _snapshot((repo.graph, ids, None, database)), persisted(case)
        if confirmation_change == "dup_reject":
            monkeypatch.delenv(IDENTITY_SIGNING_KEY_ENV)
            monkeypatch.delenv(IDENTITY_POLICY_ENV)
        review_body = {"decision": "REJECT" if confirmation_change == "dup_reject" else "APPROVE",
            "expected_case_version": confirmed.json()["match_case_version"],
            "reason": "Independent review of the actual synthetic duplicate proposal",
            "risk_acknowledged": confirmation_change != "dup_reject"}
        review_headers = {"Idempotency-Key": "synthetic-duplicate-independent-review-001",
            "If-Match": f'W/"{confirmed.json()["version"]}"'}
        reviewed = client.post(f"/api/v1/canonical-identity-decisions/{confirmed.json()['decision_id']}/actions/review",
            json=review_body, headers=review_headers)
        if confirmation_change == "dup_reject":
            assert reviewed.status_code == 200 and reviewed.json()["status"] == "REJECTED", reviewed.text
            assert reviewed.json()["job_id"] is None
            rejected = client.get(f"/api/v1/canonical-identity-decisions/{confirmed.json()['decision_id']}")
            assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED", rejected.text
            assert rejected.json()["reviewer_subject_id"] == reviewer_id and rejected.json()["job_id"] is None
            after_review = _snapshot((repo.graph, ids, None, database)), persisted(case)
            for table in ("intake.intakes", "intake.intake_stage_transitions", "workflow.jobs",
                    "identity.properties", "identity.source_identity_edges", "expansion.listings", "expansion.listing_revisions"):
                assert after_review[0][table] == before_review[0][table], table
            assert [row for row in after_review[0]["delivery"] if row["job_type"] != "canonical-audit-delivery"] == [
                row for row in before_review[0]["delivery"] if row["job_type"] != "canonical-audit-delivery"]
            old_audits = {row["audit_event_id"] for row in before_review[0]["audit.audit_events"]}
            new_audits = {row["audit_event_id"] for row in after_review[0]["audit.audit_events"]} - old_audits
            old_jobs = {row["job_id"] for row in before_review[0]["delivery"]}
            new_jobs = [row for row in after_review[0]["delivery"] if row["job_id"] not in old_jobs]
            assert new_jobs and all(row["job_type"] == "canonical-audit-delivery" for row in new_jobs)
            assert {json.loads(row["payload_json"])["audit_event_id"] for row in new_jobs} == new_audits
            assert reviewed.json()["audit_event_id"] in new_audits
            audit = repo.graph.audit_sink.writer.get_event(ids["tenant"], reviewed.json()["audit_event_id"])
            assert audit["action"] == "identity.decision_rejected.v1" and audit["actor_subject_id"] == reviewer_id
            assert audit["service_principal"] is None and audit["after_value"]["risk_acknowledged"] is False
            assert after_review[1]["intake.human_corrections"] == before_review[1]["intake.human_corrections"]
            assert next(row for row in after_review[0]["identity.match_cases"]
                if row["match_case_id"] == proposed["match_case_id"])["status"] == "REJECTED"
            replayed = client.post(f"/api/v1/canonical-identity-decisions/{confirmed.json()['decision_id']}/actions/review",
                json=review_body, headers=review_headers)
            assert replayed.status_code == 200 and replayed.json() == reviewed.json(), replayed.text
            assert replayed.headers.get("Idempotency-Replayed") == "true"
            assert (_snapshot((repo.graph, ids, None, database)), persisted(case)) == after_review
            return
        if confirmation_change in {"dup_self_review", "dup_reviewer_role", "dup_reviewer_mask",
                "dup_reviewer_steward", "dup_source_revoke"}:
            assert reviewed.status_code == (409 if confirmation_change == "dup_source_revoke" else 403), reviewed.text
            _assert_denied_business_unchanged(before_review, (_snapshot((repo.graph, ids, None, database)), persisted(case)))
            return
        assert reviewed.status_code == 200, reviewed.text
        execution_receipt = reviewed.json()
    delivery, registry, bundle = _claim((repo.graph, ids, None, database), execution_receipt["job_id"])
    if confirmation_change is not None and confirmation_change.startswith(("post_", "dup_post_")):
        from fastapi import HTTPException

        from modules.listing.application.identity_execution import IdentityExecutionService

        execution_graph = bundle.canonical_identity_graph()
        with database.connect() as connection:
            if confirmation_change in {"post_stale_version", "dup_post_stale_version"}:
                connection.execute("UPDATE expansion.listings SET version=version+1 WHERE listing_id=%s",
                    (selected_listing if mode == "DUPLICATE" else ids["listing"],))
            elif confirmation_change in {"post_revoked_confirmer", "dup_post_proposer_role", "dup_post_reviewer_role"}:
                connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s",
                    (reviewer_id if confirmation_change == "dup_post_reviewer_role" else proposer_id,))
            elif confirmation_change == "dup_post_reviewer_scope":
                connection.execute("UPDATE identity.account_scopes SET modules='[\"external_data\"]'::jsonb WHERE account_id=%s",
                    (reviewer_id,))
        if confirmation_change == "dup_post_review_audit":
            original = execution_graph.audit_sink.writer.get_event

            def unavailable(tenant, audit_id):
                event = original(tenant, audit_id)
                return None if event and event["action"] == "identity.decision_approved.v1" else event

            monkeypatch.setattr(execution_graph.audit_sink.writer, "get_event", unavailable)
        before = _snapshot((repo.graph, ids, None, database)), persisted(case)
        role_error = confirmation_change in {"post_revoked_confirmer", "dup_post_proposer_role", "dup_post_reviewer_role"}
        expected_error = HTTPException if role_error else (
            (HTTPException, PermissionError) if confirmation_change == "dup_post_reviewer_scope" else IdentityConflictError)
        with pytest.raises(expected_error) as denied:
            IdentityExecutionService(execution_graph).run(delivery)
        if role_error:
            assert denied.value.status_code == 403 and denied.value.detail == "ROLE_DENIED"
        elif confirmation_change == "dup_post_reviewer_scope":
            assert not isinstance(denied.value, HTTPException) or denied.value.status_code == 403
        elif confirmation_change == "dup_post_review_audit":
            assert str(denied.value) == "IDENTITY_CONFIRMATION_PROOF_REQUIRED"
        else:
            assert str(denied.value) == ("IDENTITY_LISTING_TARGET_CONFLICT" if mode == "DUPLICATE" else "LISTING_VERSION_CONFLICT")
        assert (_snapshot((repo.graph, ids, None, database)), persisted(case)) == before
        return
    registry.handle(delivery, bundle)
    assert repo.graph.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED, repo.graph.queue.get(delivery.job_id).error_message
    with repo.graph.transaction(ids["tenant"]):
        intake = repo.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?", (ids["tenant"], parsed["intake_id"]))
        assert intake["processing_state"] == "READY" and intake["resolved_listing_id"]
        if mode in {"REVISION", "DUPLICATE"}:
            assert intake["resolved_listing_id"] == selected_listing
        if review:
            assert intake["version"] == parsed["version"] + 2
            transition = repo.graph._one("SELECT * FROM intake.intake_stage_transitions WHERE tenant_id=? "
                "AND intake_id=? AND resulting_version=?", (ids["tenant"], parsed["intake_id"], intake["version"]))
            assert transition["from_state"] == "NEEDS_REVIEW" and transition["to_state"] == "READY"
            assert transition["service_principal"] == "identity-execution" and transition["actor_subject_id"] is None
            match = repo.graph._one("SELECT * FROM identity.match_cases WHERE tenant_id=? AND match_case_id=?",
                (ids["tenant"], proposed["match_case_id"]))
            assert match["outcome"] == "POSSIBLE_MATCH" and match["status"] == "EXECUTED"
        decision = repo.graph._one("SELECT * FROM identity.match_decisions WHERE tenant_id=? AND match_decision_id=?",
            (ids["tenant"], confirmed.json()["decision_id"]))
        assert decision["status"] == "EXECUTED" and decision["proposer_subject_id"] == proposer_id
        assert decision["reviewer_subject_id"] == reviewer_id
        assert decision["proposer_service_principal"] is None
    if mode == "DUPLICATE":
        duplicate_after = _snapshot((repo.graph, ids, None, database)), persisted(case)
        for table in ("identity.properties", "identity.source_identity_edges", "expansion.listings", "expansion.listing_revisions"):
            assert duplicate_after[0][table] == duplicate_before[0][table], table
        assert duplicate_after[1]["intake.human_corrections"] == duplicate_before[1]["intake.human_corrections"]
        events = [row for row in duplicate_after[0]["workflow.outbox_events"] if row["event_type"] == "match.decided"
            and row["aggregate_id"] == proposed["match_case_id"]
            and (json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"])["payload"]["match_decision_id"]
                == confirmed.json()["decision_id"]]
        assert len(events) == 3
        for row in events:
            event = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            assert event["event_version"] == 1 and validate_event(event) == []
            assert event["payload"]["proposer_subject_id"] == proposer_id
            assert "proposer_service_principal" not in event["payload"]
        review_replay = client.post(f"/api/v1/canonical-identity-decisions/{confirmed.json()['decision_id']}/actions/review",
            json=review_body, headers=review_headers)
        assert review_replay.status_code == 200 and review_replay.json() == reviewed.json(), review_replay.text
        assert review_replay.headers.get("Idempotency-Replayed") == "true"
    readback = client.get(f"/api/v1/canonical-identity-decisions/{confirmed.json()['decision_id']}")
    assert readback.status_code == 200 and readback.json()["job_status"] == "SUCCEEDED", readback.text
    intake_readback = client.get(f"/api/v1/canonical-intakes/{parsed['intake_id']}")
    assert intake_readback.status_code == 200, intake_readback.text
    assert intake_readback.json()["processing_state"] == "READY"
    with repo.graph.transaction(ids["tenant"]):
        stored = repo.graph._one("SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=?",
            (ids["tenant"], intake["resolved_listing_id"]))
        resolved = repo.graph.resolve_listing(ids["tenant"], stored["listing_id"])
    saved_resolution = intake_readback.json()["resolution"]
    saved_fields = saved_resolution.pop("fields")
    assert saved_resolution == {"listing_id": stored["listing_id"], "listing_version": stored["version"],
        "lifecycle_state": stored["lifecycle_state"], "current_revision_id": stored["current_revision_id"],
        "property_id_at_creation": stored["property_id"], "effective_property_id": resolved["effective_property_id"],
        "property_version": resolved["current_property_version"]}
    with repo.graph.transaction(ids["tenant"]):
        revision = repo.graph._one("SELECT effective_values FROM expansion.listing_revisions WHERE tenant_id=? AND listing_revision_id=?",
            (ids["tenant"], stored["current_revision_id"]))
    saved_values = revision["effective_values"]
    saved_values = json.loads(saved_values) if isinstance(saved_values, str) else saved_values
    assert {field["field_path"] for field in saved_fields} == set(saved_values)
    if mode != "DUPLICATE":
        assert all(not field["masked"] and field["classification"] == "INTERNAL" for field in saved_fields)
        assert {field["field_path"]: field["effective"] for field in saved_fields} == saved_values
    if prior_decision_ids:
        previous = client.get(f"/api/v1/canonical-identity-decisions/{prior_decision_ids[0]}")
        assert previous.status_code == 200 and previous.json()["status"] == "EXECUTED", previous.text
        assert previous.json()["reviewer_subject_id"] == graph_reviewer
        assert previous.json()["plan"] == plan
        if mode == "DUPLICATE":
            assert persisted(case)["intake.human_corrections"] == repair_state["intake.human_corrections"]
    before = _snapshot((repo.graph, ids, None, database)), persisted(case)
    caller["principal"] = proposer_principal
    replay = client.post(f"/api/v1/match-cases/{proposed['match_case_id']}/identity-decisions", json=body,
        headers={"Idempotency-Key": "synthetic-matched-human-confirm-001", "If-Match": f'W/"{proposed["version"]}"'})
    assert replay.status_code == 201 and replay.json() == confirmed.json(), replay.text
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert (_snapshot((repo.graph, ids, None, database)), persisted(case)) == before
    if mode == "NEW" and confirmation_change is None:
        from apps.api.app.routes import canonical_intakes

        # The original parser fixture has its own requester identity. These
        # read controls explicitly use the case fixture's synthetic principal,
        # matching the existing case/decision HTTP authentication seam.
        monkeypatch.setattr(canonical_intakes, "principal_from_headers", lambda _: caller["principal"])
        # The live proposal is not the saved value. Classification changes
        # still apply to the immutable current revision at every fresh read.
        with database.connect() as connection:
            connection.execute("UPDATE intake.human_corrections SET field_classification='RESTRICTED',"
                "corrected_value=to_jsonb(9876543::integer) WHERE listing_id=%s AND field_path='rent' AND status='APPLIED'",
                (stored["listing_id"],))
        path = f"/api/v1/canonical-intakes/{parsed['intake_id']}"
        restricted = client.get(path)
        assert restricted.status_code == 200, restricted.text
        field = next(row for row in restricted.json()["resolution"]["fields"] if row["field_path"] == "rent")
        assert field["masked"] and field["effective"] is None
        caller["principal"] = replace(proposer_principal,
            scope=replace(proposer_principal.scope, clearance=DataClassification.HIGHLY_RESTRICTED))
        visible = client.get(path)
        assert visible.status_code == 200, visible.text
        field = next(row for row in visible.json()["resolution"]["fields"] if row["field_path"] == "rent")
        assert not field["masked"] and field["effective"] == saved_values["rent"] != 9876543
        with database.connect() as connection:
            connection.execute("UPDATE intake.human_corrections SET status='SUPERSEDED' WHERE listing_id=%s AND field_path='rent'",
                (stored["listing_id"],))
        missing = client.get(path)
        assert missing.status_code == 200, missing.text
        field = next(row for row in missing.json()["resolution"]["fields"] if row["field_path"] == "rent")
        assert field["classification"] is None and field["masked"] and field["effective"] is None


EXACT_SERVICE = pytest.mark.parametrize("matching_case", [{"mode": "EXACT_DUPLICATE", "policy_changes": {
    "service_subjects": ["service:synthetic-native-matcher", "service:native-intake-identity"]}}], indirect=True)


@pytest.fixture
def exact_service_case(matching_case, case_api, monkeypatch, request):
    from datetime import UTC, datetime, timedelta

    from apps.api.oday_api.security import dependencies
    from modules.listing.application.identity_delivery import (
        IDENTITY_POLICY_ENV,
        IDENTITY_SIGNING_KEY_ENV,
    )
    from modules.listing.application.native_intake_identity import NativeIntakeIdentityOwner
    from modules.opsboard.auth.jwt import SigningKey, encode_compact_jwt

    case, parsed, repo, _, _, policy_id, _ = matching_case
    tenant = case[1]["tenant"]
    subject = "service:native-intake-identity"
    key = SigningKey("synthetic-exact-service", "HS256", b"Explicit synthetic exact service JWT key only")
    config = AuthBoundaryConfig(service_issuer="urn:synthetic:exact-service", service_signing_keys={key.kid: key},
        service_audiences=frozenset({"synthetic-native-intake"}), leeway_seconds=0,
        principal_mappings={subject: {"roles": [], "scope": {"tenant_id": tenant,
            "modules": ["listing"], "clearance": "INTERNAL"}}})
    boundary = AuthenticationBoundary(config)
    now = datetime.now(UTC)
    token = encode_compact_jwt({"sub": subject, "iss": config.service_issuer,
        "aud": "synthetic-native-intake", "iat": now.timestamp(), "exp": (now + timedelta(minutes=5)).timestamp(),
        "roles": ["platform_admin"]}, key)
    observed = boundary.authenticate(Credentials(bearer_token=token))
    assert observed.authenticated and observed.token_type == "service" and not observed.principal.roles
    monkeypatch.setattr(dependencies, "default_boundary", lambda: boundary)
    repo.graph.identity_service_boundary = dependencies.default_boundary
    monkeypatch.setenv("ODP_NATIVE_INTAKE_IDENTITY_BEARER_TOKEN", token)
    monkeypatch.setenv(IDENTITY_SIGNING_KEY_ENV, "Explicit synthetic independent identity executor signing material only")
    monkeypatch.setenv(IDENTITY_POLICY_ENV, canonical_json({"policy_id": "synthetic-exact-executor",
        "version": 1, "max_attempts": 3, "deadline_seconds": 120, "backoff_seconds": [1, 2]}))
    proposal = None if getattr(request, "param", False) else _match(matching_case)
    owner = NativeIntakeIdentityOwner(repo.graph)
    return case, parsed, owner, proposal, policy_id, case_api[2]


@pytest.fixture
def native_scheduler(exact_service_case, monkeypatch):
    import os

    from apps.scheduler.oday_scheduler.main import ODayScheduler
    from modules.external_data.application.native_parser_deployments import DEPLOYMENTS_ENV
    from shared.infrastructure.persistence.factory import build_persistence
    from tests.intake_workflow_fixture import SyntheticIntakeWorkflowPolicy

    case, _, identity, _, policy, store = exact_service_case
    _configure_full_scheduler_runtime(case, monkeypatch)
    deployment = case[0].artifact_registry._deployments[case[4]]
    monkeypatch.setenv(DEPLOYMENTS_ENV, canonical_json({"version": 1, "deployments": [{
        "source_id": deployment.source_id, "parser_release_id": deployment.parser_release_id,
        "artifact_uri": deployment.archive_uri, "archive_path": str(deployment.archive_path),
        "matching_policy_version_id": policy}]}))
    monkeypatch.setenv("ODP_NATIVE_INTAKE_MATCHER_BEARER_TOKEN", os.environ["ODP_NATIVE_INTAKE_IDENTITY_BEARER_TOKEN"])
    # The whole-pipeline fixture submits new SQL Intakes. Supply its explicit
    # synthetic workflow dependency; memory construction has no runtime grant.
    bundle = replace(build_persistence(mode="memory"), engine=identity.engine,
        job_queue=identity.graph.queue, assisted_intake_store=store,
        assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy(),
        feature_flag_registry=identity.graph.queue._flags)
    return ODayScheduler(persistence=bundle, tenant_id=case[1]["tenant"])


def _configure_full_scheduler_runtime(case, monkeypatch):
    """Explicit owned test startup settings, not source or actor grants."""
    import os

    from modules.listing.application.native_intake_assisted_entry_policy import (
        STORAGE_ENV as MANUAL_STORAGE,
    )
    from modules.listing.application.native_intake_retrieval_policy import (
        POLICY_ENV,
        SIGNING_KEY_ENV,
        STORAGE_ENV,
    )
    from modules.listing.application.native_intake_source_policy_recheck import (
        POLICY_ENV as RECHECK_POLICY,
    )
    from modules.listing.application.native_intake_source_policy_recheck import (
        SIGNING_KEY_ENV as RECHECK_KEY,
    )
    from tests.security.test_native_intake_source_policy import policy

    tenant = case[1]["tenant"]
    monkeypatch.setenv(RECHECK_POLICY, canonical_json({**policy(tenant), "policy_id": "synthetic-full-scheduler-recheck"}))
    monkeypatch.setenv(RECHECK_KEY, "synthetic-full-scheduler-separate-recheck-key-" * 2)
    storage = json.loads(os.environ[MANUAL_STORAGE])
    monkeypatch.setenv(POLICY_ENV, canonical_json({"policy_id": "synthetic-full-scheduler-retrieval", "version": 1,
        "tenant_ids": [tenant], "max_attempts": 2, "deadline_seconds": 120, "backoff_seconds": [0.05]}))
    monkeypatch.setenv(STORAGE_ENV, canonical_json({key: storage[key] for key in ("version", "global_max_concurrency", "storage_bindings")}))
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-full-scheduler-separate-retrieval-key-" * 2)


@pytest.mark.parametrize("exact_service_case", [True], indirect=True)
@pytest.mark.parametrize("matching_case", [{"mode": "NEW", "policy_changes": {
    "service_subjects": ["service:synthetic-native-matcher", "service:native-intake-identity"]}}], indirect=True)
def test_new_url_and_human_entry_reach_matching_through_existing_scheduler_and_registered_workers(native_scheduler, exact_service_case, case_api, monkeypatch):
    from modules.listing.application.native_intake_retrieval_policy import canonical_json
    from shared.infrastructure.persistence.factory import PersistenceBundle
    from shared.jobs.queue import JobStatus
    from tests.integration.test_canonical_identity_admission_postgresql import _claim

    case, parsed, identity, _, _, _ = exact_service_case
    ids, database, capture = case[1], case[2], case[5]
    original_entry, original_parser = PersistenceBundle.canonical_intake_assisted_entry, PersistenceBundle.canonical_intake_parser
    monkeypatch.setattr(PersistenceBundle, "canonical_intake_assisted_entry",
        lambda bundle: original_entry(bundle, snapshot_transport_factory=capture.factory))
    monkeypatch.setattr(PersistenceBundle, "canonical_intake_parser",
        lambda bundle: original_parser(bundle, storage_factory=capture.factory))
    client = case_api[0]
    # The case-only HTTP fixture intentionally exposes just its native case
    # ports. This whole-pipeline check uses the actual runtime bundle instead.
    monkeypatch.setattr(client.app.state, "persistence_bundle", native_scheduler.persistence)
    parent = client.get(f"/api/v1/canonical-intakes/{parsed['intake_id']}")
    assert parent.status_code == 200, parent.text
    with database.connect() as connection:
        original_url = connection.execute("SELECT original_url FROM intake.intakes WHERE intake_id=%s", (parsed["intake_id"],)).fetchone()[0]
    before = _snapshot((identity.graph, ids, None, database))
    response = client.post("/api/v1/canonical-intakes", json={"intake_method": "URL", "scope": parent.json()["scope"],
        "original_url": original_url.rsplit("/", 1)[0] + "/SchedulerFreshListing", "reason": "Explicit synthetic end-to-end scheduled intake"},
        headers={"Idempotency-Key": "synthetic-full-scheduler-url-" + uuid4().hex})
    assert response.status_code == 201 and response.json()["processing_state"] == "SUBMITTED", response.text
    intake_id = response.json()["intake_id"]
    executed = []

    def tick_and_execute(phase, expected_state, *, parser=False):
        native_scheduler.run_once()
        report = native_scheduler.last_native_handoff
        receipts = report["partitions"][0][phase] if parser else report["tenant_phases"][phase]
        owned = [row for row in receipts if row.get("intake_id") == intake_id]
        assert len(owned) == 1, canonical_json(report)
        job_id = owned[0]["job_id"]
        delivery, registry, _ = _claim((identity.graph, ids, None, database), job_id)
        registry.handle(delivery, native_scheduler.persistence)
        assert native_scheduler.persistence.job_queue.get(job_id).status == JobStatus.SUCCEEDED
        state = client.get(f"/api/v1/canonical-intakes/{intake_id}")
        assert state.status_code == 200 and state.json()["processing_state"] == expected_state, state.text
        executed.append(job_id)
        return state.json()

    tick_and_execute("identity_starts", "CHECKING_IDENTITY")
    tick_and_execute("identity_checks", "CHECKING_SOURCE_POLICY")
    waiting = tick_and_execute("source_policy_checks", "AWAITING_ASSISTED_ENTRY")
    manual = client.post(f"/api/v1/canonical-intakes/{intake_id}/assisted-entry-completions", json=BODY,
        headers={"Idempotency-Key": "synthetic-full-scheduler-human-" + uuid4().hex, "If-Match": f'W/"{waiting["version"]}"'})
    assert manual.status_code == 202 and manual.json()["status"] == "REQUESTED", manual.text
    tick_and_execute("assisted_entry_admissions", "PARSING")
    tick_and_execute("parser_admissions", "MATCHING", parser=True)
    native_scheduler.run_once()
    selected = client.get(f"/api/v1/intakes/{intake_id}/match-case")
    assert selected.status_code == 200 and selected.json()["outcome"] == "NEW", selected.text
    assert selected.json()["identity_decision_ids"] == [] and len(set(executed)) == 5
    after = _snapshot((identity.graph, ids, None, database))
    for table in ("identity.properties", "identity.source_identity_edges", "expansion.listings", "expansion.listing_revisions"):
        assert after[table] == before[table]
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM intake.human_corrections WHERE intake_id=%s AND status='PROPOSED'",
            (intake_id,)).fetchone()[0] == len(BODY["fields"])
    native_scheduler.run_once()
    assert _snapshot((identity.graph, ids, None, database)) == after


@pytest.mark.parametrize("exact_service_case", [True], indirect=True)
@pytest.mark.parametrize("matching_case", [{"mode": mode, "policy_changes": {
    "service_subjects": ["service:synthetic-native-matcher", "service:native-intake-identity"]}}
    for mode in ("NEW", "REVISION", "POSSIBLE_MATCH", "EXACT_DUPLICATE")], indirect=True)
def test_scheduler_resumes_real_parser_result_and_preserves_human_confirmation(native_scheduler, exact_service_case, matching_case):
    from shared.jobs.queue import JobStatus
    from tests.integration.test_canonical_identity_admission_postgresql import _claim

    case, parsed, identity, _, _, _ = exact_service_case
    ids = case[1]
    before = _snapshot((identity.graph, ids, None, case[2]))
    native_scheduler.run_once()
    part = native_scheduler.last_native_handoff["partitions"][0]
    assert part["parser_admissions"] == [] and len(part["matching_proposals"]) == 1
    assert native_scheduler.last_native_handoff["errors"] == []
    context = native_scheduler.persistence.canonical_match_cases().get_for_intake(ids["tenant"], parsed["intake_id"])
    assert context["match_case"]["outcome"] == matching_case[-1]
    if matching_case[-1] == "EXACT_DUPLICATE":
        assert len(part["exact_admissions"]) == 1
        job_id = part["exact_admissions"][0]["job_id"]
        delivery, registry, bundle = _claim((identity.graph, ids, None, case[2]), job_id)
        registry.handle(delivery, bundle)
        assert bundle.job_queue.get(job_id).status == JobStatus.SUCCEEDED
        context = native_scheduler.persistence.canonical_match_cases().get_for_intake(ids["tenant"], parsed["intake_id"])
        assert context["intake"]["processing_state"] == "READY"
        assert context["intake"]["resolved_listing_id"] == ids["listing"]
    else:
        assert part["exact_admissions"] == []
        assert context["match_case"]["status"] == ("PENDING_REVIEW" if matching_case[-1] == "POSSIBLE_MATCH" else "PROPOSED")
        assert context["intake"]["processing_state"] == ("NEEDS_REVIEW" if matching_case[-1] == "POSSIBLE_MATCH" else "MATCHING")
    after = _snapshot((identity.graph, ids, None, case[2]))
    for table in ("identity.properties", "identity.source_identity_edges", "expansion.listings", "expansion.listing_revisions"):
        assert after[table] == before[table]
    fields_before = persisted(case)
    native_scheduler.run_once()
    assert all(native_scheduler.last_native_handoff["partitions"][0][name] == []
        for name in ("parser_admissions", "matching_proposals", "exact_admissions"))
    assert (_snapshot((identity.graph, ids, None, case[2])), persisted(case)) == (after, fields_before)
    assert case[0].queue.get(parsed["job_id"]).status == JobStatus.SUCCEEDED


@EXACT_SERVICE
@pytest.mark.parametrize("exact_service_case", [True], indirect=True)
def test_scheduler_recovers_committed_case_after_exact_authentication_failure(native_scheduler, exact_service_case, monkeypatch, capsys):
    import os

    from modules.external_data.application.market_data_facade import (
        CUTOVER_MODE_PLATFORM_PRIMARY,
        FACADE_MODE_ENV,
    )
    from product_ops.deployment import cloud_run_job_entrypoint as entrypoint
    from shared.jobs.queue import JobStatus

    case, parsed, identity, _, _, _ = exact_service_case
    monkeypatch.setattr(entrypoint, "bootstrap_runtime", lambda: native_scheduler.persistence)
    monkeypatch.setenv("ODP_SCHEDULED_INGESTION_TENANT_ID", case[1]["tenant"])
    monkeypatch.setenv(FACADE_MODE_ENV, CUTOVER_MODE_PLATFORM_PRIMARY)
    token = os.environ["ODP_NATIVE_INTAKE_IDENTITY_BEARER_TOKEN"]
    monkeypatch.setenv("ODP_NATIVE_INTAKE_IDENTITY_BEARER_TOKEN", "invalid synthetic token")
    assert entrypoint.run_scheduler() == entrypoint.EXIT_FAILED
    first = json.loads(capsys.readouterr().out.splitlines()[-1])
    report = first["native_intake"]
    assert first["reason"] == "native_intake_handoff_incomplete" and first["jobs_enqueued"] == 0
    assert len(report["partitions"][0]["matching_proposals"]) == 1
    assert len(report["errors"]) == 1 and report["errors"][0]["phase"] == "exact_admissions"
    assert report["errors"][0]["error_class"] == "PermissionError"
    assert case[0].queue.get(parsed["job_id"]).status == JobStatus.SUCCEEDED
    with case[2].connect() as connection:
        assert connection.execute("SELECT count(*) FROM identity.match_decisions d JOIN identity.match_cases c "
            "ON c.tenant_id=d.tenant_id AND c.match_case_id=d.match_case_id WHERE c.intake_id=%s",
            (parsed["intake_id"],)).fetchone()[0] == 0
    monkeypatch.setenv("ODP_NATIVE_INTAKE_IDENTITY_BEARER_TOKEN", token)
    assert entrypoint.run_scheduler() == 0
    resumed = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert resumed["jobs_enqueued"] == 1 and resumed["job_type"] == "identity-execution"
    report = resumed["native_intake"]
    part = report["partitions"][0]
    assert part["matching_proposals"] == [] and len(part["exact_admissions"]) == 1
    assert report["errors"] == []
    assert entrypoint.run_scheduler() == 0
    idle = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert idle["reason"] == "native_intake_handoff" and idle["jobs_enqueued"] == 0
    with case[2].connect() as connection:
        assert connection.execute("SELECT count(*) FROM identity.match_cases WHERE intake_id=%s",
            (parsed["intake_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM identity.match_decisions d JOIN identity.match_cases c "
            "ON c.tenant_id=d.tenant_id AND c.match_case_id=d.match_case_id WHERE c.intake_id=%s",
            (parsed["intake_id"],)).fetchone()[0] == 1


def test_scheduler_parser_admission_remains_durable_when_later_service_auth_is_unavailable(parser_admission_case, case_api, monkeypatch):
    from apps.api.oday_api.security import dependencies
    from apps.scheduler.oday_scheduler.main import ODayScheduler, SchedulerNativeIntakeHandoffError
    from modules.external_data.application.native_parser_deployments import DEPLOYMENTS_ENV
    from shared.infrastructure.persistence.factory import build_persistence
    from shared.jobs.queue import JobStatus
    from tests.integration.test_native_intake_parser_admission_postgresql import admit
    from tests.integration.test_native_intake_parser_admission_postgresql import (
        persisted as admissions,
    )

    case = parser_admission_case
    _configure_full_scheduler_runtime(case, monkeypatch)
    owner, ids, _, _, release, _, _ = case
    deployment = owner.artifact_registry._deployments[release]
    monkeypatch.setenv(DEPLOYMENTS_ENV, canonical_json({"version": 1, "deployments": [{
        "source_id": deployment.source_id, "parser_release_id": release, "artifact_uri": deployment.archive_uri,
        "archive_path": str(deployment.archive_path), "matching_policy_version_id": "synthetic-explicit-ref-no-service-grant"}]}))
    monkeypatch.setattr(dependencies, "default_boundary", lambda: None)
    bundle = replace(build_persistence(mode="memory"), engine=owner.engine,
        job_queue=owner.queue, assisted_intake_store=case_api[2])
    scheduler = ODayScheduler(persistence=bundle, tenant_id=ids["tenant"])
    before = admissions(case)
    with pytest.raises(SchedulerNativeIntakeHandoffError):
        scheduler.run_once()
    report = scheduler.last_native_handoff
    part = report["partitions"][0]
    assert len(part["parser_admissions"]) == 1 and part["parser_progress"]["admitted"] == 1
    assert any(error["phase"] == "matching_proposals" and error["error_class"] == "PermissionError"
        for error in report["errors"])
    receipt = admit(case)
    assert part["parser_admissions"][0]["job_id"] == receipt["job_id"]
    after = admissions(case)
    for table in ("intake.intakes", "intake.source_snapshots", "intake.human_corrections", "identity.match_cases", "expansion.listings"):
        assert after[table] == before[table]
    assert len(after["intake.parser_runs"]) == len(before["intake.parser_runs"]) + 1
    assert owner.queue.get(receipt["job_id"]).status == JobStatus.QUEUED
    with pytest.raises(SchedulerNativeIntakeHandoffError):
        scheduler.run_once()
    assert scheduler.last_native_handoff["partitions"][0]["parser_admissions"] == []
    assert admissions(case) == after


@EXACT_SERVICE
def test_exact_service_cli_registered_worker_and_readback_use_existing_canonical_flow(exact_service_case, case_api, monkeypatch, capsys):
    from types import MethodType, SimpleNamespace

    from apps.api import server
    from apps.worker.oday_worker import native_intake_identity as cli
    from modules.listing.application.intake_audit import CanonicalIntakeAuditSink
    from shared.audit.canonical_delivery import CanonicalAuditDelivery
    from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
    from shared.domain.events import validate_event
    from shared.infrastructure.persistence import PostgresDocumentStore
    from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
    from shared.infrastructure.persistence.factory import PersistenceBundle
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.postgresql import PostgresEngine
    from shared.jobs.queue import JobStatus
    from tests.integration.test_canonical_identity_admission_postgresql import _claim

    case, parsed, owner, proposal, policy_id, replay_store = exact_service_case
    ids, database = case[1], case[2]
    canonical = owner.graph, ids, None, database
    before, fields_before = _snapshot(canonical), persisted(case)
    # A real second runtime opens the same database; CLI closes its own engine.
    engine = PostgresEngine(database.url(), bootstrap=False, validate_schema=False)
    queue = DurableJobQueue(engine)
    writer = CanonicalPostgresAuditLog(engine)
    audit = CanonicalIntakeAuditSink(writer, owner.graph.audit_sink.retention,
        CanonicalAuditDelivery(writer, queue, owner.graph.audit_sink.delivery.sink))
    store = DurableAssistedIntakeStore(PostgresDocumentStore(engine))
    store.configure_canonical_audit(audit)
    bundle = SimpleNamespace(engine=engine, job_queue=queue, assisted_intake_store=store)
    for name in ("canonical_identity_graph", "canonical_intake_identity"):
        setattr(bundle, name, MethodType(getattr(PersistenceBundle, name), bundle))
    monkeypatch.setattr(server, "bootstrap_runtime", lambda: bundle)
    with database.connect() as connection:
        source = connection.execute("SELECT source_id FROM intake.intakes WHERE intake_id=%s", (parsed["intake_id"],)).fetchone()[0]
    monkeypatch.setattr("sys.argv", ["native_intake_identity", "--tenant-id", ids["tenant"], "--match-cases",
        "--source-id", source, "--matching-policy-version-id", policy_id, "--limit", "1"])
    cli.main()
    import json

    rows = [json.loads(s) for s in capsys.readouterr().out.splitlines() if s.startswith('{"phase"')]
    assert len(rows) == 1 and rows[0]["phase"] == "native_exact_identity_admitted"
    receipt = rows[0]["admission_receipt"]
    admitted = _snapshot(canonical)
    for table in ("intake.intakes", "expansion.listings", "expansion.listing_revisions", "identity.properties", "identity.source_identity_edges"):
        assert admitted[table] == before[table]
    delivery, registry, runtime = _claim(canonical, receipt["job_id"])
    registry.handle(delivery, runtime)
    assert owner.graph.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED
    after, fields_after = _snapshot(canonical), persisted(case)
    for table in ("expansion.listings", "expansion.listing_revisions", "identity.properties", "identity.source_identity_edges"):
        assert after[table] == before[table], table
    assert fields_after["intake.human_corrections"] == fields_before["intake.human_corrections"]
    with owner.graph.transaction(ids["tenant"]):
        intake = owner.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=?", (ids["tenant"], parsed["intake_id"]))
        assert intake["processing_state"] == "READY" and intake["resolved_listing_id"] == ids["listing"]
        decision = owner.graph._one("SELECT * FROM identity.match_decisions WHERE tenant_id=? AND match_decision_id=?",
            (ids["tenant"], receipt["decision_id"]))
        assert decision["status"] == "EXECUTED" and decision["proposer_subject_id"] is None
        assert decision["proposer_service_principal"] == "native-intake-identity" and decision["reviewer_subject_id"] is None
        assert decision["risk_acknowledged"] is False
        events = owner.graph.engine.query("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? "
            "AND event_type='match.decided' AND payload->'payload'->>'match_decision_id'=?", (ids["tenant"], receipt["decision_id"]))
        payloads = [json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"] for row in events]
        assert len(payloads) == 3 and all(row["event_version"] == 2 and validate_event(row) == [] for row in payloads)
    replay = owner.confirm_exact_case(ids["tenant"], proposal["match_case_id"], expected_case_version=proposal["version"],
        policy_version_id=policy_id, replay_store=replay_store)
    assert replay == receipt and _snapshot(canonical) == after
    response = case_api[0].get(f"/api/v1/canonical-identity-decisions/{receipt['decision_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["proposer_subject_id"] is None
    assert response.json()["proposer_service_principal"] == "native-intake-identity"
    assert response.json()["job_status"] == "SUCCEEDED"


@EXACT_SERVICE
@pytest.mark.parametrize("revocation", ["token", "purpose_owner", "identity_partition"])
def test_exact_service_approval_needs_current_independent_service_and_owner_authority(exact_service_case, monkeypatch, revocation):
    import json
    import os

    from modules.listing.application.native_intake_identity import POLICY_ENV

    case, _, owner, proposal, policy_id, store = exact_service_case
    ids, database = case[1], case[2]
    if revocation == "token":
        monkeypatch.setenv("ODP_NATIVE_INTAKE_IDENTITY_BEARER_TOKEN", "invalid")
    elif revocation == "purpose_owner":
        with database.connect() as connection:
            connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (ids["actor"],))
    else:
        policy = json.loads(os.environ[POLICY_ENV])
        policy["tenant_ids"] = [str(uuid4())]
        monkeypatch.setenv(POLICY_ENV, canonical_json(policy))
    before = _snapshot((owner.graph, ids, None, database)), persisted(case)
    expected = IntakeWorkflowUnavailable if revocation == "purpose_owner" else PermissionError
    with pytest.raises(expected):
        owner.confirm_exact_case(ids["tenant"], proposal["match_case_id"], expected_case_version=proposal["version"],
            policy_version_id=policy_id, replay_store=store)
    assert (_snapshot((owner.graph, ids, None, database)), persisted(case)) == before


@EXACT_SERVICE
@pytest.mark.parametrize("revocation", ["token", "owner_scope", "listing_version", "field_version"])
def test_exact_executor_rechecks_current_service_input_and_target_before_acceptance(exact_service_case, monkeypatch, revocation):
    from apps.api.oday_api.security.dependencies import default_boundary
    from modules.listing.application.identity_execution import IdentityExecutionService
    from modules.listing.application.native_intake_identity import NativeIntakeIdentityConflict
    from tests.integration.test_canonical_identity_admission_postgresql import _claim

    case, parsed, owner, proposal, policy_id, store = exact_service_case
    ids, database = case[1], case[2]
    receipt = owner.confirm_exact_case(ids["tenant"], proposal["match_case_id"], expected_case_version=proposal["version"],
        policy_version_id=policy_id, replay_store=store)
    canonical = owner.graph, ids, None, database
    delivery, _, runtime = _claim(canonical, receipt["job_id"])
    if revocation == "token":
        monkeypatch.setenv("ODP_NATIVE_INTAKE_IDENTITY_BEARER_TOKEN", "invalid")
    else:
        with database.connect() as connection:
            if revocation == "owner_scope":
                connection.execute("UPDATE identity.account_scopes SET modules='[\"priceops\"]'::jsonb WHERE account_id=%s", (ids["actor"],))
            elif revocation == "listing_version":
                connection.execute("UPDATE expansion.listings SET version=version+1 WHERE listing_id=%s", (ids["listing"],))
            else:
                connection.execute("UPDATE intake.human_corrections SET version=version+1 WHERE intake_id=%s", (parsed["intake_id"],))
    before = _snapshot(canonical), persisted(case)
    expected = PermissionError if revocation == "token" else IntakeWorkflowUnavailable if revocation == "owner_scope" else (
        NativeIntakeIdentityConflict, IdentityConflictError)
    graph = runtime.canonical_identity_graph(identity_service_boundary=default_boundary)
    with pytest.raises(expected):
        IdentityExecutionService(graph).run(delivery)
    assert (_snapshot(canonical), persisted(case)) == before


@EXACT_SERVICE
@pytest.mark.parametrize("failure", ["revoked_service", "late_audit"])
def test_accepted_exact_execution_rolls_back_intake_and_all_effective_facts(exact_service_case, monkeypatch, failure):
    from apps.api.oday_api.security.dependencies import default_boundary
    from modules.listing.application.identity_execution import IdentityExecutionService
    from shared.audit.errors import CanonicalAuditUnavailable
    from tests.integration.test_canonical_identity_admission_postgresql import _claim

    case, _, owner, proposal, policy_id, store = exact_service_case
    ids, database = case[1], case[2]
    receipt = owner.confirm_exact_case(ids["tenant"], proposal["match_case_id"], expected_case_version=proposal["version"],
        policy_version_id=policy_id, replay_store=store)
    canonical = owner.graph, ids, None, database
    delivery, _, runtime = _claim(canonical, receipt["job_id"])
    graph = runtime.canonical_identity_graph(identity_service_boundary=default_boundary)
    service = IdentityExecutionService(graph)
    fence = service.begin(ids["tenant"], receipt["decision_id"], delivery)
    before = _snapshot(canonical), persisted(case)
    if failure == "revoked_service":
        monkeypatch.setenv("ODP_NATIVE_INTAKE_IDENTITY_BEARER_TOKEN", "invalid")
    else:
        original = graph.audit_sink.persist

        def unavailable(record, scope):
            if record["action"] == "identity.executed.v1":
                raise CanonicalAuditUnavailable("Synthetic exact final audit unavailable")
            return original(record, scope)

        monkeypatch.setattr(graph.audit_sink, "persist", unavailable)
    with pytest.raises(PermissionError if failure == "revoked_service" else CanonicalAuditUnavailable):
        graph.execute(ids["tenant"], receipt["decision_id"], delivery, business_fence=fence)
    assert (_snapshot(canonical), persisted(case)) == before


@EXACT_SERVICE
@pytest.mark.parametrize("revocation", ["token", "owner_scope", "approval_window"])
def test_exact_admission_receipt_replay_is_not_a_current_source_grant(exact_service_case, monkeypatch, revocation):
    case, _, owner, proposal, policy_id, store = exact_service_case
    ids, database = case[1], case[2]
    owner.confirm_exact_case(ids["tenant"], proposal["match_case_id"], expected_case_version=proposal["version"],
        policy_version_id=policy_id, replay_store=store)
    if revocation == "token":
        monkeypatch.setenv("ODP_NATIVE_INTAKE_IDENTITY_BEARER_TOKEN", "invalid")
    else:
        with database.connect() as connection:
            if revocation == "owner_scope":
                connection.execute("UPDATE identity.account_scopes SET modules='[\"priceops\"]'::jsonb WHERE account_id=%s", (ids["actor"],))
            else:
                connection.execute("UPDATE workflow.decision_policies SET effective_to=clock_timestamp() WHERE policy_version_id=%s", (policy_id,))
    before = _snapshot((owner.graph, ids, None, database)), persisted(case)
    with pytest.raises(PermissionError if revocation == "token" else IntakeWorkflowUnavailable):
        owner.confirm_exact_case(ids["tenant"], proposal["match_case_id"], expected_case_version=proposal["version"],
            policy_version_id=policy_id, replay_store=store)
    assert (_snapshot((owner.graph, ids, None, database)), persisted(case)) == before


@EXACT_SERVICE
@pytest.mark.parametrize("parser_admission_case", [{"fields": {**BODY["fields"], "providerListingId": "contradicting-provider"}}], indirect=True)
def test_service_exact_cannot_treat_a_contradictory_proposed_source_key_as_unchanged_identity(exact_service_case):
    from modules.listing.application.native_intake_identity import NativeIntakeIdentityConflict

    case, _, owner, proposal, policy_id, store = exact_service_case
    before = _snapshot((owner.graph, case[1], None, case[2])), persisted(case)
    with pytest.raises(NativeIntakeIdentityConflict, match="NATIVE_EXACT_SOURCE_KEY_CONTRADICTION"):
        owner.confirm_exact_case(case[1]["tenant"], proposal["match_case_id"], expected_case_version=proposal["version"],
            policy_version_id=policy_id, replay_store=store)
    assert (_snapshot((owner.graph, case[1], None, case[2])), persisted(case)) == before
