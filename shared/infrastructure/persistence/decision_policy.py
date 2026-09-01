"""SQL-backed access to the versioned decision-policy registry."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from shared.governance import DecisionPolicy


class SqlDecisionPolicyRepository:
    """Resolve policies from the canonical PostgreSQL registry.

    The registry is owned by the ``workflow`` schema and is populated by the
    canonical decision-policy migration. This repository is intentionally
    read-only: policy creation and supersession belong to governance tooling,
    while decision-producing runtimes only resolve the version that governed
    the decision timestamp.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def find_effective(
        self, *, policy_kind: str, tenant_id: str, at: datetime
    ) -> DecisionPolicy | None:
        normalized_tenant_id = str(tenant_id or "").strip()
        if not normalized_tenant_id:
            raise ValueError("tenant_id is required for decision-policy resolution")
        row = self._engine.query_one(
            """
            SELECT policy_version_id, policy_label, policy_id, policy_version,
                   policy_kind, tenant_id, effective_from, effective_to,
                   change_reason, rollback_policy_version, parameters,
                   declared_inputs, approved_by, owner_role
            FROM workflow.decision_policies
            WHERE policy_kind = ?
              AND tenant_id = ?
              AND effective_from <= ?
              AND (effective_to IS NULL OR ? < effective_to)
            ORDER BY effective_from DESC
            LIMIT 1
            """,
            (policy_kind, normalized_tenant_id, at, at),
        )
        if row is None:
            return None
        return DecisionPolicy(
            policy_version_id=str(row["policy_version_id"]),
            policy_label=str(row["policy_label"]),
            policy_id=str(row["policy_id"]),
            policy_version=str(row["policy_version"]),
            policy_kind=str(row["policy_kind"]),
            tenant_id=str(row["tenant_id"]),
            effective_from=_parse_datetime(row["effective_from"]),
            effective_to=(
                _parse_datetime(row["effective_to"])
                if row.get("effective_to") is not None
                else None
            ),
            parameters=_parse_parameters(row["parameters"]),
            declared_inputs=_parse_declared_inputs(row["declared_inputs"]),
            change_reason=str(row.get("change_reason") or ""),
            rollback_policy_version=(
                str(row["rollback_policy_version"])
                if row.get("rollback_policy_version") is not None
                else None
            ),
            approved_by=str(row.get("approved_by") or ""),
            owner_role=str(row.get("owner_role") or ""),
        )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_json(value: Any, *, field_name: str) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"decision policy {field_name} is not valid JSON") from exc
    return value


def _parse_parameters(value: Any) -> Mapping[str, Any]:
    parsed = _parse_json(value, field_name="parameters")
    if not isinstance(parsed, Mapping):
        raise ValueError("decision policy parameters must be a JSON object")
    return dict(parsed)


def _parse_declared_inputs(value: Any) -> tuple[str, ...]:
    parsed = _parse_json(value, field_name="declared_inputs")
    if not isinstance(parsed, Sequence) or isinstance(parsed, (str, bytes)):
        raise ValueError("decision policy declared_inputs must be an array")
    inputs = tuple(str(item) for item in parsed)
    if not inputs:
        raise ValueError("decision policy declared_inputs must not be empty")
    return inputs


# Keep a descriptive alias for callers that refer to all database-backed
# repositories as durable repositories.
DurableDecisionPolicyRepository = SqlDecisionPolicyRepository


__all__ = ["DurableDecisionPolicyRepository", "SqlDecisionPolicyRepository"]
