"""Publish FastAPI's lazy per-branch route table atomically.

FastAPI 0.138 does not flatten an included router into its parent. Each
``include_router`` call appends a ``_IncludedRouter`` branch that keeps the real
router in ``original_router`` and resolves its own routes lazily, the first time
a request walks that branch::

    def effective_candidates(self):
        routes_version = self.original_router._get_routes_version()
        if routes_version == self._effective_candidates_version:
            return self._effective_candidates
        self._effective_candidates = []              # (1) visible to everyone
        for route in self.original_router.routes:    # (2) filled incrementally
            ...
            self._effective_candidates.append(route_context)
        self._effective_candidates_version = routes_version   # (3) stamped
        return self._effective_candidates            # (4) whatever is there now

The accumulator is the shared instance attribute, so the half-built table is
reachable by every thread between (1) and (3). Two requests arriving on a cold
app race like this:

* A passes the version check, rebinds the attribute to ``L1`` and appends the
  first few contexts -- including ``POST /jobs`` -- into ``L1``.
* B passes the same version check (A has not stamped yet), rebinds the
  attribute to a fresh ``L2`` and starts filling it.
* A's remaining ``self._effective_candidates.append(...)`` calls now land in
  ``L2``, because the name is re-resolved on every iteration. A then stamps the
  version and returns ``L2`` -- which is missing everything A had already put in
  ``L1``.

A therefore matches against a route table with ``/jobs`` deleted and the request
404s. Worse, the truncated table was stamped with the current routes version, so
it is *cached*: the branch keeps answering 404 until the route set changes.

This is exactly the intermittent ``POST /jobs`` 404 that blocked PR #1229's
performance gate: its load test builds a fresh app and immediately fires ten
concurrent requests, which is the cold-start window described above.

:func:`ensure_atomic_route_table_publication` fixes the publication, not the
timing. The rebuild accumulates into a thread-local list and assigns it to the
instance in one statement, so a reader either sees the previous table or a
complete new one -- never a partially filled one. Concurrent first callers may
each build a table; every one of them is complete, and the last assignment wins.

Deliberately *not* done here:

* No lock around the lookup. Serialising route resolution would put every
  request through one critical section for a defect that only exists while the
  table is being published.
* No eager warm-up of all branches at ``create_app`` time. Materialising all 68
  branches of this app costs ~3.4 s measured on this checkout, against ~166 ms
  for the handful of branches a request actually walks; the API test suite
  builds an app per test and would pay that on every one of them.
* No retry, no warm-up request, and no change to any route, dependency, or
  response shape. The rebuilt table is byte-for-byte the one FastAPI would have
  built single-threaded.

Compatibility and fail-closed behaviour
---------------------------------------

This reaches into FastAPI internals (``_IncludedRouter`` and its memo fields),
so it is guarded rather than assumed:

* If ``_IncludedRouter`` is absent, the framework no longer resolves included
  routers through a lazily built per-branch table and there is nothing to
  republish. The installer returns quietly.
* If ``_IncludedRouter`` is present but any attribute this module rewrites is
  missing, the internals moved under us and this module can no longer promise
  atomic publication. It raises :class:`RouteTableSafetyError` from
  ``create_app``, so the service refuses to start instead of silently serving
  traffic through a route table that can lose routes.

Pinned version at the time of writing: fastapi 0.138.1 (``uv.lock``).
"""

from __future__ import annotations

import threading
from typing import Any

__all__ = ["RouteTableSafetyError", "ensure_atomic_route_table_publication"]


class RouteTableSafetyError(RuntimeError):
    """Raised when FastAPI's route-branch internals no longer match this shim."""


_INSTALL_LOCK = threading.Lock()
_installed = False

# Everything the replacement methods below read or write. A missing name means
# the upstream implementation changed and the replacements cannot be trusted.
_REQUIRED_BRANCH_ATTRS = (
    "effective_candidates",
    "effective_low_priority_routes",
    "_build_effective_context",
    "_effective_candidates",
    "_effective_candidates_version",
    "_effective_low_priority_routes",
    "_effective_low_priority_routes_version",
    "original_router",
    "include_context",
)


def _check_shape(branch_cls: type) -> None:
    missing = [
        name
        for name in _REQUIRED_BRANCH_ATTRS
        if not (hasattr(branch_cls, name) or name in getattr(branch_cls, "__annotations__", {}))
    ]
    if missing:
        raise RouteTableSafetyError(
            "fastapi.routing._IncludedRouter no longer exposes "
            f"{', '.join(missing)}; refusing to start because the route table "
            "can no longer be published atomically. Re-verify "
            "shared/api/route_table_safety.py against the installed FastAPI."
        )


def ensure_atomic_route_table_publication() -> None:
    """Make every included-router branch publish its route table in one step.

    Idempotent and cheap after the first call, so it is safe to call from
    ``create_app`` on every app construction.
    """
    global _installed
    if _installed:
        return
    with _INSTALL_LOCK:
        if _installed:
            return

        from fastapi import routing as fastapi_routing

        branch_cls: Any = getattr(fastapi_routing, "_IncludedRouter", None)
        if branch_cls is None:
            # No lazily built per-branch table in this FastAPI: nothing to fix.
            _installed = True
            return

        _check_shape(branch_cls)

        def effective_candidates(self: Any) -> list[Any]:
            routes_version = self.original_router._get_routes_version()
            if routes_version == self._effective_candidates_version:
                return self._effective_candidates
            built: list[Any] = []
            for route in self.original_router.routes:
                if isinstance(route, branch_cls):
                    child_context = self.include_context.combine(route.include_context)
                    built.append(
                        branch_cls(
                            original_router=route.original_router,
                            include_context=child_context,
                        )
                    )
                    continue
                route_context = self._build_effective_context(route)
                if route_context is not None:
                    built.append(route_context)
            # Single publication point: readers see the old table or this whole
            # one, never a table that is still being filled.
            self._effective_candidates = built
            self._effective_candidates_version = routes_version
            return built

        def effective_low_priority_routes(self: Any) -> list[Any]:
            routes_version = self.original_router._get_routes_version()
            if routes_version == self._effective_low_priority_routes_version:
                return self._effective_low_priority_routes
            built: list[Any] = []
            for route in self.original_router._low_priority_routes:
                route_context = self._build_effective_context(route)
                if route_context is not None:
                    built.append(route_context)
            for route in self.original_router.routes:
                if isinstance(route, branch_cls):
                    child_context = self.include_context.combine(route.include_context)
                    child_branch = branch_cls(
                        original_router=route.original_router,
                        include_context=child_context,
                    )
                    built.extend(child_branch.effective_low_priority_routes())
            self._effective_low_priority_routes = built
            self._effective_low_priority_routes_version = routes_version
            return built

        effective_candidates.__doc__ = branch_cls.effective_candidates.__doc__
        effective_low_priority_routes.__doc__ = (
            branch_cls.effective_low_priority_routes.__doc__
        )

        branch_cls.effective_candidates = effective_candidates
        branch_cls.effective_low_priority_routes = effective_low_priority_routes
        _installed = True
