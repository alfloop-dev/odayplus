"""Real SQL parser/source admission with explicitly synthetic evidence records."""
from uuid import uuid4

import pytest

from modules.listing.application.intake_score_admission import validate_score_admission
from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied
from tests.intake_registry_fixture import APPROVER, TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_intake_score_delivery_postgresql import (
    _approved_score,
)
from tests.integration.test_intake_score_delivery_postgresql import (
    explicit_local_signing_key as explicit_local_signing_key,
)
from tests.integration.test_operator_intake_promotion_postgresql import _promotion_client
from tests.integration.test_operator_intake_workflow_postgresql import (
    bridge_database as bridge_database,
)

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def parser_admission(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        intake, listing_id, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        candidate = next(row for row in bundle.listing_repository_for_tenant(TENANT).list_candidates()
                         if row.candidate_site.candidate_site_id == receipt["candidate_site_id"])
        release_id, run_id = str(uuid4()), str(uuid4())
        with bridge_database.connect() as conn:
            conn.execute("UPDATE intake.source_registry SET retrieval_mode='APPROVED_RETRIEVAL',version=2 WHERE source_id=%s",
                         (candidate.listing.source_id,))
            conn.execute("UPDATE intake.source_snapshots SET capture_method='SERVER_RETRIEVAL',version=2 WHERE source_snapshot_id=%s",
                         (candidate.listing.snapshot_id,))
            conn.execute(
                "INSERT INTO intake.parser_releases(parser_release_id,source_id,package_name,semantic_version,"
                "input_schema_version,output_schema_version,artifact_uri,artifact_sha256,test_corpus_version,"
                "validation_status,released_by,released_at,version) VALUES(%s,%s,'synthetic-parser','1.2.0',"
                "'raw-v1','listing-v2','gs://synthetic-local-parser/artifact',%s,'synthetic-corpus-v1',"
                "'PRODUCTION',%s,now()-interval '1 day',2)",
                (release_id, candidate.listing.source_id, "b" * 64, APPROVER),
            )
            conn.execute(
                "INSERT INTO intake.parser_runs(parser_run_id,tenant_id,intake_id,source_snapshot_id,parser_release_id,"
                "status,normalized_payload,validation_errors,completed_at,correlation_id) "
                "VALUES(%s,%s,%s,%s,%s,'SUCCEEDED','{\"recorded\":true}'::jsonb,'[]'::jsonb,now()-interval '1 minute',%s)",
                (run_id, TENANT, intake["id"], candidate.listing.snapshot_id, release_id, str(uuid4())),
            )
        store = bundle.assisted_intake_store
        with store.transaction():
            store.refresh(TENANT)
            canonical = store.intakes[intake["id"]]
            canonical.update(source_policy_version=2, policy_state="APPROVED_RETRIEVAL", parser_release_id=release_id,
                             parser_release_version=2, parser_run_id=run_id, parser_input_schema_version="raw-v1",
                             parser_output_schema_version="listing-v2")
            canonical["version"] += 1
            store.flush()
        yield bridge_database, bundle, canonical, candidate
    finally:
        bundle.engine.close()


def _check(bundle, intake, candidate):
    with bundle.engine.lock:
        bundle.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (TENANT,))
        return validate_score_admission(bundle.engine, intake, candidate)


def test_real_jsonb_successful_parser_run_is_accepted(parser_admission):
    _, bundle, intake, candidate = parser_admission
    result = _check(bundle, intake, candidate)
    assert result["parser_run_id"] == intake["parser_run_id"]
    assert result["parser_release_version"] == 2
    assert result["parser_output_schema_version"] == "listing-v2"


@pytest.mark.parametrize("change", ["schema", "revoked", "run_error", "source_expired"])
def test_live_parser_or_source_changes_are_denied(parser_admission, change):
    database, bundle, intake, candidate = parser_admission
    with database.connect() as conn:
        if change == "schema":
            conn.execute("UPDATE intake.parser_releases SET output_schema_version='listing-v3' WHERE parser_release_id=%s",
                         (intake["parser_release_id"],))
        elif change == "revoked":
            conn.execute("UPDATE intake.parser_releases SET validation_status='ROLLED_BACK' WHERE parser_release_id=%s",
                         (intake["parser_release_id"],))
        elif change == "run_error":
            conn.execute("UPDATE intake.parser_runs SET validation_errors='[\"missing-rent\"]'::jsonb WHERE parser_run_id=%s",
                         (intake["parser_run_id"],))
        else:
            conn.execute("UPDATE intake.source_registry SET review_expires_at=now()-interval '1 minute' WHERE source_id=%s",
                         (candidate.listing.source_id,))
    with pytest.raises(ScoreDeliveryDenied):
        _check(bundle, intake, candidate)


def test_snapshot_storage_metadata_changes_cannot_keep_old_admission_hash(parser_admission):
    database, bundle, intake, candidate = parser_admission
    initial = _check(bundle, intake, candidate)
    with database.connect() as conn:
        conn.execute("UPDATE intake.source_snapshots SET raw_object_uri='gs://synthetic-local-parser/replaced' WHERE source_snapshot_id=%s",
                     (candidate.listing.snapshot_id,))
    current = _check(bundle, intake, candidate)
    assert current["source_snapshot_version"] == initial["source_snapshot_version"]
    assert current["source_snapshot_metadata_sha256"] != initial["source_snapshot_metadata_sha256"]
