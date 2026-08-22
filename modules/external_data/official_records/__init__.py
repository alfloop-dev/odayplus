"""Retained record contracts for official open-data sources (XR-CUTOVER-001).

The provider adapters that fetched these sources were decommissioned by the
cutover; the datasets are now ingested by ``oday-data-platform``. What lives
here is the parse-and-record half odayplus still needs to project artifacts it
already holds: schemas, identity rules and the approved-source registry, with
no HTTP client and no credential of any kind.
"""

__all__: tuple[str, ...] = ()
