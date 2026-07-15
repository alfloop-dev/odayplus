"""Consistent pagination, filtering and sorting for collection endpoints.

Every collection endpoint hand-rolled ``{"items": [...], "count": len(items)}``
with no offset, no total, and no sort. Two endpoints accepted ``limit`` and
disagreed on what it meant: ``heatzone`` took the *head* of the list while
``external_data`` took the *tail*, and neither validated the bound -- a negative
``limit`` slipped past ``external_data``'s cap and returned the whole table.

:class:`PageParams` is the one declaration of those semantics, and
:func:`paginate` the one implementation.

Compatibility
-------------
The envelope is additive. ``items`` and ``count`` keep their existing meaning --
``count`` is the number of rows *on this page*, which is what today's clients
and tests already assume -- and ``total``/``limit``/``offset``/``has_more`` are
new. An existing caller that ignores the new fields and passes no query
parameters sees byte-identical output to before, because the default limit is
applied only when the caller asks for one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

__all__ = ["PageParams", "Page", "paginate", "page_params", "DEFAULT_LIMIT", "MAX_LIMIT"]


DEFAULT_LIMIT = 100
MAX_LIMIT = 500


@dataclass(frozen=True)
class PageParams:
    """Validated pagination + sort inputs."""

    limit: int = DEFAULT_LIMIT
    offset: int = 0
    sort: str | None = None
    order: str = "asc"

    @property
    def descending(self) -> bool:
        return self.order.lower() == "desc"


class Page(BaseModel):
    """The collection envelope returned by every list endpoint."""

    items: list[Any] = Field(description="Rows on this page.")
    count: int = Field(description="Number of rows on this page (len(items)).")
    total: int = Field(description="Total rows matching the filter, across all pages.")
    limit: int = Field(description="Page size applied.")
    offset: int = Field(description="Rows skipped before this page.")
    has_more: bool = Field(description="True when further rows exist after this page.")


def page_params(
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    sort: str | None = None,
    order: str = "asc",
) -> PageParams:
    """FastAPI dependency yielding validated :class:`PageParams`.

    Bounds are enforced here rather than trusted from the wire: ``limit`` is
    clamped into ``[1, MAX_LIMIT]`` and ``offset`` floored at 0, so the negative
    ``limit`` that bypassed ``external_data``'s cap cannot recur.
    """
    from fastapi import Query  # noqa: F401  (imported for callers wiring Query(...))

    safe_limit = max(1, min(int(limit), MAX_LIMIT))
    safe_offset = max(0, int(offset))
    safe_order = order.lower() if order.lower() in {"asc", "desc"} else "asc"
    return PageParams(limit=safe_limit, offset=safe_offset, sort=sort, order=safe_order)


def _sort_key(row: Any, field: str) -> tuple[int, str]:
    """Sort key that tolerates heterogeneous and missing values.

    Rows are plain dicts assembled by ``to_dict()`` across many domains, so a
    sort field may be absent on some rows or mix ``None`` with strings and
    numbers. Comparing those directly raises ``TypeError`` and would 500 the
    endpoint. Missing/None sorts last, and every present value is compared as a
    string, so the sort is total and never raises.
    """
    value = row.get(field) if isinstance(row, dict) else getattr(row, field, None)
    if value is None:
        return (1, "")
    return (0, str(value))


def paginate[T](rows: list[T], params: PageParams) -> dict[str, Any]:
    """Apply sort then window, and return the collection envelope.

    ``total`` is computed before windowing so callers can render "showing 20 of
    413" without a second request.
    """
    ordered = list(rows)
    if params.sort:
        ordered.sort(key=lambda row: _sort_key(row, params.sort), reverse=params.descending)
    elif params.descending:
        ordered.reverse()

    total = len(ordered)
    window = ordered[params.offset : params.offset + params.limit]
    return {
        "items": window,
        "count": len(window),
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
        "has_more": params.offset + len(window) < total,
    }
