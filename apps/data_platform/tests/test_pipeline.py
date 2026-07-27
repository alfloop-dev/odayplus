from __future__ import annotations

import inspect
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any

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
from apps.data_platform.source import envelope_for_document


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

    def load(
        self, source_kind: SourceKind, envelopes: Sequence[SourceEnvelope]
    ) -> RawLoadResult:
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

    def install(self) -> None:
        return None

    def begin_run(
        self,
        run_id: str,
        source_kind: SourceKind,
        partition_key: str,
        resumed_from: str | None,
        started_at: datetime,
    ) -> None:
        del source_kind, partition_key, resumed_from, started_at
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
                self.run_quarantine[envelope.run_id][
                    envelope.source_snapshot_id
                ] = reason
                continue
            checksum = f"{envelope.source_snapshot_id}:{envelope.content_sha256}"
            valid.append(checksum)
            self.run_valid[envelope.run_id].add(checksum)
            self.canonical_snapshots.add(envelope.source_snapshot_id)
        return ProjectionBatchResult(tuple(valid), reasons)

    def get_checkpoint(
        self, source_kind: SourceKind, partition_key: str
    ) -> str | None:
        return self.checkpoints.get((source_kind, partition_key))

    def record_checkpoint(
        self,
        source_kind: SourceKind,
        partition_key: str,
        envelope: SourceEnvelope,
        processed_count: int,
    ) -> None:
        del processed_count
        self.checkpoints[(source_kind, partition_key)] = str(
            envelope.source_document["_id"]
        )

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
        del run_id, kwargs

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


def test_production_constructor_has_no_fixture_or_synthetic_fallback() -> None:
    source = inspect.getsource(DataPlaneRunner.from_env)
    assert "MongoSource(config)" in source
    assert "DltRawLoader(config)" in source
    assert "PsycopgCanonicalStore(config)" in source
    assert "fixture" not in source.lower()
    assert "synthetic" not in source.lower()
