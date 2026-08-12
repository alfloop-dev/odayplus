"""Route label normalization for HTTP telemetry.

ODP-SD-11 §5.1 declares ``route`` as a bounded label on ``api_request_count`` /
``api_error_count`` / ``api_latency_ms``. A raw request path is *not* bounded:
``/jobs/<uuid>`` mints one series per job and blows past any cardinality budget
within a single load wave.

This module maps a concrete request path back to the registered route template
(``/jobs/{job_id}``) so the emitted label set is bounded by the size of the
route table rather than by traffic volume. It duck-types the router — it only
reads ``path_format`` and nested ``routes`` off route objects — so it stays
importable without a web-framework dependency and is unit-testable in isolation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any

# Single reserved bucket for paths matching no registered route (404 probes,
# scanners). Without it every bogus path a scanner tries would mint a series.
UNMATCHED_ROUTE_TEMPLATE = "__unmatched__"

_PARAM_RE = re.compile(r"\{([^{}:]+)(?::([^{}]+))?\}")

# Converter name -> the path characters it may consume. Anything unknown falls
# back to a single non-slash segment, which is the framework default.
_CONVERTER_PATTERNS = {
    "path": r".*",
    "int": r"[0-9]+",
    "float": r"[0-9]+(?:\.[0-9]+)?",
    "uuid": r"[0-9a-fA-F-]+",
}
_DEFAULT_CONVERTER_PATTERN = r"[^/]+"


def compile_route_template(template: str) -> re.Pattern[str]:
    """Compile a ``/jobs/{job_id}`` style template into a full-match regex."""
    parts: list[str] = []
    cursor = 0
    for match in _PARAM_RE.finditer(template):
        parts.append(re.escape(template[cursor : match.start()]))
        converter = (match.group(2) or "").strip()
        parts.append(_CONVERTER_PATTERNS.get(converter, _DEFAULT_CONVERTER_PATTERN))
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("".join(parts) + r"/?$")


def _join(prefix: str, path_format: str) -> str:
    joined = f"{prefix.rstrip('/')}/{path_format.lstrip('/')}"
    return joined if joined.startswith("/") else f"/{joined}"


def iter_route_templates(routes: Iterable[Any], prefix: str = "") -> Iterator[str]:
    """Yield the fully-qualified template of every leaf route, recursing mounts.

    Traversal mirrors ``shared.api.versioning._iter_router_paths``: FastAPI 0.138
    does not flatten an included router into its parent, it wraps it in an
    ``_IncludedRouter`` that reports no ``path`` and keeps the real router on
    ``original_router`` with the mount prefix on ``include_context``. A naive
    ``routes`` scan misses the entire operator sub-tree, which would send every
    domain path into the unmatched bucket.
    """
    for route in routes:
        path_format = getattr(route, "path_format", None) or getattr(route, "path", None)
        if isinstance(path_format, str) and path_format:
            sub_routes = getattr(route, "routes", None)
            if sub_routes:
                # A Mount contributes only a prefix; children carry the real paths.
                yield from iter_route_templates(sub_routes, _join(prefix, path_format))
            else:
                yield _join(prefix, path_format)
            continue

        nested = getattr(route, "original_router", None)
        if nested is None:
            continue
        context = getattr(route, "include_context", None)
        nested_prefix = getattr(context, "prefix", "") or ""
        yield from iter_route_templates(
            getattr(nested, "routes", []), _join(prefix, nested_prefix) if nested_prefix else prefix
        )


class RouteTemplateResolver:
    """Resolves a concrete request path to its bounded route template.

    Templates are evaluated in registration order, mirroring how the router
    itself dispatches, so the label always names the handler that actually ran.
    """

    def __init__(self, routes: Iterable[Any]) -> None:
        seen: set[str] = set()
        self._patterns: list[tuple[str, re.Pattern[str]]] = []
        for template in iter_route_templates(routes):
            if template in seen:
                continue
            seen.add(template)
            self._patterns.append((template, compile_route_template(template)))

    @property
    def template_count(self) -> int:
        return len(self._patterns)

    def templates(self) -> list[str]:
        return [template for template, _ in self._patterns]

    def resolve(self, path: str) -> str:
        """Return the bounded route template for ``path``.

        Falls back to :data:`UNMATCHED_ROUTE_TEMPLATE` so an unrouted path can
        never contribute an unbounded label value.
        """
        if not isinstance(path, str) or not path:
            return UNMATCHED_ROUTE_TEMPLATE
        for template, pattern in self._patterns:
            if pattern.match(path):
                return template
        return UNMATCHED_ROUTE_TEMPLATE
