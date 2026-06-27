from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

CORRELATION_ID_HEADER = "x-correlation-id"


def new_correlation_id() -> str:
    return str(uuid4())


@dataclass(frozen=True)
class CorrelationContext:
    correlation_id: str = field(default_factory=new_correlation_id)

    @classmethod
    def from_header(cls, value: str | None) -> CorrelationContext:
        if value and value.strip():
            return cls(correlation_id=value.strip())
        return cls()
