"""Real canonical manual history binds parser facts without restoring grants."""
import hashlib
import json

import pytest
from fastapi import HTTPException

from modules.external_data.application.native_manual_json_parser import parse_manual_evidence
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_parser_input import NativeParserManualInputReader
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_parser_prerequisites import (
    NativeParserCommandBudget,
    NativeParserSQLClock,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    accepted as accepted,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    assisted as assisted,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    complete,
    retire_settings,
    snapshot,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    past_manual as past_manual,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    recheck as recheck,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    storage_ready as storage_ready,
)
from tests.integration.test_native_intake_assisted_entry_execution_postgresql import (
    waiting as waiting,
)

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def completed_manual(accepted):
    return accepted, complete(accepted)


def read(case, *, current=False):
    accepted, result = case
    assisted, owner, _, _, _ = accepted
    tenant = assisted[2]["tenant"]
    with owner.graph.transaction(tenant):
        budget = NativeParserCommandBudget(NativeParserSQLClock(owner.engine))
        budget()
        intake = owner.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE",
            (tenant, result["intake_id"]))
        reader = NativeParserManualInputReader(owner.graph)
        value = reader.read(intake, before=budget)
        if current:
            principal, source, classified = reader.current_authority(intake, value, before=budget)
            assert principal.subject_id == value.lineage.requested_by
            assert source["source_id"] == value.lineage.source_id and classified.valid_until is not None
        budget()
        return value


def test_actual_manual_parent_binds_real_source_bytes_and_exact_original_proposals(completed_manual):
    accepted, result = completed_manual
    assisted, _, delivery, _, provider = accepted
    frozen, calls = snapshot(assisted), list(provider.calls)
    value = read(completed_manual, current=True)
    body, metadata = next(iter(provider.objects.values()))
    assert value.parent_job_id == delivery.job_id and value.parent_event_id == result["outbox_event_id"]
    assert value.snapshot()["source_snapshot_id"] == result["source_snapshot_id"]
    assert value.snapshot()["object_generation"] == metadata.generation
    assert hashlib.sha256(body).hexdigest() == value.lineage.content_sha256
    facts = parse_manual_evidence(body, value.lineage).payloads()
    assert facts["parsed_payload"] == value.command().fields.values() and facts["field_confidence"] == {}
    assert value.lineage.requested_by == assisted[2]["actor"]
    assert value.command().fields.sha256 == value.lineage.fields_sha256
    assert "parsed_payload" not in value.binding() and "command_json" not in value.binding()
    assert value.sha256 == read(completed_manual).sha256
    assert snapshot(assisted) == frozen and provider.calls == calls


@pytest.mark.parametrize("loss", ["human_role", "source_permission", "old_settings"])
def test_history_survives_retirement_but_does_not_grant_current_execution(completed_manual, monkeypatch, loss):
    accepted, result = completed_manual
    assisted, _, _, _, provider = accepted
    if loss == "old_settings":
        retire_settings(monkeypatch)
        # Old settings are irrelevant to immutable lineage; parser runtime
        # policy/key authority is a separate mandatory owner check.
    else:
        with assisted[3].connect() as connection:
            if loss == "human_role":
                connection.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (assisted[2]["actor"],))
            else:
                connection.execute("UPDATE intake.source_registry SET kill_switch=true,assisted_entry_approval_ref=NULL")
    calls, frozen = list(provider.calls), snapshot(assisted)
    assert read(completed_manual).snapshot()["source_snapshot_id"] == result["source_snapshot_id"]
    if loss != "old_settings":
        with pytest.raises((PermissionError, HTTPException, NativeAssistedEntryConflict)):
            read(completed_manual, current=True)
    assert provider.calls == calls and snapshot(assisted) == frozen


def test_independent_review_status_keeps_original_facts_but_changed_original_value_is_rejected(completed_manual):
    accepted, result = completed_manual
    assisted = accepted[0]
    first = read(completed_manual)
    with assisted[3].connect() as connection:
        # Simulate a later independent lifecycle record; this test does not
        # implement or confer authority to approve/apply any correction.
        connection.execute("UPDATE intake.human_corrections SET status='REJECTED',reviewed_by=%s,"
            "reviewed_at=clock_timestamp(),version=version+1 WHERE intake_id=%s",
            (assisted[2]["reviewer"], result["intake_id"]))
    assert read(completed_manual).sha256 == first.sha256
    with assisted[3].connect() as connection:
        connection.execute("UPDATE intake.human_corrections SET corrected_value='999'::jsonb WHERE intake_id=%s "
            "AND field_path='rent'", (result["intake_id"],))
    frozen, calls = snapshot(assisted), list(accepted[4].calls)
    # The parser boundary deliberately translates underlying ValueError
    # families into its own typed rejection; it must still reject the actual
    # changed value and leave both evidence and provider state untouched.
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_MANUAL_INPUT_REJECTED$"):
        read(completed_manual)
    assert snapshot(assisted) == frozen and accepted[4].calls == calls


def test_parent_audit_corruption_cannot_be_replaced_by_success_labels(completed_manual):
    accepted, result = completed_manual
    assisted = accepted[0]
    with assisted[3].connect() as connection:
        connection.execute("UPDATE audit.audit_events SET after_value=%s::jsonb WHERE audit_event_id=%s",
            (json.dumps({"processing_state": "PARSING", "status": "SUCCEEDED"}), result["audit_event_id"]))
    frozen, calls = snapshot(assisted), list(accepted[4].calls)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_MANUAL_INPUT_REJECTED$"):
        read(completed_manual)
    assert snapshot(assisted) == frozen and accepted[4].calls == calls
