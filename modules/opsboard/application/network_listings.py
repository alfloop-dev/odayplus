"""Network listing intake service for Operator Console R4/R5.

Owns the Listing Radar state used by ``/api/v1/operator/network-listings``:

- R4 HeatZone/listing/candidate identifiers.
- Listing to candidate conversion.
- Duplicate merge while retaining source evidence.
- Hard-rule archive with reason.
- R5 human-assisted URL intake (submit/correct/decide/retry/promote).

Persistence is injected, not assumed. The service composes with two optional
repositories and never reaches into their internals:

- ``listing_repository`` for Listing Radar rows.
- ``intake_repository`` (:class:`AssistedIntakeRepository`) for assisted intake
  records and their idempotency cache.

When a repository is omitted the service falls back to a process-local
implementation (:class:`InMemoryAssistedIntakeRepository`), which keeps tests
and fixture-only runs self-contained. In the composed application both are
durable — see ``shared.infrastructure.persistence.operator_network_listings``
and the wiring in ``apps/api/app/routes/operator.py`` — so intake records and
replayed writes survive a restart.

Writes are deterministic and idempotent: a replayed ``(action, key)`` returns
the original response from the repository-backed idempotency cache rather than
applying the effect twice.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Protocol

# These bindings are recorded by the property/observation pipeline. The
# operator adapter carries them through writes; it never derives new IDs.
_LISTING_LINEAGE_FIELDS = (
    "property_id", "propertyId", "platform_property_id",
    "listing_obs_id", "listingObsId", "platform_observation_id",
)


def _atomic_intake_submission(method: Callable[..., Any]) -> Callable[..., Any]:
    """Keep the workflow, intake and existing actor-bound replay in one unit of work."""
    @wraps(method)
    def invoke(self: Any, *args: Any, **kwargs: Any) -> Any:
        transaction = getattr(self._intakes, "transaction", None)
        if transaction is None:
            return method(self, *args, **kwargs)
        before = copy.deepcopy((self._state, self._idempotency_cache, self._idempotency_bindings))
        try:
            with transaction(intake_id=kwargs.get("intake_id")):
                self._load_intakes()
                self._load_idempotency_cache()
                if kwargs.get("intake_id"):
                    authorize = kwargs.get("authorize_existing")
                    if authorize is None and not self._seed_fixtures:
                        raise NetworkListingPolicyError("INTAKE_SCOPE_CONTEXT_REQUIRED")
                    if authorize is not None:
                        authorize(_copy(self._listing_intake(kwargs["intake_id"])))
                return method(self, *args, **kwargs)
        except BaseException:
            self._state, self._idempotency_cache, self._idempotency_bindings = before
            raise
    return invoke


@dataclass(frozen=True)
class IntakeIdempotencyRecord:
    """A replayable write response keyed by ``(action, key)``."""

    action: str
    key: str
    response: dict[str, Any]
    actor_subject_id: str | None = None
    request_sha256: str | None = None


@dataclass(frozen=True)
class OperatorWrite:
    action: str
    key: str | None
    actor_subject_id: str
    request_sha256: str
    replayed_response: dict[str, Any] | None = None


class AssistedIntakeRepository(Protocol):
    """Public persistence contract for assisted listing intake.

    The service depends on this contract only, so a durable implementation can
    be substituted without the application layer reaching into a generic
    document store or any other backing detail.
    """

    def list_intakes(self) -> list[dict[str, Any]]: ...

    def get_intake(self, intake_id: str) -> dict[str, Any] | None: ...

    def save_intake(self, intake: dict[str, Any]) -> None: ...

    def create_intake_if_absent(self, intake: dict[str, Any]) -> tuple[dict[str, Any], bool]: ...

    def list_idempotency_records(self) -> list[IntakeIdempotencyRecord]: ...

    def save_idempotency_record(self, record: IntakeIdempotencyRecord) -> None: ...

    def get_listing_metadata(self, listing_id: str) -> dict[str, Any]: ...

    def save_listing_metadata(self, listing_id: str, metadata: dict[str, Any]) -> None: ...

    def get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]: ...

    def save_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any]) -> None: ...

    def get_promotion(self, promo_id: str) -> dict[str, Any] | None: ...

    def save_promotion(self, promo: dict[str, Any]) -> None: ...

    def list_promotions(self) -> list[dict[str, Any]]: ...

    def clear(self) -> None: ...


@dataclass
class InMemoryAssistedIntakeRepository:
    """Process-local implementation of :class:`AssistedIntakeRepository`.

    Used when the service runs without a durable backend; state is lost on
    restart, which is exactly what the durable implementation exists to fix.
    """

    intakes: dict[str, dict[str, Any]] = field(default_factory=dict)
    idempotency: dict[tuple[str, str], IntakeIdempotencyRecord] = field(default_factory=dict)
    listing_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidate_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    promotions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def list_intakes(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.intakes.values()]

    def get_intake(self, intake_id: str) -> dict[str, Any] | None:
        item = self.intakes.get(intake_id)
        return copy.deepcopy(item) if item is not None else None

    def save_intake(self, intake: dict[str, Any]) -> None:
        self.intakes[intake["id"]] = copy.deepcopy(intake)

    def create_intake_if_absent(self, intake: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if intake["id"] in self.intakes:
            return copy.deepcopy(self.intakes[intake["id"]]), False
        self.intakes[intake["id"]] = copy.deepcopy(intake)
        return copy.deepcopy(intake), True

    def list_idempotency_records(self) -> list[IntakeIdempotencyRecord]:
        return list(self.idempotency.values())

    def save_idempotency_record(self, record: IntakeIdempotencyRecord) -> None:
        self.idempotency[(record.action, record.key)] = record

    def get_listing_metadata(self, listing_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.listing_metadata.get(listing_id) or {})

    def save_listing_metadata(self, listing_id: str, metadata: dict[str, Any]) -> None:
        self.listing_metadata[listing_id] = copy.deepcopy(metadata)

    def get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.candidate_metadata.get(candidate_id) or {})

    def save_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any]) -> None:
        self.candidate_metadata[candidate_id] = copy.deepcopy(metadata)

    def get_promotion(self, promo_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.promotions.get(promo_id))

    def save_promotion(self, promo: dict[str, Any]) -> None:
        self.promotions[promo["promotion_decision_id"]] = copy.deepcopy(promo)

    def list_promotions(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.promotions.values()]

    def clear(self) -> None:
        self.intakes.clear()
        self.idempotency.clear()
        self.listing_metadata.clear()
        self.candidate_metadata.clear()
        self.promotions.clear()


class NetworkListingNotFound(RuntimeError):
    """Raised when a listing/candidate/zone id is unknown."""


class NetworkListingConflict(RuntimeError):
    """Raised when a requested mutation conflicts with current state."""


class NetworkListingPolicyError(RuntimeError):
    """Raised when a mutation violates the network intake policy."""


class NetworkListingPreconditionRequired(NetworkListingPolicyError):
    """A governed write did not carry its actual listing revision."""


class NetworkListingVersionUnavailable(NetworkListingPolicyError):
    """The listing producer has not published an authoritative revision."""


def _require_acknowledged_risk(
    *, risk_summary: str | None, risk_acknowledged: bool, action_label: str
) -> str:
    """Validate the caller-supplied risk disclosure for a high-impact write.

    The summary must come from the caller and be acknowledged there: a
    server-invented summary would record consent to text the operator never
    saw, which is exactly what the audit trail is supposed to evidence.
    """
    summary = (risk_summary or "").strip()
    if not summary:
        raise NetworkListingPolicyError(f"risk summary is required to {action_label}")
    if not risk_acknowledged:
        raise NetworkListingPolicyError(
            f"risk acknowledgement is required to {action_label}"
        )
    return summary


def _require_governed_write_context(
    *,
    idempotency_key: str | None,
    correlation_id: str | None,
    action_label: str,
) -> str:
    key = (idempotency_key or "").strip()
    if not key:
        raise NetworkListingPolicyError(f"idempotency key is required to {action_label}")
    if not (correlation_id or "").strip():
        raise NetworkListingPolicyError(f"correlation id is required to {action_label}")
    return key


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            output.append(value)
            seen.add(value)
    return output


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    """Return the first key that is present and not None.

    Used by the listing/candidate dict round trip so a legitimate ``0`` or
    ``0.0`` is preserved instead of being swallowed by an ``or`` fallback.
    """

    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _missing_assisted_entry_fields(values: dict[str, Any]) -> list[str]:
    """Which mandatory assisted-entry fields ``values`` still does not supply.

    Matching may only run once every mandatory field carries a real value, so a
    blank or non-positive ``rent``/``areaPing`` counts as missing rather than
    being coerced to ``0`` -- a zero rent would otherwise present an incomplete
    submission as a complete one.
    """

    from modules.external_data.application.assisted_intake import (
        ASSISTED_ENTRY_REQUIRED_FIELDS,
    )

    missing: list[str] = []
    for field_name in ASSISTED_ENTRY_REQUIRED_FIELDS:
        value = values.get(field_name)
        if value in (None, ""):
            missing.append(field_name)
            continue
        if field_name in ("rent", "areaPing"):
            try:
                if float(value) <= 0:
                    missing.append(field_name)
            except (ValueError, TypeError):
                missing.append(field_name)
    return missing


def _optional_number(value: Any, cast: Any) -> Any:
    """Cast a point-in-time feature, keeping ``None`` as an explicit gap.

    ``None`` must stay ``None`` rather than becoming ``0``: the SiteScore v2
    contract distinguishes "no recorded cell activity" (a data gap that fails
    closed) from a genuine zero.
    """

    if value is None or value == "":
        return None
    return cast(value)


def _optional_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _seed_state(*, tenant_id: str = "tenant-a") -> dict[str, Any]:
    state = {
        "heatZones": [
            {
                "id": "HZ-01",
                "label": "信義松仁生活圈",
                "rank": 1,
                "centroid": [121.5668, 25.0330],
                "demandGap": 0.86,
                "competitionIndex": 0.34,
                "cannibalizationRisk": "medium",
                "rentBand": "NT$52k-88k/month",
                "confidence": 0.93,
                "recommendedLens": "demand",
                "reasons": [
                    "Office-lunch and late-night demand gap remains high.",
                    "Transit and residential POI mix supports ODAY_G2 format.",
                    "L-2024 has clean geocode, floor, area, and source evidence.",
                ],
                "risks": ["Rent negotiation pressure", "Nearby competitor 220m away"],
                "nextStep": "Convert L-2024 into CS-1001 and continue SiteScore evidence review.",
            },
            {
                "id": "HZ-02",
                "label": "板橋府中生活圈",
                "rank": 2,
                "centroid": [121.4575, 25.0100],
                "demandGap": 0.78,
                "competitionIndex": 0.43,
                "cannibalizationRisk": "medium",
                "rentBand": "NT$48k-72k/month",
                "confidence": 0.88,
                "recommendedLens": "fit",
                "reasons": [
                    "Weekend service demand is under-realized.",
                    "L-2025 has duplicate-source coverage after merge.",
                ],
                "risks": ["Duplicate broker postings require evidence merge before review"],
                "nextStep": "Keep L-2025 active after merging L-2029 duplicate source evidence.",
            },
        ],
        "listingSources": [
            {
                "id": "SRC-591",
                "name": "591 licensed broker intake",
                "status": "connected",
                "complianceNote": "R4 proof uses licensed/manual intake fields and preserves source evidence refs.",
                "lastSyncedAt": "2026-07-14T06:10:00Z",
            },
            {
                "id": "SRC-BROKER",
                "name": "Broker confirmation",
                "status": "manualOnly",
                "complianceNote": "Broker notes are retained as source evidence before any merge/archive action.",
                "lastSyncedAt": "2026-07-14T06:12:00Z",
            },
        ],
        "listings": [
            {
                "id": "L-2024",
                "tenantId": "tenant-a",
                "sourceId": "SRC-591",
                "sourceListingId": "s591-2024",
                "heatZoneId": "HZ-01",
                "address": "台北市信義區松仁路 96 號 1F",
                "status": "new",
                "rentPerMonth": 58000,
                "areaPing": 18,
                "floor": "1F 臨路",
                "frontageMeters": 6,
                "geocodeConfidence": 0.94,
                "hardRuleFailures": [],
                "hardRuleSummary": "3/3 pass: area, floor, permitted use",
                "sourceEvidence": [
                    "EV-L-2024-RAW-591",
                    "EV-L-2024-GEOCODE",
                    "EV-L-2024-BROKER-CALL",
                ],
                "fitScore": 72,
                "firstSeenAt": "2026-07-14T06:10:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2024",
            },
            {
                "id": "L-2025",
                "tenantId": "tenant-a",
                "sourceId": "SRC-BROKER",
                "sourceListingId": "broker-2025",
                "heatZoneId": "HZ-02",
                "address": "新北市板橋區府中路 52 號 1F",
                "status": "watching",
                "rentPerMonth": 53000,
                "areaPing": 22,
                "floor": "1F",
                "frontageMeters": 5,
                "geocodeConfidence": 0.93,
                "hardRuleFailures": [],
                "hardRuleSummary": "3/3 pass: area, floor, permitted use",
                "sourceEvidence": ["EV-L-2025-BROKER", "EV-L-2025-GEOCODE"],
                "fitScore": 70,
                "firstSeenAt": "2026-07-13T09:25:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2025",
            },
            {
                "id": "L-2029",
                "tenantId": "tenant-a",
                "sourceId": "SRC-591",
                "sourceListingId": "s591-2029",
                "heatZoneId": "HZ-02",
                "address": "新北市板橋區府中路 52 號 1F",
                "status": "duplicate",
                "rentPerMonth": 53000,
                "areaPing": 22,
                "floor": "1F",
                "frontageMeters": 5,
                "geocodeConfidence": 0.93,
                "duplicateOfId": "L-2025",
                "hardRuleFailures": [],
                "hardRuleSummary": "3/3 pass, duplicate same-address source",
                "sourceEvidence": [
                    "EV-L-2029-RAW-591",
                    "EV-L-2029-ADDRESS-MATCH",
                    "EV-L-2029-RENT-MATCH",
                ],
                "fitScore": 70,
                "firstSeenAt": "2026-07-14T06:10:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2029",
            },
            {
                "id": "L-2030",
                "tenantId": "tenant-a",
                "sourceId": "SRC-591",
                "sourceListingId": "s591-2030",
                "heatZoneId": "HZ-01",
                "address": "台北市信義區松仁路 110 號 1-2F",
                "status": "hardfail",
                "rentPerMonth": 128000,
                "areaPing": 40,
                "floor": "1-2F",
                "frontageMeters": 8,
                "geocodeConfidence": 0.95,
                "hardRuleFailures": ["area_above_format_maximum", "floor_not_ground_level"],
                "hardRuleSummary": "2/3 blocked: area above 30 ping and second-floor operations required",
                "sourceEvidence": ["EV-L-2030-RAW-591", "EV-L-2030-HARD-RULES"],
                "fitScore": 44,
                "firstSeenAt": "2026-07-14T06:10:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2030",
            },
        ],

        "candidates": [],
        "siteReviews": [],
        "auditEvents": [],
    }
    for listing in state["listings"]:
        listing["tenantId"] = tenant_id
    return state


def _empty_state() -> dict[str, Any]:
    return {
        "heatZones": [],
        "listingSources": [],
        "listings": [],
        "candidates": [],
        "siteReviews": [],
        "assistedIntakes": [],
        "auditEvents": [],
    }


class NetworkListingService:
    """Application service for R4 Listing Radar intake actions."""

    def __init__(
        self,
        listing_repository: Any | None = None,
        intake_repository: AssistedIntakeRepository | None = None,
        *,
        initial_state: dict[str, Any] | None = None,
        seed_fixtures: bool = True,
        tenant_id: str | None = None,
        high_risk_write_guard: Callable[[str], None] | None = None,
        review_decision_repository: Any | None = None,
    ) -> None:
        self._seed_fixtures = seed_fixtures
        self._high_risk_write_guard = high_risk_write_guard
        self._review_decision_repository = review_decision_repository
        self._tenant_id = tenant_id.strip() if tenant_id and tenant_id.strip() else None
        self._listing_repository = listing_repository
        self._intakes: AssistedIntakeRepository = (
            intake_repository if intake_repository is not None else InMemoryAssistedIntakeRepository()
        )
        state = _copy(
            initial_state
            if initial_state is not None
            else _seed_state(tenant_id=self._tenant_id or "tenant-a")
            if seed_fixtures
            else _empty_state()
        )
        self._idempotency_cache = _copy(state.pop("idempotencyCache", {}))
        self._idempotency_bindings: dict[tuple[str, str], tuple[str | None, str | None]] = {}
        self._state = state
        self._load_intakes()
        self._load_idempotency_cache()
        self._load_listings()
        self._load_candidates()

    def _require_actor(self, actor_name: str | None, actor_role_id: str) -> str:
        if not isinstance(actor_name, str) or not actor_name.strip():
            raise NetworkListingPolicyError("AUTHENTICATED_ACTOR_REQUIRED")
        if not self._seed_fixtures:
            try:
                actor_name = str(uuid.UUID(actor_name))
                uuid.UUID(self._tenant_id or "")
            except ValueError as exc:
                raise NetworkListingPolicyError("CANONICAL_ACTOR_TENANT_REQUIRED") from exc
            if actor_role_id not in {"expansion_user", "site_reviewer", "executive", "data_owner", "finance_legal"}:
                raise NetworkListingPolicyError("VERIFIED_PLATFORM_ROLE_REQUIRED")
        return actor_name

    def _require_tenant(self, tenant_id: str | None) -> str:
        value = tenant_id or self._tenant_id
        if not value:
            if self._seed_fixtures:
                return "tenant-a"
            raise NetworkListingPolicyError("TENANT_SCOPE_REQUIRED")
        if self._tenant_id and value != self._tenant_id:
            raise NetworkListingPolicyError("TENANT_SCOPE_DENIED")
        if not self._seed_fixtures:
            try:
                if str(uuid.UUID(value)) != value:
                    raise ValueError("tenant identity is not canonical")
            except ValueError as exc:
                raise NetworkListingPolicyError("CANONICAL_TENANT_REQUIRED") from exc
        return value

    def _prepare_write(self, action: str, key: str | None, actor: str,
                       request_value: dict[str, Any], correlation_id: str | None) -> OperatorWrite:
        if not self._seed_fixtures:
            key = _require_governed_write_context(
                idempotency_key=key, correlation_id=correlation_id, action_label=action,
            )
        try:
            encoded = json.dumps(request_value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise NetworkListingPolicyError("INVALID_REQUEST_CONTEXT") from exc
        request_sha = hashlib.sha256(encoded.encode()).hexdigest()
        scoped_key = None
        if key:
            scoped_key = hashlib.sha256(actor.encode()).hexdigest() + ":" + key
            pair = (action, scoped_key)
            cached = self._idempotency_cache.get(pair)
            if cached is not None:
                if self._idempotency_bindings.get(pair) != (actor, request_sha):
                    raise NetworkListingConflict("IDEMPOTENCY_CONFLICT")
                return OperatorWrite(action, scoped_key, actor, request_sha, _copy(cached))
            if (action, key) in self._idempotency_cache:
                # Legacy receipts cannot be attributed from a display name. Stop
                # for governed reconciliation instead of replaying or reapplying.
                raise NetworkListingConflict("UNBOUND_LEGACY_REPLAY")
        high_risk = action in {"convert", "merge", "decide_intake", "promote_intake"}
        if action == "correct_intake":
            from modules.external_data.application.assisted_intake import IDENTITY_FIELDS

            high_risk = any(field in IDENTITY_FIELDS for field in request_value["fields"])
        if action == "retry_intake":
            high_risk = self._listing_intake(request_value["intake_id"])["stage"] == "QUARANTINED"
        if high_risk and self._high_risk_write_guard is not None:
            self._high_risk_write_guard(self._require_tenant(None))
        return OperatorWrite(action, scoped_key, actor, request_sha)

    def _save_write(self, write: OperatorWrite, response: dict[str, Any]) -> None:
        if write.key:
            self._save_idempotency(write.action, write.key, response,
                                   actor_subject_id=write.actor_subject_id,
                                   request_sha256=write.request_sha256)

    def export_state(self) -> dict[str, Any]:
        return {
            **_copy(self._state),
            "idempotencyCache": _copy(self._idempotency_cache),
        }

    def _load_listings(self) -> None:
        if self._listing_repository is not None:
            repo_listings = self._listing_repository.list_listings()
            if repo_listings or not self._seed_fixtures:
                self._state["listings"] = []
                for lst in repo_listings:
                    self._state["listings"].append(self._listing_to_dict(lst))
            elif self._seed_fixtures:
                for lst_dict in self._state["listings"]:
                    # Persist the explicit fixture verdict and provenance with
                    # the row, so the next request reads the same evidence.
                    self._sync_listing_to_repo(lst_dict["id"])

    def _load_candidates(self) -> None:
        if self._listing_repository is not None:
            repo_candidates = self._listing_repository.list_candidates()
            if repo_candidates or not self._seed_fixtures:
                self._state["candidates"] = []
                for cand in repo_candidates:
                    self._state["candidates"].append(self._candidate_to_dict(cand))
            elif self._seed_fixtures:
                for cand_dict in self._state["candidates"]:
                    cand_obj = self._dict_to_candidate(cand_dict)
                    self._listing_repository.save_candidate(cand_obj)

    def _repo_address(self, address_id: str) -> Any:
        """Look up a persisted address, tolerating repositories without it."""

        if self._listing_repository is None or not address_id:
            return None
        get_address = getattr(self._listing_repository, "get_address", None)
        if get_address is None:
            return None
        return get_address(address_id)

    def _get_listing_metadata(self, listing_id: str) -> dict[str, Any]:
        return self._intakes.get_listing_metadata(listing_id)

    def _save_listing_metadata(self, listing_id: str, metadata: dict[str, Any]) -> None:
        self._intakes.save_listing_metadata(listing_id, metadata)

    def _get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]:
        return self._intakes.get_candidate_metadata(candidate_id)

    def _save_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any]) -> None:
        self._intakes.save_candidate_metadata(candidate_id, metadata)

    def _listing_to_dict(self, lst: Any) -> dict[str, Any]:
        res = {
            "id": lst.listing_id,
            "addressId": lst.address_id,
            "sourceId": lst.source_id,
            "sourceListingId": lst.source_listing_id,
            "status": lst.listing_status,
            "rentPerMonth": lst.rent_amount,
            "areaPing": lst.area_ping,
            "floor": lst.floor,
            "frontageMeters": lst.frontage_m,
            "listingConfidence": lst.effective_confidence,
            "listingConfidenceProvenance": lst.effective_confidence_provenance,
            # Not lst.confidence. That is extraction confidence -- how sure the
            # parser is about the rent and area it read -- and a listing whose
            # address never resolved would otherwise report a fully-confident
            # geocode to the promotion gate, which is the same fail-open the V1
            # adapter had. Unknown until the address supplies a real value.
            "geocodeConfidence": None,
            "sourceUrl": lst.snapshot_id,
            "snapshotId": lst.snapshot_id or None,
            "hardRuleFailures": _copy(getattr(lst, "hard_rule_failures", None)),
        }
        for name in _LISTING_LINEAGE_FIELDS:
            value = getattr(lst, name, None)
            if value is not None:
                res[name] = value
        if self._tenant_id is not None:
            res["tenantId"] = self._tenant_id
        # Carry the persisted geocode through the dict layer. Without this the
        # round trip loses latitude/longitude/h3 and _dict_to_listing has to
        # invent them, which is how an ungeocoded listing used to reach the
        # SiteScore v2 model as a scoreable row.
        address = self._repo_address(lst.address_id)
        if address is not None:
            res.update(
                {
                    "address": address.normalized_address or address.raw_address,
                    "latitude": address.latitude,
                    "longitude": address.longitude,
                    "geocodeConfidence": address.geocode_confidence,
                    "h3Index": address.h3_res_9,
                }
            )
        meta = self._get_listing_metadata(lst.listing_id)
        if meta:
            res.update(meta)
        elif self._seed_fixtures:
            for item in _seed_state(tenant_id=self._tenant_id or "tenant-a")["listings"]:
                if item["id"] == lst.listing_id:
                    for k, v in item.items():
                        if k not in res:
                            res[k] = v
                    break
        return res

    def _dict_to_listing(self, d: dict[str, Any]) -> tuple[Any, Any, Any]:
        from modules.listing.domain.models import ListingDedupKey
        from shared.domain.models import AddressLocation, Listing
        existing = (
            self._listing_repository.get_listing(d["id"])
            if self._listing_repository is not None else None
        )
        changes = {
            "listing_status": d["status"], "rent_amount": float(d["rentPerMonth"]),
            "area_ping": float(d["areaPing"]), "floor": d["floor"],
        }
        if existing is not None:
            # Operator decisions edit these supplied facts/status only. The
            # presentation omits other canonical measurements and exposes
            # effective confidence, so rebuilding from it would erase source
            # provenance, utilities, currency and recorded extension fields.
            lst = replace(existing, **changes)
            for name, value in vars(existing).items():
                if name not in existing.__dataclass_fields__:
                    object.__setattr__(lst, name, _copy(value))
            address_id = existing.address_id
        else:
            address_id = d.get("addressId") or d.get("address_id") or f"ADDR-{d['id']}"
            lst = Listing(
                listing_id=d["id"], source_listing_id=d["sourceListingId"], source_id=d["sourceId"],
                address_id=address_id, **changes,
                frontage_m=float(d.get("frontageMeters") or 0),
                confidence=_optional_number(d.get("listingConfidence"), float),
                snapshot_id=(d.get("snapshotId") if "snapshotId" in d else d.get("sourceUrl")) or "",
                tenant_id=d.get("tenantId") or self._tenant_id or "",
            )
        for name in _LISTING_LINEAGE_FIELDS:
            value = d.get(name)
            if value is not None and getattr(lst, name, None) is None:
                object.__setattr__(lst, name, value)
        # These commands do not authorize a new geocode or address revision.
        # Retain the complete recorded address, including its raw text,
        # precision, revisions and extension provenance, when already present.
        addr = self._repo_address(address_id)
        if addr is None:
            addr = AddressLocation(
                address_id=address_id, raw_address=d.get("address") or "",
                normalized_address=d.get("address") or "",
                latitude=_optional_number(_first_present(d, "latitude", "lat"), float),
                longitude=_optional_number(_first_present(d, "longitude", "lng"), float),
                geocode_confidence=_optional_number(d.get("geocodeConfidence"), float),
                h3_res_9=d.get("h3Index") or d.get("h3_index") or "",
                tenant_id=d.get("tenantId") or self._tenant_id or "",
            )
        key = ListingDedupKey(
            source_id=lst.source_id, source_listing_id=lst.source_listing_id,
            normalized_address=addr.normalized_address,
            rent_amount=lst.rent_amount, area_ping=lst.area_ping,
        )
        return lst, addr, key

    def validate_review_candidate(self, decision: Any) -> Any:
        candidates = [row for row in self._listing_repository.list_candidates()
                      if row.candidate_site.candidate_site_id == decision.candidate_site_id]
        if len(candidates) != 1 or decision.tenant_id != self._tenant_id:
            raise NetworkListingPolicyError("CANONICAL_REVIEW_CANDIDATE_ASSOCIATION_REQUIRED")
        if candidates[0].review_id and candidates[0].review_id != decision.decision_id:
            raise NetworkListingPolicyError("CANONICAL_REVIEW_CURRENT_CANDIDATE_REQUIRED")
        return candidates[0]

    def record_review_decision(self, decision: Any) -> dict[str, Any]:
        """Persist Operator workflow status without inventing shared site enums."""
        candidate = self.validate_review_candidate(decision)
        action = decision.network_action
        state = {"GO": "approved", "WAIT": "onhold", "RETURN": "needdata", "REJECT": "rejected"}[action]
        # onhold/needdata are Operator workflow states; CandidateSite retains
        # its existing domain enum and physical lifecycle status.
        if action in {"GO", "REJECT"}:
            candidate = replace(candidate, candidate_site=replace(candidate.candidate_site, site_status=state))
        candidate = replace(candidate, review_id=decision.decision_id)
        self._listing_repository.save_candidate(candidate)
        metadata = self._get_candidate_metadata(decision.candidate_site_id)
        metadata["reviewWorkflow"] = {
            "decisionId": decision.decision_id, "approvalId": decision.approval_id,
            "reportId": decision.report_id, "reportVersion": decision.report_version,
            "decisionVersion": decision.version, "tenantId": decision.tenant_id,
            "networkAction": action, "status": state,
            "conditions": decision.conditions, "requiredData": list(decision.required_data or ()),
        }
        self._save_candidate_metadata(decision.candidate_site_id, metadata)
        return _copy(metadata["reviewWorkflow"])

    def _candidate_to_dict(self, cand: Any) -> dict[str, Any]:
        snapshot_time = cand.feature_snapshot_time
        res = {
            "id": cand.candidate_site.candidate_site_id,
            "listingId": cand.listing.listing_id,
            "targetFormatCode": cand.candidate_site.target_format_code,
            "heatZoneId": cand.heat_zone_id,
            "title": f"{cand.listing.listing_id} 候選點",
            "address": cand.address.raw_address,
            "status": cand.candidate_site.site_status,
            "score": cand.score,
            "recommendation": cand.recommendation,
            "modelVersion": cand.model_version,
            "datasetSnapshotId": cand.dataset_snapshot_id,
            "reviewId": cand.review_id,
            "missingData": list(cand.feasibility_flags or ()),
            # SiteScore v2 point-in-time contract. These must survive the dict
            # round trip: _sync_candidate_to_repo() rewrites the persisted
            # draft from this dict, so anything omitted here is wiped from an
            # already-complete candidate on the next operator write.
            "feasibilityFlags": list(cand.feasibility_flags or ()),
            "prior90dCellNetRevenue": cand.prior_90d_cell_net_revenue,
            "prior90dCellTransactionCount": cand.prior_90d_cell_transaction_count,
            "prior90dCellStoreCount": cand.prior_90d_cell_store_count,
            "featureSnapshotTime": (
                snapshot_time.isoformat() if snapshot_time is not None else None
            ),
        }
        meta = self._get_candidate_metadata(cand.candidate_site.candidate_site_id)
        if meta and self._seed_fixtures:
            res.update(meta)
        elif meta:
            # Presentation metadata cannot replace a committed worker result.
            if meta.get("title"):
                res["title"] = meta["title"]
        workflow = meta.get("reviewWorkflow") if isinstance(meta, dict) else None
        if isinstance(workflow, dict) and self._review_decision_repository is not None:
            decision = self._review_decision_repository.get_decision(workflow.get("decisionId", ""))
            if (decision is not None and decision.candidate_site_id == res["id"]
                    and getattr(decision, "network_action", None) in {"GO", "WAIT", "RETURN", "REJECT"}
                    and cand.review_id == decision.decision_id
                    and decision.tenant_id == self._tenant_id
                    and getattr(decision, "network_action", None) == workflow.get("networkAction")
                    and decision.report_id == workflow.get("reportId")
                    and decision.report_version == workflow.get("reportVersion")
                    and getattr(decision, "version", None) == workflow.get("decisionVersion")
                    and getattr(decision, "approval_id", None) == workflow.get("approvalId")
                    and getattr(decision, "conditions", None) == workflow.get("conditions")
                    and list(getattr(decision, "required_data", ()) or ()) == workflow.get("requiredData")
                    and {"GO": "approved", "WAIT": "onhold", "RETURN": "needdata", "REJECT": "rejected"}.get(
                        getattr(decision, "network_action", None)) == workflow.get("status")):
                res["canonicalSiteStatus"] = res["status"]
                res["status"] = workflow["status"]
                res["reviewId"] = decision.decision_id
                res["reviewWorkflow"] = _copy(workflow)
                if decision.network_action == "RETURN":
                    res["missingData"] = list(decision.required_data or ())
        elif self._seed_fixtures:
            if res["id"] == "CS-1001":
                res["title"] = "信義松仁候選點"
                res["score"] = 82
                res["recommendation"] = "GO"
                res["reviewId"] = "RV-1001"
        # Coordinates come only from the persisted candidate address, never
        # operator presentation metadata or a HeatZone approximation.
        res.update({
            "latitude": cand.address.latitude,
            "longitude": cand.address.longitude,
            "geocodeConfidence": cand.address.geocode_confidence,
            "h3Index": cand.address.h3_res_9 or None,
        })
        return res

    def _dict_to_candidate(self, d: dict[str, Any]) -> Any:
        from modules.listing.domain.models import (
            CandidateSiteDraft,
            ListingPipelineStatus,
        )
        from shared.domain.models import CandidateSite
        listing = self._listing(d["listingId"])
        lst_obj, addr_obj, _ = self._dict_to_listing(listing)
        previous = next((candidate for candidate in self._listing_repository.list_candidates()
                         if candidate.candidate_site.candidate_site_id == d["id"]), None) if self._listing_repository else None
        site_status = d.get("canonicalSiteStatus", d["status"])
        cand_site = replace(previous.candidate_site, site_status=site_status) if previous else CandidateSite(
            candidate_site_id=d["id"],
            listing_id=d["listingId"],
            address_id=addr_obj.address_id,
            target_format_code=d.get("targetFormatCode") or "",
            site_status=site_status,
        )
        heat_zone_id = d.get("heatZoneId")
        # A blank heat zone is the same data gap as an absent one: it makes the
        # candidate unjoinable to its H3 cell aggregates, so every later
        # SiteScore v2 lookup would silently score against the wrong cell.
        # Reject missing, null, empty and whitespace-only before persistence
        # rather than storing "" and failing open downstream.
        if not isinstance(heat_zone_id, str) or not heat_zone_id.strip():
            raise ValueError(
                f"candidate {d['id']!r} has no usable heatZoneId "
                f"({heat_zone_id!r}); refusing to persist a candidate site "
                "with an unknown heat zone"
            )
        # Preserve the recorded verdict. Unknown is not an empty passing list.
        flags = d.get("feasibilityFlags")
        if not self._seed_fixtures and (not isinstance(flags, list)
                or not all(isinstance(item, str) for item in flags)):
            raise ValueError("HARD_RULE_EVIDENCE_REQUIRED: candidate verdict is unknown")
        draft = CandidateSiteDraft(
            listing=lst_obj,
            address=addr_obj,
            candidate_site=cand_site,
            feasibility_flags=tuple(flags or ()),
            heat_zone_id=heat_zone_id,
            listing_source=listing.get("sourceId") or "",
            status=ListingPipelineStatus.CANDIDATE,
            score=d.get("score"),
            recommendation=d.get("recommendation"),
            model_version=d.get("modelVersion"),
            dataset_snapshot_id=d.get("datasetSnapshotId"),
            review_id=d.get("reviewId"),
            prior_90d_cell_net_revenue=_optional_number(
                _first_present(
                    d, "prior90dCellNetRevenue", "prior_90d_cell_net_revenue"
                ),
                float,
            ),
            prior_90d_cell_transaction_count=_optional_number(
                _first_present(
                    d,
                    "prior90dCellTransactionCount",
                    "prior_90d_cell_transaction_count",
                ),
                int,
            ),
            prior_90d_cell_store_count=_optional_number(
                _first_present(
                    d, "prior90dCellStoreCount", "prior_90d_cell_store_count"
                ),
                int,
            ),
            feature_snapshot_time=_optional_datetime(
                _first_present(d, "featureSnapshotTime", "feature_snapshot_time")
            ),
        )
        if previous is not None:
            # The operator projection does not carry every canonical lineage
            # field. Preserve those persisted facts during a compatibility write.
            return replace(previous, **{name: getattr(draft, name) for name in (
                "candidate_site", "feasibility_flags", "heat_zone_id",
                "score", "recommendation", "model_version",
                "dataset_snapshot_id", "review_id", "prior_90d_cell_net_revenue",
                "prior_90d_cell_transaction_count", "prior_90d_cell_store_count", "feature_snapshot_time",
            )})
        return draft

    def _sync_listing_to_repo(self, listing_id: str) -> None:
        if self._listing_repository is not None:
            listing = self._listing(listing_id)
            lst_obj, addr_obj, key_obj = self._dict_to_listing(listing)
            self._listing_repository.save_listing(lst_obj, addr_obj, key_obj)

            meta = {
                # These facts belong to their durable producer (scope, hold,
                # revision and custody). A presentation write may update its
                # own fields but cannot erase facts it does not project.
                **self._get_listing_metadata(listing_id),
                "tenantId": listing.get("tenantId"),
                "status": listing["status"],
                # Listing.frontage_m retains its legacy numeric absent value;
                # preserve explicit unknown in the operator presentation.
                "frontageMeters": listing.get("frontageMeters"),
                "sourceUrl": listing.get("sourceUrl"),
                "heatZoneId": listing.get("heatZoneId"),
                "hardRuleFailures": listing.get("hardRuleFailures"),
                "hardRuleSummary": listing.get("hardRuleSummary"),
                "sourceEvidence": listing.get("sourceEvidence"),
                "fitScore": listing.get("fitScore"),
                "firstSeenAt": listing.get("firstSeenAt"),
                # The domain Listing carries only `status`, which cannot tell an
                # already-merged duplicate from a merge-eligible one. Without
                # these, a restart drops the terminal marker and merge_listing
                # would accept a second request for the same source.
                "duplicateOfId": listing.get("duplicateOfId"),
                "mergedIntoId": listing.get("mergedIntoId"),
                "mergedAt": listing.get("mergedAt"),
                "mergeReason": listing.get("mergeReason"),
                "mergedSourceListingIds": listing.get("mergedSourceListingIds"),
                # Candidate linkage and archive provenance live only on the
                # dict layer; without them a rebuild from the repository turns
                # a converted listing back into an unlinked row and drops the
                # governed archive reason.
                "candidateId": listing.get("candidateId"),
                "convertedAt": listing.get("convertedAt"),
                "archivedReason": listing.get("archivedReason"),
                "archivedAt": listing.get("archivedAt"),
                # SiteScore v2 point-in-time cell aggregates. The domain Listing
                # has no field for them, so without this a restart (or any
                # listing write) drops the contract and every candidate later
                # converted from this listing fails closed.
                "prior90dCellNetRevenue": _first_present(
                    listing, "prior90dCellNetRevenue", "prior_90d_cell_net_revenue"
                ),
                "prior90dCellTransactionCount": _first_present(
                    listing,
                    "prior90dCellTransactionCount",
                    "prior_90d_cell_transaction_count",
                ),
                "prior90dCellStoreCount": _first_present(
                    listing, "prior90dCellStoreCount", "prior_90d_cell_store_count"
                ),
                "featureSnapshotTime": _first_present(
                    listing, "featureSnapshotTime", "feature_snapshot_time"
                ),
            }
            self._save_listing_metadata(listing_id, meta)

    def _sync_candidate_to_repo(self, candidate_id: str) -> None:
        if self._listing_repository is not None:
            candidate = None
            for cand in self._state["candidates"]:
                if cand["id"] == candidate_id:
                    candidate = cand
                    break
            if candidate:
                cand_obj = self._dict_to_candidate(candidate)
                self._listing_repository.save_candidate(cand_obj)

                meta = {
                    "title": candidate.get("title"),
                    "score": candidate.get("score"),
                    "recommendation": candidate.get("recommendation"),
                    "modelVersion": candidate.get("modelVersion"),
                    "datasetSnapshotId": candidate.get("datasetSnapshotId"),
                    "missingData": candidate.get("missingData"),
                    "reviewId": candidate.get("reviewId"),
                }
                self._save_candidate_metadata(candidate_id, meta)

    def _load_intakes(self) -> None:
        self._state["assistedIntakes"] = self._intakes.list_intakes()

    def _load_idempotency_cache(self) -> None:
        records = self._intakes.list_idempotency_records()
        self._idempotency_cache = {
            (record.action, record.key): record.response
            for record in records
        }
        self._idempotency_bindings = {
            (record.action, record.key): (getattr(record, "actor_subject_id", None),
                                         getattr(record, "request_sha256", None))
            for record in records
        }

    def _save_idempotency(self, action: str, key: str, response: dict[str, Any], *,
                          actor_subject_id: str | None = None, request_sha256: str | None = None) -> None:
        self._idempotency_cache[(action, key)] = _copy(response)
        self._idempotency_bindings[(action, key)] = (actor_subject_id, request_sha256)
        self._intakes.save_idempotency_record(
            IntakeIdempotencyRecord(action=action, key=key, response=_copy(response),
                                    actor_subject_id=actor_subject_id, request_sha256=request_sha256)
        )

    def _save_intake_to_state(self, intake: dict[str, Any]) -> None:
        self._state.setdefault("assistedIntakes", [])
        for idx, item in enumerate(self._state["assistedIntakes"]):
            if item["id"] == intake["id"]:
                self._state["assistedIntakes"][idx] = intake
                return
        self._state["assistedIntakes"].append(intake)

    def _save_intake(self, intake: dict[str, Any]) -> None:
        intake["updatedAt"] = _now()
        self._save_intake_to_state(intake)
        self._intakes.save_intake(intake)

    def _create_intake_if_absent(self, intake: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if hasattr(self._intakes, "create_intake_if_absent"):
            stored, created = self._intakes.create_intake_if_absent(intake)
        else:
            existing = self._intakes.get_intake(intake["id"])
            if existing is not None:
                stored, created = existing, False
            else:
                self._intakes.save_intake(intake)
                stored, created = intake, True
        self._save_intake_to_state(stored)
        return stored, created

    def reset(self) -> dict[str, Any]:
        self._state = (
            _seed_state(tenant_id=self._tenant_id or "tenant-a")
            if self._seed_fixtures
            else _empty_state()
        )
        self._idempotency_cache = {}
        self._idempotency_bindings = {}
        self._intakes.clear()
        self._state["assistedIntakes"] = []

        if self._listing_repository is not None and self._seed_fixtures:
            self._listing_repository.clear()
            for lst_dict in self._state["listings"]:
                self._sync_listing_to_repo(lst_dict["id"])

        return self.snapshot()

    def snapshot(
        self,
        *,
        selected_heat_zone_id: str | None = None,
        lens: str | None = None,
        correlation_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        selected_id = selected_heat_zone_id or (
            self._state["heatZones"][0]["id"]
            if self._state["heatZones"]
            else None
        )
        active_lens = lens or "demand"
        self._state.setdefault("assistedIntakes", [])
        visible_tenant = tenant_id.strip() if tenant_id and tenant_id.strip() else self._tenant_id
        listings = _copy(self._state["listings"])
        if visible_tenant is not None:
            listings = [item for item in listings if item.get("tenantId") == visible_tenant]
        listing_ids = {item.get("id") for item in listings}
        candidates = _copy(self._state["candidates"])
        if visible_tenant is not None:
            candidates = [
                item
                for item in candidates
                if item.get("tenantId") == visible_tenant
                or item.get("listingId") in listing_ids
            ]
        assisted_intakes = _copy(self._state["assistedIntakes"])
        if visible_tenant is not None:
            assisted_intakes = [
                item for item in assisted_intakes if item.get("tenantId") == visible_tenant
            ]
        return {
            "source": "api",
            "heatZones": _copy(self._state["heatZones"]),
            "listingSources": _copy(self._state["listingSources"]),
            "listings": listings,
            "candidates": candidates,
            "siteReviews": _copy(self._state["siteReviews"]),
            "assistedIntakes": assisted_intakes,
            "expansionSteps": self._expansion_steps(selected_id=selected_id),
            "selectedHeatZoneId": selected_id,
            "selectedLens": active_lens,
            "auditEvents": _copy(self._state["auditEvents"]),
            "correlationId": correlation_id,
            "counts": {
                "heatZones": len(self._state["heatZones"]),
                "listings": len(listings),
                "candidates": len(candidates),
                "siteReviews": len(self._state["siteReviews"]),
                "assistedIntakes": len(assisted_intakes),
            },
        }

    @_atomic_intake_submission
    def convert_listing(
        self,
        *,
        listing_id: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        # Server-side role check
        actor_name = self._require_actor(actor_name, actor_role_id)
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer", "executive", "data_owner"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to convert listing")

        write = self._prepare_write("convert", idempotency_key, actor_name,
                                    {"listing_id": listing_id}, correlation_id)
        if write.replayed_response is not None:
            return write.replayed_response

        if not self._seed_fixtures:
            self._load_listings()
            self._load_candidates()
        listing = self._listing(listing_id)
        failures = listing.get("hardRuleFailures")
        if not isinstance(failures, list) or not all(isinstance(item, str) for item in failures):
            raise NetworkListingPolicyError("HARD_RULE_EVIDENCE_REQUIRED: recorded hard-rule verdict is required")
        if failures:
            raise NetworkListingPolicyError(
                f"{listing_id} cannot convert until hard-rule failures are resolved"
            )
        if listing.get("status") in {"duplicate", "archived", "expired"}:
            raise NetworkListingConflict(f"{listing_id} is {listing.get('status')} and cannot convert")

        existing = self._candidate_for_listing(listing_id)
        if not self._seed_fixtures:
            # This compatibility writer may resync an independently approved
            # candidate, but it cannot create one or establish approval itself.
            target = existing.get("targetFormatCode") if existing else None
            approved = any(
                promotion.get("tenant_id") == self._tenant_id
                and promotion.get("listing_id") == listing_id
                and promotion.get("candidate_site_id") == (existing or {}).get("id")
                and promotion.get("decision_type") == "STANDARD"
                and promotion.get("status") in {"SCORE_QUEUED", "COMPLETED"}
                and promotion.get("target_format_code") == target
                and bool(promotion.get("reviewed_at"))
                and bool(promotion.get("proposer_subject_id") or promotion.get("proposer"))
                and bool(promotion.get("reviewer_subject_id") or promotion.get("reviewer"))
                and (promotion.get("proposer_subject_id") or promotion.get("proposer"))
                    != (promotion.get("reviewer_subject_id") or promotion.get("reviewer"))
                for promotion in self._intakes.list_promotions()
            )
            if existing is None or not isinstance(target, str) or not 1 <= len(target) <= 64 or not approved:
                raise NetworkListingPolicyError(
                    "PROMOTION_APPROVAL_REQUIRED: use the canonical intake promotion request and independent review"
                )
        created = existing is None
        if existing is None:
            candidate_id = "CS-1001" if listing_id == "L-2024" else f"CS-{1000 + len(self._state['candidates']) + 1}"
            existing = {
                "id": candidate_id,
                "listingId": listing_id,
                "heatZoneId": listing["heatZoneId"],
                "title": "信義松仁候選點" if candidate_id == "CS-1001" else f"{listing_id} 候選點",
                "address": listing["address"],
                "status": "ready" if self._seed_fixtures else "new",
                "score": (82 if candidate_id == "CS-1001" else 68) if self._seed_fixtures else None,
                "recommendation": ("GO" if candidate_id == "CS-1001" else "WAIT") if self._seed_fixtures else None,
                "modelVersion": "SiteScore v2.3" if self._seed_fixtures else None,
                "datasetSnapshotId": "FS-20260704-0600" if self._seed_fixtures else listing.get("snapshotId"),
                "missingData": [],
                "reviewId": "RV-1001" if self._seed_fixtures and candidate_id == "CS-1001" else None,
                # Propagate the SiteScore v2 point-in-time contract from the
                # listing. The prior_90d_cell_* aggregates originate in the
                # model-ready views (product_ops/modeling/sql/model_ready_views.sql);
                # this workflow carries whatever the listing recorded and
                # leaves the value None when it was never resolved, so scoring
                # fails closed instead of scoring a fabricated zero.
                "feasibilityFlags": list(listing.get("hardRuleFailures") or ()),
                "prior90dCellNetRevenue": _first_present(
                    listing, "prior90dCellNetRevenue", "prior_90d_cell_net_revenue"
                ),
                "prior90dCellTransactionCount": _first_present(
                    listing,
                    "prior90dCellTransactionCount",
                    "prior_90d_cell_transaction_count",
                ),
                "prior90dCellStoreCount": _first_present(
                    listing, "prior90dCellStoreCount", "prior_90d_cell_store_count"
                ),
                "featureSnapshotTime": _first_present(
                    listing, "featureSnapshotTime", "feature_snapshot_time"
                ),
            }
            self._state["candidates"].append(existing)
            if existing.get("reviewId"):
                self._state["siteReviews"].append(
                    {
                        "id": existing["reviewId"],
                        "candidateId": existing["id"],
                        "status": "pending",
                        "requestedByRoleId": "expansionManager",
                        "reviewerRoleIds": ["opsLead", "auditPm"],
                        "requestedAt": "2026-07-14T06:30:00Z",
                        "reasonRequired": True,
                    }
                )

        listing["status"] = "candidate"
        listing["candidateId"] = existing["id"]
        listing["convertedAt"] = listing.get("convertedAt") or _now()
        self._sync_listing_to_repo(listing_id)
        self._sync_candidate_to_repo(existing["id"])

        audit = self._audit(
            action="listing.convert",
            target_id=listing_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={"candidateId": existing["id"], "created": created},
        )
        result = {
            "listing": _copy(listing),
            "candidate": _copy(existing),
            "created": created,
            "auditEvent": audit,
            "candidateCount": len(
                [item for item in self._state["candidates"] if item.get("listingId") == listing_id]
            ),
            "correlationId": correlation_id,
            "expansionSteps": self._expansion_steps(selected_id=listing["heatZoneId"]),
        }
        self._save_write(write, result)
        return result

    def _action_listing(self, listing_id: str) -> dict[str, Any]:
        records = self._state.get("listings")
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise NetworkListingPolicyError("LISTING_COLLECTION_UNAVAILABLE")
        matches = [row for row in records if row.get("id") == listing_id]
        if not matches:
            raise NetworkListingNotFound(listing_id)
        if len(matches) != 1:
            raise NetworkListingConflict("AMBIGUOUS_LISTING_ID")
        record = matches[0]
        if self._tenant_id is not None and record.get("tenantId") not in (None, self._tenant_id):
            raise PermissionError("TENANT_SCOPE_DENIED")
        return record

    def _authorize_listing_write(
        self, records: list[dict[str, Any]], authorize_existing: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        if authorize_existing is None and not self._seed_fixtures:
            raise NetworkListingPolicyError("LISTING_SCOPE_CONTEXT_REQUIRED")
        if authorize_existing is not None:
            for record in records:
                authorize_existing(_copy(record))

    def _listing_write_records(self, action: str, key: str | None, actor: str, ids: list[str]) -> list[dict[str, Any]]:
        lookup_key = key.strip() if key and (action == "merge" or not self._seed_fixtures) else key
        cached = self._idempotency_cache.get((action, hashlib.sha256(actor.encode()).hexdigest() + ":" + lookup_key)) if lookup_key else None
        if cached is not None:
            fields = ("source", "target") if action == "merge" else ("listing",)
            if any(not isinstance(cached.get(name), dict) or not isinstance(cached[name].get("id"), str) for name in fields):
                raise NetworkListingPolicyError("REPLAY_RECEIPT_UNAVAILABLE")
            # Authorize the durable receipt's actual resources before exposing
            # it or rejecting a changed fingerprint. Never choose new body IDs
            # as the authority for an existing logical operation.
            ids = [cached[name]["id"] for name in fields]
        return [self._action_listing(listing_id) for listing_id in ids]

    def _check_listing_revision(self, record: dict[str, Any], if_match: str | None) -> None:
        # Fixture compatibility is explicit. An absent canonical revision is
        # unavailable, never an invented version 1 or a successful SQL CAS.
        revision = record.get("version")
        if self._seed_fixtures and revision is None and if_match is None:
            return
        if if_match is None:
            raise NetworkListingPreconditionRequired("PRECONDITION_REQUIRED")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise NetworkListingVersionUnavailable("LISTING_VERSION_UNAVAILABLE")
        if if_match != f'W/"{revision}"':
            raise NetworkListingConflict("VERSION_CONFLICT")

    def _guard_listing_hold(self, record: dict[str, Any]) -> None:
        for hold_field in ("hasLegalHold", "legalHold"):
            value = record.get(hold_field)
            if value is not None and not isinstance(value, bool):
                raise NetworkListingPolicyError("LEGAL_HOLD_STATE_UNAVAILABLE")
            if value is True:
                raise NetworkListingConflict("LEGAL_HOLD_CONFLICT")
        if not self._seed_fixtures and not any(isinstance(record.get(name), bool) for name in ("hasLegalHold", "legalHold")):
            raise NetworkListingPolicyError("LEGAL_HOLD_STATE_UNAVAILABLE")

    def _guard_listing_retention(self, record: dict[str, Any]) -> None:
        if str(record.get("status", "")).upper() == "ARCHIVED" or record.get("mergedIntoId"):
            raise NetworkListingConflict("LISTING_TERMINAL")
        self._guard_listing_hold(record)
        candidates = self._state.get("candidates")
        if not isinstance(candidates, list) or any(
            not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str)
            or not candidate["id"] or not isinstance(candidate.get("listingId"), str)
            or not candidate["listingId"] for candidate in candidates
        ):
            raise NetworkListingPolicyError("CANDIDATE_DEPENDENCIES_UNAVAILABLE")
        direct_id = record.get("candidateId")
        if direct_id is not None and (not isinstance(direct_id, str) or not direct_id):
            raise NetworkListingPolicyError("CANDIDATE_DEPENDENCIES_UNAVAILABLE")
        if direct_id or any(candidate["listingId"] == record["id"] for candidate in candidates):
            # The identity executor's candidate-disposition plan is not owned
            # by this legacy evidence-marker action. Do not silently reassign.
            raise NetworkListingConflict("CANDIDATE_DISPOSITION_REQUIRED")

    def merge_listing(
        self,
        *,
        source_listing_id: str,
        target_listing_id: str,
        reason: str,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        if_match: str | None = None,
        target_if_match: str | None = None,
        authorize_existing: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        # Server-side role check
        actor_name = self._require_actor(actor_name, actor_role_id)
        if not self._seed_fixtures:
            self._load_listings()
            self._load_candidates()
            self._load_idempotency_cache()
        source, target = self._listing_write_records("merge", idempotency_key, actor_name,
                                                    [source_listing_id, target_listing_id])
        self._authorize_listing_write([source, target], authorize_existing)
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer", "executive", "data_owner"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to merge listing")
        self._guard_listing_hold(source)
        self._guard_listing_hold(target)

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="merge listing",
        )

        reason = reason.strip()
        if not reason:
            raise NetworkListingPolicyError("merge reason is required")

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="merge listing",
        )

        write = self._prepare_write("merge", governed_key, actor_name,
                                    {"source_listing_id": source_listing_id, "target_listing_id": target_listing_id, "reason": reason, "risk_summary": risk_summary, "risk_acknowledged": risk_acknowledged, "if_match": if_match, "target_if_match": target_if_match}, correlation_id)
        if write.replayed_response is not None:
            return write.replayed_response

        # Same-key replay has already returned its receipt; a new request
        # preserves the established terminal explanation without another write.
        merged_into = source.get("mergedIntoId")
        if merged_into:
            raise NetworkListingConflict(
                f"{source_listing_id} is already merged into {merged_into} and cannot be merged again"
            )
        self._check_listing_revision(source, if_match)
        self._check_listing_revision(target, target_if_match)
        self._guard_listing_retention(source)
        self._guard_listing_retention(target)
        if source_listing_id == target_listing_id:
            raise NetworkListingConflict("source and target listing must be different")
        if source.get("duplicateOfId") != target_listing_id or target.get("duplicateOfId"):
            raise NetworkListingConflict("EXACT_DUPLICATE_TARGET_REQUIRED")
        for record in (source, target):
            for evidence_field in ("sourceEvidence", "mergedSourceListingIds"):
                values = record.get(evidence_field, [])
                if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                    raise NetworkListingPolicyError("LISTING_EVIDENCE_UNAVAILABLE")
        source_before_status = source.get("status")
        source_before_version = source.get("version")
        target_before_version = target.get("version")
        target_before_evidence = list(target.get("sourceEvidence", []))

        source_evidence = list(source.get("sourceEvidence", []))
        target["sourceEvidence"] = _dedupe(list(target.get("sourceEvidence", [])) + source_evidence)
        target["mergedSourceListingIds"] = _dedupe(
            list(target.get("mergedSourceListingIds", [])) + [source_listing_id]
        )
        target["mergeReason"] = reason
        target["mergedAt"] = target.get("mergedAt") or _now()
        source["status"] = "duplicate"
        source["duplicateOfId"] = target_listing_id
        source["mergedIntoId"] = target_listing_id
        source["mergedAt"] = source.get("mergedAt") or _now()
        source["mergeReason"] = reason
        self._sync_listing_to_repo(source_listing_id)
        self._sync_listing_to_repo(target_listing_id)

        audit = self._audit(
            action="listing.merge",
            target_id=target_listing_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={
                "sourceListingId": source_listing_id,
                "targetListingId": target_listing_id,
                # The operator's own words, recorded alongside the disclosure
                # they acknowledged; archive does the same.
                "reason": reason,
                "sourceEvidenceRetained": len(source_evidence),
                "targetEvidenceCount": len(target["sourceEvidence"]),
                "before": {
                    "sourceStatus": source_before_status,
                    "targetEvidenceCount": len(target_before_evidence),
                    "sourceVersion": source_before_version,
                    "targetVersion": target_before_version,
                },
                "after": {
                    "sourceStatus": "duplicate",
                    "targetEvidenceCount": len(target["sourceEvidence"]),
                    "sourceVersion": source.get("version"),
                    "targetVersion": target.get("version"),
                },
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
                "effectSummary": (
                    f"Merge source {source_listing_id} into target {target_listing_id}."
                ),
            },
        )
        result = {
            "source": _copy(source),
            "target": _copy(target),
            "actorSubjectId": actor_name,
            "actorRoleId": actor_role_id,
            "idempotencyKey": governed_key,
            "sourceEvidenceRetained": _copy(source_evidence),
            "auditEvent": audit,
            "correlationId": correlation_id,
            "expansionSteps": self._expansion_steps(selected_id=target["heatZoneId"]),
        }
        self._save_write(write, result)
        return result

    def archive_listing(
        self,
        *,
        listing_id: str,
        reason: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        if_match: str | None = None,
        authorize_existing: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        # Server-side role check
        actor_name = self._require_actor(actor_name, actor_role_id)
        if not self._seed_fixtures:
            self._load_listings()
            self._load_candidates()
            self._load_idempotency_cache()
        listing, = self._listing_write_records("archive", idempotency_key, actor_name, [listing_id])
        self._authorize_listing_write([listing], authorize_existing)
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer", "executive", "data_owner"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to archive listing")
        self._guard_listing_hold(listing)

        reason = reason.strip()
        if not reason:
            raise NetworkListingPolicyError("archive reason is required")

        write = self._prepare_write("archive", idempotency_key, actor_name,
                                    {"listing_id": listing_id, "reason": reason, "if_match": if_match}, correlation_id)
        if write.replayed_response is not None:
            return write.replayed_response

        self._check_listing_revision(listing, if_match)
        self._guard_listing_retention(listing)
        before_status = listing.get("status")
        before_version = listing.get("version")

        listing["status"] = "archived"
        listing["archivedReason"] = reason
        listing["archivedAt"] = listing.get("archivedAt") or _now()
        self._sync_listing_to_repo(listing_id)

        audit = self._audit(
            action="listing.archive",
            target_id=listing_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={
                "reason": reason,
                "hardRuleFailures": _copy(listing.get("hardRuleFailures")),
                "sourceEvidenceCount": (len(listing["sourceEvidence"])
                                        if isinstance(listing.get("sourceEvidence"), list) else None),
                "before": {
                    "status": before_status,
                    "version": before_version,
                },
                "after": {
                    "status": "archived",
                    "version": listing.get("version"),
                },
                "effectSummary": f"Archive listing {listing_id}. Reason: {reason}",
            },
        )
        result = {
            "listing": _copy(listing),
            "actorSubjectId": actor_name,
            "actorRoleId": actor_role_id,
            "idempotencyKey": write.key.split(":", 1)[1] if write.key else None,
            "auditEvent": audit,
            "correlationId": correlation_id,
            "expansionSteps": self._expansion_steps(selected_id=listing["heatZoneId"]),
        }
        self._save_write(write, result)
        return result

    @_atomic_intake_submission
    def submit_intake(
        self,
        *,
        url: str,
        heat_zone_id: str | None,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        job_queue: Any | None = None,
        async_intake: bool = False,
        tenant_id: str | None = None,
        authorize_existing: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        actor_name = self._require_actor(actor_name, actor_role_id)
        tenant_id = self._require_tenant(tenant_id)
        write = self._prepare_write("submit_intake", idempotency_key, actor_name,
                                    {"url": url.strip(), "heat_zone_id": heat_zone_id, "async_intake": async_intake}, correlation_id)
        if write.replayed_response is not None:
            current = self._listing_intake(write.replayed_response["id"])
            if authorize_existing is not None:
                authorize_existing(_copy(current))
            elif not self._seed_fixtures:
                raise NetworkListingPolicyError("INTAKE_SCOPE_CONTEXT_REQUIRED")
            return write.replayed_response

        from modules.external_data.application.assisted_intake import (
            PARSER_VERSION,
            RetrievalFailure,
            content_fingerprint,
            effective_fields,
            match_listing,
            normalize_url,
            parse_snapshot,
            resolve_source_policy,
            validate_url,
        )
        from modules.external_data.security import redact_sensitive_snapshot

        url = url.strip()
        validate_url(url)
        canon_url = normalize_url(url)

        self._state.setdefault("assistedIntakes", [])
        for intake in self._state["assistedIntakes"]:
            if intake.get("canonicalUrl") == canon_url:
                if authorize_existing is not None:
                    authorize_existing(_copy(intake))
                elif not self._seed_fixtures:
                    raise NetworkListingPolicyError("INTAKE_SCOPE_CONTEXT_REQUIRED")
                if intake.get("stage") in {"NEEDS_REVIEW", "READY", "QUARANTINED", "FAILED", "AWAITING_ASSISTED_ENTRY"}:
                    # Exact duplicate before retrieval (terminal state idempotency)
                    self._save_write(write, intake)
                    return _copy(intake)
                else:
                    raise NetworkListingConflict(f"URL {url} is already being processed (intake {intake['id']})")

        policy = resolve_source_policy(url)
        intake_id = (
            f"IN-{3001 + len(self._state['assistedIntakes'])}"
            if self._seed_fixtures
            else str(uuid.uuid4())
        )

        submitted_at = _now()
        intake = {
            "id": intake_id,
            "tenantId": tenant_id,
            "originalUrl": url,
            "canonicalUrl": canon_url,
            "submitter": actor_name,
            "submittedBy": actor_name,
            "submittedAt": submitted_at,
            "updatedAt": submitted_at,
            "lastObservedAt": None,
            "owner": actor_name,
            "heatZoneId": heat_zone_id,
            "intakeMethod": "URL",
            "stage": "SUBMITTED",
            "sourceId": policy.source_id,
            "policy": policy.policy,
            "policyLabel": policy.policy_label,
            "policyReason": policy.policy_reason,
            "rawSnapshot": None,
            "snapshotId": None,
            "capturedAt": None,
            "parserVersion": PARSER_VERSION,
            "correlationId": correlation_id,
            "parsedFields": {},
            "matchResult": None,
            "auditEvents": [],
            "idempotencyKey": idempotency_key,
            "version": 1,
        }

        if policy.quarantines or policy.policy in {"POLICY_UNKNOWN", "SOURCE_BLOCKED"}:
            intake["stage"] = "QUARANTINED"
            intake["matchResult"] = {
                "outcome": "QUARANTINED",
                "outcomeLabel": "已隔離",
                "confidence": 0.0,
                "targetListingId": None,
                "agreeingSignals": [],
                "contradictingSignals": [],
                "summary": f"依來源政策 {policy.policy} 予以隔離：{policy.policy_reason}",
            }
        elif policy.policy in {"ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED"}:
            intake["stage"] = "AWAITING_ASSISTED_ENTRY"
        elif policy.policy == "APPROVED_RETRIEVAL":
            prepare_async = getattr(self._intakes, "prepare_async_submission", None)
            if prepare_async is not None:
                # A committed durable job owns retrieval/snapshot side effects.
                # No HTTP or object-store writes occur in submission's SQL unit.
                prepare_async(job_queue)
                async_intake = True
            if async_intake and job_queue is not None:
                intake["stage"] = "SUBMITTED"
                from shared.jobs.queue import JobRequest
                payload_to_send = {
                    "intake_id": intake_id,
                    "url": url,
                    "heat_zone_id": heat_zone_id,
                    "actor_role_id": actor_role_id,
                    "actor_name": actor_name,
                    "tenant_id": tenant_id,
                }
                job, created = job_queue.enqueue(
                    JobRequest(
                        job_type="assisted-listing-intake",
                        payload=payload_to_send,
                        idempotency_key=(
                            "intake-submit:" + hashlib.sha256(f"{tenant_id}:{write.key}".encode()).hexdigest()
                            if prepare_async is not None else idempotency_key
                        ),
                    ),
                    correlation_id=correlation_id or "system",
                )
                intake["jobId"] = job.job_id
            else:
                intake["stage"] = "RETRIEVING"
                import json
                snapshot_service = self._get_snapshot_service()
                gate = self._get_security_gate(snapshot_service)
                
                resolved_tenant_id = self._require_tenant(tenant_id)
                retrieval_res = gate.fetch(
                    canon_url,
                    tenant_id=resolved_tenant_id,
                    retrieval_method="fixture_replay",
                )
                
                if retrieval_res.ok:
                    raw_dict = json.loads(retrieval_res.body.decode("utf-8"))
                    redacted_raw = redact_sensitive_snapshot(raw_dict)
                    redacted_data = json.dumps(redacted_raw).encode("utf-8")
                    
                    observed_at = datetime.now(UTC)
                    snapshot_id = snapshot_service.create_snapshot(
                        tenant_id=resolved_tenant_id,
                        intake_id=intake_id,
                        source_id=policy.source_id,
                        raw_data=retrieval_res.body,
                        original_url=url,
                        canonical_url=canon_url,
                        media_type="application/json",
                        capture_method="SERVER_RETRIEVAL",
                        retention_class="STANDARD",
                        encryption_key_ref="kms://default-key",
                        observed_at=observed_at,
                        captured_at=datetime.now(UTC),
                        bucket="taiwan-snapshots",
                        redacted_data=redacted_data,
                    )
                    
                    intake["stage"] = "PARSING"
                    intake["rawSnapshot"] = redacted_raw
                    intake["snapshotId"] = snapshot_id
                    intake["lastObservedAt"] = observed_at.isoformat().replace("+00:00", "Z")
                    intake["capturedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                    
                    from modules.external_data.application.assisted_intake import RetrievalResult
                    mock_retrieval_obj = RetrievalResult(
                        snapshot_id=snapshot_id,
                        captured_at=intake["capturedAt"],
                        raw=redacted_raw,
                    )
                    intake["parsedFields"] = parse_snapshot(mock_retrieval_obj)

                    effective_vals = effective_fields(intake["parsedFields"])

                    from modules.external_data.application.assisted_intake import (
                        ASSISTED_ENTRY_REQUIRED_FIELDS,
                    )
                    has_all_required = True
                    for rf in ASSISTED_ENTRY_REQUIRED_FIELDS:
                        val = effective_vals.get(rf)
                        if val in (None, ""):
                            has_all_required = False
                            break
                        if rf in ("rent", "areaPing"):
                            try:
                                if float(val) <= 0:
                                    has_all_required = False
                                    break
                            except (ValueError, TypeError):
                                has_all_required = False
                                break

                    if has_all_required:
                        intake["stage"] = "MATCHING"
                        fingerprint = content_fingerprint(effective_vals)

                        match_res = match_listing(
                            values=effective_vals,
                            canonical_url=canon_url,
                            source_id=policy.source_id,
                            fingerprint=fingerprint,
                            listings=self._get_match_listings(),
                        )
                        intake["matchResult"] = match_res.to_dict()
                        if match_res.outcome == "POSSIBLE_MATCH":
                            intake["stage"] = "NEEDS_REVIEW"
                        else:
                            intake["stage"] = "READY"
                    else:
                        intake["stage"] = "AWAITING_ASSISTED_ENTRY"
                else:
                    intake["stage"] = "FAILED"
                    failure_obj = RetrievalFailure(
                        code=retrieval_res.failure.code if retrieval_res.failure else "FAILED",
                        summary=retrieval_res.failure.summary if retrieval_res.failure else "retrieval failed",
                        next_action=retrieval_res.failure.next_action if retrieval_res.failure else "governance review",
                        retryable=retrieval_res.failure.retryable if retrieval_res.failure else False,
                    )
                    intake["failure"] = failure_obj.to_dict()


        audit_evt = {
            "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name,
            "action": "intake.create",
            "targetId": intake_id,
            "message": f"Intake record {intake_id} created for {url}.",
            "correlationId": correlation_id,
            "metadata": {
                "policy": policy.policy,
                "stage": intake["stage"],
                "matchOutcome": intake["matchResult"]["outcome"] if intake["matchResult"] else None,
            }
        }
        intake["auditEvents"].append(audit_evt)
        self._save_intake(intake)

        self._save_write(write, intake)
        return _copy(intake)

    def list_intakes(
        self,
        selected_heat_zone_id: str | None = None,
        *,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self._load_intakes()
        self._state.setdefault("assistedIntakes", [])
        intakes = self._state["assistedIntakes"]
        visible_tenant = tenant_id.strip() if tenant_id and tenant_id.strip() else self._tenant_id
        if visible_tenant is not None:
            intakes = [item for item in intakes if item.get("tenantId") == visible_tenant]
        if selected_heat_zone_id is not None:
            intakes = [item for item in intakes if item.get("heatZoneId") == selected_heat_zone_id]
        return _copy(intakes)

    def get_intake(self, intake_id: str) -> dict[str, Any]:
        self._load_intakes()
        self._state.setdefault("assistedIntakes", [])
        for intake in self._state["assistedIntakes"]:
            if intake["id"] == intake_id:
                return _copy(intake)
        raise NetworkListingNotFound(f"assisted intake record {intake_id} not found")

    @_atomic_intake_submission
    def correct_intake(
        self,
        *,
        intake_id: str,
        fields: dict[str, Any],
        reason: str | None,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        authorize_existing: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        # Server-side role check
        actor_name = self._require_actor(actor_name, actor_role_id)
        allowed_roles = {"expansionStaff", "expansion_user", "expansionManager", "expansion-manager", "dataSteward", "data_owner", "site_reviewer", "executive"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to correct intake")

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="correct intake",
        )
        write = self._prepare_write("correct_intake", governed_key, actor_name,
                                    {"intake_id": intake_id, "fields": fields, "reason": reason, "risk_summary": risk_summary, "risk_acknowledged": risk_acknowledged}, correlation_id)
        if write.replayed_response is not None:
            return write.replayed_response

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="correct intake",
        )

        intake = self._listing_intake(intake_id)

        from modules.external_data.application.assisted_intake import (
            IDENTITY_FIELDS,
            content_fingerprint,
            effective_fields,
            match_listing,
        )

        requires_reason = False
        for key in fields:
            if key in IDENTITY_FIELDS:
                requires_reason = True
                break
        if requires_reason and (not reason or not reason.strip()):
            raise NetworkListingPolicyError("reason is required for modifying identity-affecting fields")

        corrections_made = []
        before_after_changes = []
        for key, val in fields.items():
            if key not in intake["parsedFields"]:
                intake["parsedFields"][key] = {
                    "key": key,
                    "label": key,
                    "sourceValue": None,
                    "normalizedValue": None,
                    "correctedValue": None,
                    "correctionReason": None,
                    "identity": key in IDENTITY_FIELDS,
                    "lowConfidence": False,
                }

            before_val = intake["parsedFields"][key].get("correctedValue") or intake["parsedFields"][key].get("normalizedValue")
            intake["parsedFields"][key]["correctedValue"] = val
            intake["parsedFields"][key]["correctionReason"] = reason
            after_val = val
            corrections_made.append(f"'{key}' corrected from '{before_val}' to '{after_val}'")
            before_after_changes.append({
                "field": key,
                "before": before_val,
                "after": val,
            })

        effective_vals = effective_fields(intake["parsedFields"])

        if not _missing_assisted_entry_fields(effective_vals):
            fingerprint = content_fingerprint(effective_vals)
            match_res = match_listing(
                values=effective_vals,
                canonical_url=intake["canonicalUrl"],
                source_id=intake["sourceId"],
                fingerprint=fingerprint,
                listings=self._get_match_listings(),
            )
            intake["matchResult"] = match_res.to_dict()
            if match_res.outcome == "POSSIBLE_MATCH":
                intake["stage"] = "NEEDS_REVIEW"
            else:
                intake["stage"] = "READY"
        else:
            intake["stage"] = "AWAITING_ASSISTED_ENTRY"

        audit_evt = {
            "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name,
            "action": "intake.correct",
            "targetId": intake_id,
            "message": f"Fields corrected: {'; '.join(corrections_made)}. Reason: {reason}",
            "correlationId": correlation_id,
            "metadata": {
                "fields": list(fields.keys()),
                "reason": reason,
                "stage": intake["stage"],
                "matchOutcome": intake["matchResult"]["outcome"] if intake["matchResult"] else None,
                "beforeAfter": before_after_changes,
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
            }
        }
        intake["auditEvents"].append(audit_evt)
        intake["version"] = intake.get("version", 0) + 1
        self._save_intake(intake)
        self._save_write(write, intake)
        return _copy(intake)

    # A batch import row carries operator-supplied values, so it enters the
    # pipeline through the assisted-entry field table rather than through
    # retrieval. Each entry maps one assisted-entry field to the column names a
    # batch row may use for it.
    BATCH_ROW_FIELD_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("address", ("address_raw", "addressRaw", "address")),
        ("rent", ("rent_per_month", "rentPerMonth", "rent")),
        ("areaPing", ("area_ping", "areaPing", "area")),
        ("floor", ("floor",)),
        ("listingType", ("listing_type", "listingType")),
        ("providerListingId", ("provider_listing_id", "providerListingId")),
    )

    def record_batch_assisted_entry(
        self,
        *,
        intake_id: str,
        tenant_id: str,
        row: dict[str, Any],
        source_id: str,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
        actor_name: str | None = None,
    ) -> dict[str, Any]:
        """Land one batch-import row as an assisted-entry intake record.

        The caller owns ``intake_id`` and must derive it from the tenant and the
        batch member rather than from anything the submitter supplies -- see
        :func:`apps.worker.oday_worker.handlers.batch_listing_intake_id`. That is
        what makes this write durably idempotent: replaying a row after a crash
        addresses the record the interrupted attempt wrote instead of creating a
        second one, and one tenant cannot address another tenant's record.

        Stage comes from the row's own data under the same rules
        :meth:`correct_intake` applies -- a row missing a mandatory
        assisted-entry field stops at ``AWAITING_ASSISTED_ENTRY`` instead of
        being reported as a complete listing. Nothing is retrieved from a
        source: a batch row has no URL and no snapshot, and those fields stay
        ``None`` rather than being filled with a placeholder.
        """

        from modules.external_data.application.assisted_intake import (
            IDENTITY_FIELDS,
            content_fingerprint,
            effective_fields,
            match_listing,
        )

        tenant_id = str(tenant_id or "").strip()
        if not tenant_id:
            raise NetworkListingPolicyError(
                "batch assisted entry requires an authenticated tenant scope"
            )

        self._load_intakes()
        try:
            existing = self._listing_intake(intake_id)
        except NetworkListingNotFound:
            existing = None
        if existing is None and hasattr(self._intakes, "get_intake"):
            existing = self._intakes.get_intake(intake_id)

        if existing is not None and str(existing.get("tenantId") or "") != tenant_id:
            # Defence in depth: the id is already tenant-derived, so reaching
            # here means the id scheme was bypassed. Refuse rather than
            # overwrite another tenant's record.
            raise NetworkListingPolicyError(
                f"assisted intake record {intake_id} belongs to another tenant"
            )

        if existing is not None:
            # Crash replay / idempotency: reuse the existing intake without destructive
            # upsert, preserving any operator corrections and current stage.
            return _copy(existing)

        def _resolve_scope(camel_key: str, snake_key: str) -> Any:
            has_camel = camel_key in row and row[camel_key] is not None
            has_snake = snake_key in row and row[snake_key] is not None
            val_camel = (
                str(row[camel_key]).strip()
                if has_camel and str(row[camel_key]).strip()
                else None
            )
            val_snake = (
                str(row[snake_key]).strip()
                if has_snake and str(row[snake_key]).strip()
                else None
            )
            if val_camel is not None and val_snake is not None and val_camel != val_snake:
                raise NetworkListingPolicyError(
                    f"Conflicting scope aliases in batch row for {camel_key} ('{val_camel}') and {snake_key} ('{val_snake}')"
                )
            return val_camel if val_camel is not None else val_snake

        heat_zone_id = _resolve_scope("heatZoneId", "heat_zone_id")
        region_id = _resolve_scope("regionId", "region_id")
        brand_id = _resolve_scope("brandId", "brand_id")
        assigned_area_id = _resolve_scope("assignedAreaId", "assigned_area_id")

        now = _now()
        intake: dict[str, Any] = {
            "id": intake_id,
            "tenantId": tenant_id,
            "originalUrl": None,
            "canonicalUrl": None,
            "submitter": actor_name or "批次匯入",
            # The legacy worker actor_name can be a display label; it does not
            # establish a verified subject identity.
            "submittedBy": None,
            "submittedAt": now,
            "updatedAt": now,
            "lastObservedAt": None,
            "owner": actor_name or "批次匯入",
            "heatZoneId": heat_zone_id,
            "regionId": region_id,
            "brandId": brand_id,
            "assignedAreaId": assigned_area_id,
            "intakeMethod": "BATCH_ASSISTED_ENTRY",
            "stage": "SUBMITTED",
            "sourceId": source_id,
            "policy": "ASSISTED_ENTRY_ONLY",
            "policyLabel": "僅限人工協助輸入",
            "policyReason": "批次匯入的既有房源由營運方提供，未經來源檢索。",
            "rawSnapshot": None,
            "snapshotId": None,
            "capturedAt": None,
            "parserVersion": None,
            "correlationId": correlation_id,
            "parsedFields": {},
            "matchResult": None,
            "auditEvents": [],
            "idempotencyKey": idempotency_key,
            "createdAt": now,
            "version": 1,
        }

        parsed_fields: dict[str, Any] = dict(intake.get("parsedFields") or {})
        for field_name, columns in self.BATCH_ROW_FIELD_COLUMNS:
            raw_value = _first_present(row, *columns)
            if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
                continue
            normalized = raw_value.strip() if isinstance(raw_value, str) else raw_value
            parsed_fields[field_name] = {
                "key": field_name,
                "label": field_name,
                "sourceValue": raw_value,
                "normalizedValue": normalized,
                "correctedValue": None,
                "correctionReason": None,
                "identity": field_name in IDENTITY_FIELDS,
                "lowConfidence": False,
            }
        intake["parsedFields"] = parsed_fields
        intake["updatedAt"] = now

        effective_vals = effective_fields(parsed_fields)
        missing = _missing_assisted_entry_fields(effective_vals)
        if missing:
            intake["stage"] = "AWAITING_ASSISTED_ENTRY"
            intake["matchResult"] = None
            intake["contentFingerprint"] = None
            intake["missingRequiredFields"] = missing
        else:
            intake.pop("missingRequiredFields", None)
            fingerprint = content_fingerprint(effective_vals)
            match_res = match_listing(
                values=effective_vals,
                canonical_url="",
                source_id=source_id,
                fingerprint=fingerprint,
                listings=self._get_match_listings(),
            )
            intake["contentFingerprint"] = fingerprint
            intake["matchResult"] = match_res.to_dict()
            intake["stage"] = (
                "NEEDS_REVIEW" if match_res.outcome == "POSSIBLE_MATCH" else "READY"
            )

        # A deterministic audit id keeps a replay from growing a second entry
        # for the same batch member.
        audit_evt = {
            "id": f"AUD-INTAKE-BATCH-{intake_id}",
            "occurredAt": now,
            "actorRoleId": "system",
            "actorName": actor_name or "批次匯入",
            "action": "intake.batch_assisted_entry",
            "targetId": intake_id,
            "message": f"批次匯入建立協助輸入待辦，階段 {intake['stage']}。",
            "correlationId": correlation_id,
            "metadata": {
                "fields": sorted(parsed_fields),
                "stage": intake["stage"],
                "missingRequiredFields": missing,
                "matchOutcome": (
                    intake["matchResult"]["outcome"] if intake["matchResult"] else None
                ),
                "idempotencyKey": idempotency_key,
            },
        }
        intake["auditEvents"] = [
            evt for evt in intake.get("auditEvents", []) if evt.get("id") != audit_evt["id"]
        ]
        intake["auditEvents"].append(audit_evt)

        stored_intake, _ = self._create_intake_if_absent(intake)
        return _copy(stored_intake)

    @_atomic_intake_submission
    def decide_intake(
        self,
        *,
        intake_id: str,
        action: str,
        reason: str | None,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        authorize_existing: Callable[[dict[str, Any]], None] | None = None,
        target_listing_id: str | None = None,
    ) -> dict[str, Any]:
        # Server-side role check
        actor_name = self._require_actor(actor_name, actor_role_id)
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer", "dataSteward", "data_owner", "executive"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to decide intake")

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="decide intake",
        )
        write = self._prepare_write("decide_intake", governed_key, actor_name,
                                    {"intake_id": intake_id, "action": action, "target_listing_id": target_listing_id, "reason": reason, "risk_summary": risk_summary, "risk_acknowledged": risk_acknowledged}, correlation_id)
        if write.replayed_response is not None:
            return write.replayed_response

        reason_text = (reason or "").strip()
        if not reason_text:
            raise NetworkListingPolicyError("decision reason is required")

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="decide intake",
        )

        intake = self._listing_intake(intake_id)
        action = action.strip().lower()

        if action not in {"create", "revise", "duplicate", "quarantine", "reject"}:
            raise NetworkListingPolicyError(f"invalid action: {action}")

        from modules.external_data.application.assisted_intake import effective_fields
        effective_vals = effective_fields(intake["parsedFields"])

        before_stage = intake["stage"]
        before_after = {"stage": {"before": before_stage}}
        effect_summary = f"Manual decision '{action}' recorded for intake {intake_id}."

        if action == "create":
            missing = _missing_assisted_entry_fields(effective_vals)
            address = effective_vals.get("address")
            if not isinstance(address, str) or not address.strip():
                missing.append("address")
            for numeric_field in ("rent", "areaPing"):
                value = effective_vals.get(numeric_field)
                try:
                    if isinstance(value, bool) or not math.isfinite(float(value)):
                        missing.append(numeric_field)
                except (TypeError, ValueError):
                    missing.append(numeric_field)
            if missing:
                raise NetworkListingPolicyError(
                    "required assisted-entry fields are missing or invalid: "
                    + ", ".join(_dedupe(missing))
                )

            new_id = (
                f"L-{2031 + len(self._state['listings'])}"
                if self._seed_fixtures else str(uuid.uuid4())
            )
            snapshot_id = intake.get("snapshotId")
            new_listing = {
                "id": new_id,
                "addressId": f"ADDR-{new_id}" if self._seed_fixtures else str(uuid.uuid4()),
                "tenantId": intake["tenantId"],
                "sourceId": intake["sourceId"],
                "sourceListingId": effective_vals.get("providerListingId") or "",
                "heatZoneId": intake.get("heatZoneId"),
                "address": address,
                "status": "new",
                "rentPerMonth": float(effective_vals["rent"]),
                "areaPing": float(effective_vals["areaPing"]),
                "floor": effective_vals.get("floor") or "",
                # Assisted-entry corrections are source facts, not geocoder
                # receipts, feasibility verdicts, or scoring measurements.
                # None of those may be invented by a manual create decision.
                "frontageMeters": None,
                "geocodeConfidence": None,
                "latitude": None,
                "longitude": None,
                "h3Index": None,
                "hardRuleFailures": ["missing_geocode_evidence"],
                "hardRuleSummary": "尚未檢核；缺少地理編碼證據",
                "sourceEvidence": [snapshot_id] if snapshot_id else [],
                "snapshotId": snapshot_id,
                "fitScore": None,
                "firstSeenAt": _now(),
                "sourceUrl": intake["originalUrl"],
            }
            self._state["listings"].append(new_listing)
            self._sync_listing_to_repo(new_id)
            intake["stage"] = "READY"
            if not intake.get("matchResult"):
                intake["matchResult"] = {
                    "outcome": "NEW",
                    "outcomeLabel": "新物件",
                    "confidence": 1.0,
                    "targetListingId": new_id,
                    "agreeingSignals": [],
                    "contradictingSignals": [],
                    "summary": f"已手動建立為新物件 {new_id}。"
                }
            else:
                intake["matchResult"]["targetListingId"] = new_id
                intake["matchResult"]["outcome"] = "NEW"
                intake["matchResult"]["outcomeLabel"] = "新物件"
                intake["matchResult"]["summary"] = f"已手動建立為新物件 {new_id}。"

            before_after["stage"]["after"] = "READY"
            before_after["listings_count"] = {"before": len(self._state["listings"]) - 1, "after": len(self._state["listings"])}
            effect_summary = f"Created new listing {new_id} from intake {intake_id}."

        elif action == "revise":
            target_id = target_listing_id or (intake.get("matchResult") or {}).get("targetListingId") or (self._state["listings"][0]["id"] if self._state.get("listings") else None)
            if not target_id:
                raise NetworkListingConflict("no target listing found for revision")
            target = self._listing(target_id)
            before_rent = target["rentPerMonth"]
            before_area = target["areaPing"]
            before_floor = target["floor"]

            target["rentPerMonth"] = effective_vals.get("rent", target["rentPerMonth"])
            target["areaPing"] = effective_vals.get("areaPing", target["areaPing"])
            target["floor"] = effective_vals.get("floor", target["floor"])
            snapshot_id = intake.get("snapshotId")
            target["sourceEvidence"] = _dedupe(
                list(target.get("sourceEvidence") or ())
                + ([snapshot_id] if snapshot_id else [])
            )
            target["status"] = "watching"
            self._sync_listing_to_repo(target_id)
            intake["stage"] = "READY"
            if not intake.get("matchResult"):
                intake["matchResult"] = {"targetListingId": target_id, "confidence": 0.9}
            else:
                intake["matchResult"]["targetListingId"] = target_id
            intake["matchResult"]["summary"] = f"已手動將版本更新至既有物件 {target_id}。"

            before_after["stage"]["after"] = "READY"
            before_after["target_rent"] = {"before": before_rent, "after": target["rentPerMonth"]}
            before_after["target_area"] = {"before": before_area, "after": target["areaPing"]}
            before_after["target_floor"] = {"before": before_floor, "after": target["floor"]}
            effect_summary = f"Revised listing {target_id} from intake {intake_id}."

        elif action == "duplicate":
            target_id = intake["matchResult"].get("targetListingId")
            if not target_id:
                raise NetworkListingConflict("no target listing found for duplicate merge")
            target = self._listing(target_id)
            before_evidence_count = len(target.get("sourceEvidence") or ())

            snapshot_id = intake.get("snapshotId")
            target["sourceEvidence"] = _dedupe(
                list(target.get("sourceEvidence") or ())
                + ([snapshot_id] if snapshot_id else [])
            )
            target["status"] = "watching"
            self._sync_listing_to_repo(target_id)
            intake["stage"] = "READY"
            intake["matchResult"]["summary"] = f"已手動標記為重複並合併至 {target_id}。"

            before_after["stage"]["after"] = "READY"
            before_after["target_evidence_count"] = {"before": before_evidence_count, "after": len(target["sourceEvidence"])}
            effect_summary = f"Merged duplicate intake {intake_id} into listing {target_id}."

        elif action == "quarantine":
            intake["stage"] = "QUARANTINED"
            if not intake.get("matchResult"):
                intake["matchResult"] = {
                    "outcome": "QUARANTINED",
                    "outcomeLabel": "已隔離",
                    "confidence": 0.0,
                    "targetListingId": None,
                    "agreeingSignals": [],
                    "contradictingSignals": [],
                    "summary": f"已手動送交隔離。原因：{reason_text}"
                }
            else:
                intake["matchResult"]["outcome"] = "QUARANTINED"
                intake["matchResult"]["summary"] = f"已手動送交隔離。原因：{reason_text}"

            before_after["stage"]["after"] = "QUARANTINED"
            effect_summary = f"Quarantined intake {intake_id}."

        elif action == "reject":
            intake["stage"] = "FAILED"
            if not intake.get("matchResult"):
                intake["matchResult"] = {
                    "outcome": "QUARANTINED",
                    "outcomeLabel": "已隔離",
                    "confidence": 0.0,
                    "targetListingId": None,
                    "agreeingSignals": [],
                    "contradictingSignals": [],
                    "summary": f"已拒絕此送件。原因：{reason_text}"
                }
            else:
                intake["matchResult"]["summary"] = f"已拒絕此送件。原因：{reason_text}"

            before_after["stage"]["after"] = "FAILED"
            effect_summary = f"Rejected intake {intake_id}."

        audit_evt = {
            "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name,
            "action": f"intake.decide.{action}",
            "targetId": intake_id,
            "message": f"Intake decision '{action}' recorded. Reason: {reason_text}",
            "correlationId": correlation_id,
            "metadata": {
                "decision": action,
                "reason": reason_text,
                "stage": intake["stage"],
                "targetListingId": intake["matchResult"].get("targetListingId"),
                "beforeAfter": before_after,
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
                "effectSummary": effect_summary,
            }
        }
        intake["auditEvents"].append(audit_evt)
        intake["version"] = intake.get("version", 0) + 1
        self._save_intake(intake)
        self._save_write(write, intake)
        return _copy(intake)

    @_atomic_intake_submission
    def retry_intake(
        self,
        *,
        intake_id: str,
        actor_role_id: str,
        actor_name: str | None,
        correlation_id: str | None,
        job_queue: Any | None = None,
        tenant_id: str | None = None,
        idempotency_key: str | None = None,
        authorize_existing: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        actor_name = self._require_actor(actor_name, actor_role_id)
        tenant_id = self._require_tenant(tenant_id)
        write = self._prepare_write("retry_intake", idempotency_key, actor_name,
                                    {"intake_id": intake_id}, correlation_id)
        if write.replayed_response is not None:
            return write.replayed_response
        intake = self._listing_intake(intake_id)
        if intake["stage"] not in {"FAILED", "READY", "NEEDS_REVIEW", "AWAITING_ASSISTED_ENTRY"}:
            raise NetworkListingConflict(f"intake {intake_id} is in stage {intake['stage']} and cannot be retried")

        prepare_async = getattr(self._intakes, "prepare_async_submission", None)
        job = None
        if prepare_async is not None and intake.get("jobId"):
            prepare_async(job_queue)
            job = job_queue.get(intake["jobId"])
            if (job is None or job.payload.get("tenant_id") != tenant_id
                    or job.payload.get("intake_id") != intake_id):
                raise NetworkListingPolicyError("INTAKE_JOB_BINDING_REQUIRED")
        elif prepare_async is None and job_queue is not None and intake.get("idempotencyKey"):
            job = job_queue.get_by_idempotency_key(intake["idempotencyKey"])
        if job is not None:
            if prepare_async is not None:
                from shared.jobs.queue import JobStatus

                if job.status not in {JobStatus.FAILED, JobStatus.PARTIAL}:
                    raise NetworkListingConflict("INTAKE_JOB_NOT_RETRYABLE")
                if (intake.get("failure") or {}).get("retryable") is False:
                    raise NetworkListingConflict("INTAKE_FAILURE_NOT_RETRYABLE")
            intake["stage"] = "SUBMITTED"
            if prepare_async is not None:
                job_queue.replay(job.job_id, expected_version=job.version, fence_token=job.fence_token)
            else:
                job_queue.replay(job.job_id)
            audit_evt = {
                "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
                "occurredAt": _now(),
                "actorRoleId": actor_role_id,
                "actorName": actor_name,
                "action": "intake.retry",
                "targetId": intake_id,
                "message": f"Replayed async intake job for {intake['originalUrl']}.",
                "correlationId": correlation_id,
                "metadata": {
                    "job_id": job.job_id,
                    "stage": intake["stage"],
                }
            }
            intake["auditEvents"].append(audit_evt)
            intake["version"] = intake.get("version", 0) + 1
            self._save_intake(intake)
            self._save_write(write, intake)
            return _copy(intake)


        from modules.external_data.application.assisted_intake import (
            RetrievalFailure,
            content_fingerprint,
            effective_fields,
            match_listing,
            parse_snapshot,
            resolve_source_policy,
        )
        from modules.external_data.security import redact_sensitive_snapshot

        policy = resolve_source_policy(intake["originalUrl"])
        if prepare_async is not None and policy.policy == "APPROVED_RETRIEVAL":
            from modules.listing.application.intake_workflow_bootstrap import (
                IntakeWorkflowUnavailable,
            )

            raise IntakeWorkflowUnavailable("Canonical retry requires its linked durable retrieval job")
        if policy.quarantines or policy.policy in {"POLICY_UNKNOWN", "SOURCE_BLOCKED"}:
            intake["stage"] = "QUARANTINED"
            intake["matchResult"] = {
                "outcome": "QUARANTINED",
                "outcomeLabel": "已隔離",
                "confidence": 0.0,
                "targetListingId": None,
                "agreeingSignals": [],
                "contradictingSignals": [],
                "summary": f"依來源政策 {policy.policy} 予以隔離：{policy.policy_reason}",
            }
            retrieval = None
        elif policy.policy in {"ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED"}:
            intake["stage"] = "AWAITING_ASSISTED_ENTRY"
            retrieval = None
        else:
            intake["stage"] = "RETRIEVING"
            import json
            snapshot_service = self._get_snapshot_service()
            gate = self._get_security_gate(snapshot_service)
            
            resolved_tenant_id = self._require_tenant(tenant_id)
            retrieval_res = gate.fetch(
                intake["canonicalUrl"],
                tenant_id=resolved_tenant_id,
                retrieval_method="fixture_replay",
            )
            
            if retrieval_res.ok:
                raw_dict = json.loads(retrieval_res.body.decode("utf-8"))
                redacted_raw = redact_sensitive_snapshot(raw_dict)
                redacted_data = json.dumps(redacted_raw).encode("utf-8")
                
                observed_at = datetime.now(UTC)
                snapshot_id = snapshot_service.create_snapshot(
                    tenant_id=resolved_tenant_id,
                    intake_id=intake_id,
                    source_id=policy.source_id,
                    raw_data=retrieval_res.body,
                    original_url=intake["originalUrl"],
                    canonical_url=intake["canonicalUrl"],
                    media_type="application/json",
                    capture_method="SERVER_RETRIEVAL",
                    retention_class="STANDARD",
                    encryption_key_ref="kms://default-key",
                    observed_at=observed_at,
                    captured_at=datetime.now(UTC),
                    bucket="taiwan-snapshots",
                    redacted_data=redacted_data,
                )
                
                intake["lastObservedAt"] = observed_at.isoformat().replace("+00:00", "Z")
                from modules.external_data.application.assisted_intake import RetrievalResult
                retrieval = RetrievalResult(
                    snapshot_id=snapshot_id,
                    captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    raw=redacted_raw,
                )
            else:
                from modules.external_data.application.assisted_intake import (
                    RetrievalFailure,
                    RetrievalResult,
                )
                retrieval = RetrievalResult(
                    snapshot_id="FAILED",
                    captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    failure=RetrievalFailure(
                        code=retrieval_res.failure.code if retrieval_res.failure else "FAILED",
                        summary=retrieval_res.failure.summary if retrieval_res.failure else "retrieval failed",
                        next_action=retrieval_res.failure.next_action if retrieval_res.failure else "governance review",
                        retryable=retrieval_res.failure.retryable if retrieval_res.failure else False,
                    )
                )

        if retrieval is None:
            audit_evt = {
                "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
                "occurredAt": _now(),
                "actorRoleId": actor_role_id,
                "actorName": actor_name,
                "action": "intake.retry",
                "targetId": intake_id,
                "message": f"Retried URL retrieval for {intake['originalUrl']}.",
                "correlationId": correlation_id,
                "metadata": {
                    "policy": policy.policy,
                    "stage": intake["stage"],
                    "matchOutcome": intake["matchResult"]["outcome"] if intake["matchResult"] else None,
                }
            }
            intake["auditEvents"].append(audit_evt)
            intake["version"] = intake.get("version", 0) + 1
            self._save_intake(intake)
            self._save_write(write, intake)
            return _copy(intake)

        intake["stage"] = "RETRIEVING"

        if retrieval.ok:
            intake["stage"] = "PARSING"
            intake["rawSnapshot"] = retrieval.raw
            intake["snapshotId"] = retrieval.snapshot_id
            intake["capturedAt"] = retrieval.captured_at

            preserved_corrections = {
                k: (v.get("correctedValue"), v.get("correctionReason"))
                for k, v in intake["parsedFields"].items()
                if v.get("correctedValue") is not None
            }

            intake["parsedFields"] = parse_snapshot(retrieval)
            for k, (c_val, c_reason) in preserved_corrections.items():
                if k in intake["parsedFields"]:
                    intake["parsedFields"][k]["correctedValue"] = c_val
                    intake["parsedFields"][k]["correctionReason"] = c_reason

            effective_vals = effective_fields(intake["parsedFields"])

            from modules.external_data.application.assisted_intake import (
                ASSISTED_ENTRY_REQUIRED_FIELDS,
            )
            has_all_required = True
            for rf in ASSISTED_ENTRY_REQUIRED_FIELDS:
                val = effective_vals.get(rf)
                if val in (None, ""):
                    has_all_required = False
                    break
                if rf in ("rent", "areaPing"):
                    try:
                        if float(val) <= 0:
                            has_all_required = False
                            break
                    except (ValueError, TypeError):
                        has_all_required = False
                        break

            if has_all_required:
                intake["stage"] = "MATCHING"
                fingerprint = content_fingerprint(effective_vals)

                match_res = match_listing(
                    values=effective_vals,
                    canonical_url=intake["canonicalUrl"],
                    source_id=policy.source_id,
                    fingerprint=fingerprint,
                    listings=self._get_match_listings(),
                )
                intake["matchResult"] = match_res.to_dict()
                if match_res.outcome == "POSSIBLE_MATCH":
                    intake["stage"] = "NEEDS_REVIEW"
                else:
                    intake["stage"] = "READY"
            else:
                intake["stage"] = "AWAITING_ASSISTED_ENTRY"
        else:
            intake["stage"] = "FAILED"
            failure_obj = RetrievalFailure(
                code=retrieval.failure.code if retrieval.failure else "FAILED",
                summary=retrieval.failure.summary if retrieval.failure else "retrieval failed",
                next_action=retrieval.failure.next_action if retrieval.failure else "governance review",
                retryable=retrieval.failure.retryable if retrieval.failure else False,
            )
            intake["failure"] = failure_obj.to_dict()


        audit_evt = {
            "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name,
            "action": "intake.retry",
            "targetId": intake_id,
            "message": f"Retried URL retrieval for {intake['originalUrl']}.",
            "correlationId": correlation_id,
            "metadata": {
                "stage": intake["stage"],
                "matchOutcome": intake["matchResult"]["outcome"] if intake["matchResult"] else None,
            }
        }
        intake["auditEvents"].append(audit_evt)
        intake["version"] = intake.get("version", 0) + 1
        self._save_intake(intake)
        self._save_write(write, intake)
        return _copy(intake)

    # Adapter methods for PromotionService
    def get_listing(self, listing_id: str) -> dict[str, Any] | None:
        try:
            return self._listing(listing_id)
        except NetworkListingNotFound:
            return None

    def save_listing(self, listing: dict[str, Any], address: Any = None, key: Any = None) -> None:
        for i, lst in enumerate(self._state["listings"]):
            if lst["id"] == listing["id"]:
                self._state["listings"][i] = listing
                break
        self._sync_listing_to_repo(listing["id"])

    def list_candidates(self) -> list[dict[str, Any]]:
        cands = list(self._state["candidates"])
        if hasattr(self, "_listing_repository") and self._listing_repository:
            for draft in self._listing_repository.list_candidates():
                # Handle draft being either a CandidateSiteDraft or a dictionary
                if hasattr(draft, "candidate_site"):
                    listing_id = draft.candidate_site.listing_id
                    c_id = draft.candidate_site.candidate_site_id
                    status = draft.candidate_site.site_status
                else:
                    listing_id = draft.get("listingId") or draft.get("listing_id")
                    c_id = draft.get("candidateSiteId") or draft.get("id")
                    status = draft.get("status")
                if not any(c.get("listingId") == listing_id or c.get("listing_id") == listing_id for c in cands):
                    cands.append({
                        "id": c_id,
                        "listingId": listing_id,
                        "status": status,
                    })
        return cands

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        for cand in self._state["candidates"]:
            if cand["id"] == candidate_id:
                return cand
        return None

    def save_candidate(self, candidate: dict[str, Any]) -> None:
        for i, cand in enumerate(self._state["candidates"]):
            if cand["id"] == candidate["id"]:
                self._state["candidates"][i] = candidate
                self._sync_candidate_to_repo(candidate["id"])
                return
        self._state["candidates"].append(candidate)
        self._sync_candidate_to_repo(candidate["id"])

    def get_listing_intake(self, intake_id: str) -> dict[str, Any] | None:
        try:
            return self._listing_intake(intake_id)
        except NetworkListingNotFound:
            return None

    def save_intake(self, intake: dict[str, Any]) -> None:
        self._save_intake(intake)

    @_atomic_intake_submission
    def promote_intake(
        self,
        *,
        intake_id: str,
        reason: str | None = None,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        authorize_existing: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        actor_name = self._require_actor(actor_name, actor_role_id)
        allowed_roles = {
            "expansionManager", "expansion-manager", "siteReviewer", "site_reviewer",
            "expansionUser", "expansion-user", "expansion_user", "executive", "data_owner"
        }
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(f"role {actor_role_id!r} is not allowed to promote intake")

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="promote intake",
        )
        write = self._prepare_write("promote_intake", governed_key, actor_name,
                                    {"intake_id": intake_id, "reason": reason, "risk_summary": risk_summary, "risk_acknowledged": risk_acknowledged}, correlation_id)
        if write.replayed_response is not None:
            return write.replayed_response

        reason_text = (reason or "").strip()
        if not reason_text:
            raise NetworkListingPolicyError("promotion reason is required")

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="promote intake",
        )

        intake = self._listing_intake(intake_id)
        target_listing_id = intake["matchResult"].get("targetListingId")
        if not target_listing_id:
            raise NetworkListingConflict("intake must be resolved to a listing before promotion")

        listing = self._listing(target_listing_id)

        # Enforce segregation of duties & run reviewed promotion saga
        proposer_id = actor_name

        gate_snap = str(listing.get("fitScore", 0))
        gate_snapshot_sha256 = hashlib.sha256(gate_snap.encode()).hexdigest()

        from modules.listing.application.promotion import PromotionService
        from modules.listing.domain.intake_states import Actor, PrincipalRole, TransitionContext

        promo_service = PromotionService(
            promotion_repository=self._intakes,
            listing_repository=self,
            intake_repository=self,
            outbox_repository=(getattr(self._intakes, "outbox_repository", None)
                               or getattr(self._listing_repository, "outbox_repository", None)),
        )

        # 1. Propose
        if actor_role_id in {"site_reviewer", "executive", "expansionManager", "expansion-manager", "siteReviewer"}:
            proposer_role = PrincipalRole.EXPANSION_MANAGER
        elif actor_role_id in {"data_owner", "dataSteward"}:
            proposer_role = PrincipalRole.DATA_STEWARD
        else:
            proposer_role = PrincipalRole.EXPANSION_STAFF
        proposer_actor = Actor(
            actor_id=proposer_id,
            role=proposer_role,
            tenant_id=self._require_tenant(listing.get("tenantId")),
        )
        proposer_context = TransitionContext(
            actor=proposer_actor,
            idempotency_key=governed_key,
            correlation_id=correlation_id,
            risk_acknowledged=risk_acknowledged,
            reason=reason_text,
        )

        try:
            promo_record = promo_service.request_promotion(
                intake_id=intake_id,
                target_format_code="FORMAT-A",
                reason=reason_text,
                gate_snapshot_sha256=gate_snapshot_sha256,
                context=proposer_context,
            )
        except Exception as exc:
            if "DUPLICATE_CANDIDATE" in str(exc) or "DEPENDENCY_CONFLICT" in str(exc):
                raise NetworkListingConflict(str(exc)) from exc
            raise NetworkListingPolicyError(str(exc)) from exc

        audit_evt = {
            "id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": proposer_id,
            "action": "intake.promote_request",
            "targetId": intake_id,
            "message": f"Requested promotion of target listing {target_listing_id}. Reason: {reason_text}",
            "correlationId": correlation_id,
            "metadata": {
                "targetListingId": target_listing_id,
                "reason": reason_text,
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
            }
        }
        intake["auditEvents"].append(audit_evt)
        intake["version"] = intake.get("version", 0) + 1
        self._save_intake(intake)

        result = {
            "promotion_decision_id": promo_record["promotion_decision_id"],
            "intake_id": promo_record["intake_id"],
            "listing_id": promo_record["listing_id"],
            "status": promo_record["status"],
            "version": promo_record["version"],
            "created": False,
        }

        self._save_write(write, result)
        return result

    def _get_match_listings(self) -> list[dict[str, Any]]:
        res = []
        for lst in self._state["listings"]:
            source_id = lst.get("sourceId", "")
            source_listing_id = lst.get("sourceListingId", "")

            fp = lst.get("contentFingerprint")
            if not fp:
                from modules.external_data.application.assisted_intake import content_fingerprint
                fields_for_fp = {
                    "address": lst.get("address", ""),
                    "floor": lst.get("floor", ""),
                    "areaPing": lst.get("areaPing"),
                    "rent": lst.get("rentPerMonth"),
                    "listingType": lst.get("listingType", "店面"),
                    "listingStatus": lst.get("listingStatus", "active"),
                }
                fp = content_fingerprint(fields_for_fp)
                lst["contentFingerprint"] = fp

            res.append({
                "id": lst["id"],
                "sourceId": source_id,
                "sourceListingId": source_listing_id,
                "canonicalUrl": lst.get("sourceUrl", ""),
                "contentFingerprint": fp,
                "address": lst.get("address", ""),
                "floor": lst.get("floor", ""),
                "areaPing": lst.get("areaPing"),
                "rentPerMonth": lst.get("rentPerMonth"),
                "listingType": lst.get("listingType"),
            })
        return res

    def _listing_intake(self, intake_id: str) -> dict[str, Any]:
        self._state.setdefault("assistedIntakes", [])
        for intake in self._state["assistedIntakes"]:
            if intake["id"] == intake_id:
                return intake
        raise NetworkListingNotFound(f"assisted intake record {intake_id} not found")

    def _listing(self, listing_id: str) -> dict[str, Any]:
        for listing in self._state["listings"]:
            if listing.get("id") == listing_id:
                return listing
        raise NetworkListingNotFound(f"listing {listing_id} not found")

    def _candidate_for_listing(self, listing_id: str) -> dict[str, Any] | None:
        return next(
            (candidate for candidate in self._state["candidates"] if candidate.get("listingId") == listing_id),
            None,
        )

    def _get_snapshot_service(self) -> Any:
        import os

        from modules.external_data.application.source_snapshots import SourceSnapshotService
        from shared.infrastructure.object_store.client import GcsObjectStore, InMemoryObjectStore

        doc_store = None
        db_conn = None
        if hasattr(self._intakes, "_store"):
            doc_store = self._intakes._store
            if hasattr(doc_store, "_engine"):
                db_conn = doc_store._engine

        def residency_resolver(tenant_id: str) -> str:
            if doc_store:
                tenant_meta = doc_store.get("operator.tenant_metadata", tenant_id)
                if tenant_meta:
                    return tenant_meta.get("residency_mode", "TW_ONLY")
            return "TW_ONLY"

        if os.environ.get("ODP_OBJECT_STORE") == "gcs":
            object_store = GcsObjectStore(tenant_residency_resolver=residency_resolver)
        else:
            object_store = InMemoryObjectStore(tenant_residency_resolver=residency_resolver)

        return SourceSnapshotService(
            db_conn=db_conn,
            object_store=object_store,
            document_store=doc_store,
        )

    def _get_security_gate(self, snapshot_service: Any) -> Any:
        from modules.external_data.security.assisted_listing_retrieval import RetrievalSecurityGate
        return RetrievalSecurityGate(source_snapshot_service=snapshot_service)

    def _audit(
        self,
        *,
        action: str,
        target_id: str,
        actor_role_id: str,
        actor_name: str | None,
        correlation_id: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "id": f"AUD-NET-{uuid.uuid4().hex[:10]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name,
            "category": "workflow",
            "action": action,
            "targetType": "listing",
            "targetId": target_id,
            "message": f"{action} recorded for {target_id}",
            "correlationId": correlation_id,
            "metadata": metadata,
        }
        self._state["auditEvents"].insert(0, event)
        return _copy(event)

    def _expansion_steps(
        self,
        *,
        selected_id: str | None,
    ) -> list[dict[str, Any]]:
        if not self._seed_fixtures:
            # This endpoint owns listings, not a selected scoring/report/review
            # context. Keep the six-step API shape without exposing a global
            # first row or claiming that candidate existence requires scoring.
            return [
                {
                    "id": step_id,
                    "label": label,
                    "tabIndex": tab_index,
                    "state": "blocked",
                    "entityId": None,
                    "summary": (
                        "Select an exact candidate context; progress requires "
                        "current authorized listing, report and review reads."
                    ),
                }
                for step_id, label, tab_index in (
                    ("find", "Find Area", 0),
                    ("radar", "Listing Radar", 1),
                    ("candidate", "Candidate", 2),
                    ("sitescore", "SiteScore", 3),
                    ("compare", "Compare", 4),
                    ("review", "Review", 5),
                )
            ]
        has_selected_zone = any(zone["id"] == selected_id for zone in self._state["heatZones"])
        candidate = next(
            (item for item in self._state["candidates"] if item.get("id") == "CS-1001"),
            None,
        )
        listing = self._listing("L-2024")
        candidate_created = candidate is not None
        return [
            {
                "id": "find",
                "label": "Find Area",
                "tabIndex": 0,
                "state": "completed" if has_selected_zone else "current",
                "entityId": selected_id,
                "summary": f"{selected_id} selected and synchronized to the map.",
            },
            {
                "id": "radar",
                "label": "Listing Radar",
                "tabIndex": 1,
                "state": "completed" if candidate_created else "current",
                "entityId": "L-2024",
                "summary": f"{listing['id']} clean; duplicate and hard-rule rows visible.",
            },
            {
                "id": "candidate",
                "label": "Candidate",
                "tabIndex": 2,
                "state": "current" if candidate_created else "next",
                "entityId": "CS-1001" if candidate_created else "L-2024",
                "summary": "CS-1001 created once from L-2024." if candidate_created else "Convert L-2024 to create CS-1001.",
            },
            {
                "id": "sitescore",
                "label": "SiteScore",
                "tabIndex": 3,
                "state": "next" if candidate_created else "blocked",
                "entityId": "CS-1001",
                "summary": "Ready for GO 82 evidence review." if candidate_created else "Blocked until candidate exists.",
            },
            {
                "id": "compare",
                "label": "Compare",
                "tabIndex": 4,
                "state": "next" if candidate_created else "blocked",
                "entityId": "CS-1001",
                "summary": "Compare HZ-01 candidate against HZ-02 pipeline." if candidate_created else "Blocked by missing candidate.",
            },
            {
                "id": "review",
                "label": "Review",
                "tabIndex": 5,
                "state": "blocked" if not candidate_created else "next",
                "entityId": "RV-1001" if candidate_created else None,
                "summary": "Reasoned review is available after scoring gate." if candidate_created else "No candidate review packet yet.",
            },
        ]


__all__ = [
    "NetworkListingConflict",
    "NetworkListingNotFound",
    "NetworkListingPolicyError",
    "NetworkListingService",
]
