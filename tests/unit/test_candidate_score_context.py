"""Local synthetic current-input/source/PIT observations and worker parity."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from modules.listing.application.candidate_score_context import CandidateScoreContextReader
from modules.listing.application.intake_score_delivery import (
    IntakeScoreJobRepository,
    ScoreDeliveryDenied,
)
from shared.infrastructure.persistence.assisted_listing_intake import _STATE_COLLECTIONS
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from tests.unit.test_intake_score_admission import _retrieved
from tests.unit.test_intake_score_admission import admission as admission
from tests.unit.test_network_scoring_gate_measurement_contract import draft


class LocalObservationEngine:
    dialect = "postgresql"  # Synthetic query double, never a real SQL server.

    def __init__(self, source: Any) -> None:
        self.source = source
        self.jobs: list[dict[str, Any]] = []
        self.calls: list[tuple[str, tuple]] = []
        self.depth = 0

    @property
    @contextmanager
    def lock(self):
        self.depth += 1
        try:
            yield self
        finally:
            self.depth -= 1

    def query_one(self, sql: str, params: tuple = ()) -> Any:
        assert self.depth > 0, "all observations need the caller's short transaction"
        self.calls.append((sql, params))
        if "set_config" in sql:
            assert params == (self.source.intake["scope"]["tenant_id"],)
            return {}
        return self.source.engine.query_one(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        assert self.depth > 0
        self.calls.append((sql, params))
        assert "workflow.jobs" in sql and "tenant_id=?" in sql and "aggregate_id=?" in sql
        assert "job_type='intake-sitescore'" in sql
        return deepcopy(self.jobs)

    def execute(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("current observation must never write SQL")


class LocalLockedStore:
    def __init__(self, engine: LocalObservationEngine) -> None:
        self._engine = engine
        self.records: dict[tuple[str, str], Any] = {}
        self.calls: list[tuple[str, str]] = []

    def _read_locked(self, collection: str, key: str) -> Any:
        assert self._engine.depth > 0
        self.calls.append((collection, key))
        return deepcopy(self.records.get((collection, key)))

    def refresh(self, *_args: Any) -> None:
        raise AssertionError("read port must not refresh/cache business inputs")


def _listing_with(listing: Any, **changes: Any) -> Any:
    updated = replace(listing, **changes)
    # Listing's producer extensions are not dataclass fields. The actual status
    # producer preserves them; the synthetic prerequisite must do the same.
    for name, value in vars(listing).items():
        if name not in vars(updated):
            object.__setattr__(updated, name, deepcopy(value))
    return updated


class LocalCurrentRepository:
    def __init__(self, tenant: str, engine: Any, candidate: Any) -> None:
        self.tenant_id = tenant
        self._store = TenantScopedDocumentStore(SimpleNamespace(engine=engine), tenant)
        self.listing = _listing_with(candidate.listing, listing_status="candidate")
        self.address = deepcopy(candidate.address)
        self.calls: list[tuple[str, str]] = []
        self.candidate = candidate

    def list_candidates(self) -> list[Any]:
        return [deepcopy(self.candidate)]

    def get_listing(self, key: str) -> Any:
        self.calls.append(("listing", key))
        assert key == self.listing.listing_id
        return deepcopy(self.listing)

    def get_address(self, key: str) -> Any:
        self.calls.append(("address", key))
        assert key == self.address.address_id
        return deepcopy(self.address)


@pytest.fixture
def context(admission):
    tenant = admission.intake["scope"]["tenant_id"]
    record = draft()
    record.listing = replace(
        record.listing, tenant_id=tenant, source_id=admission.intake["source_id"],
        snapshot_id=admission.intake["source_snapshot_id"],
    )
    object.__setattr__(record.listing, "hard_rule_failures", [])
    record.address = replace(record.address, tenant_id=tenant)
    record.dataset_snapshot_id = admission.intake["source_snapshot_id"]
    record.feature_snapshot_time = datetime.now(UTC) - timedelta(minutes=2)
    engine = LocalObservationEngine(admission)
    store = LocalLockedStore(engine)
    repository = LocalCurrentRepository(tenant, engine, record)
    promotion_id, job_id = str(uuid4()), str(uuid4())
    promotion = {
        "promotion_decision_id": promotion_id, "intake_id": admission.intake["intake_id"],
        "tenant_id": tenant, "candidate_site_id": record.candidate_site.candidate_site_id,
        "listing_id": record.candidate_site.listing_id, "site_score_job_id": job_id,
    }
    payload = {
        "promotion_decision_id": promotion_id, "intake_id": admission.intake["intake_id"],
        "candidate_site_id": record.candidate_site.candidate_site_id,
    }
    engine.jobs = [{"job_id": job_id, "payload": payload}]
    store.records[(_STATE_COLLECTIONS["promotions"], f"{tenant}:{promotion_id}")] = promotion
    store.records[(_STATE_COLLECTIONS["intakes"], f"{tenant}:{admission.intake['intake_id']}")] = admission.intake
    metadata_key = (repository._store._collection(DurableAssistedIntakeRepository._LISTING_META),
                    record.candidate_site.listing_id)
    reader = CandidateScoreContextReader(tenant_id=tenant, repository=repository, engine=engine, store=store)
    # Calling the actual worker method verifies that the extraction preserves
    # the existing worker priority; no job constructor or execution is involved.
    worker = object.__new__(IntakeScoreJobRepository)
    worker.store = store
    worker._repository = lambda requested: repository if requested == tenant else None
    return SimpleNamespace(reader=reader, record=record, engine=engine, store=store,
                           repository=repository, tenant=tenant, source=admission,
                           promotion=promotion, payload=payload, metadata_key=metadata_key, worker=worker)


def _worker_current(context):
    with context.engine.lock:
        context.worker._current_inputs(context.tenant, context.record)


def test_qualified_recorded_empty_verdict_without_metadata_row_remains_positive(context):
    before = deepcopy(vars(context.record))
    observed = context.reader.read(context.record)
    assert observed.admission["currentInputs"] == {"state": "verified", "reasonCode": None}
    assert observed.admission["source"]["state"] == "verified"
    assert observed.admission["source"]["sourcePolicyVersion"] == 3
    assert observed.admission["source"]["jobId"] == context.promotion["site_score_job_id"]
    assert observed.admission["pit"] == {"state": "verified", "reasonCode": None}
    assert observed.admission["model"]["state"] == "unverified"
    assert observed.current_draft.listing.hard_rule_failures == []
    assert vars(context.record) == before
    assert context.store.calls[-1] == context.metadata_key
    assert context.store.calls[:2] == [
        (_STATE_COLLECTIONS["intakes"], f"{context.tenant}:{context.source.intake['intake_id']}"),
        (_STATE_COLLECTIONS["promotions"], f"{context.tenant}:{context.promotion['promotion_decision_id']}"),
    ]
    _worker_current(context)
    assert context.engine.depth == 0
    assert all(not sql.startswith(("UPDATE", "INSERT", "DELETE")) for sql, _ in context.engine.calls)


@pytest.mark.parametrize("metadata", [{}, {"otherMetadata": "recorded"}, {
    "hardRuleFailures": [], "prior90dCellNetRevenue": None,
    "prior90dCellTransactionCount": None, "prior90dCellStoreCount": None, "featureSnapshotTime": None,
}])
def test_missing_key_and_legacy_null_pit_do_not_withdraw_domain_recorded_pass(context, metadata):
    context.store.records[context.metadata_key] = metadata
    assert context.reader.read(context.record).admission["currentInputs"]["state"] == "verified"
    _worker_current(context)


@pytest.mark.parametrize("verdict", [None, ["recorded-floor-failure"], "", {}, (), [None]])
def test_current_metadata_withdrawal_keeps_worker_denial_and_observed_verdict(context, verdict):
    context.store.records[context.metadata_key] = {"hardRuleFailures": verdict}
    observed = context.reader.read(context.record)
    assert observed.admission["currentInputs"] == {
        "state": "blocked", "reasonCode": "SITESCORE_CURRENT_HARD_RULE_GATE_DENIED",
    }
    assert observed.current_draft.listing.hard_rule_failures == verdict
    assert context.record.listing.hard_rule_failures == []
    with pytest.raises(ScoreDeliveryDenied, match="SITESCORE_CURRENT_HARD_RULE_GATE_DENIED"):
        _worker_current(context)


@pytest.mark.parametrize("field,value", [
    ("prior90dCellNetRevenue", 123.0), ("prior90dCellTransactionCount", 5),
    ("prior90dCellStoreCount", 3), ("featureSnapshotTime", "2026-10-12T10:00:00+00:00"),
])
def test_current_nonnull_pit_overlay_conflict_cannot_refresh_candidate(context, field, value):
    context.store.records[context.metadata_key] = {field: value}
    observed = context.reader.read(context.record)
    assert observed.admission["currentInputs"]["reasonCode"] == "SITESCORE_CANDIDATE_INPUT_STALE"
    with pytest.raises(ScoreDeliveryDenied, match="SITESCORE_CANDIDATE_INPUT_STALE"):
        _worker_current(context)


@pytest.mark.parametrize("change", ["rent", "address", "extension"])
def test_current_business_records_must_agree_in_full_with_candidate_snapshot(context, change):
    if change == "rent":
        context.repository.listing = _listing_with(context.repository.listing, rent_amount=1.0)
    elif change == "address":
        context.repository.address = replace(context.repository.address, raw_address="Changed recorded address")
    else:
        object.__setattr__(context.repository.listing, "recorded_extension", "changed")
    observed = context.reader.read(context.record)
    assert observed.admission["currentInputs"]["reasonCode"] == "SITESCORE_CANDIDATE_INPUT_STALE"
    with pytest.raises(ScoreDeliveryDenied, match="SITESCORE_CANDIDATE_INPUT_STALE"):
        _worker_current(context)


def test_current_hard_rule_denial_precedes_stale_and_worker_candidate_gate(context):
    context.repository.listing = _listing_with(context.repository.listing, listing_status="archived", rent_amount=1.0)
    context.store.records[context.metadata_key] = {"hardRuleFailures": None}
    with pytest.raises(ScoreDeliveryDenied, match="SITESCORE_CURRENT_HARD_RULE_GATE_DENIED"):
        _worker_current(context)


def test_listing_status_change_is_excluded_from_input_digest_but_still_checked_by_worker(context):
    context.repository.listing = _listing_with(context.repository.listing, listing_status="archived")
    observed = context.reader.read(context.record)
    assert observed.admission["currentInputs"]["reasonCode"] == "SITESCORE_CURRENT_CANDIDATE_GATE_DENIED"
    with pytest.raises(ScoreDeliveryDenied, match="SITESCORE_CURRENT_CANDIDATE_GATE_DENIED"):
        _worker_current(context)


@pytest.mark.parametrize("missing", ["listing", "address"])
def test_missing_current_record_preserves_worker_unavailable_denial(context, missing):
    setattr(context.repository, f"get_{missing}", lambda _key: None)
    observed = context.reader.read(context.record)
    assert observed.admission["currentInputs"]["reasonCode"] == "SITESCORE_CURRENT_INPUT_UNAVAILABLE"
    assert observed.current_draft is None
    assert context.metadata_key not in context.store.calls
    with pytest.raises(ScoreDeliveryDenied, match="SITESCORE_CURRENT_INPUT_UNAVAILABLE"):
        _worker_current(context)


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_exact_job_parent_remains_unverified_without_first_row(context, count):
    context.engine.jobs = context.engine.jobs * count
    observed = context.reader.read(context.record)
    assert observed.admission["source"] == {
        "state": "unverified", "reasonCode": "SITESCORE_SOURCE_PARENT_UNVERIFIED",
    }
    assert observed.admission["currentInputs"]["state"] == "verified"
    assert context.store.calls == [context.metadata_key]
    assert context.source.queries == []


@pytest.mark.parametrize("field", ["tenant_id", "candidate_site_id", "listing_id", "site_score_job_id", "intake_id"])
def test_exact_job_promotion_intake_join_rejects_foreign_parent(context, field):
    context.promotion[field] = str(uuid4())
    observed = context.reader.read(context.record)
    assert observed.admission["source"]["reasonCode"] == "SITESCORE_PARENT_BINDING_DENIED"
    assert context.source.queries == []


@pytest.mark.parametrize("binding", ["tenant", "engine", "foreign_record"])
def test_untrusted_reader_binding_cannot_read_current_records_or_registry(context, binding):
    if binding == "tenant":
        context.repository.tenant_id = "foreign-tenant"
    elif binding == "engine":
        context.repository._store = SimpleNamespace(engine=object())
    else:
        context.record.listing = replace(context.record.listing, tenant_id="foreign-tenant")
    observed = context.reader.read(context.record)
    assert observed.admission["currentInputs"]["state"] in {"blocked", "unverified"}
    assert observed.admission["source"]["state"] == "unverified"
    assert context.engine.calls == [] and context.repository.calls == []
    assert context.store.calls == [] and context.source.queries == []


@pytest.mark.parametrize("field,value,code", [
    ("kill_switch", True, "SITESCORE_SOURCE_POLICY_DENIED"),
    ("production_enabled", False, "SITESCORE_SOURCE_POLICY_DENIED"),
    ("version", 4, "SITESCORE_SOURCE_POLICY_DENIED"),
    ("review_expires_at", datetime(2020, 1, 1, tzinfo=UTC), "SITESCORE_SOURCE_REVIEW_EXPIRED"),
])
def test_existing_current_source_guard_is_reused_for_read_revocation(context, field, value, code):
    context.source.rows["source_registry"][field] = value
    observed = context.reader.read(context.record)
    assert observed.admission["source"] == {"state": "blocked", "reasonCode": code}
    assert observed.admission["currentInputs"]["state"] == "verified"
    assert observed.admission["pit"]["state"] == "verified"
    assert observed.admission["model"]["state"] == "unverified"


def test_existing_recorded_parser_positive_and_revocation_remain_distinct(context):
    _retrieved(context.source)
    assert context.reader.read(context.record).admission["source"]["state"] == "verified"
    context.source.rows["parser_releases"]["validation_status"] = "ROLLED_BACK"
    assert context.reader.read(context.record).admission["source"]["reasonCode"] == "SITESCORE_PARSER_RELEASE_DENIED"


@pytest.mark.parametrize("case", ["missing_time", "stale", "future", "missing_measurement", "zero_axis", "naive_time"])
def test_data_pass_does_not_replace_existing_feature_pit_contract(context, case):
    if case == "missing_time":
        context.record.feature_snapshot_time = None
    elif case == "stale":
        context.record.feature_snapshot_time = datetime.now(UTC) - timedelta(days=91)
    elif case == "future":
        context.record.feature_snapshot_time = datetime.now(UTC) + timedelta(days=1)
    elif case == "missing_measurement":
        context.record.prior_90d_cell_net_revenue = None
    elif case == "naive_time":
        context.record.feature_snapshot_time = datetime(2026, 10, 13)
    else:
        context.record.address = replace(context.record.address, latitude=0.0)
        context.repository.address = deepcopy(context.record.address)
    observed = context.reader.read(context.record)
    assert observed.admission["currentInputs"]["state"] == "verified"
    assert observed.admission["pit"]["state"] == "blocked"
    assert observed.admission["model"]["state"] == "unverified"


def test_unavailable_current_store_is_unverified_without_refresh_or_stale_verified_result(context):
    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("synthetic store unavailable")

    context.store._read_locked = unavailable
    observed = context.reader.read(context.record)
    assert observed.current_draft is None
    assert observed.admission["checkedAt"] is None
    for key in ("currentInputs", "source", "pit", "model"):
        assert observed.admission[key]["state"] == "unverified"
    assert context.engine.depth == 0


def test_parent_lock_order_matches_worker_before_metadata_and_job_lock(context):
    context.reader.read(context.record)
    assert context.store.calls == [
        (_STATE_COLLECTIONS["intakes"], f"{context.tenant}:{context.source.intake['intake_id']}"),
        (_STATE_COLLECTIONS["promotions"], f"{context.tenant}:{context.promotion['promotion_decision_id']}"),
        context.metadata_key,
    ]
    job_queries = [sql for sql, _ in context.engine.calls if "workflow.jobs" in sql]
    assert len(job_queries) == 2
    assert "FOR SHARE" not in job_queries[0]
    assert "FOR SHARE" in job_queries[1]


def test_job_parent_reference_change_during_read_is_not_current_admission(context):
    original = context.engine.query

    def changed(sql: str, params: tuple = ()) -> Any:
        rows = original(sql, params)
        if "FOR SHARE" in sql:
            rows[0]["payload"]["intake_id"] = str(uuid4())
        return rows

    context.engine.query = changed
    observed = context.reader.read(context.record)
    assert observed.admission["source"]["reasonCode"] == "SITESCORE_PARENT_BINDING_DENIED"
    assert context.source.queries == []


@pytest.mark.parametrize("axis", ["latitude", "longitude"])
def test_missing_axis_is_not_a_complete_declared_model_feature_row(context, axis):
    context.record.address = replace(context.record.address, **{axis: None})
    context.repository.address = deepcopy(context.record.address)
    observed = context.reader.read(context.record)
    assert observed.admission["currentInputs"]["state"] == "verified"  # worker's narrower check
    assert observed.admission["pit"]["state"] == "blocked"
    assert observed.admission["model"]["state"] == "unverified"
