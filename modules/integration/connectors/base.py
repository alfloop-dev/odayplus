"""Source connector framework shared by the Integration Layer and the External
Data Platform.

A *connector* is the thin, testable unit that turns one upstream dataset into
landed, canonicalized records. It composes the pieces that already exist:

  * the declarative contract engine (``modules.integration.domain.contracts``)
    runs the data-quality gate and produces ODP-DATA-05 §8 quarantine reasons;
  * the source-to-canonical mapper (``modules.integration.application.mapping``)
    and identity resolution turn accepted records into canonical entities; and
  * the geo pipeline (``modules.external_data.geo``) enriches address-bearing
    records with geocode / H3 output (wired by the external connectors).

Every record a connector emits carries a :class:`RecordLineage` envelope that
preserves the source id, observation/event time, ingestion time, schema version,
field-level lineage, and (for rejected records) the quarantine reason. That is
the contract the rest of the platform depends on (ODP-DATA-03 §9, ODP-DATA-05).

This module is the dependency-free core; concrete external connectors live in
``modules.external_data.connectors``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

from modules.integration.application.mapping import (
    ENTITY_TYPES,
    FieldLineage,
    SourceToCanonicalMapper,
)
from modules.integration.domain.contracts import (
    ContractIssue,
    SourceContract,
    load_index,
    validate_record,
)

# Record fields that, in order, supply the canonical observation / event time.
_OBSERVATION_FIELDS = (
    "observation_time",
    "observed_at",
    "last_verified_at",
    "source_snapshot_time",
    "snapshot_time",
    "received_at",
)
_EVENT_FIELDS = (
    "event_time",
    "business_time",
    "occurred_at",
    "effective_date",
    "available_from",
)

# canonical_target -> source-to-canonical mapper entity type. Only the entities
# the mapper understands today are listed; everything else is landed and
# validated with lineage but left for a downstream typed mapping.
CANONICAL_TO_MAPPER_ENTITY = {
    "store": "store",
    "machine": "machine",
    "transaction": "transaction",
    "listing": "listing",
    "address_location": "address",
}

_registry_version_cache: list[str] = []


def registry_version() -> str:
    """Source-contract registry version, used as a schema-version fallback."""
    if not _registry_version_cache:
        _registry_version_cache.append(str(load_index().get("version", "")))
    return _registry_version_cache[0]


@dataclass(frozen=True)
class RecordLineage:
    """Provenance envelope preserved for every landed source record."""

    contract_id: str
    source_system: str
    source_id: str
    source_record_id: str
    canonical_target: str
    mapping_id: str
    schema_version: str
    event_time: datetime | None
    observation_time: datetime | None
    ingestion_time: datetime
    field_lineage: tuple[FieldLineage, ...] = ()
    quarantine_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorRecord:
    """One record after the connector has landed and canonicalized it."""

    accepted: bool
    canonical_target: str
    canonical: Any | None
    lineage: RecordLineage
    geocode: Any | None = None  # GeocodeResult when geo enrichment ran
    issues: tuple[ContractIssue, ...] = ()

    @property
    def quarantined(self) -> bool:
        return not self.accepted


@dataclass(frozen=True)
class ConnectorRun:
    """Outcome of running a connector over a batch of raw records."""

    connector_id: str
    contract_id: str
    canonical_target: str
    records: tuple[ConnectorRecord, ...] = ()

    @property
    def accepted(self) -> tuple[ConnectorRecord, ...]:
        return tuple(r for r in self.records if r.accepted)

    @property
    def quarantined(self) -> tuple[ConnectorRecord, ...]:
        return tuple(r for r in self.records if not r.accepted)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def quarantined_count(self) -> int:
        return len(self.quarantined)

    @property
    def total(self) -> int:
        return len(self.records)

    def canonical_entities(self) -> list[Any]:
        return [r.canonical for r in self.accepted if r.canonical is not None]

    def quarantine_reasons(self) -> set[str]:
        reasons: set[str] = set()
        for record in self.quarantined:
            reasons.update(record.lineage.quarantine_reasons)
        return reasons


class SourceConnector:
    """Land + validate + canonicalize one source dataset.

    Subclasses override :meth:`canonicalize` to produce a typed canonical entity
    (and optionally a geocode result); the default implementation routes through
    the shared source-to-canonical mapper when the canonical target is supported
    and otherwise lands the validated record as a passthrough payload.
    """

    #: Override in subclasses to pin the canonical target; defaults to contract.
    canonical_target: str = ""

    def __init__(
        self,
        contract: SourceContract,
        *,
        geo_pipeline: Any | None = None,
        mapper: SourceToCanonicalMapper | None = None,
        source_record_id_fields: Sequence[str] = (),
        tenant_id: str = "",
    ) -> None:
        self.contract = contract
        self.geo_pipeline = geo_pipeline
        self.mapper = mapper or SourceToCanonicalMapper()
        self.tenant_id = tenant_id
        self.source_record_id_fields = (
            tuple(source_record_id_fields) or default_source_id_fields(contract)
        )
        self.schema_version = contract.schema_version or registry_version()

    @property
    def connector_id(self) -> str:
        return f"CONN::{self.contract.contract_id}"

    @property
    def target(self) -> str:
        return self.canonical_target or self.contract.canonical_target

    def ingest(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        ingestion_time: datetime | None = None,
        as_of: datetime | None = None,
    ) -> ConnectorRun:
        landed_at = ingestion_time or datetime.now(UTC)
        out: list[ConnectorRecord] = []
        for record in records:
            result = validate_record(self.contract, dict(record))
            base = self._base_lineage(record, landed_at)
            if not result.ok:
                out.append(
                    ConnectorRecord(
                        accepted=False,
                        canonical_target=self.target,
                        canonical=None,
                        lineage=replace(
                            base, quarantine_reasons=result.quarantine_reasons()
                        ),
                        issues=result.issues,
                    )
                )
                continue
            canonical, lineage_fields, geocode = self.canonicalize(record, as_of=as_of)
            out.append(
                ConnectorRecord(
                    accepted=True,
                    canonical_target=self.target,
                    canonical=canonical,
                    lineage=replace(base, field_lineage=tuple(lineage_fields)),
                    geocode=geocode,
                )
            )
        return ConnectorRun(
            connector_id=self.connector_id,
            contract_id=self.contract.contract_id,
            canonical_target=self.target,
            records=tuple(out),
        )

    # -- canonicalization ---------------------------------------------------

    def canonicalize(
        self, record: Mapping[str, Any], *, as_of: datetime | None = None
    ) -> tuple[Any, tuple[FieldLineage, ...], Any | None]:
        """Map an accepted record to a canonical entity.

        Default: route through the shared mapper when the canonical target is
        supported, else land the validated record as a passthrough payload.
        """

        entity_type = CANONICAL_TO_MAPPER_ENTITY.get(self.target)
        if entity_type in ENTITY_TYPES:
            mapping = self.mapper.map_record(
                entity_type,
                record,
                source_id=self.contract.source_system,
                tenant_id=self.tenant_id,
            )
            return mapping.canonical, mapping.field_lineage, None
        return dict(record), (), None

    # -- lineage helpers ----------------------------------------------------

    def _base_lineage(
        self, record: Mapping[str, Any], ingestion_time: datetime
    ) -> RecordLineage:
        return RecordLineage(
            contract_id=self.contract.contract_id,
            source_system=self.contract.source_system,
            source_id=str(
                record.get("source_id") or self.contract.source_system or ""
            ),
            source_record_id=self._source_record_id(record),
            canonical_target=self.target,
            mapping_id=self.contract.mapping_id,
            schema_version=self.schema_version,
            event_time=first_time(record, _EVENT_FIELDS),
            observation_time=first_time(record, _OBSERVATION_FIELDS),
            ingestion_time=ingestion_time,
        )

    def _source_record_id(self, record: Mapping[str, Any]) -> str:
        for name in self.source_record_id_fields:
            value = record.get(name)
            if value not in (None, ""):
                return str(value)
        return ""


def default_source_id_fields(contract: SourceContract) -> tuple[str, ...]:
    """Best-effort natural-key fields: ``source_*_id`` then first required."""
    explicit = tuple(
        f.name
        for f in contract.fields
        if f.name.startswith("source_") and f.name.endswith("_id")
    )
    if explicit:
        return explicit
    required = contract.required_fields()
    return required[:1]


def first_time(
    record: Mapping[str, Any], candidate_fields: Sequence[str]
) -> datetime | None:
    for name in candidate_fields:
        parsed = parse_datetime(record.get(name))
        if parsed is not None:
            return parsed
    return None


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def build_field_lineage(
    record: Mapping[str, Any], pairs: Sequence[tuple[str, str]]
) -> tuple[FieldLineage, ...]:
    """Build field lineage for ``(canonical_field, source_field)`` pairs that are
    present in the record."""
    lineage: list[FieldLineage] = []
    for canonical_field, source_field in pairs:
        value = record.get(source_field)
        if value not in (None, ""):
            lineage.append(FieldLineage(canonical_field, source_field, value))
    return tuple(lineage)


__all__ = [
    "CANONICAL_TO_MAPPER_ENTITY",
    "ConnectorRecord",
    "ConnectorRun",
    "RecordLineage",
    "SourceConnector",
    "build_field_lineage",
    "default_source_id_fields",
    "first_time",
    "parse_datetime",
    "registry_version",
]
