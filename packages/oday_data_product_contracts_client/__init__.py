"""ODay data-platform product contract client.

Contract: `odayplus.data-platform-product-client.v1` (task ODP-XR-PRODUCT-CLIENT-001).

odayplus reads EMGI data products through the *released* package published by
`alfloop-dev/oday-data-platform`, pinned in `config/oday_data_product_contracts.toml`.
The release artifacts are vendored and checksum-verified, the models under
`models/` are generated from the pinned schemas, and the compatibility gate
fails CI when a product contract moves underneath the pin.

The producer's internal tables, storage DDL, and relation-ownership catalog are
deliberately not part of this client: odayplus consumes contracts, not producer tables.

Typical use::

    from packages.oday_data_product_contracts_client import product_version
    from packages.oday_data_product_contracts_client.models.site_market_context import SiteMarketContextDocument

    version = product_version()                         # exact pinned release, verified
    context = SiteMarketContextDocument.from_dict(row)  # generated consumer model
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
    ProductVersion,
    diagnostics,
    product_contracts,
    product_version,
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
from .release import ProductRelease, canonical_digest, load_release

__all__ = [
    "ArtifactDigestError",
    "CompatibilityReport",
    "ContractClientError",
    "ContractDrift",
    "ContractInfo",
    "ContractPin",
    "GeneratedClientStaleError",
    "IncompatibleContractError",
    "PinError",
    "PinnedContract",
    "ProductRelease",
    "ProductVersion",
    "canonical_digest",
    "check_release",
    "diagnostics",
    "load_pin",
    "load_release",
    "product_contracts",
    "product_version",
    "reset_cache",
    "verify_release",
]
