from __future__ import annotations

from datetime import UTC, datetime
from itertools import islice
from typing import Iterable, Iterator, Sequence
from uuid import uuid4

from apps.data_platform.config import DataPlaneConfig
from apps.data_platform.contracts import (
    BackfillWindow,
    ProjectionBatchResult,
    RunSummary,
    SourceEnvelope,
    SourceKind,
)
from apps.data_platform.dlt_runtime import DltRawLoader, RawLoader
from apps.data_platform.serialization import aggregate_checksum
from apps.data_platform.source import MongoSource
from apps.data_platform.store import CanonicalStore, PsycopgCanonicalStore


class DataPlaneRunError(RuntimeError):
    """Raised when infrastructure or reconciliation prevents a durable run."""


def _batches(
    values: Iterable[SourceEnvelope], size: int
) -> Iterator[tuple[SourceEnvelope, ...]]:
    iterator = iter(values)
    while batch := tuple(islice(iterator, size)):
        yield batch


class DataPlaneRunner:
    def __init__(
        self,
        config: DataPlaneConfig,
        *,
        source: MongoSource,
        raw_loader: RawLoader,
        canonical_store: CanonicalStore,
    ) -> None:
        config.validate()
        self._config = config
        self._source = source
        self._raw_loader = raw_loader
        self._store = canonical_store

    @classmethod
    def from_env(cls) -> DataPlaneRunner:
        config = DataPlaneConfig.from_env()
        store = PsycopgCanonicalStore(config)
        store.install()
        return cls(
            config,
            source=MongoSource(config),
            raw_loader=DltRawLoader(config),
            canonical_store=store,
        )

    def run_partition(
        self,
        source_kind: SourceKind,
        window: BackfillWindow,
        *,
        resume: bool = True,
        limit: int | None = None,
        run_id: str | None = None,
    ) -> RunSummary:
        effective_limit = limit or self._config.max_records_per_run
        if effective_limit > self._config.max_records_per_run:
            raise ValueError("Requested limit exceeds ODP_DATA_MAX_RECORDS_PER_RUN")
        run_id = run_id or str(uuid4())
        started_at = datetime.now(UTC)
        resumed_from = (
            self._store.get_checkpoint(source_kind, window.partition_key)
            if resume
            else None
        )
        self._store.begin_run(
            run_id,
            source_kind,
            window.partition_key,
            resumed_from,
            started_at,
        )
        processed = 0
        source_snapshot_checksums: list[str] = []
        valid_snapshot_checksums: list[str] = []
        final_cursor = resumed_from
        active_snapshots: Sequence[str] = ()
        try:
            envelopes = self._source.iter_envelopes(
                source_kind,
                window,
                run_id=run_id,
                resume_after=resumed_from,
                limit=effective_limit,
            )
            for batch in _batches(envelopes, self._config.batch_size):
                active_snapshots = tuple(value.source_snapshot_id for value in batch)
                raw_result = self._raw_loader.load(source_kind, batch)
                if raw_result.loaded_count != len(batch):
                    raise DataPlaneRunError(
                        f"dlt loaded {raw_result.loaded_count} of {len(batch)} raw records"
                    )
                projection: ProjectionBatchResult = self._store.apply_batch(
                    source_kind,
                    batch,
                    partition_key=window.partition_key,
                )
                if projection.valid_loaded + projection.quarantined_count != len(batch):
                    raise DataPlaneRunError(
                        "Canonical validation did not account for every raw record"
                    )
                source_snapshot_checksums.extend(
                    f"{value.source_snapshot_id}:{value.content_sha256}"
                    for value in batch
                )
                valid_snapshot_checksums.extend(projection.valid_snapshot_checksums)
                processed += len(batch)
                final = batch[-1]
                final_cursor = str(
                    final.source_document.get("_id") or final.source_id
                )
                self._store.record_checkpoint(
                    source_kind,
                    window.partition_key,
                    final,
                    processed,
                )
                active_snapshots = ()
            reconciliation = self._store.reconcile(
                run_id,
                source_kind,
                processed,
                aggregate_checksum(source_snapshot_checksums),
                aggregate_checksum(valid_snapshot_checksums),
            )
            finished_at = datetime.now(UTC)
            self._store.complete_run(
                run_id,
                final_cursor=final_cursor,
                processed_count=processed,
                reconciliation=reconciliation,
                finished_at=finished_at,
            )
            summary = RunSummary(
                run_id=run_id,
                source_kind=source_kind,
                partition_key=window.partition_key,
                processed_count=processed,
                resumed_from=resumed_from,
                final_cursor=final_cursor,
                reconciliation=reconciliation,
                started_at=started_at,
                finished_at=finished_at,
            )
            if not reconciliation.reconciled:
                raise DataPlaneRunError(
                    f"Reconciliation failed for run {run_id}: {summary.as_dict()}"
                )
            return summary
        except BaseException as exc:
            if not isinstance(exc, DataPlaneRunError) or "Reconciliation failed" not in str(exc):
                self._store.fail_run(
                    run_id,
                    source_kind=source_kind,
                    partition_key=window.partition_key,
                    source_snapshot_ids=active_snapshots,
                    error=exc,
                    retryable=True,
                )
            raise
