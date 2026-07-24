"""Live read-model composition for the Operator Console.

The Operator Console is a projection over several authoritative domain
repositories.  This module performs that projection without caching or seeding:
every read asks the injected persistence bundle for its current records.  An
empty database is therefore a valid, ready, empty operator workspace.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol


class OperatorLiveRepositoryError(RuntimeError):
    """Raised when one of the authoritative repositories cannot be read."""


class OperatorTenantScopeRequiredError(OperatorLiveRepositoryError):
    """Raised when a live Operator read has no authorized tenant."""


@dataclass(frozen=True)
class OperatorReadScope:
    """Verified tenant and optional object scopes applied at repository reads."""

    tenant_id: str
    brand_ids: tuple[str, ...] = ()
    region_ids: tuple[str, ...] = ()
    store_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperatorRepositoryProbe:
    """Result of probing every repository used by the operator projection."""

    ready: bool
    checked_at: str
    repository: str
    persistence_mode: str
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "checkedAt": self.checked_at,
            "repository": self.repository,
            "persistenceMode": self.persistence_mode,
            "errors": list(self.errors),
        }


class OperatorLiveRepositoryProtocol(Protocol):
    """Injectable contract consumed by :class:`OperatorStateService`."""

    @property
    def data_origin(self) -> dict[str, Any]: ...

    def probe(self) -> OperatorRepositoryProbe: ...

    def load_state(
        self,
        *,
        tenant_id: str,
        brand_ids: tuple[str, ...] = (),
        region_ids: tuple[str, ...] = (),
        store_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]: ...


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _record_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if hasattr(value, "to_dict"):
        result = value.to_dict()
        return deepcopy(result) if isinstance(result, dict) else {}
    if is_dataclass(value):
        return asdict(value)
    return {
        key: deepcopy(item)
        for key, item in vars(value).items()
        if not key.startswith("_")
    }


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _status(value: Any) -> str:
    return str(_enum_value(value) or "").strip()


def _roles(*role_ids: str) -> list[str]:
    return list(role_ids)


_INTERVENTION_TERMINAL = {
    "INELIGIBLE",
    "REJECTED",
    "CLOSED",
    "STOPPED",
    "ROLLED_BACK",
}
_INTERVENTION_APPROVAL = {"PENDING_APPROVAL"}
_DECISION_PENDING = {"DRAFT", "SYSTEM_RECOMMENDED", "PENDING_REVIEW"}
_LISTING_REVIEW = {"manual_review", "stale"}
_INGESTION_PROBLEM = {"failed", "partial", "quarantined", "degraded"}


class OperatorLiveRepository:
    """Compose the operator read model from a persistence bundle.

    The bundle may be backed by PostgreSQL in production or a durable test
    adapter in integration tests.  The repository deliberately depends on the
    bundle's public repository methods rather than on SQL or storage details.
    """

    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence
        self._mode = str(getattr(persistence, "mode", "unknown")).strip().lower()

    @property
    def data_origin(self) -> dict[str, Any]:
        return {
            "kind": "live",
            "sourceId": "operator-live-repository",
            "repository": type(self).__name__,
            "persistenceMode": self._mode,
        }

    @staticmethod
    def _require_scope(
        *,
        tenant_id: str,
        brand_ids: tuple[str, ...],
        region_ids: tuple[str, ...],
        store_ids: tuple[str, ...],
    ) -> OperatorReadScope:
        normalized_tenant = str(tenant_id or "").strip()
        if not normalized_tenant:
            raise OperatorTenantScopeRequiredError(
                "authorized tenant_id is required for Operator live reads"
            )
        return OperatorReadScope(
            tenant_id=normalized_tenant,
            brand_ids=tuple(sorted({value for value in brand_ids if value})),
            region_ids=tuple(sorted({value for value in region_ids if value})),
            store_ids=tuple(sorted({value for value in store_ids if value})),
        )

    @staticmethod
    def _call(
        name: str,
        repository: Any,
        method_name: str,
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        method = getattr(repository, method_name, None)
        if not callable(method):
            raise OperatorLiveRepositoryError(
                f"{name}: missing tenant-scoped {method_name}()"
            )
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            raise OperatorLiveRepositoryError(
                f"{name}: {type(exc).__name__}: {exc}"
            ) from exc

    def _read_sources(self, scope: OperatorReadScope) -> dict[str, Any]:
        stores = list(
            self._call(
                "stores",
                self._persistence.store_repository,
                "list_stores",
                tenant_id=scope.tenant_id,
                brand_ids=scope.brand_ids,
                region_codes=scope.region_ids,
                store_ids=scope.store_ids,
            )
        )
        visible_store_ids = tuple(
            sorted(str(_value(store, "store_id")) for store in stores)
        )
        transactions = list(
            self._call(
                "transactions",
                self._persistence.transaction_repository,
                "list_transactions",
                tenant_id=scope.tenant_id,
                store_ids=visible_store_ids,
            )
        )

        interventions: list[Any] = []
        alerts: list[Any] = []
        for store_id in visible_store_ids:
            interventions.extend(
                self._call(
                    "interventions",
                    self._persistence.intervention_repository,
                    "list_by_store",
                    store_id,
                )
            )
            alerts.extend(
                self._call(
                    "forecast_alerts",
                    self._persistence.forecastops_repository,
                    "list_alerts_by_store",
                    store_id,
                )
            )

        # These legacy projections do not yet carry a tenant key. They stay
        # invisible in the live Operator read model instead of being read
        # cross-tenant. Their owning, tenant-aware intake APIs remain available.
        listings: list[Any] = []
        candidates: list[Any] = []
        decisions: list[Any] = []
        ingestion_runs: list[Any] = []
        heatzones: list[Any] = []

        audit_events = list(
            self._call(
                "audit_events",
                self._persistence.audit_log,
                "list_events",
                tenant_id=scope.tenant_id,
            )
        )
        active_jobs = int(
            self._call(
                "active_jobs",
                self._persistence.job_queue,
                "count_active_jobs",
                tenant_id=scope.tenant_id,
            )
        )
        return {
            "stores": stores,
            "transactions": transactions,
            "interventions": interventions,
            "forecast_alerts": alerts,
            "listings": listings,
            "candidates": candidates,
            "sitescore_decisions": decisions,
            "ingestion_runs": ingestion_runs,
            "heatzones": heatzones,
            "audit_events": audit_events,
            "active_jobs": active_jobs,
        }

    def probe(self) -> OperatorRepositoryProbe:
        errors: tuple[str, ...] = ()
        try:
            engine = getattr(self._persistence, "engine", None)
            if engine is not None and callable(getattr(engine, "query_one", None)):
                engine.query_one("SELECT 1 AS ready")
            else:
                self._call(
                    "stores",
                    self._persistence.store_repository,
                    "list_stores",
                    tenant_id="__operator_probe__",
                )
        except Exception as exc:
            errors = (f"{type(exc).__name__}: {exc}",)
        return OperatorRepositoryProbe(
            ready=not errors,
            checked_at=datetime.now(UTC).isoformat(),
            repository=type(self).__name__,
            persistence_mode=self._mode,
            errors=errors,
        )

    def load_state(
        self,
        *,
        tenant_id: str,
        brand_ids: tuple[str, ...] = (),
        region_ids: tuple[str, ...] = (),
        store_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        scope = self._require_scope(
            tenant_id=tenant_id,
            brand_ids=brand_ids,
            region_ids=region_ids,
            store_ids=store_ids,
        )
        sources = self._read_sources(scope)
        stores = list(sources["stores"])
        transactions = list(sources["transactions"])
        interventions = list(sources["interventions"])
        alerts = list(sources["forecast_alerts"])
        listings = list(sources["listings"])
        candidates = list(sources["candidates"])
        decisions = list(sources["sitescore_decisions"])
        ingestion_runs = list(sources["ingestion_runs"])
        audit_events = list(sources["audit_events"])
        active_jobs = int(sources["active_jobs"])

        queue = [
            *self._alert_tasks(alerts),
            *self._intervention_tasks(interventions),
            *self._listing_tasks(listings),
            *self._candidate_tasks(candidates),
            *self._ingestion_tasks(ingestion_runs),
        ]
        approvals = [
            *self._sitescore_approvals(decisions),
            *self._intervention_approvals(interventions),
        ]
        notifications = [
            *self._alert_notifications(alerts),
            *self._ingestion_notifications(ingestion_runs),
        ]
        audit_feed = self._audit_feed(audit_events)
        successful_transactions = [
            item
            for item in transactions
            if _status(_value(item, "transaction_status")).lower() == "succeeded"
        ]
        transaction_net = sum(
            float(_value(item, "net_amount", 0.0) or 0.0)
            for item in successful_transactions
        )
        open_listings = sum(
            1
            for item in listings
            if _status(_value(item, "listing_status")).lower() == "active"
        )

        record_counts = {
            "stores": len(stores),
            "transactions": len(transactions),
            "interventions": len(interventions),
            "forecastAlerts": len(alerts),
            "listings": len(listings),
            "candidates": len(candidates),
            "siteScoreDecisions": len(decisions),
            "ingestionRuns": len(ingestion_runs),
            "heatZones": len(sources["heatzones"]),
            "auditEvents": len(audit_events),
            "activeJobs": active_jobs,
        }
        return {
            "_meta": {
                "source": "operator-live-repository",
                "generatedAt": datetime.now(UTC).isoformat(),
                "recordCounts": record_counts,
                "scopeLabel": f"{len(stores)} stores",
                "tenantId": scope.tenant_id,
            },
            "kpis": [
                {
                    "label": "營運任務",
                    "value": str(len(queue)),
                    "delta": "",
                    "meta": "live repositories",
                    "tone": "warning" if queue else "success",
                },
                {
                    "label": "待核准",
                    "value": str(len(approvals)),
                    "delta": "",
                    "meta": "live repositories",
                    "tone": "warning" if approvals else "success",
                },
                {
                    "label": "有效門市",
                    "value": str(
                        sum(
                            1
                            for item in stores
                            if _status(_value(item, "store_status")).lower()
                            == "open"
                        )
                    ),
                    "delta": "",
                    "meta": "store repository",
                    "tone": "neutral",
                },
                {
                    "label": "交易淨額",
                    "value": f"{transaction_net:.2f}",
                    "delta": "",
                    "meta": "successful persisted transactions",
                    "tone": "neutral",
                },
                {
                    "label": "有效物件",
                    "value": str(open_listings),
                    "delta": "",
                    "meta": "listing repository",
                    "tone": "neutral",
                },
                {
                    "label": "執行中工作",
                    "value": str(active_jobs),
                    "delta": "",
                    "meta": "job repository",
                    "tone": "info" if active_jobs else "neutral",
                },
            ],
            "workQueue": queue,
            "decisions": approvals,
            "riskRows": [],
            "auditFeed": audit_feed,
            "notifications": notifications,
        }

    def _alert_tasks(self, alerts: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for alert in alerts:
            status = _status(_value(alert, "status")).lower()
            if status == "closed":
                continue
            level = _status(_value(alert, "alert_level")).lower()
            alert_id = str(_value(alert, "alert_id"))
            store_id = str(_value(alert, "store_id"))
            rows.append(
                {
                    "id": alert_id,
                    "title": str(_value(alert, "alert_reason_code")),
                    "description": f"Forecast alert for store {store_id}",
                    "meta": store_id,
                    "owner": "ForecastOps",
                    "status": status,
                    "time": _iso(_value(alert, "opened_at")),
                    "tone": "danger" if level in {"critical", "red"} else "warning",
                    "workspace": "store",
                    "roles": _roles("ops-lead", "field-lead", "pm-audit"),
                    "tags": ["ForecastOps", level],
                    "target": {
                        "workspace": "store",
                        "entityId": alert_id,
                        "tab": "triage",
                    },
                }
            )
        return rows

    def _intervention_tasks(self, interventions: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for intervention in interventions:
            status = _status(_value(intervention, "status")).upper()
            if status in _INTERVENTION_TERMINAL:
                continue
            intervention_id = str(_value(intervention, "intervention_id"))
            kind = _status(_value(intervention, "kind"))
            store_id = str(_value(intervention, "store_id"))
            workspace = "growth" if kind in {
                "PRICE_CHANGE",
                "AD_CAMPAIGN",
                "PROMOTION",
                "CRM_RECALL",
                "OPENING_CAMPAIGN",
            } else "store"
            rows.append(
                {
                    "id": intervention_id,
                    "title": f"{kind} intervention",
                    "description": str(_value(intervention, "expected_outcome", "")),
                    "meta": (
                        f"{store_id} · "
                        f"{_value(intervention, 'trigger_ref', '')}"
                    ).strip(" ·"),
                    "owner": str(_value(intervention, "created_by", "")),
                    "status": status,
                    "time": _iso(_value(intervention, "created_at")),
                    "tone": "warning" if status == "PENDING_APPROVAL" else "info",
                    "workspace": workspace,
                    "roles": _roles(
                        "ops-lead",
                        "marketing-manager",
                        "field-lead",
                        "pm-audit",
                    ),
                    "tags": ["Intervention", kind],
                    "target": {
                        "workspace": workspace,
                        "entityId": intervention_id,
                        "tab": "overview",
                    },
                }
            )
        return rows

    def _listing_tasks(self, listings: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for listing in listings:
            status = _status(_value(listing, "listing_status")).lower()
            if status not in _LISTING_REVIEW:
                continue
            listing_id = str(_value(listing, "listing_id"))
            rows.append(
                {
                    "id": listing_id,
                    "title": "Listing requires review",
                    "description": str(_value(listing, "source_listing_id", "")),
                    "meta": str(_value(listing, "source_id", "")),
                    "owner": "Expansion",
                    "status": status,
                    "time": "",
                    "tone": "warning",
                    "workspace": "network",
                    "roles": _roles(
                        "ops-lead",
                        "expansion-manager",
                        "expansion-staff",
                        "pm-audit",
                    ),
                    "tags": ["Listing", status],
                    "target": {
                        "workspace": "network",
                        "entityId": listing_id,
                        "tab": "review",
                    },
                }
            )
        return rows

    def _candidate_tasks(self, candidates: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_site = _value(candidate, "candidate_site")
            candidate_id = str(_value(candidate_site, "candidate_site_id"))
            status = _status(_value(candidate_site, "site_status")).lower()
            if status not in {"new", "screened", "scored", "visited"}:
                continue
            rows.append(
                {
                    "id": candidate_id,
                    "title": "Candidate site review",
                    "description": str(_value(candidate, "recommendation", "")),
                    "meta": str(_value(candidate, "heat_zone_id", "")),
                    "owner": str(_value(candidate_site, "created_by", "")),
                    "status": status,
                    "time": _iso(_value(candidate_site, "created_at")),
                    "tone": "info",
                    "workspace": "network",
                    "roles": _roles(
                        "ops-lead",
                        "expansion-manager",
                        "expansion-staff",
                        "pm-audit",
                    ),
                    "tags": ["CandidateSite", status],
                    "target": {
                        "workspace": "network",
                        "entityId": candidate_id,
                        "tab": "review",
                    },
                }
            )
        return rows

    def _ingestion_tasks(self, runs: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for run in runs:
            status = _status(_value(run, "status")).lower()
            quarantined = int(_value(run, "quarantined_count", 0) or 0)
            if status not in _INGESTION_PROBLEM and quarantined == 0:
                continue
            run_id = str(_value(run, "run_id"))
            rows.append(
                {
                    "id": run_id,
                    "title": "External ingestion requires review",
                    "description": str(_value(run, "message", "")),
                    "meta": str(_value(run, "provider_id", "")),
                    "owner": "Data Operations",
                    "status": status,
                    "time": _iso(_value(run, "completed_at")),
                    "tone": "danger" if status == "failed" else "warning",
                    "workspace": "govern",
                    "roles": _roles("ops-lead", "pm-audit"),
                    "tags": ["ExternalData", status],
                    "target": {
                        "workspace": "govern",
                        "entityId": run_id,
                        "tab": "data-quality",
                    },
                }
            )
        return rows

    def _sitescore_approvals(self, decisions: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for decision in decisions:
            status = _status(_value(decision, "status")).upper()
            if status not in _DECISION_PENDING:
                continue
            decision_id = str(_value(decision, "decision_id"))
            candidate_id = str(_value(decision, "candidate_site_id"))
            rows.append(
                {
                    "id": decision_id,
                    "title": "SiteScore decision",
                    "meta": candidate_id,
                    "status": status,
                    "cta": "Open Govern",
                    "tone": "warning",
                    "roles": _roles(
                        "ops-lead",
                        "expansion-manager",
                        "pm-audit",
                    ),
                    "target": {
                        "workspace": "govern",
                        "entityId": decision_id,
                        "tab": "approvals",
                    },
                }
            )
        return rows

    def _intervention_approvals(self, interventions: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for intervention in interventions:
            status = _status(_value(intervention, "status")).upper()
            if status not in _INTERVENTION_APPROVAL:
                continue
            intervention_id = str(_value(intervention, "intervention_id"))
            rows.append(
                {
                    "id": intervention_id,
                    "title": "Intervention approval",
                    "meta": _status(_value(intervention, "kind")),
                    "status": status,
                    "cta": "Open Govern",
                    "tone": "warning",
                    "roles": _roles(
                        "ops-lead",
                        "marketing-manager",
                        "pm-audit",
                    ),
                    "target": {
                        "workspace": "govern",
                        "entityId": intervention_id,
                        "tab": "approvals",
                    },
                }
            )
        return rows

    def _alert_notifications(self, alerts: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for alert in alerts:
            status = _status(_value(alert, "status")).lower()
            if status == "closed":
                continue
            level = _status(_value(alert, "alert_level")).lower()
            alert_id = str(_value(alert, "alert_id"))
            rows.append(
                {
                    "id": f"notification-{alert_id}",
                    "title": str(_value(alert, "alert_reason_code")),
                    "detail": f"Store {_value(alert, 'store_id')}",
                    "tone": "danger" if level in {"critical", "red"} else "warning",
                    "roles": _roles("ops-lead", "field-lead", "pm-audit"),
                    "target": {
                        "workspace": "store",
                        "entityId": alert_id,
                        "tab": "triage",
                    },
                }
            )
        return rows

    def _ingestion_notifications(self, runs: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for run in runs:
            status = _status(_value(run, "status")).lower()
            quarantined = int(_value(run, "quarantined_count", 0) or 0)
            if status not in _INGESTION_PROBLEM and quarantined == 0:
                continue
            run_id = str(_value(run, "run_id"))
            rows.append(
                {
                    "id": f"notification-{run_id}",
                    "title": "External data ingestion",
                    "detail": str(_value(run, "message", status)),
                    "tone": "danger" if status == "failed" else "warning",
                    "roles": _roles("ops-lead", "pm-audit"),
                    "target": {
                        "workspace": "govern",
                        "entityId": run_id,
                        "tab": "data-quality",
                    },
                }
            )
        return rows

    def _audit_feed(self, events: list[Any]) -> list[dict[str, Any]]:
        ordered = sorted(
            events,
            key=lambda item: _iso(_value(item, "occurred_at")),
            reverse=True,
        )
        rows: list[dict[str, Any]] = []
        for event in ordered[:20]:
            payload = _record_dict(event)
            rows.append(
                {
                    "actor": str(payload.get("actor", "")),
                    "category": str(payload.get("event_type", "Audit trail")),
                    "detail": (
                        f"{payload.get('action', '')} "
                        f"{payload.get('resource', '')}: "
                        f"{payload.get('outcome', payload.get('result', ''))}"
                    ).strip(),
                    "time": str(payload.get("occurred_at", "")),
                    "auditEventId": str(payload.get("event_id", "")),
                    "correlationId": str(payload.get("correlation_id", "")),
                    "roles": _roles("ops-lead", "pm-audit"),
                }
            )
        return rows


__all__ = [
    "OperatorLiveRepository",
    "OperatorLiveRepositoryError",
    "OperatorLiveRepositoryProtocol",
    "OperatorRepositoryProbe",
]
