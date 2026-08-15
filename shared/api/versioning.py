"""Explicit API versioning with tested compatibility aliases.

Only 2 of 14 routers were mounted under ``/api/v1``; the other 12 answered on
bare paths like ``/priceops`` and ``/netplan``. The two files even disagreed
about whose job it was -- ``operator.py`` documents "the API gateway adds
/api/v1 externally" while ``main.py`` adds it in-process.

:func:`mount_versioned` makes ``/api/v1`` the one contract by mounting each
router twice:

* ``/api/v1/<path>`` -- the versioned contract. This is what the OpenAPI
  artifact and the generated client describe.
* ``/<path>`` -- a compatibility alias for callers written against the
  unversioned paths. It is excluded from the schema (so it cannot be mistaken
  for contract surface or leak into the generated client) and its responses
  carry ``Deprecation: true`` plus a ``Link`` header naming the successor.

Mounting rather than redirecting is deliberate: a 307 drops the request body on
some clients and would force every caller to opt into redirect-following on
mutations. The alias serves the request directly, so existing callers are
unaffected.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

__all__ = [
    "API_V1_PREFIX",
    "DEPRECATION_HEADER",
    "mount_versioned",
    "install_deprecation_headers",
    "alias_paths",
    "versioned_paths",
]

API_V1_PREFIX = "/api/v1"
DEPRECATION_HEADER = "Deprecation"

_ALIAS_TEMPLATES_KEY = "_odp_alias_templates"
_ALIAS_MATCHERS_KEY = "_odp_alias_matchers"
_EXACT_RESPONSES_KEY = "_odp_exact_openapi_responses"
_EXACT_RESPONSES_INSTALLED_KEY = "_odp_exact_openapi_responses_installed"


def _iter_router_paths(router: Any, prefix: str = "") -> Iterator[str]:
    """Yield every path template ``router`` contributes, mounted at ``prefix``.

    Walks nested routers explicitly. FastAPI 0.138 does not flatten an included
    router into its parent: it wraps it in a ``_IncludedRouter`` that reports no
    ``path`` of its own and keeps the real router in ``original_router``, with
    the mount prefix on ``include_context``. A naive ``router.routes`` scan
    therefore silently misses the whole operator sub-tree (~57 paths). The
    ``getattr`` fallbacks keep this working on a FastAPI that does flatten.
    """
    for route in getattr(router, "routes", []):
        path = getattr(route, "path", "")
        if path:
            yield f"{prefix}{path}"
            continue
        nested = getattr(route, "original_router", None)
        if nested is None:
            continue
        context = getattr(route, "include_context", None)
        nested_prefix = getattr(context, "prefix", "") or ""
        yield from _iter_router_paths(nested, f"{prefix}{nested_prefix}")


def _iter_operation_routes(router: Any) -> Iterator[Any]:
    for route in getattr(router, "routes", []):
        if getattr(route, "operation_id", None):
            yield route
            continue
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _iter_operation_routes(nested)


def _install_exact_response_filter(app: Any) -> None:
    if getattr(app.state, _EXACT_RESPONSES_INSTALLED_KEY, False):
        return

    base_openapi = app.openapi

    def exact_openapi() -> dict[str, Any]:
        schema = base_openapi()
        allowed_by_operation: dict[str, set[str]] = getattr(
            app.state, _EXACT_RESPONSES_KEY, {}
        )
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                allowed = allowed_by_operation.get(operation.get("operationId", ""))
                if allowed is None:
                    continue
                operation["responses"] = {
                    status: response
                    for status, response in operation.get("responses", {}).items()
                    if status in allowed
                }
        return schema

    app.openapi = exact_openapi
    setattr(app.state, _EXACT_RESPONSES_INSTALLED_KEY, True)


def mount_versioned(
    app: Any,
    router: Any,
    *,
    prefix: str = "",
    exact_responses: bool = False,
) -> None:
    """Mount ``router`` at ``/api/v1`` and again as a deprecated alias.

    ``prefix`` is any extra prefix the caller would have passed to
    ``include_router``.
    """
    from starlette.routing import compile_path

    from shared.api.errors import ERROR_RESPONSES

    exact_operation_routes = list(_iter_operation_routes(router)) if exact_responses else []
    app.include_router(
        router,
        prefix=f"{API_V1_PREFIX}{prefix}",
        responses={} if exact_responses else ERROR_RESPONSES,
    )
    if exact_responses:
        allowed_by_operation: dict[str, set[str]] = getattr(
            app.state, _EXACT_RESPONSES_KEY, {}
        )
        for route in exact_operation_routes:
            operation_id = route.operation_id
            allowed = {str(getattr(route, "status_code", None) or 200)}
            allowed.update(str(status) for status in getattr(route, "responses", {}))
            allowed_by_operation[operation_id] = allowed
        setattr(app.state, _EXACT_RESPONSES_KEY, allowed_by_operation)
        _install_exact_response_filter(app)
    # include_in_schema=False keeps the alias out of the OpenAPI artifact, so
    # the generated client only ever targets the versioned contract.
    app.include_router(router, prefix=prefix, include_in_schema=False)

    # Record the alias templates here, at mount time, from the router itself.
    # Deriving them from app.openapi() instead would be exact but costs ~1.5s to
    # build the schema -- paid either inside the first aliased request or on
    # every create_app(), and the test suite builds a fresh app per test.
    templates: set[str] = getattr(app.state, _ALIAS_TEMPLATES_KEY, set())
    matchers: list[re.Pattern[str]] = getattr(app.state, _ALIAS_MATCHERS_KEY, [])
    for template in _iter_router_paths(router, prefix):
        if template in templates:
            continue
        templates.add(template)
        # Match on the compiled path regex, not a string prefix: a prefix test
        # would wrongly flag any app-level route that happens to sit under a
        # mounted router's prefix but has no versioned counterpart.
        matchers.append(compile_path(template)[0])
    setattr(app.state, _ALIAS_TEMPLATES_KEY, templates)
    setattr(app.state, _ALIAS_MATCHERS_KEY, matchers)


def alias_paths(app: Any) -> list[str]:
    """Unversioned path templates served as compatibility aliases."""
    return sorted(getattr(app.state, _ALIAS_TEMPLATES_KEY, set()))


def versioned_paths(app: Any) -> list[str]:
    """Path templates served under ``/api/v1``, read from the OpenAPI schema.

    The schema is the contract, so deriving this from ``app.openapi()`` asserts
    the same surface a client would see rather than an internal route list.
    """
    schema = app.openapi()
    return sorted(p for p in schema.get("paths", {}) if p.startswith(API_V1_PREFIX))


def install_deprecation_headers(app: Any) -> None:
    """Stamp deprecation metadata on responses served via a legacy alias.

    RFC 8594 ``Deprecation`` plus an RFC 8288 ``Link`` rel="successor-version",
    so a caller can discover the versioned path from the response alone rather
    than from documentation it may never read.
    """
    from starlette.requests import Request

    @app.middleware("http")
    async def _mark_deprecated_alias(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        path = request.url.path
        if path.startswith(API_V1_PREFIX):
            return response
        matchers: list[re.Pattern[str]] = getattr(app.state, _ALIAS_MATCHERS_KEY, [])
        if any(matcher.fullmatch(path) for matcher in matchers):
            response.headers[DEPRECATION_HEADER] = "true"
            response.headers["Link"] = f'<{API_V1_PREFIX}{path}>; rel="successor-version"'
        return response
