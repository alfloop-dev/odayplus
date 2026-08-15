from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Command:
    name: str
    payload: dict[str, Any]
    command_id: str = field(default_factory=lambda: str(uuid4()))
    idempotency_key: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class Query:
    name: str
    filters: dict[str, Any]
    correlation_id: str | None = None
