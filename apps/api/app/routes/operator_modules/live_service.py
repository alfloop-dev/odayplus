"""Request-scoped durable service binding for live Operator domain routes."""

from __future__ import annotations

from collections.abc import Callable, Collection
from threading import RLock
from typing import Any

from fastapi import HTTPException, Request, status

from shared.infrastructure.persistence.operator_domains import (
    DurableOperatorDomainStateRepository,
)

ServiceFactory = Callable[[dict[str, Any] | None, str], Any]
ServiceExporter = Callable[[Any], dict[str, Any]]
AfterSave = Callable[[Any, str], None]


class DurableTenantServiceResolver:
    """Resolve a fresh tenant aggregate for every service method invocation.

    Mutations are serialized per tenant in-process.  A successful mutation
    writes the complete aggregate, including its idempotency records, before
    returning the response.
    """

    def __init__(
        self,
        repository: DurableOperatorDomainStateRepository,
        *,
        factory: ServiceFactory,
        exporter: ServiceExporter,
        mutating_methods: Collection[str],
        after_save: AfterSave | None = None,
    ) -> None:
        self._repository = repository
        self._factory = factory
        self._exporter = exporter
        self._mutating_methods = frozenset(mutating_methods)
        self._after_save = after_save
        self._locks: dict[str, RLock] = {}
        self._locks_guard = RLock()

    def __call__(self, request: Request) -> Any:
        tenant_id = str(
            getattr(request.state, "operator_tenant_id", "") or ""
        ).strip()
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operator Console tenant scope is required",
            )
        return _TenantServiceProxy(self, tenant_id)

    def _lock_for(self, tenant_id: str) -> RLock:
        with self._locks_guard:
            return self._locks.setdefault(tenant_id, RLock())

    def _invoke(
        self,
        partition_tenant_id: str,
        method_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        with self._lock_for(partition_tenant_id):
            service = self._factory(
                self._repository.load(partition_tenant_id),
                partition_tenant_id,
            )
            result = getattr(service, method_name)(*args, **kwargs)
            if method_name in self._mutating_methods:
                self._repository.save(
                    partition_tenant_id,
                    self._exporter(service),
                )
                if self._after_save is not None:
                    self._after_save(service, partition_tenant_id)
            return result


class _TenantServiceProxy:
    def __init__(
        self,
        resolver: DurableTenantServiceResolver,
        tenant_id: str,
    ) -> None:
        self._resolver = resolver
        self._tenant_id = tenant_id

    def __getattr__(self, method_name: str) -> Callable[..., Any]:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            return self._resolver._invoke(
                self._tenant_id,
                method_name,
                *args,
                **kwargs,
            )

        return invoke


def resolve_service(
    request: Request,
    service: Any,
    resolver: Callable[[Request], Any] | None,
) -> Any:
    return resolver(request) if resolver is not None else service


__all__ = [
    "DurableTenantServiceResolver",
    "resolve_service",
]
