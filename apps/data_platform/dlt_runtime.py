from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from apps.data_platform.config import DataPlaneConfig
from apps.data_platform.contracts import SourceEnvelope, SourceKind


@dataclass(frozen=True)
class RawLoadResult:
    source_kind: SourceKind
    loaded_count: int
    load_package_ids: tuple[str, ...]


class RawLoader(Protocol):
    def load(
        self, source_kind: SourceKind, envelopes: Sequence[SourceEnvelope]
    ) -> RawLoadResult: ...


class DltRawLoader:
    """Content-addressed raw landing implemented by the dlt PostgreSQL destination."""

    def __init__(self, config: DataPlaneConfig) -> None:
        config.validate()
        self._config = config
        try:
            import dlt
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError(
                "dlt[postgres] is required for production raw ingestion"
            ) from exc
        self._dlt = dlt

    def load(
        self, source_kind: SourceKind, envelopes: Sequence[SourceEnvelope]
    ) -> RawLoadResult:
        if not envelopes:
            return RawLoadResult(source_kind, 0, ())
        table_name = f"raw_{source_kind.value}"
        records = [
            {
                **envelope.as_raw_record(),
                "source_document": envelope.source_document,
            }
            for envelope in envelopes
        ]
        resource = self._dlt.resource(
            records,
            name=table_name,
            table_name=table_name,
            write_disposition="merge",
            primary_key="source_snapshot_id",
            max_table_nesting=0,
        )
        destination = self._dlt.destinations.postgres(
            credentials=self._config.postgres_dsn
        )
        pipeline = self._dlt.pipeline(
            pipeline_name=f"oday_fongniao_{source_kind.value}",
            destination=destination,
            dataset_name=self._config.raw_schema,
            progress="log",
        )
        load_info: Any = pipeline.run(resource)
        packages = tuple(str(value) for value in getattr(load_info, "loads_ids", ()))
        return RawLoadResult(
            source_kind=source_kind,
            loaded_count=len(records),
            load_package_ids=packages,
        )
