"""Source connector framework + internal (IoT / upstream) connectors.

The :class:`SourceConnector` base lands, validates, and canonicalizes one source
dataset with a preserved lineage envelope. Internal connectors here cover the
IoT / upstream datasets; external connectors live in
``modules.external_data.connectors``.
"""

from __future__ import annotations

from modules.integration.application.internal_contracts import internal_contracts
from modules.integration.application.mapping import SourceToCanonicalMapper
from modules.integration.connectors.base import (
    CANONICAL_TO_MAPPER_ENTITY,
    ConnectorRecord,
    ConnectorRun,
    RecordLineage,
    SourceConnector,
    build_field_lineage,
    default_source_id_fields,
    first_time,
    parse_datetime,
    registry_version,
)


def build_internal_connectors(
    *, mapper: SourceToCanonicalMapper | None = None, tenant_id: str = ""
) -> dict[str, SourceConnector]:
    """One connector per internal contract, keyed by contract id."""
    shared_mapper = mapper or SourceToCanonicalMapper()
    return {
        contract.contract_id: SourceConnector(
            contract, mapper=shared_mapper, tenant_id=tenant_id
        )
        for contract in internal_contracts()
    }


__all__ = [
    "CANONICAL_TO_MAPPER_ENTITY",
    "ConnectorRecord",
    "ConnectorRun",
    "RecordLineage",
    "SourceConnector",
    "build_field_lineage",
    "build_internal_connectors",
    "default_source_id_fields",
    "first_time",
    "parse_datetime",
    "registry_version",
]
