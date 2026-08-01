"""Authoritative read-model composition for the Operator Console.

Every section is read from an injected repository.  A successful empty read is
different from a repository that is absent, unsafe to query across tenants, or
temporarily failing; the response preserves that distinction instead of
turning unavailable sections into plausible-looking zeroes.
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


@dataclass(frozen=True)
class OperatorSectionAvailability:
    """Availability and provenance for one Operator projection section."""

    state: str
    source: str
    record_count: int | None
    reason_code: str | None = None
    message: str | None = None

    @property
    def available(self) -> bool:
        return self.state in {"available", "degraded"}

    @property
    def complete(self) -> bool:
        return self.state == "available"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "state": self.state,
            "available": self.available,
            "complete": self.complete,
            "source": self.source,
            "recordCount": self.record_count,
        }
        if self.reason_code is not None:
            payload["reasonCode"] = self.reason_code
        if self.message is not None:
            payload["message"] = self.message
        return payload


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
            "kind": "authoritative",
            "sourceId": "operator-live-repository",
            "repository": type(self).__name__,
            "persistenceMode": self._mode,
            "completeness": "request-scoped",
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

    @staticmethod
    def _available(
        source: str,
        records: Any,
    ) -> OperatorSectionAvailability:
        count = records if isinstance(records, int) else len(records)
        return OperatorSectionAvailability(
            state="available",
            source=source,
            record_count=int(count),
        )

    @staticmethod
    def _unavailable(
        source: str,
        *,
        reason_code: str,
        message: str,
    ) -> OperatorSectionAvailability:
        return OperatorSectionAvailability(
            state="unavailable",
            source=source,
            record_count=None,
            reason_code=reason_code,
            message=message,
        )

    @staticmethod
    def _degraded(
        source: str,
        records: Any,
        *,
        reason_code: str,
        message: str,
    ) -> OperatorSectionAvailability:
        count = records if isinstance(records, int) else len(records)
        return OperatorSectionAvailability(
            state="degraded",
            source=source,
            record_count=int(count),
            reason_code=reason_code,
            message=message,
        )

    def _read_list(
        self,
        section: str,
        repository: Any,
        method_name: str,
        /,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[list[Any], OperatorSectionAvailability]:
        source = f"{type(repository).__name__}.{method_name}"
        try:
            records = list(
                self._call(
                    section,
                    repository,
                    method_name,
                    *args,
                    **kwargs,
                )
            )
        except OperatorLiveRepositoryError as exc:
            return [], self._unavailable(
                source,
                reason_code=f"OPERATOR_{section.upper()}_UNAVAILABLE",
                message=str(exc),
            )
        return records, self._available(source, records)

    def _tenant_scoped_repository(
        self,
        attribute: str,
        scope: OperatorReadScope,
    ) -> tuple[Any | None, str | None]:
        """Resolve a repository without ever enumerating an unscoped document set."""

        provider = getattr(
            self._persistence,
            f"{attribute}_for_tenant",
            None,
        )
        if callable(provider):
            try:
                return provider(scope.tenant_id), None
            except Exception as exc:
                return None, f"{type(exc).__name__}: {exc}"

        repository = getattr(self._persistence, attribute, None)
        if repository is None:
            return None, "tenant-aware repository is not configured"
        store = getattr(repository, "_store", None)
        if store is None:
            return repository, None
        try:
            from shared.infrastructure.persistence.operator_domains import (
                TenantScopedDocumentStore,
            )

            return type(repository)(
                TenantScopedDocumentStore(store, scope.tenant_id)
            ), None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def _read_sources(self, scope: OperatorReadScope) -> dict[str, Any]:
        sections: dict[str, OperatorSectionAvailability] = {}
        stores, sections["stores"] = self._read_list(
            "stores",
            self._persistence.store_repository,
            "list_stores",
            tenant_id=scope.tenant_id,
            brand_ids=scope.brand_ids,
            region_codes=scope.region_ids,
            store_ids=scope.store_ids,
        )
        visible_store_ids = tuple(
            sorted(str(_value(store, "store_id")) for store in stores)
        )
        if sections["stores"].available:
            transactions, sections["transactions"] = self._read_list(
                "transactions",
                self._persistence.transaction_repository,
                "list_transactions",
                tenant_id=scope.tenant_id,
                store_ids=visible_store_ids,
            )
        else:
            transactions = []
            sections["transactions"] = self._unavailable(
                "transaction_repository.list_transactions",
                reason_code="OPERATOR_STORES_DEPENDENCY_UNAVAILABLE",
                message="transactions require an available tenant-scoped store set",
            )

        interventions: list[Any] = []
        alerts: list[Any] = []
        intervention_errors: list[str] = []
        alert_errors: list[str] = []
        if sections["stores"].available:
            for store_id in visible_store_ids:
                try:
                    interventions.extend(
                        self._call(
                            "interventions",
                            self._persistence.intervention_repository,
                            "list_by_store",
                            store_id,
                        )
                    )
                except OperatorLiveRepositoryError as exc:
                    intervention_errors.append(str(exc))
                try:
                    alerts.extend(
                        self._call(
                            "forecast_alerts",
                            self._persistence.forecastops_repository,
                            "list_alerts_by_store",
                            scope.tenant_id,
                            store_id,
                        )
                    )
                except OperatorLiveRepositoryError as exc:
                    alert_errors.append(str(exc))
            sections["interventions"] = (
                self._degraded(
                    "intervention_repository.list_by_store",
                    interventions,
                    reason_code="OPERATOR_INTERVENTIONS_PARTIAL",
                    message="; ".join(intervention_errors),
                )
                if intervention_errors
                else self._available(
                    "intervention_repository.list_by_store",
                    interventions,
                )
            )
            sections["forecastAlerts"] = (
                self._degraded(
                    "forecastops_repository.list_alerts_by_store",
                    alerts,
                    reason_code="OPERATOR_FORECAST_ALERTS_PARTIAL",
                    message="; ".join(alert_errors),
                )
                if alert_errors
                else self._available(
                    "forecastops_repository.list_alerts_by_store",
                    alerts,
                )
            )
        else:
            sections["interventions"] = self._unavailable(
                "intervention_repository.list_by_store",
                reason_code="OPERATOR_STORES_DEPENDENCY_UNAVAILABLE",
                message="interventions require an available tenant-scoped store set",
            )
            sections["forecastAlerts"] = self._unavailable(
                "forecastops_repository.list_alerts_by_store",
                reason_code="OPERATOR_STORES_DEPENDENCY_UNAVAILABLE",
                message="forecast alerts require an available tenant-scoped store set",
            )

        listing_repository, listing_error = self._tenant_scoped_repository(
            "listing_repository",
            scope,
        )
        if listing_repository is None:
            listings = []
            candidates = []
            message = listing_error or "tenant-aware listing repository is unavailable"
            sections["listings"] = self._unavailable(
                "listing_repository.list_listings",
                reason_code="OPERATOR_TENANT_LISTINGS_UNAVAILABLE",
                message=message,
            )
            sections["candidates"] = self._unavailable(
                "listing_repository.list_candidates",
                reason_code="OPERATOR_TENANT_CANDIDATES_UNAVAILABLE",
                message=message,
            )
        else:
            listings, sections["listings"] = self._read_list(
                "listings",
                listing_repository,
                "list_listings",
            )
            candidates, sections["candidates"] = self._read_list(
                "candidates",
                listing_repository,
                "list_candidates",
            )

        decision_repository, decision_error = self._tenant_scoped_repository(
            "sitescore_decision_store",
            scope,
        )
        if decision_repository is None:
            decisions = []
            sections["siteScoreDecisions"] = self._unavailable(
                "sitescore_decision_store.list_decisions",
                reason_code="OPERATOR_TENANT_SITESCORE_DECISIONS_UNAVAILABLE",
                message=decision_error
                or "tenant-aware SiteScore decision repository is unavailable",
            )
        else:
            decisions, sections["siteScoreDecisions"] = self._read_list(
                "sitescore_decisions",
                decision_repository,
                "list_decisions",
            )

        ingestion_repository, ingestion_error = self._tenant_scoped_repository(
            "ingestion_run_store",
            scope,
        )
        if ingestion_repository is None:
            ingestion_runs = []
            sections["ingestionRuns"] = self._unavailable(
                "ingestion_run_store.list_runs",
                reason_code="OPERATOR_TENANT_INGESTION_RUNS_UNAVAILABLE",
                message=ingestion_error or "tenant-aware ingestion run store is unavailable",
            )
        else:
            ingestion_runs, sections["ingestionRuns"] = self._read_list(
                "ingestion_runs",
                ingestion_repository,
                "list_runs",
            )

        heatzone_repository, heatzone_error = self._tenant_scoped_repository(
            "heatzone_store",
            scope,
        )
        if heatzone_repository is None:
            heatzones = []
            sections["heatZones"] = self._unavailable(
                "heatzone_store.list_scores",
                reason_code="OPERATOR_TENANT_HEATZONES_UNAVAILABLE",
                message=heatzone_error or "tenant-aware heatzone store is unavailable",
            )
        else:
            heatzones, sections["heatZones"] = self._read_list(
                "heat_zones",
                heatzone_repository,
                "list_scores",
            )

        audit_events, sections["auditEvents"] = self._read_list(
            "audit_events",
            self._persistence.audit_log,
            "list_events",
            tenant_id=scope.tenant_id,
        )
        try:
            active_jobs = int(
                self._call(
                    "active_jobs",
                    self._persistence.job_queue,
                    "count_active_jobs",
                    tenant_id=scope.tenant_id,
                )
            )
        except OperatorLiveRepositoryError as exc:
            active_jobs = 0
            sections["activeJobs"] = self._unavailable(
                "job_queue.count_active_jobs",
                reason_code="OPERATOR_ACTIVE_JOBS_UNAVAILABLE",
                message=str(exc),
            )
        else:
            sections["activeJobs"] = self._available(
                "active_jobs",
                active_jobs,
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
            "sections": sections,
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
        sections: dict[str, OperatorSectionAvailability] = dict(
            sources["sections"]
        )
        risk_rows, sections["riskRows"] = self._project_risk_rows(
            stores,
            interventions,
            alerts,
            sections,
        )

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

        section_payload = {
            name: availability.to_dict()
            for name, availability in sections.items()
        }
        unavailable_sections = sorted(
            name
            for name, availability in sections.items()
            if availability.state == "unavailable"
        )
        degraded_sections = sorted(
            name
            for name, availability in sections.items()
            if availability.state == "degraded"
        )
        available_sections = sorted(
            name
            for name, availability in sections.items()
            if availability.available
        )
        data_mode = (
            "unavailable"
            if not available_sections
            else "degraded"
            if unavailable_sections or degraded_sections
            else "live"
        )
        record_counts = {
            name: availability.record_count
            for name, availability in sections.items()
        }
        queue_availability = (
            "degraded"
            if any(
                sections[name].state != "available"
                for name in (
                    "forecastAlerts",
                    "interventions",
                    "listings",
                    "candidates",
                    "ingestionRuns",
                )
            )
            else "available"
        )
        approval_availability = (
            "degraded"
            if any(
                sections[name].state != "available"
                for name in ("siteScoreDecisions", "interventions")
            )
            else "available"
        )
        notification_availability = (
            "degraded"
            if any(
                sections[name].state != "available"
                for name in ("forecastAlerts", "ingestionRuns")
            )
            else "available"
        )
        section_payload["workQueue"] = {
            "state": queue_availability,
            "available": True,
            "complete": queue_availability == "available",
            "source": "operator-work-queue-projection",
            "recordCount": len(queue),
            **(
                {
                    "reasonCode": "OPERATOR_WORK_QUEUE_PARTIAL",
                    "message": "one or more task sources are unavailable",
                }
                if queue_availability == "degraded"
                else {}
            ),
        }
        section_payload["approvals"] = {
            "state": approval_availability,
            "available": True,
            "complete": approval_availability == "available",
            "source": "operator-approval-projection",
            "recordCount": len(approvals),
            **(
                {
                    "reasonCode": "OPERATOR_APPROVALS_PARTIAL",
                    "message": "one or more approval sources are unavailable",
                }
                if approval_availability == "degraded"
                else {}
            ),
        }
        section_payload["notifications"] = {
            "state": notification_availability,
            "available": True,
            "complete": notification_availability == "available",
            "source": "operator-notification-projection",
            "recordCount": len(notifications),
            **(
                {
                    "reasonCode": "OPERATOR_NOTIFICATIONS_PARTIAL",
                    "message": "one or more notification sources are unavailable",
                }
                if notification_availability == "degraded"
                else {}
            ),
        }
        record_counts.update(
            {
                "workQueue": len(queue),
                "approvals": len(approvals),
                "notifications": len(notifications),
            }
        )
        return {
            "_meta": {
                "source": "operator-live-repository",
                "generatedAt": datetime.now(UTC).isoformat(),
                "recordCounts": record_counts,
                "scopeLabel": f"{len(stores)} stores",
                "tenantId": scope.tenant_id,
                "dataMode": data_mode,
                "complete": data_mode == "live",
                "sections": section_payload,
                "unavailableSections": unavailable_sections,
                "degradedSections": degraded_sections,
                "dataOrigin": {
                    **self.data_origin,
                    "kind": "authoritative" if data_mode == "live" else data_mode,
                    "complete": data_mode == "live",
                },
            },
            "kpis": [
                {
                    "label": "營運任務",
                    "value": str(len(queue)),
                    "delta": "",
                    "meta": "authoritative repositories",
                    "tone": "warning" if queue else "success",
                    "availability": queue_availability,
                },
                {
                    "label": "待核准",
                    "value": str(len(approvals)),
                    "delta": "",
                    "meta": "authoritative repositories",
                    "tone": "warning" if approvals else "success",
                    "availability": approval_availability,
                },
                {
                    "label": "有效門市",
                    "value": (
                        str(
                            sum(
                                1
                                for item in stores
                                if _status(_value(item, "store_status")).lower()
                                == "open"
                            )
                        )
                        if sections["stores"].available
                        else None
                    ),
                    "delta": "",
                    "meta": "store repository",
                    "tone": "neutral",
                    "availability": sections["stores"].state,
                },
                {
                    "label": "交易淨額",
                    "value": (
                        f"{transaction_net:.2f}"
                        if sections["transactions"].available
                        else None
                    ),
                    "delta": "",
                    "meta": "successful persisted transactions",
                    "tone": "neutral",
                    "availability": sections["transactions"].state,
                },
                {
                    "label": "有效物件",
                    "value": (
                        str(open_listings)
                        if sections["listings"].available
                        else None
                    ),
                    "delta": "",
                    "meta": "listing repository",
                    "tone": "neutral",
                    "availability": sections["listings"].state,
                },
                {
                    "label": "執行中工作",
                    "value": (
                        str(active_jobs)
                        if sections["activeJobs"].available
                        else None
                    ),
                    "delta": "",
                    "meta": "job repository",
                    "tone": "info" if active_jobs else "neutral",
                    "availability": sections["activeJobs"].state,
                },
            ],
            "workQueue": queue,
            "decisions": approvals,
            "riskRows": risk_rows,
            "auditFeed": audit_feed,
            "notifications": notifications,
        }

    def _project_risk_rows(
        self,
        stores: list[Any],
        interventions: list[Any],
        alerts: list[Any],
        sections: dict[str, OperatorSectionAvailability],
    ) -> tuple[list[dict[str, Any]], OperatorSectionAvailability]:
        if not sections["stores"].available:
            return [], self._unavailable(
                "operator-tenant-risk-projection",
                reason_code="OPERATOR_STORES_DEPENDENCY_UNAVAILABLE",
                message="risk projection requires an available tenant-scoped store set",
            )

        rows: list[dict[str, Any]] = []
        for store in stores:
            store_id = str(_value(store, "store_id", ""))
            store_name = str(_value(store, "store_name", store_id))
            store_status = _status(_value(store, "store_status")).lower()

            store_alerts = [
                a
                for a in alerts
                if str(_value(a, "store_id", "")) == store_id
                and _status(_value(a, "status")).lower() != "closed"
            ]
            store_interventions = [
                i
                for i in interventions
                if str(_value(i, "store_id", "")) == store_id
                and _status(_value(i, "status")).upper() not in _INTERVENTION_TERMINAL
            ]

            if store_alerts or store_interventions:
                critical = any(
                    _status(_value(a, "alert_level")).lower() in {"critical", "red"}
                    for a in store_alerts
                )
                score = 85 if critical else 70
                tone = "danger" if critical else "warning"
                signals: list[str] = []
                for a in store_alerts:
                    signals.append(str(_value(a, "alert_reason_code", "Forecast alert")))
                for i in store_interventions:
                    signals.append(f"{_status(_value(i, 'kind', ''))} intervention")
                signal_text = " + ".join(signals[:2]) or "Operational risk"
            else:
                score = 35 if store_status == "open" else 15
                tone = "success" if store_status == "open" else "neutral"
                signal_text = "Normal operation" if store_status == "open" else "Inactive store"

            rows.append(
                {
                    "label": store_name,
                    "score": score,
                    "signal": signal_text,
                    "tone": tone,
                    "storeId": store_id,
                }
            )

        degraded = (
            sections["interventions"].state == "degraded"
            or sections["forecastAlerts"].state == "degraded"
        )
        availability = (
            self._degraded(
                "operator-tenant-risk-projection",
                rows,
                reason_code="OPERATOR_RISK_ROWS_PARTIAL",
                message="risk projection compiled with partial alert or intervention signals",
            )
            if degraded
            else self._available("operator-tenant-risk-projection", rows)
        )
        return rows, availability

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
