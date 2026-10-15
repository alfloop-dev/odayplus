"""Platform-global flags with fresh reads and same-transaction command audit.

The pure FeatureFlagRegistry remains the policy implementation. This adapter
never accepts caller-supplied approval names and never publishes a RAM mutation
before its actual SQL transaction commits. It is not a human release approval.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date
from typing import Any

from shared.audit import AuditEvent
from shared.auth.feature_flags import DEFAULT_FLAGS, FeatureFlag, FeatureFlagRegistry, Readiness
from shared.infrastructure.persistence.document_store import SqliteDocumentStore

COLLECTION = "auth.platform_feature_flags"
DOCUMENT = "global"


class FeatureFlagStoreUnavailable(RuntimeError):
    pass


class UnavailableFeatureFlagRegistry:
    """An absent formal owner is unavailable, never an empty/disabled policy."""

    def _unavailable(self, *_args, **_kwargs):
        raise FeatureFlagStoreUnavailable("Platform feature flag owner is unavailable")

    get = all = is_enabled = expired = _unavailable


def require_durable_flag_owner(owner: Any, engine: Any, *, queue: Any):
    """One actual global SQL owner; a RAM/default policy is not a substitute."""
    if (engine is None or not isinstance(owner, DurableFeatureFlagRegistry)
            or owner.engine is not engine
            or getattr(queue, "_engine", None) is not engine
            or getattr(queue, "_flags", None) is not owner):
        raise FeatureFlagStoreUnavailable("Durable execution requires its same-engine platform flag owner")
    return owner


class FeatureFlagWriteConflict(RuntimeError):
    pass


class FeatureFlagAlreadyExists(ValueError):
    pass


@dataclass(frozen=True)
class FlagMutationResult:
    flag: FeatureFlag
    audit_event_id: str
    audit_delivery: str


class DurableFeatureFlagRegistry:
    """One environment-wide owner; actor tenants do not partition flag policy."""

    def __init__(self, store: SqliteDocumentStore) -> None:
        self.store = store
        self.engine = store.engine

    def initialize_defaults(self) -> None:
        """Create only the original disabled defaults, never replace a record."""
        initial = self._encode(dict(DEFAULT_FLAGS))
        self.store.compare_and_put(COLLECTION, DOCUMENT, initial, expected_version=None)
        self.read_versioned()  # A pre-existing malformed record is not repaired.

    def read_versioned(self) -> tuple[dict[str, FeatureFlag], int]:
        try:
            row = self.store.get_versioned(COLLECTION, DOCUMENT)
            if row is None:
                raise FeatureFlagStoreUnavailable("Platform feature flag owner is not initialized")
            value, version = row
            return self._decode(value), version
        except FeatureFlagStoreUnavailable:
            raise
        except Exception as exc:
            raise FeatureFlagStoreUnavailable("Platform feature flag storage is unavailable") from exc

    def get(self, key: str) -> FeatureFlag | None:
        return self.read_versioned()[0].get(key)

    def all(self) -> tuple[FeatureFlag, ...]:
        return tuple(self.read_versioned()[0].values())

    def is_enabled(self, key: str, *, on: date | None = None) -> bool:
        flag = self.get(key)
        return flag is not None and flag.is_active(on or date.today())

    def expired(self, *, on: date) -> tuple[FeatureFlag, ...]:
        return tuple(flag for flag in self.all() if flag.is_expired(on))

    def execute(
        self, operation: str, key: str, *, actor: str, correlation_id: str,
        audit_log: Any, queue: Any = None, flag: FeatureFlag | None = None,
        reason: str | None = None, risk_acknowledged: bool | None = None,
    ) -> FlagMutationResult:
        """CAS a private policy copy and its true audit, or commit neither.

        The HTTP boundary requires a reason and explicit acknowledgment. These
        remain optional for existing internal policy/bootstrap callers; absent
        proof is recorded as absent, never inferred as human confirmation.
        """
        active = (
            getattr(getattr(self.engine, "_local", None), "connection", None) is not None
            or getattr(self.engine, "_tx_depth", 0) > 0
            or bool(getattr(getattr(self.engine, "_conn", None), "in_transaction", False))
        )
        if active:
            raise FeatureFlagStoreUnavailable("Flag commands must own their outer transaction")
        if not actor or not actor.strip() or not correlation_id:
            raise ValueError("A verified command actor and correlation ID are required")
        if getattr(audit_log, "_engine", None) is not self.engine:
            raise FeatureFlagStoreUnavailable("Flag command audit must share its SQL engine")
        sink = getattr(audit_log, "_worm_sink", None)
        delivery = None
        if sink is not None:
            from shared.audit.platform_flag_delivery import PlatformFlagAuditDelivery
            delivery = PlatformFlagAuditDelivery(self.engine, queue, sink)
        for _ in range(8):
            try:
                with self.engine.transaction():
                    current, version = self.read_versioned()
                    before = current.get(key)
                    policy = FeatureFlagRegistry(current)
                    if operation == "register":
                        if before is not None:
                            raise FeatureFlagAlreadyExists("feature flag already exists")
                        if flag is None or flag.key != key or flag.enabled or flag.approved_by:
                            raise ValueError("New flags must be unapproved and disabled")
                        policy.register(flag)
                        updated = flag
                    elif operation == "approve":
                        updated = policy.add_approval(key, actor)
                    elif operation == "enable":
                        # Only the latest stored approvals enter the original rule.
                        updated = policy.enable(key)
                    elif operation == "disable":
                        updated = policy.disable(key)
                    else:
                        raise ValueError("Unknown feature flag command")
                    after = {item.key: item for item in policy.all()}
                    changed = self.store.compare_and_put(
                        COLLECTION, DOCUMENT, self._encode(after), expected_version=version,
                    )
                    if changed is None:
                        raise FeatureFlagWriteConflict("Platform feature flag version changed")
                    event = AuditEvent(
                        event_type="feature_flag." + operation, actor=actor,
                        action=operation, resource="feature_flag:" + key, outcome="allow",
                        correlation_id=correlation_id,
                        metadata={
                            "scope": "platform-feature-flags", "key": key,
                            "before": before.to_dict() if before else None,
                            "after": updated.to_dict(), "reason": reason,
                            "riskAcknowledged": risk_acknowledged,
                            "flag_state_version": changed,
                        },
                    )
                    context = audit_log.transactional_worm_sink(delivery) if delivery else nullcontext()
                    with context:
                        audit_log.record(event)
                    result = FlagMutationResult(
                        updated, event.event_id, "pending" if delivery else "not_configured",
                    )
                # No mutable registry cache is published before the outer commit.
                return result
            except FeatureFlagWriteConflict:
                continue
        raise FeatureFlagWriteConflict("Platform feature flag update exhausted CAS retries")

    @staticmethod
    def _encode(flags: dict[str, FeatureFlag]) -> dict[str, Any]:
        return {"schema_version": 1, "flags": {
            key: {
                "key": flag.key, "owner": flag.owner, "enabled": flag.enabled,
                "readiness": flag.readiness.value, "high_risk": flag.high_risk,
                "expires_on": flag.expires_on.isoformat() if flag.expires_on else None,
                "description": flag.description, "approved_by": sorted(flag.approved_by),
            }
            for key, flag in flags.items()
        }}

    @staticmethod
    def _decode(value: Any) -> dict[str, FeatureFlag]:
        try:
            if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
                    or value["schema_version"] != 1):
                raise ValueError("Invalid platform flag schema")
            raw_flags = value.get("flags")
            if not isinstance(raw_flags, dict) or not set(DEFAULT_FLAGS).issubset(raw_flags):
                raise ValueError("Invalid platform flag collection")
            result = {}
            for key, row in raw_flags.items():
                if not isinstance(key, str) or not key or not isinstance(row, dict):
                    raise ValueError("Invalid platform flag row")
                if row.get("key") != key or not isinstance(row.get("owner"), str) or not row["owner"].strip():
                    raise ValueError("Invalid platform flag identity")
                if type(row.get("enabled")) is not bool or type(row.get("high_risk")) is not bool:
                    raise ValueError("Invalid platform flag boolean")
                if key in DEFAULT_FLAGS and not row["high_risk"]:
                    raise ValueError("Original high-risk flag policy cannot be downgraded")
                approvals = row.get("approved_by")
                if not isinstance(approvals, list) or any(
                    not isinstance(actor, str) or not actor.strip() for actor in approvals
                ) or len(approvals) != len(set(approvals)):
                    raise ValueError("Invalid recorded platform flag approvals")
                if not isinstance(row.get("description"), str):
                    raise ValueError("Invalid platform flag description")
                readiness = Readiness(row["readiness"])
                expiry = row.get("expires_on")
                if expiry is not None and not isinstance(expiry, str):
                    raise ValueError("Invalid platform flag expiry")
                result[key] = FeatureFlag(
                    key=key, owner=row["owner"], enabled=row["enabled"],
                    readiness=readiness, high_risk=row["high_risk"],
                    expires_on=date.fromisoformat(expiry) if expiry is not None else None,
                    description=row["description"], approved_by=frozenset(approvals),
                )
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise FeatureFlagStoreUnavailable("Persisted platform feature flags are invalid") from exc
