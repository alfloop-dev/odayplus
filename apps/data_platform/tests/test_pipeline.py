from __future__ import annotations

import inspect
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from apps.data_platform.config import DataPlaneConfig
from apps.data_platform.contracts import (
    BackfillWindow,
    ProjectionBatchResult,
    QuarantineReason,
    ReconciliationResult,
    SourceEnvelope,
    SourceKind,
)
from apps.data_platform.dlt_runtime import RawLoadResult
from apps.data_platform.mapping import SourceContractError, project_merchant
from apps.data_platform.pipeline import DataPlaneRunner
from apps.data_platform.serialization import aggregate_checksum
from apps.data_platform.source import SNAPSHOT_SOURCE_KINDS, MongoSource, envelope_for_document
from apps.data_platform.tests.test_brand_classification_sync import (  # noqa: F401 -- reuse owned PG fixtures
    _pg_admin_params,
    live_store,
)


def _config() -> DataPlaneConfig:
    return DataPlaneConfig(
        mongo_uri="mongodb+srv://service:secret@approved.example/data",
        postgres_dsn="postgresql://service:secret@sql.example/oday",
        batch_size=100,
        max_records_per_run=1_000,
    )


def _window() -> BackfillWindow:
    return BackfillWindow(
        datetime(2026, 7, 23, tzinfo=UTC),
        datetime(2026, 7, 24, tzinfo=UTC),
        "2026-07-23",
    )


class FakeSource:
    def __init__(self, documents: Sequence[dict[str, Any]]) -> None:
        self.documents = documents
        self.requested_limits: list[int] = []

    def iter_envelopes(
        self,
        kind: SourceKind,
        window: BackfillWindow,
        *,
        run_id: str,
        resume_after: str | None,
        limit: int,
    ) -> Iterator[SourceEnvelope]:
        del window
        self.requested_limits.append(limit)
        selected = [
            document
            for document in self.documents
            if resume_after is None or str(document["_id"]) > resume_after
        ]
        for document in selected[:limit]:
            yield envelope_for_document(
                kind,
                document,
                run_id=run_id,
                observed_at=datetime(2026, 7, 24, tzinfo=UTC),
            )


class FakeRawLoader:
    def __init__(self, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self.snapshots: set[str] = set()
        self.fail_on_call = fail_on_call

    def load(self, source_kind: SourceKind, envelopes: Sequence[SourceEnvelope]) -> RawLoadResult:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise TimeoutError("retryable raw destination timeout")
        self.snapshots.update(value.source_snapshot_id for value in envelopes)
        return RawLoadResult(source_kind, len(envelopes), (f"load-{self.calls}",))


class FakeCanonicalStore:
    def __init__(self) -> None:
        self.checkpoints: dict[tuple[SourceKind, str], str] = {}
        self.run_valid: dict[str, set[str]] = {}
        self.run_quarantine: dict[str, dict[str, str]] = {}
        self.canonical_snapshots: set[str] = set()
        self.failed: list[tuple[str, tuple[str, ...]]] = []
        self.completed: list[tuple[str, dict[str, Any]]] = []
        self.run_windows: dict[str, BackfillWindow | None] = {}
        self.checkpoint_windows: list[BackfillWindow | None] = []

    def install(self) -> None:
        return None

    def begin_run(
        self,
        run_id: str,
        source_kind: SourceKind,
        partition_key: str,
        resumed_from: str | None,
        started_at: datetime,
        *,
        window: BackfillWindow | None = None,
    ) -> None:
        del source_kind, partition_key, resumed_from, started_at
        self.run_windows[run_id] = window
        self.run_valid[run_id] = set()
        self.run_quarantine[run_id] = {}

    def apply_batch(
        self,
        source_kind: SourceKind,
        envelopes: Sequence[SourceEnvelope],
        *,
        partition_key: str,
    ) -> ProjectionBatchResult:
        del partition_key
        valid: list[str] = []
        reasons: dict[str, int] = {}
        for envelope in envelopes:
            try:
                if source_kind is not SourceKind.MERCHANT:
                    raise AssertionError("FakeCanonicalStore supports merchant tests only")
                project_merchant(envelope)
            except SourceContractError as exc:
                reason = exc.reason_code.value
                reasons[reason] = reasons.get(reason, 0) + 1
                self.run_quarantine[envelope.run_id][envelope.source_snapshot_id] = reason
                continue
            checksum = f"{envelope.source_snapshot_id}:{envelope.content_sha256}"
            valid.append(checksum)
            self.run_valid[envelope.run_id].add(checksum)
            self.canonical_snapshots.add(envelope.source_snapshot_id)
        return ProjectionBatchResult(tuple(valid), reasons)

    def get_checkpoint(
        self,
        source_kind: SourceKind,
        partition_key: str,
        *,
        window: BackfillWindow | None = None,
    ) -> str | None:
        self.checkpoint_windows.append(window)
        return self.checkpoints.get((source_kind, partition_key))

    def record_checkpoint(
        self,
        source_kind: SourceKind,
        partition_key: str,
        envelope: SourceEnvelope,
        processed_count: int,
    ) -> None:
        del processed_count
        self.checkpoints[(source_kind, partition_key)] = str(envelope.source_document["_id"])

    def reconcile(
        self,
        run_id: str,
        source_kind: SourceKind,
        source_count: int,
        source_checksum: str,
        valid_checksum: str,
    ) -> ReconciliationResult:
        del source_kind
        valid = self.run_valid[run_id]
        quarantined = self.run_quarantine[run_id]
        reason_counts: dict[str, int] = {}
        for reason in quarantined.values():
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        assert valid_checksum == aggregate_checksum(list(valid))
        return ReconciliationResult(
            source_total=source_count,
            valid_loaded=len(valid),
            quarantined_count=len(quarantined),
            raw_count=source_count,
            canonical_count=len(valid),
            source_checksum=source_checksum,
            raw_checksum=source_checksum,
            valid_checksum=valid_checksum,
            canonical_checksum=valid_checksum,
            quarantine_reason_counts=reason_counts,
        )

    def complete_run(self, run_id: str, **kwargs: Any) -> None:
        self.completed.append((run_id, dict(kwargs)))

    def fail_run(
        self,
        run_id: str,
        *,
        source_kind: SourceKind,
        partition_key: str,
        source_snapshot_ids: Sequence[str],
        error: BaseException,
        retryable: bool,
    ) -> None:
        del source_kind, partition_key, error
        assert retryable is True
        self.failed.append((run_id, tuple(source_snapshot_ids)))


def _merchant(index: int, *, malformed: bool = False) -> dict[str, Any]:
    document: dict[str, Any] = {
        "_id": f"{index:04d}",
        "companyName": f"Merchant {index}",
        "country": "TW",
        "currency": "TWD",
        "operation": "active",
        "createdAt": "2026-07-23T01:00:00Z",
    }
    if malformed:
        document.pop("country")
        document.pop("createdAt")
    return document


def test_malformed_record_is_quarantined_without_stopping_valid_rows() -> None:
    store = FakeCanonicalStore()
    runner = DataPlaneRunner(
        _config(),
        source=FakeSource([_merchant(1), _merchant(2, malformed=True), _merchant(3)]),
        raw_loader=FakeRawLoader(),
        canonical_store=store,
    )
    summary = runner.run_partition(SourceKind.MERCHANT, _window())
    assert summary.status == "SUCCEEDED"
    assert summary.reconciliation.source_total == 3
    assert summary.reconciliation.valid_loaded == 2
    assert summary.reconciliation.quarantined_count == 1
    assert summary.reconciliation.quarantine_reason_counts == {
        QuarantineReason.MISSING_REQUIRED_FIELD.value: 1
    }


def test_content_addressed_projection_is_idempotent_across_runs() -> None:
    store = FakeCanonicalStore()
    source = FakeSource([_merchant(1), _merchant(2)])
    runner = DataPlaneRunner(
        _config(),
        source=source,
        raw_loader=FakeRawLoader(),
        canonical_store=store,
    )
    runner.run_partition(SourceKind.MERCHANT, _window(), resume=False)
    runner.run_partition(SourceKind.MERCHANT, _window(), resume=False)
    assert len(store.canonical_snapshots) == 2


def test_partition_is_complete_only_after_a_non_truncated_authoritative_read() -> None:
    store = FakeCanonicalStore()
    source = FakeSource([_merchant(1), _merchant(2), _merchant(3)])
    runner = DataPlaneRunner(
        _config(),
        source=source,
        raw_loader=FakeRawLoader(),
        canonical_store=store,
    )

    first = runner.run_partition(
        SourceKind.MERCHANT,
        _window(),
        resume=False,
        limit=2,
    )
    second = runner.run_partition(
        SourceKind.MERCHANT,
        _window(),
        resume=True,
        limit=2,
    )

    assert first.processed_count == 2
    assert source.requested_limits == [3, 3]
    assert store.completed[0][1]["partition_complete"] is False
    assert second.processed_count == 1
    assert store.completed[1][1]["partition_complete"] is True


def test_partition_at_exact_limit_is_proven_complete_by_one_row_probe() -> None:
    store = FakeCanonicalStore()
    source = FakeSource([_merchant(1), _merchant(2)])
    runner = DataPlaneRunner(
        _config(),
        source=source,
        raw_loader=FakeRawLoader(),
        canonical_store=store,
    )

    summary = runner.run_partition(
        SourceKind.MERCHANT,
        _window(),
        resume=False,
        limit=2,
    )

    assert summary.processed_count == 2
    assert source.requested_limits == [3]
    assert store.completed[0][1]["partition_complete"] is True


def test_retry_resumes_after_last_durable_checkpoint() -> None:
    documents = [_merchant(index) for index in range(101)]
    store = FakeCanonicalStore()
    raw_loader = FakeRawLoader(fail_on_call=2)
    runner = DataPlaneRunner(
        _config(),
        source=FakeSource(documents),
        raw_loader=raw_loader,
        canonical_store=store,
    )
    with pytest.raises(TimeoutError):
        runner.run_partition(SourceKind.MERCHANT, _window())
    assert store.checkpoints[(SourceKind.MERCHANT, "2026-07-23")] == "0099"
    assert len(store.failed) == 1
    assert len(store.failed[0][1]) == 1

    summary = runner.run_partition(SourceKind.MERCHANT, _window())
    assert summary.resumed_from == "0099"
    assert summary.processed_count == 1
    assert len(store.canonical_snapshots) == 101


@pytest.mark.parametrize("kind", list(SourceKind))
def test_runner_persists_only_bounds_applied_by_the_original_source(kind: SourceKind) -> None:
    # Exercise the real MongoSource query builder through the runner, not a
    # copied source-kind classifier or a fake which discards window arguments.
    collection = MagicMock()
    cursor = collection.find.return_value.sort.return_value.limit.return_value.batch_size.return_value
    cursor.__iter__.return_value = iter(())
    config = _config()
    source = MongoSource(config, database={kind.value: collection})
    store = FakeCanonicalStore()
    window = BackfillWindow(
        datetime(2026, 7, 23, 4, tzinfo=UTC),
        datetime(2026, 7, 23, 8, tzinfo=UTC),
        "2026-07-23__2026-07-23",
    )
    runner = DataPlaneRunner(
        config, source=source, raw_loader=FakeRawLoader(), canonical_store=store
    )
    summary = runner.run_partition(kind, window)
    query = collection.find.call_args.args[0]
    if kind in SNAPSHOT_SOURCE_KINDS:
        assert query == {}
        assert store.run_windows[summary.run_id] is None
        assert store.checkpoint_windows == [None]
    else:
        assert query["$or"]
        for predicate in query["$or"]:
            assert list(predicate.values()) == [{"$gte": window.start, "$lt": window.end}]
        assert store.run_windows[summary.run_id] is window
        assert store.checkpoint_windows == [window]
    assert summary.processed_count == 0
    assert store.completed[0][1]["partition_complete"] is True


@pytest.mark.requires_live_env
def test_postgresql_run_and_checkpoint_preserve_actual_window_identity(live_store) -> None:  # noqa: F811 -- imported pytest fixture
    store = live_store.store
    window = BackfillWindow(
        datetime(2026, 7, 23, 4, tzinfo=UTC),
        datetime(2026, 7, 23, 8, tzinfo=UTC),
        "2026-07-23__2026-07-23",
    )
    alias = BackfillWindow(
        datetime(2026, 7, 23, 10, tzinfo=UTC),
        datetime(2026, 7, 23, 12, tzinfo=UTC),
        window.partition_key,
    )
    run_id = str(uuid4())
    store.begin_run(run_id, SourceKind.ORDERS, window.partition_key, None, window.end, window=window)
    envelope = envelope_for_document(
        SourceKind.ORDERS,
        {"_id": "000000000000000000000001", "createdAt": window.start.isoformat()},
        run_id=run_id,
        observed_at=window.end,
    )
    store.record_checkpoint(SourceKind.ORDERS, window.partition_key, envelope, 1)

    # Reopen a store as a worker restart would. The existing checkpoint FK,
    # not a label-only lookup or a second cursor registry, owns provenance.
    restarted = live_store.build()
    assert restarted.get_checkpoint(SourceKind.ORDERS, window.partition_key, window=window) == envelope.source_id
    assert restarted.get_checkpoint(SourceKind.ORDERS, alias.partition_key, window=alias) is None
    assert restarted.get_checkpoint(SourceKind.ORDERS, window.partition_key) is None
    assert restarted.get_checkpoint(SourceKind.TRADE, window.partition_key, window=window) is None
    with live_store.connect() as connection:
        assert connection.execute(
            "SELECT window_start, window_end FROM data_plane.ingestion_runs WHERE run_id = %s",
            (run_id,),
        ).fetchone() == (window.start, window.end)

    # Original runner -> MongoSource -> PostgreSQL complete_run, including a
    # zero-new-row EOF. Aliased labels must use the initial-read query, not A's
    # cursor; zero-row completion does not transfer A's checkpoint to B.
    collection = MagicMock()
    query_cursor = collection.find.return_value.sort.return_value.limit.return_value.batch_size.return_value
    query_cursor.__iter__.return_value = iter(())
    runner = DataPlaneRunner(
        _config(), source=MongoSource(_config(), database={"orders": collection}),
        raw_loader=FakeRawLoader(), canonical_store=restarted,
    )
    for bounds, expected_cursor in ((window, envelope.source_id), (alias, None)):
        summary = runner.run_partition(SourceKind.ORDERS, bounds)
        assert summary.resumed_from == expected_cursor
        assert summary.processed_count == 0
        assert summary.final_cursor == expected_cursor
        query = collection.find.call_args.args[0]
        if expected_cursor is None:
            assert "$and" not in query
            predicates = query["$or"]
        else:
            assert str(query["$and"][1]["_id"]["$gt"]) == expected_cursor
            predicates = query["$and"][0]["$or"]
        for predicate in predicates:
            assert list(predicate.values()) == [{"$gte": bounds.start, "$lt": bounds.end}]
        with live_store.connect() as connection:
            assert connection.execute(
                "SELECT window_start, window_end, resumed_from, final_cursor, "
                "partition_complete, reconciled FROM data_plane.ingestion_runs WHERE run_id = %s",
                (summary.run_id,),
            ).fetchone() == (bounds.start, bounds.end, expected_cursor, expected_cursor, True, True)
    assert restarted.get_checkpoint(SourceKind.ORDERS, window.partition_key, window=window) == envelope.source_id

    # Exact replay remains compatible, but a reused UUID cannot adopt or erase
    # window/source/partition/cursor identity, even before any source is read.
    restarted.begin_run(run_id, SourceKind.ORDERS, window.partition_key, None, window.end, window=window)
    for kind, key, bounds, cursor in (
        (SourceKind.ORDERS, window.partition_key, alias, None),
        (SourceKind.ORDERS, window.partition_key, None, None),
        (SourceKind.TRANSACTION, window.partition_key, window, None),
        (SourceKind.ORDERS, "another-label", None, None),
        (SourceKind.ORDERS, window.partition_key, window, "advanced-cursor"),
    ):
        with pytest.raises(ValueError, match="different extraction identity"):
            restarted.begin_run(run_id, kind, key, cursor, window.end, window=bounds)

    # Existing metadata callers keep their original signature and NULL bounds;
    # new extraction calls must not silently reclassify their unproven history.
    metadata_id = str(uuid4())
    restarted.begin_run(metadata_id, SourceKind.PLACE, window.partition_key, None, window.end)
    with pytest.raises(ValueError, match="different extraction identity"):
        restarted.begin_run(metadata_id, SourceKind.PLACE, window.partition_key, None, window.end, window=window)
    with live_store.connect() as connection:
        assert connection.execute(
            "SELECT window_start, window_end FROM data_plane.ingestion_runs WHERE run_id = %s",
            (metadata_id,),
        ).fetchone() == (None, None)
        # A checkpoint accidentally linked to another source kind/label is not
        # evidence for the requested extraction, despite a matching cursor.
        connection.execute(
            "UPDATE data_plane.checkpoints SET run_id = %s WHERE source_kind = 'orders'",
            (metadata_id,),
        )
    assert restarted.get_checkpoint(SourceKind.ORDERS, window.partition_key, window=window) is None


@pytest.mark.requires_live_env
def test_postgresql_window_install_is_additive_and_leaves_legacy_scope_unproven(live_store) -> None:  # noqa: F811 -- imported pytest fixture
    from psycopg.errors import CheckViolation

    store = live_store.store
    legacy_id = str(uuid4())
    with live_store.connect() as connection:
        connection.execute(
            "ALTER TABLE data_plane.ingestion_runs "
            "DROP CONSTRAINT ingestion_runs_window_bounds_check, "
            "DROP COLUMN window_start, DROP COLUMN window_end"
        )
        connection.execute(
            "INSERT INTO data_plane.ingestion_runs "
            "(run_id, source_database, source_kind, partition_key, status, started_at) "
            "VALUES (%s, 'fongniao_prod', 'orders', '2026-07-23', 'FAILED', %s)",
            (legacy_id, _window().start),
        )
    store.install()
    store.install()
    with live_store.connect() as connection:
        assert connection.execute(
            "SELECT window_start, window_end FROM data_plane.ingestion_runs WHERE run_id = %s",
            (legacy_id,),
        ).fetchone() == (None, None)
        for start, end in (
            (_window().start, None), (None, _window().end),
            (_window().start, _window().start), (_window().end, _window().start),
        ):
            with pytest.raises(CheckViolation), connection.transaction():
                connection.execute(
                    "UPDATE data_plane.ingestion_runs SET window_start = %s, window_end = %s "
                    "WHERE run_id = %s", (start, end, legacy_id),
                )
    legacy_envelope = envelope_for_document(
        SourceKind.ORDERS, {"_id": "legacy-cursor"}, run_id=legacy_id, observed_at=_window().end
    )
    store.record_checkpoint(SourceKind.ORDERS, _window().partition_key, legacy_envelope, 1)
    assert store.get_checkpoint(SourceKind.ORDERS, _window().partition_key, window=_window()) is None


def test_production_constructor_has_no_fixture_or_synthetic_fallback() -> None:
    source = inspect.getsource(DataPlaneRunner.from_env)
    assert "MongoSource(config)" in source
    assert "DltRawLoader(config)" in source
    assert "PsycopgCanonicalStore(config)" in source
    assert "fixture" not in source.lower()
    assert "synthetic" not in source.lower()
