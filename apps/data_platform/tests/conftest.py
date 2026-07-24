from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from apps.data_platform.contracts import SourceEnvelope, SourceKind
from apps.data_platform.source import envelope_for_document


@pytest.fixture
def observed_at() -> datetime:
    return datetime(2026, 7, 24, 12, tzinfo=UTC)


@pytest.fixture
def envelope_factory(observed_at: datetime):
    def build(
        kind: SourceKind,
        document: dict[str, Any],
        *,
        run_id: str = "00000000-0000-4000-8000-000000000001",
    ) -> SourceEnvelope:
        return envelope_for_document(
            kind,
            document,
            run_id=run_id,
            observed_at=observed_at,
        )

    return build
