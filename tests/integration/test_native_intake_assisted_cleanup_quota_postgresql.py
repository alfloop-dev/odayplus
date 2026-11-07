"""Actual cleanup admission failure must preserve the original business error.

The existing accepted fixture creates the human command and both real jobs
through their native APIs. Its storage provider is explicitly synthetic. The
extra reservation below is an actual quota admission, with no process started;
its continued presence is not proof that any child was reaped. No positive
business history or approval is inserted by this test.
"""

import json

import pytest

from modules.external_data.application.native_assisted_snapshot_transport import (
    NativeAssistedSnapshotProofRejected,
)
from modules.listing.application.native_intake_assisted_entry_runtime import (
    NativeIntakeAssistedEntryRuntime,
)
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_retrieval_quota import (
    NativeRetrievalQuotaExceeded,
)
from shared.jobs.queue import JobStatus
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
from tests.integration.test_native_intake_assisted_entry_requests_postgresql import snapshot

pytestmark = pytest.mark.requires_live_env


def _preserved_business(assisted):
    state = snapshot(assisted)
    return {name: state[name] for name in (
        "intake.intakes", "intake.intake_stage_transitions", "identity.match_cases",
        "expansion.listings", "intake.source_snapshots", "intake.parser_runs",
        "intake.human_corrections", "expansion.promotion_decisions",
    )}


def test_actual_full_cleanup_quota_preserves_original_error_and_unresolved_object(accepted):
    assisted, owner, delivery, io, provider = accepted
    tenant = assisted[2]["tenant"]
    frozen_business = _preserved_business(assisted)
    io.capture()  # Actual native admission/journals; synthetic storage bytes.
    assert io.object_metadata is not None and not io.terminal_attempted
    stored_objects = dict(provider.objects)
    source_id = io.upload_intent["reservation"]["source_id"]
    with owner.graph.transaction(tenant):
        original = owner.graph._one(
            "SELECT status,attempt,timeout_at,payload FROM workflow.jobs "
            "WHERE tenant_id=? AND job_id=?", (tenant, delivery.job_id))
        assert original["status"] == "RUNNING"
        reservation = owner.quota.reserve_storage(source_id,
            global_limit=io.policy.global_max_concurrency,
            source_limit=io.policy.per_source_max_concurrency,
            before=NativeIntakeCommandBudget(owner.source.source))
    # Prove real capacity refusal first; no exception or clock is fabricated.
    with pytest.raises(NativeRetrievalQuotaExceeded,
            match="^NATIVE_ASSISTED_STORAGE_CONCURRENCY_FULL$"):
        with owner.graph.transaction(tenant):
            owner.quota.reserve_storage(source_id,
                global_limit=io.policy.global_max_concurrency,
                source_limit=io.policy.per_source_max_concurrency,
                before=NativeIntakeCommandBudget(owner.source.source))

    failure = NativeAssistedSnapshotProofRejected("SYNTHETIC_ORIGINAL_CAPTURE_PROOF_FAILURE")
    assert NativeIntakeAssistedEntryRuntime(owner)._settle(
        delivery, io.leases, failure, io=io) is None
    with owner.graph.transaction(tenant):
        settled = owner.graph._one(
            "SELECT status,attempt,timeout_at,payload,last_error,result FROM workflow.jobs "
            "WHERE tenant_id=? AND job_id=?", (tenant, delivery.job_id))
        retained = owner.engine.query_one(
            "SELECT reservation_id,source_id,expires_at,expires_at>clock_timestamp() AS unexpired "
            "FROM intake.native_retrieval_inflight_slots WHERE reservation_id=?",
            (reservation.reservation_id,))
    payload = settled["payload"]
    prior_payload = original["payload"]
    error = json.loads(settled["last_error"])
    assert type(payload) is dict and type(prior_payload) is dict and type(error) is dict
    assert settled["status"] == "FAILED" and settled["result"] is None
    assert settled["attempt"] == original["attempt"] == 1
    assert settled["timeout_at"] == original["timeout_at"]
    assert error["error_class"] == type(failure).__name__
    assert payload["settlement_receipt"]["error_class"] == type(failure).__name__
    assert payload["attempt_intents"] == prior_payload["attempt_intents"]
    assert all(item["operation"] != "gcs_delete" for item in payload["attempt_intents"])
    assert owner.queue.get(delivery.job_id).status == JobStatus.FAILED
    assert retained == {"reservation_id": reservation.reservation_id, "source_id": source_id,
        "expires_at": reservation.expires_at, "unexpired": True}
    assert provider.objects == stored_objects and all(call[0] != "delete" for call in provider.calls)
    assert _preserved_business(assisted) == frozen_business
