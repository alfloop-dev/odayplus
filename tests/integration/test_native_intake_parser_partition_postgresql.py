"""Real candidate scans and unchanged atomic admission; no provider IO.

All original human/manual/snapshot lineage is produced by the canonical
fixtures. Deliberate decoy catalog/snapshot rows confer no historical proof.
"""
import json
from uuid import uuid4

import pytest

from modules.listing.application.native_intake_assisted_entry import NativeIntakeAssistedEntryOwner
from modules.listing.application.native_intake_parser import NativeIntakeParserOwner
from modules.listing.application.native_intake_parser_policy import (
    NativeParserConflict,
    NativeParserUnavailable,
)
from shared.jobs.queue import JobStatus
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import BODY
from tests.integration.test_native_intake_identity_postgresql import defer_audit
from tests.integration.test_native_intake_parser_admission_postgresql import persisted
from tests.integration.test_native_intake_parser_io_postgresql import authority as authority
from tests.integration.test_native_intake_parser_io_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_io_postgresql import identity_case as identity_case
from tests.integration.test_native_intake_parser_io_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_io_postgresql import native_api as native_api
from tests.integration.test_native_intake_parser_io_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_intake_parser_io_postgresql import parser_realm as parser_realm
from tests.integration.test_native_intake_parser_io_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_io_postgresql import start_case as start_case
from tests.integration.test_native_intake_parser_recovery_postgresql import settle_seed
from tests.integration.test_native_intake_parser_worker_postgresql import (
    engineering_bundle,
    startup,
)
from tests.integration.test_native_intake_source_policy_postgresql import drive_upstream

pytestmark = pytest.mark.requires_live_env


def selection(case):
    return {"source_id": case[0].artifact_registry._deployments[case[4]].source_id,
        "release_id": case[4], "limit": 25}


def second_manual_input(case, source_case):
    """Create a second independent current input through real HTTP/owners."""
    owner, ids, database, manual, _, capture, start = case
    with database.connect() as connection:
        original_url = connection.execute("SELECT original_url FROM intake.intakes WHERE tenant_id=%s "
            "AND intake_id=%s", (ids["tenant"], manual["intake_id"])).fetchone()[0]
    client = start[1][0]
    response = client.post("/api/v1/canonical-intakes", json={"intake_method": "URL", "scope": {},
        "original_url": original_url + "/second", "reason": "Independent synthetic partition candidate"},
        headers={"Idempotency-Key": "synthetic-second-parser-input-" + uuid4().hex})
    assert response.status_code == 201
    started = drive_upstream(source_case,
        start[3].admit_event(ids["tenant"], response.json()["outbox_event_id"]), start[3])
    identity_owner = source_case[0][1]
    identified = drive_upstream(source_case,
        identity_owner.admit_event(ids["tenant"], started["outbox_event_id"]), identity_owner)
    source_owner = source_case[1]
    waiting = drive_upstream(source_case,
        source_owner.admit_event(ids["tenant"], identified["outbox_event_id"]), source_owner)
    assert waiting["processing_state"] == "AWAITING_ASSISTED_ENTRY"
    response = client.post("/api/v1/canonical-intakes/" + waiting["intake_id"] + "/assisted-entry-completions",
        headers={"Idempotency-Key": "synthetic-second-parser-manual-" + uuid4().hex,
            "If-Match": f'W/"{waiting["version"]}"'}, json=BODY)
    assert response.status_code == 202
    manual_owner = NativeIntakeAssistedEntryOwner(owner.graph, snapshot_transport_factory=capture.factory)
    admitted = manual_owner.admit_event(ids["tenant"], response.json()["request_id"])
    defer_audit(owner.graph)
    delivery = owner.queue.claim_next(worker_id="synthetic-second-manual-parser-input")
    assert delivery.job_id == admitted["job_id"]
    result = manual_owner.execute(delivery)
    assert result is not None and result["processing_state"] == "PARSING"
    assert result["intake_id"] != manual["intake_id"] and result["source_snapshot_id"] != manual["source_snapshot_id"]
    return result


def test_real_scan_binds_exact_transition_snapshot_and_explicit_release_despite_newer_decoys(parser_admission_case):
    case = parser_admission_case
    owner, ids, database, manual, release, capture, _ = case
    settle_seed(case)
    decoy_release, decoy_snapshot = str(uuid4()), str(uuid4())
    with database.connect() as connection:
        connection.execute("INSERT INTO intake.parser_releases (parser_release_id,source_id,package_name,"
            "semantic_version,input_schema_version,output_schema_version,artifact_uri,artifact_sha256,"
            "test_corpus_version,validation_status,canary_percent,released_by,released_at) "
            "SELECT %s,source_id,package_name,'99.0.0',input_schema_version,output_schema_version,"
            "artifact_uri,artifact_sha256,test_corpus_version,validation_status,canary_percent,released_by,"
            "clock_timestamp() FROM intake.parser_releases WHERE parser_release_id=%s", (decoy_release, release))
        connection.execute("INSERT INTO intake.source_snapshots (source_snapshot_id,tenant_id,intake_id,source_id,"
            "original_url,canonical_url,raw_object_uri,content_sha256,media_type,byte_length,captured_at,"
            "observed_at,capture_method,retention_class,encryption_key_ref) SELECT %s,tenant_id,intake_id,source_id,"
            "original_url,canonical_url,raw_object_uri,%s,media_type,byte_length,clock_timestamp(),clock_timestamp(),"
            "capture_method,retention_class,encryption_key_ref FROM intake.source_snapshots "
            "WHERE tenant_id=%s AND source_snapshot_id=%s", (decoy_snapshot, "0" * 64, ids["tenant"], manual["source_snapshot_id"]))
    previous, calls = persisted(case), list(capture.calls)
    receipts = list(owner.admit_partition(ids["tenant"], **selection(case)))
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["source_snapshot_id"] == manual["source_snapshot_id"] != decoy_snapshot
    assert receipt["parser_release_id"] == release != decoy_release
    assert receipt["admitted_intake_version"] == manual["version"]
    progress = owner.last_partition_progress
    assert progress["scanned"] == progress["admitted"] == 1 and progress["conflicts"] == 0 and not progress["has_more"]
    current = persisted(case)
    assert len(current["intake.parser_runs"]) == len(previous["intake.parser_runs"]) + 1
    assert len(current["workflow.jobs"]) == len(previous["workflow.jobs"]) + 1
    for table in ("intake.intakes", "intake.source_snapshots", "intake.human_corrections",
            "identity.match_cases", "expansion.listings", "workflow.outbox_events"):
        assert current[table] == previous[table], table
    assert capture.calls == calls
    # A later scan does not reset the actual already-admitted queued run.
    assert list(owner.admit_partition(ids["tenant"], **selection(case))) == []
    assert owner.last_partition_progress["scanned"] == 0 and persisted(case) == current
    assert owner.graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


def test_real_scan_does_not_cross_the_explicit_source_partition(parser_admission_case):
    case = parser_admission_case
    settle_seed(case)
    previous = persisted(case)
    assert list(case[0].admit_partition(case[1]["tenant"], **(selection(case) | {"source_id": "other.manual"}))) == []
    assert case[0].last_partition_progress["scanned"] == 0 and persisted(case) == previous


def test_real_scan_cursor_resumes_two_genuine_independent_manual_inputs_without_duplicate_jobs(
        parser_admission_case, source_policy_case):
    case = parser_admission_case
    owner, ids = case[:2]
    settle_seed(case)
    second = second_manual_input(case, source_policy_case)
    previous, calls = persisted(case), list(case[5].calls)
    first_receipts = list(owner.admit_partition(ids["tenant"], **(selection(case) | {"limit": 1})))
    assert len(first_receipts) == 1 and first_receipts[0]["intake_id"] == case[3]["intake_id"]
    progress = owner.last_partition_progress
    assert progress["has_more"] and progress["scanned"] == progress["admitted"] == 1
    second_receipts = list(owner.admit_partition(ids["tenant"], **selection(case), cursor=progress["next_cursor"]))
    assert len(second_receipts) == 1 and second_receipts[0]["intake_id"] == second["intake_id"]
    assert first_receipts[0]["job_id"] != second_receipts[0]["job_id"] and not owner.last_partition_progress["has_more"]
    current = persisted(case)
    assert len(current["intake.parser_runs"]) == len(previous["intake.parser_runs"]) + 2
    assert len(current["workflow.jobs"]) == len(previous["workflow.jobs"]) + 2
    for table in ("intake.intakes", "intake.source_snapshots", "intake.human_corrections",
            "identity.match_cases", "expansion.listings", "workflow.outbox_events"):
        assert current[table] == previous[table], table
    assert case[5].calls == calls
    assert list(owner.admit_partition(ids["tenant"], **selection(case))) == [] and persisted(case) == current


@pytest.mark.parametrize("loss", ["snapshot_history", "current_manual_approval"])
def test_real_selected_candidate_still_requires_full_history_and_current_authority(parser_admission_case, loss):
    case = parser_admission_case
    owner, ids, database, manual, _, _, _ = case
    settle_seed(case)
    with database.connect() as connection:
        if loss == "snapshot_history":
            connection.execute("UPDATE intake.source_snapshots SET byte_length=byte_length+1 "
                "WHERE tenant_id=%s AND source_snapshot_id=%s", (ids["tenant"], manual["source_snapshot_id"]))
        else:
            connection.execute("UPDATE intake.source_registry SET assisted_entry_approval_ref=NULL,"
                "assisted_entry_approved_by=NULL,assisted_entry_review_expires_at=NULL WHERE source_id=%s",
                (selection(case)["source_id"],))
    previous = persisted(case)
    assert list(owner.admit_partition(ids["tenant"], **selection(case))) == []
    progress = owner.last_partition_progress
    assert progress["scanned"] == progress["conflicts"] == 1 and progress["admitted"] == 0
    assert not progress["has_more"] and persisted(case) == previous


def test_real_scan_rejects_outer_root_and_absent_registry_without_writes(parser_admission_case):
    case = parser_admission_case
    owner, ids = case[:2]
    settle_seed(case)
    previous = persisted(case)
    with owner.graph.transaction(ids["tenant"]):
        with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_PARTITION_OUTER_ROOT_REJECTED$"):
            list(owner.admit_partition(ids["tenant"], **selection(case)))
    absent = NativeIntakeParserOwner(owner.graph)
    with pytest.raises(NativeParserUnavailable, match="^NATIVE_PARSER_ARTIFACT_REGISTRY_REQUIRED$"):
        list(absent.admit_partition(ids["tenant"], **selection(case)))
    assert persisted(case) == previous


def test_actual_CLI_startup_selection_factory_scan_and_durable_receipt_close_the_real_engine(
        parser_admission_case, monkeypatch, capsys):
    from apps.api import server
    from apps.worker.oday_worker import native_intake_parser as cli

    case = parser_admission_case
    owner, ids, database, manual, release, capture, _ = case
    settle_seed(case)
    startup(case, monkeypatch)
    bundle, constructed = engineering_bundle(case)
    # The sole bootstrap seam supplies this actual canonical SQL bundle.
    # Factory, registry, source selection, scan, proofs, writes and engine
    # close all execute normally. No provider operation is requested.
    monkeypatch.setattr(server, "bootstrap_runtime", lambda: bundle)
    closed = []
    close = bundle.engine.close

    def observe_close():
        close()
        closed.append(True)

    monkeypatch.setattr(bundle.engine, "close", observe_close)
    protected = ("intake.intakes", "intake.source_snapshots", "intake.human_corrections",
        "identity.match_cases", "expansion.listings", "workflow.outbox_events")

    def read_protected():
        # The CLI closes its real engine. Independently inspect durable rows
        # through the fixture connection instead of substituting a no-op close.
        with database.connect() as connection:
            return {table: connection.execute("SELECT * FROM " + table + " WHERE tenant_id=%s ORDER BY 1",
                (ids["tenant"],)).fetchall() for table in protected}

    previous, calls = read_protected(), list(capture.calls)
    assert cli.main(["--tenant-id", ids["tenant"], "--source-id", selection(case)["source_id"],
        "--limit", "1", "--include-progress"]) is None
    output = capsys.readouterr()
    assert output.err == "" and len(constructed) == 1 and closed == [True] and bundle.engine._pool.closed
    lines = [json.loads(line) for line in output.out.splitlines()]
    assert len(lines) == 2 and lines[0]["phase"] == "native_parser_admission_only"
    receipt = lines[0]["admission_receipt"]
    assert receipt["parser_release_id"] == release and receipt["source_snapshot_id"] == manual["source_snapshot_id"]
    assert receipt["intake_id"] == manual["intake_id"] and receipt["admitted_intake_version"] == manual["version"]
    assert lines[1]["phase"] == "native_parser_scan"
    progress = lines[1]["scan_receipt"]
    assert progress["admitted"] == progress["scanned"] == 1 and progress["conflicts"] == 0 and not progress["has_more"]
    with database.connect() as connection:
        job = connection.execute("SELECT status,attempt,payload FROM workflow.jobs WHERE tenant_id=%s AND job_id=%s",
            (ids["tenant"], receipt["job_id"])).fetchone()
        physical = connection.execute("SELECT status,attempts FROM odp_runtime.durable_jobs WHERE job_id=%s",
            (receipt["job_id"],)).fetchone()
        run = connection.execute("SELECT status,source_snapshot_id,parser_release_id FROM intake.parser_runs "
            "WHERE tenant_id=%s AND parser_run_id=%s", (ids["tenant"], receipt["parser_run_id"])).fetchone()
        audit = connection.execute("SELECT after_value FROM audit.audit_events WHERE tenant_id=%s AND audit_event_id=%s",
            (ids["tenant"], receipt["audit_event_id"])).fetchone()[0]
    assert job[:2] == ("QUEUED", 0) and physical == (JobStatus.QUEUED.value, 0)
    assert job[2]["admission"]["receipt"] == receipt
    assert run[0] == "QUEUED" and str(run[1]) == receipt["source_snapshot_id"] and str(run[2]) == release
    assert all(audit[key] == value for key, value in receipt.items())
    assert read_protected() == previous and capture.calls == calls
