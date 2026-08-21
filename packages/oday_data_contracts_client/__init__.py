"""ODay data-platform foundation contract client.

Contract: `odayplus.data-platform-foundation-client.v1` (task ODP-XR-CLIENT-001).

odayplus reads the EMGI foundation through the *released* package published by
`alfloop-dev/oday-data-platform`, pinned in `config/oday_data_contracts.toml`.
The release artifacts are vendored and checksum-verified, the models under
`models/` are generated from the pinned schemas, and the compatibility gate
fails CI when a kernel or internal contract moves underneath the pin.

The producer's storage DDL and relation-ownership catalog are deliberately not
part of this client: odayplus consumes contracts, not producer tables.

Typical use::

    from packages.oday_data_contracts_client import foundation_version
    from packages.oday_data_contracts_client.models.store_reference import StoreReference

    version = foundation_version()          # exact pinned release, verified
    store = StoreReference.from_dict(row)   # generated consumer model
"""

from __future__ import annotations

from .compatibility import (
    CompatibilityReport,
    ContractDrift,
    check_release,
    verify_release,
)
from .diagnostics import (
    ContractInfo,
    FoundationVersion,
    diagnostics,
    foundation_contracts,
    foundation_version,
    reset_cache,
)
from .errors import (
    ArtifactDigestError,
    ContractClientError,
    GeneratedClientStaleError,
    IncompatibleContractError,
    PinError,
)
from .pin import ContractPin, PinnedContract, load_pin
from .release import FoundationRelease, canonical_digest, load_release

__all__ = [
    "ArtifactDigestError",
    "CompatibilityReport",
    "ContractClientError",
    "ContractDrift",
    "ContractInfo",
    "ContractPin",
    "FoundationRelease",
    "FoundationVersion",
    "GeneratedClientStaleError",
    "IncompatibleContractError",
    "PinError",
    "PinnedContract",
    "canonical_digest",
    "check_release",
    "diagnostics",
    "foundation_contracts",
    "foundation_version",
    "load_pin",
    "load_release",
    "reset_cache",
    "verify_release",
]
