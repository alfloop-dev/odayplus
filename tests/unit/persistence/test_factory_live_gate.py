from __future__ import annotations

import pytest

from shared.infrastructure.persistence.factory import build_persistence


@pytest.mark.parametrize("mode", ["memory", "durable", "sqlite"])
def test_live_data_runtime_rejects_surrogate_persistence(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")

    with pytest.raises(RuntimeError, match="production PostgreSQL persistence adapter"):
        build_persistence(mode=mode)


def test_unknown_persistence_mode_never_falls_back_to_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)

    with pytest.raises(ValueError, match="Unsupported ODP_PERSISTENCE mode"):
        build_persistence(mode="postgres")


def test_local_memory_mode_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)

    bundle = build_persistence(mode="memory")

    assert bundle.mode == "memory"
    assert bundle.is_durable is False
