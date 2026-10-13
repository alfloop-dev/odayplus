"""Admission decision probes; actual SQL locking is covered by PostgreSQL tests."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from modules.listing.application.intake_score_admission import validate_score_admission
from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied


@pytest.fixture
def admission():
    tenant, intake_id, snapshot_id = (str(uuid4()) for _ in range(3))
    now = datetime.now(UTC)
    intake = {"intake_id": intake_id, "scope": {"tenant_id": tenant}, "source_id": "synthetic-source",
              "source_policy_version": 3, "policy_state": "ASSISTED_ENTRY_ONLY", "source_snapshot_id": snapshot_id}
    candidate = SimpleNamespace(listing=SimpleNamespace(source_id="synthetic-source", snapshot_id=snapshot_id),
                                dataset_snapshot_id=snapshot_id)
    rows = {
        "source_registry": {"source_id": "synthetic-source", "version": 3, "kill_switch": False,
                            "production_enabled": True, "retrieval_mode": "ASSISTED_ENTRY_ONLY",
                            "legal_approval_ref": "synthetic-legal-reference", "license_approval_ref": "synthetic-license-reference",
                            "review_expires_at": now + timedelta(days=1)},
        "source_snapshots": {"source_snapshot_id": snapshot_id, "intake_id": intake_id, "source_id": "synthetic-source",
                             "tenant_id": tenant, "content_sha256": "a" * 64, "raw_object_uri": "gs://synthetic-snapshot/object",
                             "encryption_key_ref": "synthetic-key-reference", "media_type": "application/json", "version": 1,
                             "capture_method": "MANUAL_ENTRY", "captured_at": now - timedelta(minutes=1),
                             "observed_at": now - timedelta(minutes=2), "purge_after": None, "legal_hold": False},
    }
    queries = []

    def query_one(sql, params):
        queries.append((sql, params))
        return next((deepcopy(row) for table, row in rows.items() if f"intake.{table} " in sql), None)

    return SimpleNamespace(intake=intake, candidate=candidate, rows=rows, queries=queries,
                           engine=SimpleNamespace(dialect="postgresql", query_one=query_one))


def test_recorded_manual_capture_needs_no_invented_parser(admission):
    receipt = validate_score_admission(admission.engine, admission.intake, admission.candidate)
    assert receipt["source_policy_version"] == 3
    assert receipt["source_snapshot_id"] == admission.intake["source_snapshot_id"]
    assert receipt["parser_release_id"] is None
    assert all("FOR SHARE" in sql for sql, _ in admission.queries)
    assert all("parser" not in sql for sql, _ in admission.queries)


@pytest.mark.parametrize(("field", "value", "code"), [
    ("kill_switch", True, "SOURCE_POLICY_DENIED"),
    ("production_enabled", False, "SOURCE_POLICY_DENIED"),
    ("version", 4, "SOURCE_POLICY_DENIED"),
    ("retrieval_mode", "SOURCE_BLOCKED", "SOURCE_POLICY_DENIED"),
    ("legal_approval_ref", "", "SOURCE_POLICY_DENIED"),
    ("license_approval_ref", None, "SOURCE_POLICY_DENIED"),
    ("review_expires_at", None, "SOURCE_REVIEW_EXPIRY_REQUIRED"),
    ("review_expires_at", datetime(2020, 1, 1, tzinfo=UTC), "SOURCE_REVIEW_EXPIRED"),
])
def test_live_source_revocation_and_missing_approval_deny(admission, field, value, code):
    admission.rows["source_registry"][field] = value
    with pytest.raises(ScoreDeliveryDenied, match=code):
        validate_score_admission(admission.engine, admission.intake, admission.candidate)


@pytest.mark.parametrize(("field", "value", "code"), [
    ("source_policy_version", None, "SOURCE_POLICY_PIN_REQUIRED"),
    ("source_policy_version", True, "SOURCE_POLICY_PIN_REQUIRED"),
    ("source_id", "another-source", "SOURCE_BINDING_DENIED"),
    ("source_snapshot_id", "00000000-0000-4000-8000-000000000901", "SOURCE_SNAPSHOT_BINDING_DENIED"),
    ("policy_state", "APPROVED_RETRIEVAL", "SOURCE_POLICY_STATE_MISMATCH"),
    ("parser_release_id", "00000000-0000-4000-8000-000000000902", "MANUAL_PARSER_BINDING_DENIED"),
])
def test_intake_binding_cannot_be_inferred_or_replaced(admission, field, value, code):
    admission.intake[field] = value
    with pytest.raises(ScoreDeliveryDenied, match=code):
        validate_score_admission(admission.engine, admission.intake, admission.candidate)


def _retrieved(admission):
    now = datetime.now(UTC)
    release_id, run_id = str(uuid4()), str(uuid4())
    admission.intake.update(policy_state="APPROVED_RETRIEVAL", parser_release_id=release_id,
                            parser_release_version=2, parser_run_id=run_id,
                            parser_input_schema_version="raw-v1", parser_output_schema_version="listing-v2")
    admission.rows["source_registry"]["retrieval_mode"] = "APPROVED_RETRIEVAL"
    admission.rows["source_snapshots"]["capture_method"] = "SERVER_RETRIEVAL"
    admission.rows["parser_releases"] = {
        "source_id": "synthetic-source", "version": 2, "validation_status": "PRODUCTION",
        "deprecated_at": None, "artifact_uri": "gs://synthetic-parser/artifact", "artifact_sha256": "b" * 64,
        "released_by": str(uuid4()), "released_at": now - timedelta(days=1),
        "input_schema_version": "raw-v1", "output_schema_version": "listing-v2",
    }
    admission.rows["parser_runs"] = {
        "intake_id": admission.intake["intake_id"], "source_snapshot_id": admission.intake["source_snapshot_id"],
        "parser_release_id": release_id, "status": "SUCCEEDED", "validation_errors": [],
        "normalized_payload": {"recorded": True}, "completed_at": now - timedelta(minutes=1), "version": 1,
    }


def test_recorded_parser_run_binds_current_compatible_production_release(admission):
    _retrieved(admission)
    receipt = validate_score_admission(admission.engine, admission.intake, admission.candidate)
    assert receipt["parser_release_id"] == admission.intake["parser_release_id"]
    assert receipt["parser_output_schema_version"] == "listing-v2"
    assert receipt["parser_artifact_sha256"] == "b" * 64


@pytest.mark.parametrize(("table", "field", "value", "code"), [
    ("parser_releases", "source_id", "another-source", "PARSER_RELEASE_DENIED"),
    ("parser_releases", "version", 3, "PARSER_RELEASE_DENIED"),
    ("parser_releases", "validation_status", "ROLLED_BACK", "PARSER_RELEASE_DENIED"),
    ("parser_releases", "input_schema_version", "raw-v2", "PARSER_SCHEMA_COMPATIBILITY_DENIED"),
    ("parser_releases", "output_schema_version", "listing-v3", "PARSER_SCHEMA_COMPATIBILITY_DENIED"),
    ("parser_runs", "intake_id", "00000000-0000-4000-8000-000000000903", "PARSER_RUN_BINDING_DENIED"),
    ("parser_runs", "source_snapshot_id", "00000000-0000-4000-8000-000000000904", "PARSER_RUN_BINDING_DENIED"),
    ("parser_runs", "status", "FAILED", "PARSER_RUN_BINDING_DENIED"),
    ("parser_runs", "validation_errors", ["missing-rent"], "PARSER_RUN_BINDING_DENIED"),
])
def test_parser_replay_cannot_use_revoked_or_incompatible_lineage(admission, table, field, value, code):
    _retrieved(admission)
    admission.rows[table][field] = value
    with pytest.raises(ScoreDeliveryDenied, match=code):
        validate_score_admission(admission.engine, admission.intake, admission.candidate)
