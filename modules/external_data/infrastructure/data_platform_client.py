"""Data Platform Client Infrastructure Adapter.

Contract: `odayplus.data-platform-foundation-client.v1`, `odayplus.data-platform-product-client.v1`.
Part of Task: `ODP-LEGACY-FACADE-001`.

This infrastructure adapter encapsulates communication with the generated foundation and product
contract clients published by `alfloop-dev/oday-data-platform`. It provides typed access to versioned
product models (e.g. SiteMarketContext, MarketCellProfile, CatchmentProfile, PropertyObservation)
and foundation datasets (e.g. StoreReference, StoreDayCoverage, PlatformFoundation) without exposing
raw producer tables or direct database credentials.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

# Foundation contracts client
from packages.oday_data_contracts_client import (
    ContractClientError as FoundationClientError,
)
from packages.oday_data_contracts_client import (
    FoundationVersion,
    foundation_version,
)
from packages.oday_data_contracts_client import (
    diagnostics as foundation_diagnostics,
)
from packages.oday_data_contracts_client import (
    verify_release as verify_foundation_release,
)
from packages.oday_data_contracts_client.models import (
    EMGIPlatformFoundationConfig,
    OperationalStartObservation,
    StoreDailyPerformance,
    StoreDayCoverage,
    StoreReference,
)

# Product contracts client
from packages.oday_data_product_contracts_client import (
    ContractClientError as ProductClientError,
)
from packages.oday_data_product_contracts_client import (
    ProductRelease,
    ProductVersion,
    load_release,
    product_version,
)
from packages.oday_data_product_contracts_client import (
    diagnostics as product_diagnostics,
)
from packages.oday_data_product_contracts_client import (
    verify_release as verify_product_release,
)
from packages.oday_data_product_contracts_client.models import (
    CatchmentProfileDocument,
    MarketCellProfileDocument,
    PropertyObservationDocument,
    SiteMarketContextDocument,
)
from packages.oday_data_product_contracts_client.models.catchment_profile import (
    CatchmentProfile,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    MarketCellProfile,
)
from packages.oday_data_product_contracts_client.models.property_observation import (
    PropertyEntity,
    PropertyListingObservation,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    PeriodGrain,
    SiteMarketContext,
)


class DataPlatformClientError(Exception):
    """Base exception for all DataPlatformClient infrastructure errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "data_platform_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details) if details else {}


class DataPlatformConnectionError(DataPlatformClientError):
    """Raised when communication with the data platform fails."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="data_platform_connection_error", details=details)


class DataPlatformDocumentNotFoundError(DataPlatformClientError):
    """Raised when a requested contract document or entity is not found."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="document_not_found", details=details)


class DataPlatformValidationError(DataPlatformClientError):
    """Raised when platform data fails schema validation.

    Public context must stay independent of unvalidated payloads and decoder
    values: parsing can fail before the product envelope is tenant-checked.
    """

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="contract_validation_error", details=details)


class DataPlatformIntegrityError(DataPlatformClientError):
    """Raised when release digests or contract lock verification fails."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="release_integrity_error", details=details)


@runtime_checkable
class DataPlatformTransport(Protocol):
    """Transport protocol for querying documents from the data platform."""

    def fetch_document(
        self,
        contract_id: str,
        *,
        document_id: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        """Fetch a single document payload by contract ID and optional document ID or query parameters."""
        ...

    def query_records(
        self,
        contract_id: str,
        *,
        filter_params: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Query multiple record payloads matching filter parameters."""
        ...


# Query parameters of `emgi.coverage-surface.v1` that describe a coverage cell
# rather than the surface envelope. Consumers filter a surface down to the
# cells they asked about, so these must never be matched top-level.
COVERAGE_CELL_PARAMS = ("admin_code", "h3_index", "business_date", "readiness", "state")

# Root-document fields that carry a product document's contract/version
# identity. Only the names live here: which of them a document declares, and
# what each may hold, come from the pinned release schema of that contract.
PRODUCT_VERSION_ENVELOPE_FIELDS = ("contract_id", "contract_version", "product_version")

# Keywords in a version-envelope field schema that annotate rather than
# constrain. Any other keyword is a constraint this boundary does not
# evaluate, so it fails closed instead of being silently skipped.
_ENVELOPE_ANNOTATION_KEYWORDS = frozenset(
    {"default", "description", "title", "examples", "$comment"}
)

_JSON_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "null": lambda value: value is None,
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
}

_ProductDocumentT = TypeVar(
    "_ProductDocumentT",
    SiteMarketContextDocument,
    MarketCellProfileDocument,
    CatchmentProfileDocument,
    PropertyObservationDocument,
)


def _json_equal(value: Any, expected: Any) -> bool:
    """JSON equality: ``true`` is not ``1``."""
    return value == expected and isinstance(value, bool) == isinstance(expected, bool)


def _envelope_field_violations(declared: Mapping[str, Any], value: Any) -> list[str]:
    """Return the schema keywords of one declared envelope field that ``value`` fails."""
    failed: list[str] = []
    for keyword, constraint in declared.items():
        if keyword in _ENVELOPE_ANNOTATION_KEYWORDS:
            continue
        if keyword == "type":
            types = [constraint] if isinstance(constraint, str) else list(constraint)
            ok = any(_JSON_TYPE_CHECKS.get(name, lambda _v: False)(value) for name in types)
        elif keyword == "enum":
            ok = any(_json_equal(value, option) for option in constraint)
        elif keyword == "const":
            ok = _json_equal(value, constraint)
        elif keyword == "minLength":
            ok = not isinstance(value, str) or len(value) >= constraint
        elif keyword == "maxLength":
            ok = not isinstance(value, str) or len(value) <= constraint
        elif keyword == "pattern":
            ok = not isinstance(value, str) or re.search(constraint, value) is not None
        else:
            ok = False
        if not ok:
            failed.append(keyword)
    return failed


@dataclass
class InMemoryDataPlatformTransport:
    """In-memory transport implementation for testing and local replay."""

    _documents: dict[str, dict[str, Mapping[str, Any]]] = field(default_factory=dict)
    _records: dict[str, list[Mapping[str, Any]]] = field(default_factory=dict)

    def store_document(
        self,
        contract_id: str,
        document_id: str,
        data: Mapping[str, Any],
    ) -> None:
        """Store a document by contract ID and document ID."""
        if contract_id not in self._documents:
            self._documents[contract_id] = {}
        self._documents[contract_id][document_id] = data

    def store_record(self, contract_id: str, data: Mapping[str, Any]) -> None:
        """Store a single record under a contract ID."""
        if contract_id not in self._records:
            self._records[contract_id] = []
        self._records[contract_id].append(data)

    def fetch_document(
        self,
        contract_id: str,
        *,
        document_id: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        query_params = dict(params) if params else {}

        if contract_id not in self._documents and contract_id not in self._records:
            return None

        # 1. Exact document_id match in documents. An id hit still has to
        #    satisfy the rest of the query: returning it on tenant_id alone
        #    would silently drop every other filter for keyed reads, so a
        #    caller could ask for a state it excluded and still be served.
        if (
            document_id
            and contract_id in self._documents
            and document_id in self._documents[contract_id]
        ):
            doc = self._documents[contract_id][document_id]
            if self._document_matches_params(contract_id, doc, query_params):
                return doc

        # 2. Search documents by params
        if contract_id in self._documents:
            for doc_key, doc in self._documents[contract_id].items():
                if (
                    document_id
                    and doc_key != document_id
                    and doc.get("document_id") != document_id
                    and doc.get("profile_id") != document_id
                ):
                    continue
                if self._document_matches_params(contract_id, doc, query_params):
                    return doc

        # 3. Fallback to records
        if contract_id in self._records:
            for rec in self._records[contract_id]:
                if self._record_matches_params(contract_id, rec, query_params):
                    return rec

        return None

    def query_records(
        self,
        contract_id: str,
        *,
        filter_params: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        records = self._records.get(contract_id, [])
        if not filter_params:
            return list(records)
        matched = []
        for rec in records:
            if all(rec.get(k) == v for k, v in filter_params.items()):
                matched.append(rec)
        return matched

    def _document_matches_params(
        self,
        contract_id: str,
        doc: Mapping[str, Any],
        params: Mapping[str, Any],
    ) -> bool:
        """Check if a document payload matches the requested query parameters."""
        if not params:
            return True

        # Check tenant_id at top level if specified
        if "tenant_id" in params and "tenant_id" in doc:
            if doc["tenant_id"] != params["tenant_id"]:
                return False

        if contract_id == "emgi.site-market-context.v1":
            if not self._has_item_params(params, "site_id"):
                return True
            contexts = doc.get("contexts", [])
            return any(self._context_item_matches(ctx, params) for ctx in contexts)

        if contract_id == "emgi.market-cell-profile.v1":
            if not self._has_item_params(params, "cell_id"):
                return True
            cells = doc.get("cells", [])
            return any(self._cell_item_matches(cell, params) for cell in cells)

        if contract_id == "emgi.catchment-profile.v1":
            if not self._has_item_params(params, "catchment_id"):
                return True
            profiles = doc.get("profiles", [])
            return any(self._catchment_item_matches(prof, params) for prof in profiles)

        if contract_id == "emgi.coverage-surface.v1":
            if not self._coverage_surface_matches(doc, params):
                return False
            for k, v in params.items():
                if k in COVERAGE_CELL_PARAMS:
                    continue
                if k in doc and str(doc[k]) != str(v):
                    return False
            return True

        if contract_id == "emgi.property-observation.v1":
            if "property_id" in params:
                props = doc.get("properties", [])
                if not any(p.get("property_id") == params["property_id"] for p in props):
                    return False
            if "listing_id" in params:
                listings = doc.get("listing_observations", [])
                if not any(
                    obs.get("listing_obs_id") == params["listing_id"]
                    or obs.get("source_listing_id") == params["listing_id"]
                    for obs in listings
                ):
                    return False
            return True

        if contract_id in (
            "oday.store-reference.v1",
            "oday.store-coverage.v1",
            "oday.store-daily-performance.v1",
        ):
            for k, v in params.items():
                if k == "date_key":
                    if doc.get("business_date") != v and doc.get("date_key") != v:
                        return False
                elif k in doc and doc[k] != v:
                    return False
            return True

        for k, v in params.items():
            if k in doc and str(doc[k]) != str(v):
                return False
        return True

    def _context_item_matches(self, ctx: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
        if "site_id" in params:
            identity = ctx.get("identity", {})
            if identity.get("site_id") != params["site_id"]:
                return False
        if "period_grain" in params:
            grain = str(ctx.get("period_grain"))
            target_grain = str(
                params["period_grain"].value
                if hasattr(params["period_grain"], "value")
                else params["period_grain"]
            )
            if grain != target_grain:
                return False
        if "period_key" in params and params["period_key"] is not None:
            if ctx.get("period_key") != params["period_key"]:
                return False
        return True

    def _cell_item_matches(self, cell: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
        if "cell_id" in params:
            if (
                cell.get("cell_id") != params["cell_id"]
                and cell.get("h3_index") != params["cell_id"]
            ):
                return False
        if "period_grain" in params:
            grain = str(cell.get("period_grain"))
            target_grain = str(
                params["period_grain"].value
                if hasattr(params["period_grain"], "value")
                else params["period_grain"]
            )
            if grain != target_grain:
                return False
        if "period_key" in params and params["period_key"] is not None:
            if cell.get("period_key") != params["period_key"]:
                return False
        return True

    @staticmethod
    def _has_item_params(params: Mapping[str, Any], identity_key: str) -> bool:
        """True when the query names a predicate about the nested items.

        A tenant-only query says nothing about individual contexts, cells or
        profiles, so the nested list must not decide the match: an envelope
        that carries an empty or absent list is still the document asked for.
        """
        return any(
            params.get(key) is not None for key in (identity_key, "period_grain", "period_key")
        )

    def _coverage_surface_matches(self, doc: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
        """Match a coverage surface by the cell-level predicates in the query.

        `admin_code`, `h3_index`, `business_date`, `readiness` and `state`
        describe individual coverage cells, not the surface envelope, so a
        surface qualifies only when at least one of its cells satisfies every
        supplied predicate. Matching them against the envelope instead would
        select a ready surface for a query that asked for blocked coverage.
        """
        predicates = {k: params[k] for k in COVERAGE_CELL_PARAMS if params.get(k) is not None}
        if not predicates:
            return True
        return any(
            all(str(cell.get(k)) == str(v) for k, v in predicates.items())
            for cell in doc.get("cells", [])
        )

    def _catchment_item_matches(self, prof: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
        if "catchment_id" in params:
            boundary_id = (
                prof.get("boundary", {}).get("catchment_id")
                if isinstance(prof.get("boundary"), dict)
                else None
            )
            if (
                prof.get("profile_id") != params["catchment_id"]
                and boundary_id != params["catchment_id"]
            ):
                return False
        if "period_grain" in params:
            grain = str(prof.get("period_grain"))
            target_grain = str(
                params["period_grain"].value
                if hasattr(params["period_grain"], "value")
                else params["period_grain"]
            )
            if grain != target_grain:
                return False
        if "period_key" in params and params["period_key"] is not None:
            if prof.get("period_key") != params["period_key"]:
                return False
        return True

    def _record_matches_params(
        self, contract_id: str, rec: Mapping[str, Any], params: Mapping[str, Any]
    ) -> bool:
        for k, v in params.items():
            if k in rec and str(rec[k]) != str(v):
                return False
        return True


class DataPlatformClient:
    """Client adapter for reading released foundation and product data contracts.

    Guarantees:
    1. Read models are generated only from pinned contract releases (`oday_data_contracts_client`
       and `oday_data_product_contracts_client`).
    2. Strict read-only semantics: no provider credentials, no raw fetch, and no direct source-snapshot writes.
    3. Fail-closed contract validation and comprehensive release diagnostics.
    4. Strict polymorphic transport communication via DataPlatformTransport.
    """

    def __init__(self, transport: DataPlatformTransport | None = None) -> None:
        if transport is None:
            raise DataPlatformClientError(
                "DataPlatformClient requires an explicit DataPlatformTransport; "
                "InMemoryDataPlatformTransport is not a silent production default.",
                code="missing_transport",
            )
        self._transport = transport
        self._product_release: ProductRelease | None = None

    def _verified_product_release(self) -> ProductRelease:
        """Load the checksum-verified pinned product release once per client."""
        if self._product_release is None:
            try:
                release = load_release()
                verify_product_release(release)
            except ProductClientError as err:
                raise DataPlatformIntegrityError(
                    f"Product contract release verification failed: {err}"
                ) from err
            self._product_release = release
        return self._product_release

    def _version_envelope_violations(
        self, contract_id: str, raw: Mapping[str, Any]
    ) -> list[str]:
        """Check the raw root envelope against its pinned version-field constraints.

        Generated ``from_dict`` fills a missing ``contract_version`` or
        ``product_version`` from its default and keeps any other string, so
        this must run on the fetched mapping before decoding. Only fields the
        pinned root model declares are checked, with exactly the constraints
        the schema states; a document that declares none is not given any.
        Violations name the field and keyword only, never the payload value.
        """
        release = self._verified_product_release()
        try:
            pinned = release.pin.contract(contract_id)
            schema = release.schema_for(pinned)
        except ProductClientError as err:
            raise DataPlatformIntegrityError(
                f"Pinned product contract {contract_id} is unavailable: {err}"
            ) from err
        defs = schema.get("$defs") or {}
        root = defs[pinned.root_model] if pinned.root_model in defs else schema
        properties = root.get("properties")
        if not isinstance(properties, Mapping):
            raise DataPlatformIntegrityError(
                f"Pinned product contract {contract_id} declares no root model properties"
            )
        required = set(root.get("required") or ())

        violations: list[str] = []
        for name in PRODUCT_VERSION_ENVELOPE_FIELDS:
            declared = properties.get(name)
            if not isinstance(declared, Mapping):
                continue
            if name not in raw:
                if name in required:
                    violations.append(f"{name}:required")
                continue
            violations.extend(
                f"{name}:{keyword}"
                for keyword in _envelope_field_violations(declared, raw[name])
            )
        return violations

    def _decode_product_document(
        self,
        contract_id: str,
        model: type[_ProductDocumentT],
        raw: Mapping[str, Any],
        *,
        preserve_missing_tenant: bool,
    ) -> _ProductDocumentT:
        """Decode a fetched product document after its version envelope holds."""
        if not isinstance(raw, Mapping):
            raise DataPlatformValidationError(
                f"Failed to parse {model.__name__}",
                details={"contract_id": contract_id},
            )
        violations = self._version_envelope_violations(contract_id, raw)
        if violations:
            raise DataPlatformValidationError(
                f"{model.__name__} version envelope does not satisfy the pinned contract",
                details={
                    "contract_id": contract_id,
                    "version_envelope_violations": violations,
                },
            )
        payload = self._preserve_missing_product_tenant(raw) if preserve_missing_tenant else raw
        try:
            return model.from_dict(payload)
        except Exception as err:
            raise DataPlatformValidationError(
                f"Failed to parse {model.__name__}",
                details={"contract_id": contract_id},
            ) from err

    @staticmethod
    def _preserve_missing_product_tenant(raw: Mapping[str, Any]) -> dict[str, Any]:
        """Keep an omitted tenant envelope omitted through generated parsing.

        Some released product models use ``"default"`` as the generated
        dataclass default for their optional ``tenant_id`` field. Treating
        that code-generation fallback as a real owner would turn an
        unscoped payload into a cross-tenant decision instead of the required
        fail-closed missing-tenant decision.
        """
        payload = dict(raw)
        payload.setdefault("tenant_id", None)
        return payload

    @property
    def transport(self) -> DataPlatformTransport:
        return self._transport

    # -----------------------------------------------------------------------
    # Version & Diagnostics APIs
    # -----------------------------------------------------------------------

    def get_foundation_version(self) -> FoundationVersion:
        """Return the exact verified foundation release version."""
        try:
            return foundation_version()
        except FoundationClientError as err:
            raise DataPlatformIntegrityError(
                f"Foundation contract release verification failed: {err}"
            ) from err

    def get_product_version(self) -> ProductVersion:
        """Return the exact verified product release version."""
        try:
            return product_version()
        except ProductClientError as err:
            raise DataPlatformIntegrityError(
                f"Product contract release verification failed: {err}"
            ) from err

    def verify_integrity(self) -> dict[str, Any]:
        """Verify release integrity across both foundation and product clients."""
        try:
            foundation_report = verify_foundation_release()
            product_report = verify_product_release()
            return {
                "status": "healthy",
                "foundation": {
                    "compatible": foundation_report.compatible,
                    "release_id": foundation_report.release_id,
                    "semantic_version": foundation_report.semantic_version,
                    "contracts_checked": len(foundation_report.checked_contracts),
                },
                "product": {
                    "compatible": product_report.compatible,
                    "release_id": product_report.release_id,
                    "semantic_version": product_report.semantic_version,
                    "contracts_checked": len(product_report.checked_contracts),
                },
            }
        except Exception as err:
            raise DataPlatformIntegrityError(f"Integrity check failed: {err}") from err

    def get_diagnostics(self) -> dict[str, Any]:
        """Return runtime diagnostics for all pinned data contracts."""
        return {
            "foundation": foundation_diagnostics(),
            "product": product_diagnostics(),
        }

    # -----------------------------------------------------------------------
    # Product Contracts: Site Market Context (emgi.site-market-context.v1)
    # -----------------------------------------------------------------------

    def get_site_market_context_document(
        self,
        document_id: str | None = None,
        *,
        site_id: str | None = None,
        period_grain: PeriodGrain | str | None = None,
        period_key: str | None = None,
        tenant_id: str | None = None,
    ) -> SiteMarketContextDocument:
        """Fetch and parse a complete SiteMarketContextDocument."""
        params: dict[str, Any] = {}
        if site_id:
            params["site_id"] = site_id
        if period_grain is not None:
            params["period_grain"] = str(
                period_grain.value if isinstance(period_grain, PeriodGrain) else period_grain
            )
        if period_key is not None:
            params["period_key"] = period_key
        if tenant_id:
            params["tenant_id"] = tenant_id

        raw = self._transport.fetch_document(
            "emgi.site-market-context.v1",
            document_id=document_id,
            params=params,
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"SiteMarketContextDocument not found (document_id={document_id}, site_id={site_id}, period_grain={period_grain}, period_key={period_key})",
                details={
                    "document_id": document_id,
                    "site_id": site_id,
                    "period_grain": str(period_grain),
                    "period_key": period_key,
                    "tenant_id": tenant_id,
                },
            )
        return self._decode_product_document(
            "emgi.site-market-context.v1",
            SiteMarketContextDocument,
            raw,
            preserve_missing_tenant=True,
        )

    def get_site_market_context(
        self,
        site_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
    ) -> SiteMarketContext:
        """Retrieve a specific SiteMarketContext object by site_id, matching period_grain and period_key."""
        normalized_grain = (
            PeriodGrain(period_grain) if isinstance(period_grain, str) else period_grain
        )
        doc = self.get_site_market_context_document(
            site_id=site_id,
            period_grain=normalized_grain,
            period_key=period_key,
            tenant_id=tenant_id,
        )
        for ctx in doc.contexts:
            if ctx.identity.site_id == site_id:
                if ctx.period_grain != normalized_grain:
                    continue
                if period_key is not None and ctx.period_key != period_key:
                    continue
                return ctx

        raise DataPlatformDocumentNotFoundError(
            f"SiteMarketContext not found for site_id={site_id}, period_grain={normalized_grain.value}, period_key={period_key}",
            details={
                "site_id": site_id,
                "period_grain": normalized_grain.value,
                "period_key": period_key,
                "tenant_id": tenant_id,
            },
        )

    # -----------------------------------------------------------------------
    # Product Contracts: Market Cell Profile (emgi.market-cell-profile.v1)
    # -----------------------------------------------------------------------

    def get_market_cell_profile_document(
        self,
        document_id: str | None = None,
        *,
        cell_id: str | None = None,
        period_grain: PeriodGrain | str | None = None,
        period_key: str | None = None,
        tenant_id: str | None = None,
    ) -> MarketCellProfileDocument:
        """Fetch and parse a complete MarketCellProfileDocument."""
        params: dict[str, Any] = {}
        if cell_id:
            params["cell_id"] = cell_id
        if period_grain is not None:
            params["period_grain"] = str(
                period_grain.value if isinstance(period_grain, PeriodGrain) else period_grain
            )
        if period_key is not None:
            params["period_key"] = period_key
        if tenant_id:
            params["tenant_id"] = tenant_id

        raw = self._transport.fetch_document(
            "emgi.market-cell-profile.v1",
            document_id=document_id,
            params=params,
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"MarketCellProfileDocument not found (document_id={document_id}, cell_id={cell_id}, period_grain={period_grain}, period_key={period_key})",
                details={
                    "document_id": document_id,
                    "cell_id": cell_id,
                    "period_grain": str(period_grain),
                    "period_key": period_key,
                    "tenant_id": tenant_id,
                },
            )
        return self._decode_product_document(
            "emgi.market-cell-profile.v1",
            MarketCellProfileDocument,
            raw,
            preserve_missing_tenant=True,
        )

    def get_market_cell_profile(
        self,
        cell_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
    ) -> MarketCellProfile:
        """Retrieve a specific MarketCellProfile object by cell_id, matching period_grain and period_key."""
        normalized_grain = (
            PeriodGrain(period_grain) if isinstance(period_grain, str) else period_grain
        )
        doc = self.get_market_cell_profile_document(
            cell_id=cell_id,
            period_grain=normalized_grain,
            period_key=period_key,
            tenant_id=tenant_id,
        )
        for cell in doc.cells:
            if cell.cell_id == cell_id or cell.h3_index == cell_id:
                if cell.period_grain != normalized_grain:
                    continue
                if period_key is not None and cell.period_key != period_key:
                    continue
                return cell

        raise DataPlatformDocumentNotFoundError(
            f"MarketCellProfile not found for cell_id={cell_id}, period_grain={normalized_grain.value}, period_key={period_key}",
            details={
                "cell_id": cell_id,
                "period_grain": normalized_grain.value,
                "period_key": period_key,
                "tenant_id": tenant_id,
            },
        )

    # -----------------------------------------------------------------------
    # Product Contracts: Catchment Profile (emgi.catchment-profile.v1)
    # -----------------------------------------------------------------------

    def get_catchment_profile_document(
        self,
        document_id: str | None = None,
        *,
        catchment_id: str | None = None,
        period_grain: PeriodGrain | str | None = None,
        period_key: str | None = None,
        tenant_id: str | None = None,
    ) -> CatchmentProfileDocument:
        """Fetch and parse a complete CatchmentProfileDocument."""
        params: dict[str, Any] = {}
        if catchment_id:
            params["catchment_id"] = catchment_id
        if period_grain is not None:
            params["period_grain"] = str(
                period_grain.value if isinstance(period_grain, PeriodGrain) else period_grain
            )
        if period_key is not None:
            params["period_key"] = period_key
        if tenant_id:
            params["tenant_id"] = tenant_id

        raw = self._transport.fetch_document(
            "emgi.catchment-profile.v1",
            document_id=document_id,
            params=params,
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"CatchmentProfileDocument not found (document_id={document_id}, catchment_id={catchment_id}, period_grain={period_grain}, period_key={period_key})",
                details={
                    "document_id": document_id,
                    "catchment_id": catchment_id,
                    "period_grain": str(period_grain),
                    "period_key": period_key,
                    "tenant_id": tenant_id,
                },
            )
        return self._decode_product_document(
            "emgi.catchment-profile.v1",
            CatchmentProfileDocument,
            raw,
            preserve_missing_tenant=False,
        )

    def get_catchment_profile(
        self,
        catchment_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
    ) -> CatchmentProfile:
        """Retrieve a specific CatchmentProfile object by catchment_id, matching period_grain and period_key."""
        normalized_grain = (
            PeriodGrain(period_grain) if isinstance(period_grain, str) else period_grain
        )
        doc = self.get_catchment_profile_document(
            catchment_id=catchment_id,
            period_grain=normalized_grain,
            period_key=period_key,
            tenant_id=tenant_id,
        )
        for prof in doc.profiles:
            is_match = prof.profile_id == catchment_id or (
                hasattr(prof, "boundary") and prof.boundary.catchment_id == catchment_id
            )
            if is_match:
                if prof.period_grain != normalized_grain:
                    continue
                if period_key is not None and prof.period_key != period_key:
                    continue
                return prof

        raise DataPlatformDocumentNotFoundError(
            f"CatchmentProfile not found for catchment_id={catchment_id}, period_grain={normalized_grain.value}, period_key={period_key}",
            details={
                "catchment_id": catchment_id,
                "period_grain": normalized_grain.value,
                "period_key": period_key,
                "tenant_id": tenant_id,
            },
        )

    # -----------------------------------------------------------------------
    # Product Contracts: Property Observation (emgi.property-observation.v1)
    # -----------------------------------------------------------------------

    def get_property_observation_document(
        self,
        document_id: str | None = None,
        *,
        property_id: str | None = None,
        listing_id: str | None = None,
        tenant_id: str | None = None,
    ) -> PropertyObservationDocument:
        """Fetch and parse a complete PropertyObservationDocument."""
        params: dict[str, Any] = {}
        if property_id:
            params["property_id"] = property_id
        if listing_id:
            params["listing_id"] = listing_id
        if tenant_id:
            params["tenant_id"] = tenant_id

        raw = self._transport.fetch_document(
            "emgi.property-observation.v1",
            document_id=document_id,
            params=params,
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"PropertyObservationDocument not found (document_id={document_id}, property_id={property_id}, listing_id={listing_id})",
                details={
                    "document_id": document_id,
                    "property_id": property_id,
                    "listing_id": listing_id,
                    "tenant_id": tenant_id,
                },
            )
        return self._decode_product_document(
            "emgi.property-observation.v1",
            PropertyObservationDocument,
            raw,
            preserve_missing_tenant=False,
        )

    def get_property_entity(
        self, property_id: str, *, tenant_id: str | None = None
    ) -> PropertyEntity:
        """Retrieve a specific PropertyEntity by property_id."""
        doc = self.get_property_observation_document(property_id=property_id, tenant_id=tenant_id)
        for prop in doc.properties:
            if prop.property_id == property_id:
                return prop

        raise DataPlatformDocumentNotFoundError(
            f"PropertyEntity not found for property_id={property_id}",
            details={"property_id": property_id, "tenant_id": tenant_id},
        )

    def get_listing_observation(
        self, listing_id: str, *, tenant_id: str | None = None
    ) -> PropertyListingObservation:
        """Retrieve a specific PropertyListingObservation by listing_obs_id or source_listing_id."""
        doc = self.get_property_observation_document(listing_id=listing_id, tenant_id=tenant_id)
        for obs in doc.listing_observations:
            if obs.listing_obs_id == listing_id or obs.source_listing_id == listing_id:
                return obs

        raise DataPlatformDocumentNotFoundError(
            f"PropertyListingObservation not found for listing_id={listing_id}",
            details={"listing_id": listing_id, "tenant_id": tenant_id},
        )

    # -----------------------------------------------------------------------
    # Foundation Contracts: Platform Foundation & Store Reference
    # -----------------------------------------------------------------------

    def get_platform_foundation_config(self) -> EMGIPlatformFoundationConfig:
        """Fetch and parse EMGI Platform Foundation Config."""
        raw = self._transport.fetch_document("emgi.platform-foundation.v1")
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                "EMGIPlatformFoundationConfig document not found"
            )
        try:
            return EMGIPlatformFoundationConfig.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError(
                "Failed to parse EMGIPlatformFoundationConfig"
            ) from err

    def get_store_reference(self, store_id: str) -> StoreReference:
        """Retrieve StoreReference for a store_id."""
        raw = self._transport.fetch_document(
            "oday.store-reference.v1", document_id=store_id, params={"store_id": store_id}
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"StoreReference not found for store_id={store_id}",
                details={"store_id": store_id},
            )
        try:
            return StoreReference.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError("Failed to parse StoreReference") from err

    def get_store_day_coverage(self, store_id: str, date_key: str) -> StoreDayCoverage:
        """Retrieve StoreDayCoverage for (store_id, date_key)."""
        doc_id = f"{store_id}:{date_key}"
        raw = self._transport.fetch_document(
            "oday.store-coverage.v1",
            document_id=doc_id,
            params={"store_id": store_id, "date_key": date_key, "business_date": date_key},
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"StoreDayCoverage not found for store_id={store_id}, date={date_key}",
                details={"store_id": store_id, "date_key": date_key},
            )
        try:
            return StoreDayCoverage.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError("Failed to parse StoreDayCoverage") from err

    def get_store_daily_performance(self, store_id: str, date_key: str) -> StoreDailyPerformance:
        """Retrieve StoreDailyPerformance for (store_id, date_key)."""
        doc_id = f"{store_id}:{date_key}"
        raw = self._transport.fetch_document(
            "oday.store-daily-performance.v1",
            document_id=doc_id,
            params={"store_id": store_id, "date_key": date_key, "business_date": date_key},
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"StoreDailyPerformance not found for store_id={store_id}, date={date_key}",
                details={"store_id": store_id, "date_key": date_key},
            )
        try:
            return StoreDailyPerformance.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError(
                "Failed to parse StoreDailyPerformance"
            ) from err

    def get_operational_start_observation(self, store_id: str) -> OperationalStartObservation:
        """Retrieve OperationalStartObservation for store_id."""
        doc_id = store_id
        raw = self._transport.fetch_document(
            "oday.operational-start-observation.v1",
            document_id=doc_id,
            params={"store_id": store_id},
        )
        if raw is None:
            raise DataPlatformDocumentNotFoundError(
                f"OperationalStartObservation not found for store_id={store_id}",
                details={"store_id": store_id},
            )
        try:
            return OperationalStartObservation.from_dict(raw)
        except Exception as err:
            raise DataPlatformValidationError(
                "Failed to parse OperationalStartObservation"
            ) from err


__all__ = [
    "DataPlatformClient",
    "DataPlatformClientError",
    "DataPlatformConnectionError",
    "DataPlatformDocumentNotFoundError",
    "DataPlatformIntegrityError",
    "DataPlatformTransport",
    "DataPlatformValidationError",
    "InMemoryDataPlatformTransport",
]
