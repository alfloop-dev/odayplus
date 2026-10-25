"""Canonical storage CAS only; native job/Intake/event ownership is separate."""
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation

from modules.external_data.application import native_manual_json_parser as provider
from modules.external_data.application.native_parser_artifacts import (
    NativeParserArtifactDeployment,
    NativeParserArtifactRegistry,
)
from modules.external_data.application.native_parser_output import (
    validate_native_manual_parser_output,
)
from modules.listing.application.native_intake_parser_job_proof import NativeParserJobProof
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import (
    NativeParserCommandBudget,
    NativeParserSQLClock,
    native_parser_run_id,
)
from modules.listing.application.native_intake_parser_runs import CanonicalNativeParserRunRepository
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import BODY
from tests.integration.test_native_intake_parser_input_postgresql import (
    accepted as accepted,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    assisted as assisted,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    completed_manual as completed_manual,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    past_manual as past_manual,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    read,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    recheck as recheck,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    storage_ready as storage_ready,
)
from tests.integration.test_native_intake_parser_input_postgresql import (
    waiting as waiting,
)
from tests.security.test_native_intake_parser_prerequisites import RootGuardedJob
from tests.security.test_native_parser_artifacts import installed_bytes, write_archive

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def parser_input_fields(request, monkeypatch):
    if getattr(request, "param", None) == "partial":
        # This is the actual human request, frozen before the real capture.
        # It creates genuine partial facts; no output/status is substituted.
        monkeypatch.setitem(BODY["fields"], "floor", "多樓層")


@pytest.fixture
def parser_storage_case(parser_input_fields, completed_manual, tmp_path):
    accepted, result = completed_manual
    assisted, owner, _, _, capture = accepted
    tenant = assisted[2]["tenant"]
    original = read(completed_manual)
    snapshot = original.snapshot()
    release_id = str(uuid4())
    path = tmp_path / "parser-cas-package.zip"
    digest = write_archive(path, installed_bytes().items())
    uri = "gs://synthetic-parser-cas-packages/manual-v1.zip"
    with owner.graph.transaction(tenant):
        before = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
        before()
        owner.engine.execute("INSERT INTO intake.parser_releases (parser_release_id,source_id,package_name,"
            "semantic_version,input_schema_version,output_schema_version,artifact_uri,artifact_sha256,"
            "test_corpus_version,validation_status,canary_percent,released_by,released_at) "
            "VALUES (?,?,?,'1.0.0',?,?,?,?,?,'PRODUCTION',100,?,clock_timestamp())",
            (release_id, snapshot["source_id"], provider.PACKAGE_NAME, provider.INPUT_SCHEMA_VERSION,
                provider.OUTPUT_SCHEMA_VERSION, uri, digest, provider.CORPUS_VERSION, assisted[2]["reviewer"]))
        before()
        release = owner.graph._one("SELECT * FROM intake.parser_releases WHERE parser_release_id=?", (release_id,))
    # PRODUCTION is a synthetic catalog enum here, not activation or approval.
    registry = NativeParserArtifactRegistry((NativeParserArtifactDeployment(release_id, snapshot["source_id"], uri, path),))
    artifact = registry.verify_release(release, now=datetime.now(UTC))
    body = next(iter(capture.objects.values()))[0]
    facts = provider.parse_manual_evidence(body, original.lineage).payloads()
    output = validate_native_manual_parser_output(json.dumps(facts).encode(), artifact=artifact,
        lineage=original.lineage, original_fields=original.command().fields.values())
    run_id = native_parser_run_id(tenant, snapshot["source_snapshot_id"], release_id)
    return owner, tenant, result, snapshot, release_id, run_id, output


def queued(case, before):
    owner, tenant, result, snapshot, release_id, run_id, _ = case
    return CanonicalNativeParserRunRepository(owner.engine).insert_queued(tenant=tenant, run_id=run_id,
        intake_id=result["intake_id"], snapshot_id=snapshot["source_snapshot_id"], release_id=release_id,
        correlation_id=result["correlation_id"], at=owner._clock(), before=before)


@pytest.mark.parametrize("parser_input_fields,status", [(None, "SUCCEEDED"), ("partial", "PARTIAL")], indirect=["parser_input_fields"])
def test_actual_validated_output_cas_updates_only_existing_canonical_columns(parser_storage_case, status):
    owner, tenant, _, _, _, run_id, output = parser_storage_case
    assert output.status == status
    store = CanonicalNativeParserRunRepository(owner.engine)
    with owner.graph.transaction(tenant):
        before = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
        first = queued(parser_storage_case, before)
        running = store.accept(first, at=owner._clock(), before=before)
        terminal = store.complete_output(running, output, at=owner._clock(), before=before)
        assert terminal["status"] == status and terminal["version"] == 3
        assert json.loads(terminal["parsed_payload"]) == output.facts.payloads()["parsed_payload"]
        assert json.loads(terminal["normalized_payload"]) == output.facts.payloads()["normalized_payload"]
        assert json.loads(terminal["field_confidence"]) == {}
        assert json.loads(terminal["validation_errors"]) == output.facts.payloads()["validation_errors"]
        assert "quality_summary" not in terminal
        assert store.get(tenant, run_id, before=before) == terminal
        with pytest.raises(NativeParserConflict):
            store.retry_same_run(terminal, before=before)


def test_transient_retry_reuses_one_run_and_preserves_initial_started_at(parser_storage_case):
    owner, tenant, _, _, _, run_id, _ = parser_storage_case
    store = CanonicalNativeParserRunRepository(owner.engine)
    with owner.graph.transaction(tenant):
        before = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
        deadlines = before.deadline, before._monotonic_deadline
        first = queued(parser_storage_case, before)
        running = store.accept(first, at=owner._clock(), before=before)
        retried = store.retry_same_run(running, before=before)
        second = store.accept(retried, at=owner._clock(), before=before)
        assert second["parser_run_id"] == run_id and second["version"] == 4
        assert second["started_at"] == running["started_at"]
        assert (before.deadline, before._monotonic_deadline) == deadlines
        before()
        assert owner.engine.query_one("SELECT count(*) AS n FROM intake.parser_runs WHERE tenant_id=?", (tenant,))["n"] == 1


def test_database_unique_snapshot_release_prevents_a_second_run_with_another_id(parser_storage_case):
    owner, tenant, result, snapshot, release_id, run_id, _ = parser_storage_case
    store = CanonicalNativeParserRunRepository(owner.engine)
    with owner.graph.transaction(tenant):
        before = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
        first = queued(parser_storage_case, before)
    with pytest.raises(UniqueViolation):
        with owner.graph.transaction(tenant):
            before = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
            store.insert_queued(tenant=tenant, run_id=str(uuid4()), intake_id=result["intake_id"],
                snapshot_id=snapshot["source_snapshot_id"], release_id=release_id,
                correlation_id=result["correlation_id"], at=owner._clock(), before=before)
    with owner.graph.transaction(tenant):
        before = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
        assert store.get(tenant, run_id, before=before) == first
        before()
        assert owner.engine.query_one("SELECT count(*) AS n FROM intake.parser_runs WHERE tenant_id=?", (tenant,))["n"] == 1


def test_stale_cas_cannot_accept_the_same_queued_version_twice(parser_storage_case):
    owner, tenant, *_ = parser_storage_case
    store = CanonicalNativeParserRunRepository(owner.engine)
    with owner.graph.transaction(tenant):
        before = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
        first = queued(parser_storage_case, before)
        current = store.accept(first, at=owner._clock(), before=before)
        with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_RUN_CAS_REJECTED$"):
            store.accept(first, at=owner._clock(), before=before)
        assert store.get(tenant, first["parser_run_id"], before=before) == current


@pytest.mark.parametrize("was_accepted,status", [(False, "CANCELLED"), (True, "CANCELLED"), (True, "FAILED")])
def test_negative_storage_edges_preserve_absent_output_without_positive_configuration(parser_storage_case, monkeypatch, was_accepted, status):
    owner, tenant, *_ = parser_storage_case
    store = CanonicalNativeParserRunRepository(owner.engine)
    with owner.graph.transaction(tenant):
        before = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
        row = queued(parser_storage_case, before)
        if was_accepted:
            row = store.accept(row, at=owner._clock(), before=before)
    # Storage cleanup must remain usable when current positive settings vanish;
    # proving actual owned jobs/fences is the separate runtime owner's duty.
    monkeypatch.delenv("ODP_NATIVE_INTAKE_PARSER_POLICY_JSON", raising=False)
    monkeypatch.delenv("ODP_NATIVE_INTAKE_PARSER_SIGNING_KEY", raising=False)
    with owner.graph.transaction(tenant):
        before = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
        row = store.get(tenant, row["parser_run_id"], before=before, for_update=True)
        terminal = store.complete_negative(row, status=status, at=owner._clock(), before=before)
        assert terminal["status"] == status and terminal["parsed_payload"] is None and terminal["normalized_payload"] is None
        assert json.loads(terminal["field_confidence"]) == {} and json.loads(terminal["validation_errors"]) == []
        with pytest.raises(NativeParserConflict):
            store.accept(terminal, at=owner._clock(), before=before)


def test_foreign_transaction_tenant_cannot_read_an_existing_run(parser_storage_case):
    owner, tenant, *_ = parser_storage_case
    store = CanonicalNativeParserRunRepository(owner.engine)
    with owner.graph.transaction(tenant):
        before = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
        row = queued(parser_storage_case, before)
        with pytest.raises(NativeParserConflict):
            store.get(str(uuid4()), row["parser_run_id"], before=before)


@pytest.mark.parametrize("method", ["admission", "acceptance", "get"])
@pytest.mark.parametrize("root_change", ["read_committed", "foreign_tenant"])
def test_parser_owners_require_current_serializable_tenant_root(parser_storage_case, method, root_change):
    owner, tenant, *_ = parser_storage_case
    error = "NATIVE_PARSER_RUN_CAS_REJECTED" if method == "get" else "NATIVE_PARSER_ADMISSION_PROOF_REJECTED"
    with owner.engine.transaction():
        if root_change == "foreign_tenant":
            owner.engine.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        else:
            owner.engine.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        actual_tenant = str(uuid4()) if root_change == "foreign_tenant" else tenant
        owner.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (actual_tenant,))
        before = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
        with pytest.raises(NativeParserConflict, match="^" + error + "$"):
            if method == "get":
                CanonicalNativeParserRunRepository(owner.engine).get(tenant, str(uuid4()), before=before)
            else:
                getattr(NativeParserJobProof(owner.graph), method)(RootGuardedJob(tenant_id=tenant), {}, before=before)
