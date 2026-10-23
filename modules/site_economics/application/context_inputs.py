"""Validate simulation inputs without inventing observations or granting admission.

Content binding is evidence, not proof of source authority, freshness, current
credential ownership or financial approval. Those remain request-owner duties.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

INPUT_POLICY_VERSION = "site-economics-context-input-v1"


class SiteEconomicsInputError(ValueError):
    """Safe code and field name; never includes rejected source values."""

    def __init__(self, code: str, field: str):
        self.code, self.field = code, field
        super().__init__(code + ":" + field)


def _json_value(value: Any) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if isinstance(value, Mapping) and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise SiteEconomicsInputError("SITE_ECONOMICS_CONTEXT_JSON_INVALID", "market_context")


def content_sha256(value: Mapping[str, Any]) -> str:
    """Hash finite JSON content, ignoring no fields and trusting no hash claims."""
    _json_value(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise SiteEconomicsInputError("SITE_ECONOMICS_IDENTITY_INVALID", field)
    return value


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    context: dict[str, Any]
    site_id: str
    context_id: str
    sha256: str
    document_id: str | None
    document_sha256: str
    tenant_declared: bool


def select_market_context(value: Any, *, site_id: str | None = None,
                          tenant_id: str | None = None) -> ContextSnapshot:
    """Select exactly one site in a detached copy of a released wire document."""
    try:
        root = value if isinstance(value, Mapping) else value.to_dict()
    except (AttributeError, TypeError, ValueError, KeyError):
        raise SiteEconomicsInputError("SITE_ECONOMICS_CONTEXT_INVALID", "market_context") from None
    if not isinstance(root, Mapping):
        raise SiteEconomicsInputError("SITE_ECONOMICS_CONTEXT_INVALID", "market_context")
    _json_value(root)
    # A JSON round trip also prevents the caller changing a source mid-simulation.
    root = json.loads(json.dumps(dict(root), allow_nan=False))
    if site_id is not None:
        site_id = _text(site_id, "site_id")
    if "contexts" in root:
        rows = root["contexts"]
        if type(rows) is not list or not rows or any(type(row) is not dict for row in rows):
            raise SiteEconomicsInputError("SITE_ECONOMICS_CONTEXT_INVALID", "contexts")
        matches = [row for row in rows if site_id is None or (
            isinstance(row.get("identity"), dict) and row["identity"].get("site_id") == site_id
        )]
        if len(matches) != 1:
            raise SiteEconomicsInputError("SITE_ECONOMICS_CONTEXT_SELECTION_REQUIRED", "contexts")
        context = matches[0]
        document_id = _text(root.get("document_id"), "document_id")
        for field in ("period_grain", "period_key"):
            if field in root and root[field] != context.get(field):
                raise SiteEconomicsInputError("SITE_ECONOMICS_CONTEXT_SCOPE_MISMATCH", field)
    else:
        context, document_id = root, None
    identity = context.get("identity")
    if not isinstance(identity, dict):
        raise SiteEconomicsInputError("SITE_ECONOMICS_IDENTITY_INVALID", "identity.site_id")
    selected = _text(identity.get("site_id"), "identity.site_id")
    if (site_id is not None and selected != site_id) or (
        "site_id" in context and context["site_id"] != selected
    ):
        raise SiteEconomicsInputError("SITE_ECONOMICS_CONTEXT_SCOPE_MISMATCH", "site_id")
    context_id = _text(context.get("context_id"), "context_id")
    declared_tenants = [source["tenant_id"] for source in (root, context)
                        if "tenant_id" in source]
    for declared in declared_tenants:
        _text(declared, "tenant_id")
        if (tenant_id is None or declared != tenant_id):
            raise SiteEconomicsInputError("SITE_ECONOMICS_CONTEXT_SCOPE_MISMATCH", "tenant_id")
    return ContextSnapshot(context, selected, context_id, content_sha256(context),
                           document_id, content_sha256(root), bool(declared_tenants))


def numeric_input(value: Any, field: str, *, minimum: float = 0,
                  exclusive: bool = False, maximum: float | None = None,
                  integer: bool = False) -> float:
    if type(value) not in (int, float) or (integer and type(value) is not int):
        raise SiteEconomicsInputError("SITE_ECONOMICS_INPUT_REQUIRED", field)
    try:
        valid = math.isfinite(value) and (value > minimum if exclusive else value >= minimum)
        valid = valid and (maximum is None or value <= maximum)
    except OverflowError:
        valid = False
    if not valid:
        raise SiteEconomicsInputError("SITE_ECONOMICS_INPUT_INVALID", field)
    return float(value)


def context_number(context: Mapping[str, Any], domain: str, field: str, **bounds) -> float:
    source = context.get(domain)
    if not isinstance(source, Mapping) or (
        "status" in source and source["status"] not in ("available", "partial")
    ):
        raise SiteEconomicsInputError("SITE_ECONOMICS_DOMAIN_UNAVAILABLE", domain)
    return numeric_input(source.get(field), domain + "." + field, **bounds)
